// Anonymous catalogue search used by RHYFT's Android client through ytmusicapi.
// Protocol references: sigma67/ytmusicapi helpers.py, mixins/search.py and
// parsers/search.py at 4aeaf7d0aa48e3fb56eb229ec04593655acba091.
// This public Music interface is not the official YouTube Data API. Account
// operations must continue using OAuth, and upstream blocks must be respected.
const SEARCH_URL = 'https://music.youtube.com/youtubei/v1/search?alt=json';
const MUSIC_URL = 'https://music.youtube.com/';
// Match the normal public client headers initialized by ytmusicapi 1.12.3.
// This is a stable protocol header, not a fallback around an upstream block.
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0) Gecko/20100101 Firefox/88.0';
const FILTERS = {
  songs: 'EgWKAQIIAWoMEA4QChADEAQQCRAF',
  videos: 'EgWKAQIQAWoMEA4QChADEAQQCRAF'
};

class MusicSearchError extends Error {
  constructor(status, message, code, details = {}, retryable = false) {
    super(message);
    this.name = 'MusicSearchError';
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.details = {provider: 'youtube-music', operation: 'search', method: 'POST', attempts: 1,
      stage: details.method === 'GET' ? 'catalog-init' : 'catalog-search', ...details};
  }
}

function invalidResponse(details = {}) {
  const message = details.stage === 'catalog-init' || details.method === 'GET'
    ? 'Não consegui interpretar a configuração pública do YouTube Music ao iniciar a busca.'
    : 'Não foi possível interpretar os resultados da busca pública do YouTube Music.';
  return new MusicSearchError(502, message, 'YOUTUBE_MUSIC_FORMAT_CHANGED', {cause: 'invalid-response', ...details});
}

const STRUCTURAL_KEYS = new Set(['html', 'json', 'text', 'ytcfg.set', 'ytcfg.data_', 'VISITOR_DATA',
  'INNERTUBE_CONTEXT', 'INNERTUBE_CLIENT_VERSION', 'INNERTUBE_CONTEXT_CLIENT_VERSION',
  'contents', 'responseContext', 'tabbedSearchResultsRenderer', 'singleColumnBrowseResultsRenderer',
  'twoColumnSearchResultsRenderer', 'twoColumnBrowseResultsRenderer', 'sectionListRenderer',
  'musicShelfRenderer', 'musicCardShelfRenderer', 'itemSectionRenderer', 'musicResponsiveListItemRenderer',
  'musicTwoRowItemRenderer', 'musicMultiRowListItemRenderer', 'playlistItemData', 'continuationContents',
  'musicShelfContinuation', 'messageRenderer', 'error']);

function responseShape(value) {
  const found = new Set();
  if (typeof value === 'string') {
    found.add(/^\s*(?:<!doctype\b|<html\b|<script\b)/i.test(value) ? 'html' : 'text');
    if (/ytcfg\s*\.\s*set\s*\(/.test(value)) found.add('ytcfg.set');
    if (/ytcfg\s*\.\s*data_\s*=/.test(value)) found.add('ytcfg.data_');
    for (const key of ['VISITOR_DATA', 'INNERTUBE_CONTEXT', 'INNERTUBE_CLIENT_VERSION', 'INNERTUBE_CONTEXT_CLIENT_VERSION']) {
      if (value.includes(`"${key}"`)) found.add(key);
    }
  } else {
    found.add('json');
    const queue = [{value, depth: 0}];
    for (let index = 0; index < queue.length && index < 512 && found.size < 12; index++) {
      const entry = queue[index];
      if (!entry.value || typeof entry.value !== 'object' || entry.depth > 10) continue;
      for (const [key, child] of Object.entries(entry.value)) {
        if (STRUCTURAL_KEYS.has(key)) found.add(key);
        if (child && typeof child === 'object' && queue.length < 512) queue.push({value: child, depth: entry.depth + 1});
      }
    }
  }
  return [...found].slice(0, 12);
}

function textOf(value) {
  if (typeof value?.simpleText === 'string') return value.simpleText;
  return Array.isArray(value?.runs) ? value.runs.map(run => typeof run?.text === 'string' ? run.text : '').join('') : '';
}

function durationOf(text) {
  if (!/^\d+(?::\d{1,2}){1,2}$/.test(text)) return null;
  const parts = text.split(':').map(Number);
  if (parts.slice(1).some(part => part > 59)) return null;
  return parts.reduce((seconds, part) => seconds * 60 + part, 0);
}

function endpointOf(renderer) {
  const title = renderer.flexColumns?.[0]?.musicResponsiveListItemFlexColumnRenderer?.text?.runs;
  const candidates = [
    renderer.overlay?.musicItemThumbnailOverlayRenderer?.content?.musicPlayButtonRenderer?.playNavigationEndpoint,
    renderer.thumbnailOverlay?.musicItemThumbnailOverlayRenderer?.content?.musicPlayButtonRenderer?.playNavigationEndpoint,
    renderer.navigationEndpoint,
    ...(Array.isArray(title) ? title.map(run => run?.navigationEndpoint) : []),
    renderer.onTap
  ];
  const endpoint = candidates.find(endpoint => endpoint?.watchEndpoint?.videoId);
  if (endpoint) return endpoint;
  // Actual 2024/2026 catalogue rows also carry the playable ID here; unlike a
  // playlist ID or set-video ID, this is a public song/video identifier.
  return renderer.playlistItemData?.videoId ? {watchEndpoint: {videoId: renderer.playlistItemData.videoId}} : undefined;
}

function metadataOf(runs) {
  const artists = [];
  let duration = null;
  for (const run of runs) {
    const text = typeof run?.text === 'string' ? run.text.trim() : '';
    if (!text || /^[•·,&/|]+$/.test(text)) continue;
    const seconds = durationOf(text);
    if (seconds !== null) { duration = seconds; continue; }
    const browseId = run.navigationEndpoint?.browseEndpoint?.browseId;
    if (browseId && (/^MPRE/.test(browseId) || /release_detail/.test(browseId))) continue;
    if (!browseId && (/^(?:song|video|single|album|ep|live|podcast|episode)$/i.test(text)
      || /^\d{4}$/.test(text) || /^\d[\d.,\s\u00a0]*[KMB]?\s+(?:views?|plays?|listens?)\b/i.test(text))) continue;
    if (text.length <= 200 && !artists.includes(text)) artists.push(text);
  }
  return {artists, duration};
}

function itemOf(renderer, filter, card = false) {
  if (!renderer || typeof renderer !== 'object') return null;
  if (renderer.musicItemRendererDisplayPolicy === 'MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT') return null;
  const endpoint = endpointOf(renderer);
  const watch = endpoint?.watchEndpoint;
  const id = watch?.videoId;
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{11}$/.test(id)) return null;
  const type = watch.watchEndpointMusicSupportedConfigs?.watchEndpointMusicConfig?.musicVideoType;
  if (type === 'MUSIC_VIDEO_TYPE_PODCAST_EPISODE') return null;
  if (filter === 'songs' && type && type !== 'MUSIC_VIDEO_TYPE_ATV') return null;
  if (card && /^(?:album|artist|playlist|podcast|episode)$/i.test(renderer.subtitle?.runs?.[0]?.text?.trim() || '')) return null;
  const columns = Array.isArray(renderer.flexColumns) ? renderer.flexColumns : [];
  const title = textOf(card ? renderer.title : columns[0]?.musicResponsiveListItemFlexColumnRenderer?.text).trim();
  if (!title || title.length > 500) return null;
  const runs = card ? [...(renderer.subtitle?.runs || [])]
    : columns.slice(1).flatMap(column => column?.musicResponsiveListItemFlexColumnRenderer?.text?.runs || []);
  for (const column of renderer.fixedColumns || []) {
    runs.push(...(column?.musicResponsiveListItemFixedColumnRenderer?.text?.runs || []));
  }
  const {artists, duration} = metadataOf(runs);
  return {id, title, artists, channel: artists.join(', '), duration, url: `https://www.youtube.com/watch?v=${id}`};
}

function parseResults(data, {filter = 'songs', limit = 10} = {}) {
  const shape = responseShape(data);
  const invalid = () => invalidResponse({stage: 'catalog-search', responseShape: shape});
  if (filter !== null && !FILTERS[filter] || !data || typeof data !== 'object' || Array.isArray(data) || data.error) throw invalid();
  const content = data.contents;
  // Native SearchMixin treats a recognized response context without contents as
  // an empty result. Unknown objects still fail instead of becoming cached [] .
  if (!content && data.responseContext && typeof data.responseContext === 'object' && !Array.isArray(data.responseContext)
    && !data.continuationContents) return [];
  const tabs = content?.tabbedSearchResultsRenderer?.tabs || content?.singleColumnBrowseResultsRenderer?.tabs;
  const selected = Array.isArray(tabs) ? tabs.find(tab => tab?.tabRenderer?.selected)?.tabRenderer || tabs[0]?.tabRenderer : null;
  const area = selected?.content || content?.twoColumnSearchResultsRenderer?.primaryContents || content;
  const sections = area?.sectionListRenderer?.contents;
  if (!Array.isArray(sections)) throw invalid();
  const items = [], seen = new Set();
  let recognized = sections.length === 0, malformed = false;
  const append = (renderer, card = false) => {
    const item = itemOf(renderer, filter, card);
    if (item && !seen.has(item.id)) { seen.add(item.id); items.push(item); return; }
    if (item) return;
    const type = endpointOf(renderer)?.watchEndpoint?.watchEndpointMusicSupportedConfigs?.watchEndpointMusicConfig?.musicVideoType;
    const browseId = renderer.navigationEndpoint?.browseEndpoint?.browseId;
    const nonMusicCard = card && /^(?:album|artist|playlist|podcast|episode)$/i.test(renderer.subtitle?.runs?.[0]?.text?.trim() || '');
    const skipped = renderer.musicItemRendererDisplayPolicy === 'MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT'
      || type === 'MUSIC_VIDEO_TYPE_PODCAST_EPISODE' || filter === 'songs' && type && type !== 'MUSIC_VIDEO_TYPE_ATV'
      || nonMusicCard || !endpointOf(renderer) && /^(?:UC|MPRE|VL|VM|MPLA|MPSP|MPED)/.test(browseId || '');
    if (!skipped) malformed = true;
  };
  const appendRows = rows => {
    if (!Array.isArray(rows)) { malformed = true; return; }
    for (const result of rows) {
      if (result?.messageRenderer || result?.musicDidYouMeanRenderer || result?.musicShowingResultsForRenderer
        || result?.continuationItemRenderer) continue;
      const renderer = result?.musicResponsiveListItemRenderer;
      if (!renderer) { malformed = true; continue; }
      append(renderer);
    }
  };
  for (const section of sections) {
    const shelf = section?.musicShelfRenderer;
    const itemSection = section?.itemSectionRenderer;
    const card = section?.musicCardShelfRenderer;
    if (shelf || itemSection) {
      recognized = true;
      const contents = (shelf || itemSection).contents;
      if (!Array.isArray(contents)) { malformed = true; continue; }
      const label = textOf(shelf?.title).trim().toLowerCase();
      // The request uses English metadata. Never turn padded album/playlist
      // shelves into songs simply because they appeared in a filtered response.
      if (label && ['albums', 'artists', 'playlists', 'podcasts', 'episodes'].includes(label)) continue;
      appendRows(contents);
    } else if (card) {
      // Explicitly supported by ytmusicapi parse_top_result, including responses
      // consisting of a top-result card without a separate Songs shelf.
      recognized = true;
      if (endpointOf(card)) append(card, true);
      if (card.contents !== undefined) appendRows(card.contents);
    } else if (section?.musicDidYouMeanRenderer || section?.musicShowingResultsForRenderer || section?.messageRenderer) {
      recognized = true;
    }
  }
  if (!recognized || !items.length && malformed) throw invalid();
  return items.slice(0, Math.max(1, Math.min(20, Number(limit) || 10)));
}

function retryAfter(response, now) {
  const value = response.headers?.get('retry-after');
  if (!value) return 0;
  if (/^\d+(?:\.\d+)?$/.test(value)) return Math.max(0, Number(value) * 1000);
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? Math.max(0, timestamp - now()) : 0;
}

function readJsonObject(html, start) {
  let depth = 0, quoted = false, escaped = false;
  for (let position = start; position < html.length; position++) {
    const character = html[position];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') quoted = false;
    } else if (character === '"') quoted = true;
    else if (character === '{') depth++;
    else if (character === '}' && --depth === 0) {
      try { return JSON.parse(html.slice(start, position + 1)); } catch { return null; }
    }
  }
  return null;
}

function publicConfiguration(html, now) {
  // ytmusicapi gets this public, anonymous visitor context from ytcfg.set on
  // the Music homepage. Parse balanced JSON rather than evaluating page code;
  // merge public object updates and consume only explicitly allowed fields.
  const expression = /(?:ytcfg\s*\.\s*set\s*\(\s*|ytcfg\s*\.\s*data_\s*=\s*)(?=\{)/g;
  const config = {};
  let match;
  while ((match = expression.exec(html))) {
    const update = readJsonObject(html, expression.lastIndex);
    if (update && typeof update === 'object' && !Array.isArray(update)) {
      for (const field of ['VISITOR_DATA', 'INNERTUBE_CONTEXT', 'INNERTUBE_CLIENT_VERSION', 'INNERTUBE_CONTEXT_CLIENT_VERSION']) {
        if (Object.hasOwn(update, field)) config[field] = update[field];
      }
    }
  }
  const pageClient = config.INNERTUBE_CONTEXT?.client;
  const visitor = [config.VISITOR_DATA, pageClient?.visitorData].find(value =>
    typeof value === 'string' && /^[A-Za-z0-9+/_=%.-]{1,4096}$/.test(value));
  const version = [pageClient?.clientVersion, config.INNERTUBE_CLIENT_VERSION, config.INNERTUBE_CONTEXT_CLIENT_VERSION]
    .find(value => typeof value === 'string' && /^\d+(?:\.[A-Za-z0-9_-]+){1,6}$/.test(value) && value.length <= 64);
  const date = new Date(now()).toISOString().slice(0, 10).replace(/-/g, '');
  const client = {clientName: 'WEB_REMIX', clientVersion: version || `1.${date}.01.00`, hl: 'en'};
  if (typeof pageClient?.gl === 'string' && /^[A-Z]{2}$/.test(pageClient.gl)) client.gl = pageClient.gl;
  // Native get_visitor_id returns an empty visitor when metadata is absent.
  // That metadata is optional, so a successful anonymous homepage must not
  // become a fatal format error before the public search has even been tried.
  return {visitorData: visitor || null, client};
}

function awaitAbortable(promise, signal) {
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', abort, {once: true});
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

function createClient(options = {}) {
  const fetchPublic = options.fetch || globalThis.fetch;
  const now = options.now || Date.now;
  const schedule = options.setTimeout || setTimeout;
  const unschedule = options.clearTimeout || clearTimeout;
  const timeoutMs = Math.max(1, Number(options.timeoutMs) || 7000);
  let configuration = null, configurationExpiresAt = 0, initializing = null;
  const initialize = () => {
    if (configuration && configurationExpiresAt > now()) return Promise.resolve(configuration);
    if (initializing) return initializing;
    const controller = new AbortController();
    const timer = schedule(() => controller.abort(), timeoutMs);
    initializing = (async () => {
      const response = await fetchPublic(MUSIC_URL, {method: 'GET', redirect: 'manual', signal: controller.signal,
        headers: {Accept: 'text/html', 'User-Agent': USER_AGENT, Origin: 'https://music.youtube.com', 'Accept-Language': 'en-US,en;q=0.9'}});
      const html = await response.text();
      if (response.status === 429) throw new MusicSearchError(429, 'O YouTube Music limitou temporariamente as buscas públicas. Aguarde o intervalo informado para continuar.', 'YOUTUBE_MUSIC_RATE_LIMIT', {method: 'GET', cause: 'rate-limit', upstreamStatus: 429, retryAfterMs: retryAfter(response, now)}, true);
      if ([401, 403].includes(response.status) || response.status >= 300 && response.status < 400
        || /unusual traffic from your computer network|<form[^>]+action=["'][^"']*(?:\/sorry\/|consent\.)/i.test(html)) {
        throw new MusicSearchError(403, 'O YouTube Music recusou a busca pública ou solicitou verificação no site. A migração não consegue continuar essa busca automaticamente.', 'YOUTUBE_MUSIC_CATALOG_BLOCKED', {method: 'GET', cause: 'catalog-blocked', upstreamStatus: response.status});
      }
      if (!response.ok) throw new MusicSearchError(response.status >= 500 ? response.status : 502, 'O serviço de busca pública do YouTube Music está indisponível.', 'YOUTUBE_MUSIC_UNAVAILABLE', {method: 'GET', cause: 'network', upstreamStatus: response.status}, response.status >= 500);
      configuration = publicConfiguration(html, now);
      configurationExpiresAt = now() + 15 * 60 * 1000;
      return configuration;
    })().finally(() => { unschedule(timer); initializing = null; });
    return initializing;
  };
  return {
    async search(input, {filter = 'songs', limit = 10, signal} = {}) {
      const query = String(input || '').trim().slice(0, 180);
      if (!query || filter !== null && !FILTERS[filter]) throw new MusicSearchError(400, 'Informe uma busca musical válida.', 'BAD_QUERY');
      if (signal?.aborted) throw new MusicSearchError(499, 'A busca foi cancelada.', 'REQUEST_ABORTED', {cause: 'aborted'});
      const controller = new AbortController();
      let timedOut = false, stage = 'catalog-init';
      const abort = () => controller.abort();
      signal?.addEventListener('abort', abort, {once: true});
      const timer = schedule(() => { timedOut = true; controller.abort(); }, timeoutMs);
      try {
        const publicConfig = await awaitAbortable(initialize(), controller.signal);
        stage = 'catalog-search';
        const response = await fetchPublic(SEARCH_URL, {
          method: 'POST', redirect: 'manual', signal: controller.signal,
          headers: {'Content-Type': 'application/json', Accept: 'application/json', Origin: 'https://music.youtube.com', 'User-Agent': USER_AGENT,
            'Accept-Language': 'en-US,en;q=0.9', ...(publicConfig.visitorData ? {'X-Goog-Visitor-Id': publicConfig.visitorData} : {})},
          body: JSON.stringify({query, ...(filter !== null ? {params: FILTERS[filter]} : {}), context: {client: publicConfig.client, user: {}}})
        });
        const text = await response.text();
        if (response.status === 429) throw new MusicSearchError(429, 'O YouTube Music limitou temporariamente as buscas públicas. Aguarde o intervalo informado para continuar.', 'YOUTUBE_MUSIC_RATE_LIMIT', {cause: 'rate-limit', upstreamStatus: 429, retryAfterMs: retryAfter(response, now)}, true);
        if ([401, 403].includes(response.status) || response.status >= 300 && response.status < 400
          || /^\s*(?:<html\b|<!doctype\b)/i.test(text)) {
          throw new MusicSearchError(403, 'O YouTube Music recusou a busca pública ou solicitou verificação no site. A migração não consegue continuar essa busca automaticamente.', 'YOUTUBE_MUSIC_CATALOG_BLOCKED', {cause: 'catalog-blocked', upstreamStatus: response.status});
        }
        if (!response.ok) throw new MusicSearchError(response.status >= 500 ? response.status : 502, 'O serviço de busca pública do YouTube Music está indisponível.', 'YOUTUBE_MUSIC_UNAVAILABLE', {cause: 'network', upstreamStatus: response.status}, response.status >= 500);
        let data;
        try { data = JSON.parse(text); } catch { throw invalidResponse({stage, upstreamStatus: response.status, responseShape: responseShape(text)}); }
        try {
          return {items: parseResults(data, {filter, limit}), filter};
        } catch (error) {
          if (error instanceof MusicSearchError) error.details.upstreamStatus = response.status;
          throw error;
        }
      } catch (error) {
        if (error instanceof MusicSearchError) throw error;
        const context = {stage, method: stage === 'catalog-init' ? 'GET' : 'POST'};
        if (timedOut) throw new MusicSearchError(504, 'O YouTube Music demorou demais para responder à busca pública. Tente novamente.', 'PROVIDER_TIMEOUT', {...context, cause: 'timeout', timeoutMs}, true);
        if (signal?.aborted) throw new MusicSearchError(499, 'A busca foi cancelada.', 'REQUEST_ABORTED', {...context, cause: 'aborted'});
        throw new MusicSearchError(502, 'Não consegui acessar a busca pública do YouTube Music. Verifique a conexão e tente novamente.', 'PROVIDER_UNAVAILABLE', {...context, cause: 'network'}, true);
      } finally {
        unschedule(timer);
        signal?.removeEventListener('abort', abort);
      }
    }
  };
}

module.exports = {createClient, MusicSearchError, parseResults};
