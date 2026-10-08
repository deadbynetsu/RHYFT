// Anonymous catalogue search used by RHYFT's Android client through ytmusicapi.
// Protocol references: sigma67/ytmusicapi helpers.py, mixins/search.py and
// parsers/search.py at 4aeaf7d0aa48e3fb56eb229ec04593655acba091.
// This public Music interface is not the official YouTube Data API. Account
// operations must continue using OAuth, and upstream blocks must be respected.
const SEARCH_URL = 'https://music.youtube.com/youtubei/v1/search?alt=json';
const MUSIC_URL = 'https://music.youtube.com/';
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
    this.details = {provider: 'youtube-music', operation: 'search', method: 'POST', attempts: 1, ...details};
  }
}

function invalidResponse(details = {}) {
  return new MusicSearchError(502, 'O formato da busca pública do YouTube Music mudou ou está incompleto. Não foi possível interpretar os resultados.', 'YOUTUBE_MUSIC_FORMAT_CHANGED', {cause: 'invalid-response', ...details});
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
    renderer.navigationEndpoint,
    ...(Array.isArray(title) ? title.map(run => run?.navigationEndpoint) : []),
    renderer.onTap
  ];
  return candidates.find(endpoint => endpoint?.watchEndpoint?.videoId);
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

function itemOf(renderer, filter) {
  if (!renderer || typeof renderer !== 'object') return null;
  if (renderer.musicItemRendererDisplayPolicy === 'MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT') return null;
  const endpoint = endpointOf(renderer);
  const watch = endpoint?.watchEndpoint;
  const id = watch?.videoId;
  if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{11}$/.test(id)) return null;
  const type = watch.watchEndpointMusicSupportedConfigs?.watchEndpointMusicConfig?.musicVideoType;
  if (type === 'MUSIC_VIDEO_TYPE_PODCAST_EPISODE') return null;
  if (filter === 'songs' && type && type !== 'MUSIC_VIDEO_TYPE_ATV') return null;
  const columns = Array.isArray(renderer.flexColumns) ? renderer.flexColumns : [];
  const title = textOf(columns[0]?.musicResponsiveListItemFlexColumnRenderer?.text).trim();
  if (!title || title.length > 500) return null;
  const runs = columns.slice(1).flatMap(column => column?.musicResponsiveListItemFlexColumnRenderer?.text?.runs || []);
  for (const column of renderer.fixedColumns || []) {
    runs.push(...(column?.musicResponsiveListItemFixedColumnRenderer?.text?.runs || []));
  }
  const {artists, duration} = metadataOf(runs);
  return {id, title, artists, channel: artists.join(', '), duration, url: `https://www.youtube.com/watch?v=${id}`};
}

function parseResults(data, {filter = 'songs', limit = 10} = {}) {
  if (!FILTERS[filter] || !data || typeof data !== 'object' || Array.isArray(data)) throw invalidResponse();
  const content = data.contents;
  const tabs = content?.tabbedSearchResultsRenderer?.tabs;
  const selected = Array.isArray(tabs) ? tabs.find(tab => tab?.tabRenderer?.selected)?.tabRenderer || tabs[0]?.tabRenderer : null;
  const sections = (selected?.content || content)?.sectionListRenderer?.contents;
  if (!Array.isArray(sections)) throw invalidResponse();
  const items = [], seen = new Set();
  let recognized = sections.length === 0, malformed = false;
  for (const section of sections) {
    const shelf = section?.musicShelfRenderer;
    const itemSection = section?.itemSectionRenderer;
    if (shelf || itemSection) {
      recognized = true;
      const contents = (shelf || itemSection).contents;
      if (!Array.isArray(contents)) { malformed = true; continue; }
      const label = textOf(shelf?.title).trim().toLowerCase();
      // The request uses English metadata. Never turn padded album/playlist
      // shelves into songs simply because they appeared in a filtered response.
      if (label && ['albums', 'artists', 'playlists', 'podcasts', 'episodes'].includes(label)) continue;
      for (const result of contents) {
        if (result?.messageRenderer || result?.musicDidYouMeanRenderer || result?.musicShowingResultsForRenderer) continue;
        const renderer = result?.musicResponsiveListItemRenderer;
        if (!renderer) { malformed = true; continue; }
        const item = itemOf(renderer, filter);
        if (item && !seen.has(item.id)) { seen.add(item.id); items.push(item); }
        else if (!item) {
          const type = endpointOf(renderer)?.watchEndpoint?.watchEndpointMusicSupportedConfigs?.watchEndpointMusicConfig?.musicVideoType;
          if (renderer.musicItemRendererDisplayPolicy !== 'MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT'
            && type !== 'MUSIC_VIDEO_TYPE_PODCAST_EPISODE' && !(filter === 'songs' && type && type !== 'MUSIC_VIDEO_TYPE_ATV')) malformed = true;
        }
      }
    } else if (section?.musicDidYouMeanRenderer || section?.musicShowingResultsForRenderer || section?.messageRenderer) {
      recognized = true;
    }
  }
  if (!recognized || !items.length && malformed) throw invalidResponse();
  return items.slice(0, Math.max(1, Math.min(20, Number(limit) || 10)));
}

function retryAfter(response, now) {
  const value = response.headers?.get('retry-after');
  if (!value) return 0;
  if (/^\d+(?:\.\d+)?$/.test(value)) return Math.max(0, Number(value) * 1000);
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? Math.max(0, timestamp - now()) : 0;
}

function publicVisitor(html) {
  // ytmusicapi gets this public, anonymous visitor context from ytcfg.set on
  // the Music homepage. Parse balanced JSON rather than evaluating page code.
  const expression = /ytcfg\.set\s*\(\s*(?=\{)/g;
  let match;
  while ((match = expression.exec(html))) {
    const start = expression.lastIndex;
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
        let config;
        try { config = JSON.parse(html.slice(start, position + 1)); } catch { break; }
        const visitor = config.VISITOR_DATA;
        if (typeof visitor === 'string' && /^[A-Za-z0-9+/_=%.-]{1,4096}$/.test(visitor)) return visitor;
        break;
      }
    }
  }
  throw invalidResponse({method: 'GET'});
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
  let visitor = null, visitorExpiresAt = 0, initializing = null;
  const initialize = () => {
    if (visitor && visitorExpiresAt > now()) return Promise.resolve(visitor);
    if (initializing) return initializing;
    const controller = new AbortController();
    const timer = schedule(() => controller.abort(), timeoutMs);
    initializing = (async () => {
      const response = await fetchPublic(MUSIC_URL, {method: 'GET', redirect: 'manual', signal: controller.signal, headers: {Accept: 'text/html'}});
      const html = await response.text();
      if (response.status === 429) throw new MusicSearchError(429, 'O YouTube Music limitou temporariamente as buscas públicas. Aguarde o intervalo informado para continuar.', 'YOUTUBE_MUSIC_RATE_LIMIT', {method: 'GET', cause: 'rate-limit', upstreamStatus: 429, retryAfterMs: retryAfter(response, now)}, true);
      if ([401, 403].includes(response.status) || response.status >= 300 && response.status < 400
        || /unusual traffic from your computer network|<form[^>]+action=["'][^"']*(?:\/sorry\/|consent\.)/i.test(html)) {
        throw new MusicSearchError(403, 'O YouTube Music recusou a busca pública ou solicitou verificação no site. A migração não consegue continuar essa busca automaticamente.', 'YOUTUBE_MUSIC_CATALOG_BLOCKED', {method: 'GET', cause: 'catalog-blocked', upstreamStatus: response.status});
      }
      if (!response.ok) throw new MusicSearchError(response.status >= 500 ? response.status : 502, 'O serviço de busca pública do YouTube Music está indisponível.', 'YOUTUBE_MUSIC_UNAVAILABLE', {method: 'GET', cause: 'network', upstreamStatus: response.status}, response.status >= 500);
      visitor = publicVisitor(html);
      visitorExpiresAt = now() + 15 * 60 * 1000;
      return visitor;
    })().finally(() => { unschedule(timer); initializing = null; });
    return initializing;
  };
  return {
    async search(input, {filter = 'songs', limit = 10, signal} = {}) {
      const query = String(input || '').trim().slice(0, 180);
      if (!query || !FILTERS[filter]) throw new MusicSearchError(400, 'Informe uma busca musical válida.', 'BAD_QUERY');
      if (signal?.aborted) throw new MusicSearchError(499, 'A busca foi cancelada.', 'REQUEST_ABORTED', {cause: 'aborted'});
      const controller = new AbortController();
      let timedOut = false;
      const abort = () => controller.abort();
      signal?.addEventListener('abort', abort, {once: true});
      const timer = schedule(() => { timedOut = true; controller.abort(); }, timeoutMs);
      try {
        const visitorData = await awaitAbortable(initialize(), controller.signal);
        const date = new Date(now()).toISOString().slice(0, 10).replace(/-/g, '');
        const response = await fetchPublic(SEARCH_URL, {
          method: 'POST', redirect: 'manual', signal: controller.signal,
          headers: {'Content-Type': 'application/json', Accept: 'application/json', Origin: 'https://music.youtube.com', 'X-Goog-Visitor-Id': visitorData},
          body: JSON.stringify({query, params: FILTERS[filter], context: {client: {clientName: 'WEB_REMIX', clientVersion: `1.${date}.01.00`, hl: 'en'}, user: {}}})
        });
        const text = await response.text();
        if (response.status === 429) throw new MusicSearchError(429, 'O YouTube Music limitou temporariamente as buscas públicas. Aguarde o intervalo informado para continuar.', 'YOUTUBE_MUSIC_RATE_LIMIT', {cause: 'rate-limit', upstreamStatus: 429, retryAfterMs: retryAfter(response, now)}, true);
        if ([401, 403].includes(response.status) || response.status >= 300 && response.status < 400
          || /^\s*(?:<html\b|<!doctype\b)/i.test(text)) {
          throw new MusicSearchError(403, 'O YouTube Music recusou a busca pública ou solicitou verificação no site. A migração não consegue continuar essa busca automaticamente.', 'YOUTUBE_MUSIC_CATALOG_BLOCKED', {cause: 'catalog-blocked', upstreamStatus: response.status});
        }
        if (!response.ok) throw new MusicSearchError(response.status >= 500 ? response.status : 502, 'O serviço de busca pública do YouTube Music está indisponível.', 'YOUTUBE_MUSIC_UNAVAILABLE', {cause: 'network', upstreamStatus: response.status}, response.status >= 500);
        let data;
        try { data = JSON.parse(text); } catch { throw invalidResponse(); }
        if (data?.error) throw invalidResponse();
        return {items: parseResults(data, {filter, limit}), filter};
      } catch (error) {
        if (error instanceof MusicSearchError) throw error;
        if (timedOut) throw new MusicSearchError(504, 'O YouTube Music demorou demais para responder à busca pública. Tente novamente.', 'PROVIDER_TIMEOUT', {cause: 'timeout', timeoutMs}, true);
        if (signal?.aborted) throw new MusicSearchError(499, 'A busca foi cancelada.', 'REQUEST_ABORTED', {cause: 'aborted'});
        throw new MusicSearchError(502, 'Não consegui acessar a busca pública do YouTube Music. Verifique a conexão e tente novamente.', 'PROVIDER_UNAVAILABLE', {cause: 'network'}, true);
      } finally {
        unschedule(timer);
        signal?.removeEventListener('abort', abort);
      }
    }
  };
}

module.exports = {createClient, MusicSearchError, parseResults};
