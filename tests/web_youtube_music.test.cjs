const assert = require('node:assert/strict');
const test = require('node:test');
const {createClient, MusicSearchError, parseResults} = require('../netlify/functions/lib/youtube-music');

// These fixtures use the raw responsive-row/section-list structures exercised
// by sigma67/ytmusicapi tests/parsers/test_search.py and mixins/test_search.py
// at 4aeaf7d0aa48e3fb56eb229ec04593655acba091, with playable endpoints/metadata
// from its navigation.py and parsers/search.py. They are protocol fixtures,
// not an assertion that a live Google call succeeded in this test environment.
const dot = {text: ' • '};
const linked = (text, browseId) => ({text, navigationEndpoint: {browseEndpoint: {browseId}}});
function row({id = 'ZrOKjDZOtkA', title = 'Wonderwall (Remastered)', artists = ['Oasis'], duration = '4:19', type = 'MUSIC_VIDEO_TYPE_ATV', fixed = false, ...extra} = {}) {
  const watchEndpoint = {videoId: id, watchEndpointMusicSupportedConfigs: {watchEndpointMusicConfig: {musicVideoType: type}}};
  const artistRuns = artists.flatMap((name, index) => [...(index ? [dot] : []), linked(name, `UC_artist_${index}`)]);
  const runs = [{text: type === 'MUSIC_VIDEO_TYPE_ATV' ? 'Song' : 'Video'}, dot, ...artistRuns, dot, linked('Definitely Maybe', 'MPRE_album'), dot];
  if (!fixed) runs.push({text: duration});
  return {musicResponsiveListItemRenderer: {
    flexColumns: [
      {musicResponsiveListItemFlexColumnRenderer: {text: {runs: [{text: title, navigationEndpoint: {watchEndpoint}}]}}},
      {musicResponsiveListItemFlexColumnRenderer: {text: {runs}}}
    ],
    ...(fixed ? {fixedColumns: [{musicResponsiveListItemFixedColumnRenderer: {text: {runs: [{text: duration}]}}}]} : {}),
    overlay: {musicItemThumbnailOverlayRenderer: {content: {musicPlayButtonRenderer: {playNavigationEndpoint: {watchEndpoint}}}}},
    ...extra
  }};
}
function result(rows = [row()], {tabbed = true, label = 'Songs'} = {}) {
  const content = {sectionListRenderer: {contents: [{musicShelfRenderer: {title: {runs: [{text: label}]}, contents: rows}}]}};
  return {contents: tabbed ? {tabbedSearchResultsRenderer: {tabs: [{tabRenderer: {selected: true, content}}]}} : content};
}
const response = (status, body, headers = {}) => new Response(typeof body === 'string' ? body : JSON.stringify(body), {status, headers});
const homepage = '<html><script>ytcfg.set({"VISITOR_DATA":"anonymous-public-visitor","nested":{"note":"a } in text"}});</script></html>';
function publicClient(options) {
  const searchFetch = options.fetch;
  return createClient({...options, fetch: async (url, request) => request.method === 'GET'
    ? response(200, homepage) : searchFetch(url, request)});
}

test('public catalogue metadata preserves the recording title, real artists and duration without videos.list', async () => {
  const calls = [];
  const client = createClient({now: () => Date.UTC(2026, 9, 8), fetch: async (url, options) => {
    calls.push({url, options});
    return response(200, options.method === 'GET' ? homepage : result([row({artists: ['Oasis', 'Guest Artist']})]));
  }});
  const data = await client.search('Oasis Wonderwall');
  assert.equal(calls.length, 2, 'one public homepage initialization followed by one catalogue search');
  assert.equal(calls[0].url, 'https://music.youtube.com/');
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[1].url, 'https://music.youtube.com/youtubei/v1/search?alt=json');
  assert.equal(calls[1].options.method, 'POST');
  assert.equal(calls[1].options.redirect, 'manual');
  assert.equal(calls[1].options.headers['X-Goog-Visitor-Id'], 'anonymous-public-visitor');
  const body = JSON.parse(calls[1].options.body);
  assert.equal(body.query, 'Oasis Wonderwall');
  assert.equal(body.params, 'EgWKAQIIAWoMEA4QChADEAQQCRAF');
  assert.deepEqual(body.context, {client: {clientName: 'WEB_REMIX', clientVersion: '1.20261008.01.00', hl: 'en'}, user: {}});
  assert.deepEqual(data.items[0], {id: 'ZrOKjDZOtkA', title: 'Wonderwall (Remastered)', artists: ['Oasis', 'Guest Artist'], channel: 'Oasis, Guest Artist', duration: 259, url: 'https://www.youtube.com/watch?v=ZrOKjDZOtkA'});
  assert.doesNotMatch(JSON.stringify(calls), /authorization|cookie|refresh_token|client_secret|key=/i);
});

test('video filtering supports a single explicit fallback request and fixed-column duration', async () => {
  const client = publicClient({fetch: async (_url, options) => {
    assert.equal(JSON.parse(options.body).params, 'EgWKAQIQAWoMEA4QChADEAQQCRAF');
    return response(200, result([row({type: 'MUSIC_VIDEO_TYPE_OMV', fixed: true, duration: '1:02:03', title: 'Song — Live 1995'})], {tabbed: false, label: 'Videos'}));
  }});
  const data = await client.search('Song live', {filter: 'videos'});
  assert.equal(data.filter, 'videos');
  assert.equal(data.items[0].title, 'Song — Live 1995');
  assert.equal(data.items[0].duration, 3723);
});

test('known empty catalogue responses remain empty, while unknown formats fail clearly', () => {
  assert.deepEqual(parseResults(result([])), []);
  assert.deepEqual(parseResults({contents: {sectionListRenderer: {contents: [{itemSectionRenderer: {contents: [{messageRenderer: {text: {runs: [{text: 'No results'}]}}}]}}]}}}), []);
  for (const value of [{}, {contents: {}}, {contents: {someFutureRenderer: []}}, result([{unexpectedRenderer: {}}]), result([row({id: 'invalid'})])]) {
    assert.throws(() => parseResults(value), error => error.code === 'YOUTUBE_MUSIC_FORMAT_CHANGED' && !error.retryable);
  }
});

test('unavailable tracks, podcast episodes and padded non-song shelves are excluded', () => {
  const unavailable = row({musicItemRendererDisplayPolicy: 'MUSIC_ITEM_RENDERER_DISPLAY_POLICY_GREY_OUT'});
  const podcast = row({type: 'MUSIC_VIDEO_TYPE_PODCAST_EPISODE'});
  assert.deepEqual(parseResults(result([unavailable, podcast])), []);
  assert.deepEqual(parseResults(result([row()], {label: 'Albums'})), []);
});

test('duplicate IDs are removed and bounded results remain in upstream order', () => {
  const rows = [row(), row(), row({id: 't5H_CewqpKA', title: 'Fuel'}), row({id: 'vU05Eksc_iM', title: 'Wonderwall live'})];
  assert.deepEqual(parseResults(result(rows), {limit: 2}).map(item => item.id), ['ZrOKjDZOtkA', 't5H_CewqpKA']);
});

test('title navigation is a supported endpoint fallback and unlinked numeric artists stay intact', () => {
  const entry = row({title: 'Captcha — Unusual Traffic', artists: []});
  delete entry.musicResponsiveListItemRenderer.overlay;
  entry.musicResponsiveListItemRenderer.flexColumns[1].musicResponsiveListItemFlexColumnRenderer.text.runs = [{text: '2Pac'}, dot, {text: '21'}, dot, {text: '3:48'}, dot, {text: '1.7B views'}];
  const items = parseResults(result([entry]));
  assert.deepEqual(items[0].artists, ['2Pac', '21']);
  assert.equal(items[0].duration, 228);
});

test('song names mentioning captcha are not mistaken for a service challenge', async () => {
  const client = publicClient({fetch: async () => response(200, result([row({title: 'Captcha — Unusual Traffic'})]))});
  assert.equal((await client.search('Captcha')).items[0].title, 'Captcha — Unusual Traffic');
});

test('public 429 respects Retry-After without retrying or changing authentication', async () => {
  let calls = 0;
  const client = publicClient({fetch: async () => {calls++; return response(429, {error: {message: 'rate limit exceeded'}}, {'Retry-After': '90'});}});
  await assert.rejects(client.search('Song'), error => {
    assert.ok(error instanceof MusicSearchError);
    assert.equal(error.status, 429);
    assert.equal(error.retryable, true);
    assert.equal(error.details.provider, 'youtube-music');
    assert.equal(error.details.cause, 'rate-limit');
    assert.equal(error.details.retryAfterMs, 90000);
    assert.equal(error.details.attempts, 1);
    return true;
  });
  assert.equal(calls, 1);
});

test('HTTP-date Retry-After is converted using the injected clock', async () => {
  const now = Date.UTC(2026, 9, 8);
  const client = publicClient({now: () => now, fetch: async () => response(429, {}, {'Retry-After': new Date(now + 45000).toUTCString()})});
  await assert.rejects(client.search('Song'), error => error.details.retryAfterMs === 45000);
});

for (const [status, body] of [[403, {error: {message: 'Forbidden private details'}}], [302, 'redirect'], [200, '<!doctype html><html>Captcha or consent required</html>']]) {
  test(`public blocking (${status}) is reported and never bypassed or retried`, async () => {
    let calls = 0;
    const client = publicClient({fetch: async () => {calls++; return response(status, body);}});
    await assert.rejects(client.search('private-query'), error => {
      assert.equal(error.code, 'YOUTUBE_MUSIC_CATALOG_BLOCKED');
      assert.equal(error.retryable, false);
      assert.equal(error.details.cause, 'catalog-blocked');
      assert.doesNotMatch(error.message, /private/);
      return true;
    });
    assert.equal(calls, 1);
  });
}

test('network failures retain actionable search diagnostics without exposing internals', async () => {
  const client = publicClient({fetch: async () => {throw new TypeError('private proxy details');}});
  await assert.rejects(client.search('private query'), error => {
    assert.equal(error.code, 'PROVIDER_UNAVAILABLE');
    assert.equal(error.details.cause, 'network');
    assert.equal(error.retryable, true);
    assert.doesNotMatch(error.message, /private/);
    return true;
  });
});

test('the deadline covers reading the response body as well as the initial connection', async () => {
  const client = publicClient({timeoutMs: 7000, setTimeout: callback => setTimeout(callback, 1), fetch: async (_url, options) => ({
    status: 200, ok: true, headers: new Headers(),
    text: () => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true}))
  })});
  await assert.rejects(client.search('Song'), error => error.code === 'PROVIDER_TIMEOUT' && error.details.timeoutMs === 7000);
});

test('caller cancellation does not become a timeout or retryable service failure', async () => {
  const controller = new AbortController();
  const client = publicClient({fetch: async (_url, options) => {
    controller.abort();
    throw new DOMException(options.signal.aborted ? 'Aborted' : 'Unexpected', 'AbortError');
  }});
  await assert.rejects(client.search('Song', {signal: controller.signal}), error => error.code === 'REQUEST_ABORTED' && !error.retryable);
});

test('malformed input is rejected before any public request', async () => {
  const client = publicClient({fetch: async () => {throw new Error('unexpected network');}});
  await assert.rejects(client.search(''), error => error.code === 'BAD_QUERY');
  await assert.rejects(client.search('Song', {filter: 'uploads'}), error => error.code === 'BAD_QUERY');
});

test('anonymous initialization is shared across concurrent searches and refreshed after fifteen minutes', async () => {
  let currentTime = Date.UTC(2026, 9, 8), initCalls = 0, searches = 0;
  const client = createClient({now: () => currentTime, fetch: async (_url, request) => {
    if (request.method === 'GET') {
      initCalls++;
      await Promise.resolve();
      return response(200, `<script>ytcfg.set({"VISITOR_DATA":"public-visitor-${initCalls}"});</script>`);
    }
    searches++;
    assert.equal(request.headers['X-Goog-Visitor-Id'], `public-visitor-${initCalls}`);
    return response(200, result());
  }});
  await Promise.all([client.search('One'), client.search('Two')]);
  assert.equal(initCalls, 1);
  assert.equal(searches, 2);
  await client.search('Three', {filter: 'videos'});
  assert.equal(initCalls, 1);
  currentTime += 15 * 60 * 1000;
  await client.search('Four');
  assert.equal(initCalls, 2);
  assert.equal(searches, 4);
});

test('blocked anonymous initialization stops before any search and failed initialization is not cached', async () => {
  let calls = 0;
  const client = createClient({fetch: async (_url, request) => {
    calls++;
    assert.equal(request.method, 'GET');
    return response(403, 'Forbidden');
  }});
  for (let attempt = 0; attempt < 2; attempt++) {
    await assert.rejects(client.search('Song'), error => error.code === 'YOUTUBE_MUSIC_CATALOG_BLOCKED' && error.details.method === 'GET');
  }
  assert.equal(calls, 2);
});

test('a successful homepage without visitor metadata remains searchable without executing or copying page scripts', async () => {
  const calls = [];
  const client = createClient({fetch: async (_url, request) => {
    calls.push(request);
    if (request.method === 'GET') return response(200, '<html><script>window.userToken = "must-not-be-used";</script></html>');
    assert.equal(request.headers['X-Goog-Visitor-Id'], undefined);
    assert.doesNotMatch(JSON.stringify(request), /must-not-be-used|userToken/);
    return response(200, result());
  }});
  assert.equal((await client.search('Song')).items.length, 1);
  assert.deepEqual(calls.map(request => request.method), ['GET', 'POST']);
  assert.match(calls[0].headers['User-Agent'], /^Mozilla\/5\.0/);
  assert.equal(calls[0].headers.Origin, 'https://music.youtube.com');
});

test('caller cancellation stops waiting for anonymous initialization and never sends a search', async () => {
  const controller = new AbortController();
  let searches = 0, finishInit;
  const client = createClient({fetch: async (_url, request) => {
    if (request.method === 'GET') return new Promise(resolve => {finishInit = () => resolve(response(200, homepage));});
    searches++;
    return response(200, result());
  }});
  const search = client.search('Song', {signal: controller.signal});
  controller.abort();
  await assert.rejects(search, error => error.code === 'REQUEST_ABORTED');
  finishInit();
  await Promise.resolve();
  assert.equal(searches, 0);
});

test('public page client version and nested visitor metadata are reused without any account or unrelated configuration', async () => {
  const client = createClient({fetch: async (_url, request) => {
    if (request.method === 'GET') return response(200, `<html><script>
      ytcfg.set({"unrelated":"private-page-sentinel"});
      ytcfg . set ({"INNERTUBE_CONTEXT":{"client":{"clientName":"WEB_REMIX","clientVersion":"1.20260813.03.00","visitorData":"anonymous-nested-visitor","gl":"BR","userToken":"private-account-token"},"user":{"onBehalfOfUser":"private-user"}}});
    </script></html>`);
    assert.equal(request.headers['X-Goog-Visitor-Id'], 'anonymous-nested-visitor');
    assert.deepEqual(JSON.parse(request.body).context, {client: {clientName: 'WEB_REMIX', clientVersion: '1.20260813.03.00', hl: 'en', gl: 'BR'}, user: {}});
    assert.doesNotMatch(JSON.stringify(request), /private-page-sentinel|private-account-token|private-user|userToken|onBehalfOfUser/);
    return response(200, result());
  }});
  assert.equal((await client.search('Song')).items.length, 1);
});

test('public configuration assignments and encoded visitor IDs are parsed as JSON rather than evaluated', async () => {
  const client = createClient({fetch: async (_url, request) => {
    if (request.method === 'GET') return response(200, '<script>ytcfg.data_ = {"VISITOR_DATA":"Cgt_public%3D%3D","INNERTUBE_CONTEXT_CLIENT_VERSION":"1.20260701.02.00","unrelated":{"escaped":"} \\\" {"}};</script>');
    assert.equal(request.headers['X-Goog-Visitor-Id'], 'Cgt_public%3D%3D');
    assert.equal(JSON.parse(request.body).context.client.clientVersion, '1.20260701.02.00');
    return response(200, result());
  }});
  assert.equal((await client.search('Song')).items.length, 1);
});

function topCard({id = 'ZrOKjDZOtkA', title = 'Wonderwall (Live 1995)', type = 'MUSIC_VIDEO_TYPE_ATV', category = 'Song', artists = ['Oasis'], contents} = {}) {
  return {musicCardShelfRenderer: {
    title: {runs: [{text: title}]},
    subtitle: {runs: [{text: category}, dot, ...artists.flatMap((name, index) => [...(index ? [dot] : []), linked(name, `UC_artist_${index}`)]), dot, {text: '4:19'}]},
    onTap: {watchEndpoint: {videoId: id, watchEndpointMusicSupportedConfigs: {watchEndpointMusicConfig: {musicVideoType: type}}}},
    ...(contents ? {contents} : {})
  }};
}

test('a source-supported top-result-only music card is a valid result without requiring a Songs shelf', () => {
  const data = {contents: {sectionListRenderer: {contents: [topCard()]}}};
  assert.deepEqual(parseResults(data)[0], {id: 'ZrOKjDZOtkA', title: 'Wonderwall (Live 1995)', artists: ['Oasis'], channel: 'Oasis', duration: 259, url: 'https://www.youtube.com/watch?v=ZrOKjDZOtkA'});
});

test('top-card contents are parsed in order and duplicate video IDs are removed', () => {
  const data = {contents: {sectionListRenderer: {contents: [topCard({contents: [{messageRenderer: {text: {runs: [{text: 'More from YouTube'}]}}}, row(), row({id: 'vU05Eksc_iM', title: 'Wonderwall official video'})]})]}}};
  assert.deepEqual(parseResults(data).map(item => item.id), ['ZrOKjDZOtkA', 'vU05Eksc_iM']);
});

test('unfiltered public search omits params and supports mixed catalogue shelves like the Android client', async () => {
  let searches = 0;
  const mixed = {contents: {sectionListRenderer: {contents: [
    {musicCardShelfRenderer: {title: {runs: [{text: 'Oasis'}]}, subtitle: {runs: [{text: 'Artist'}]}, onTap: {browseEndpoint: {browseId: 'UC_artist'}}}},
    {musicShelfRenderer: {title: {runs: [{text: 'Albums'}]}, contents: [{musicResponsiveListItemRenderer: {navigationEndpoint: {browseEndpoint: {browseId: 'MPRE_album'}}}}]}},
    topCard({type: 'MUSIC_VIDEO_TYPE_OMV', category: 'Video'}),
    {musicShelfRenderer: {title: {runs: [{text: 'Songs'}]}, contents: [row({id: 'vU05Eksc_iM'})]}}
  ]}}};
  const client = publicClient({fetch: async (_url, request) => {
    searches++;
    assert.equal(Object.hasOwn(JSON.parse(request.body), 'params'), false);
    return response(200, mixed);
  }});
  const found = await client.search('Oasis Wonderwall', {filter: null});
  assert.equal(found.filter, null);
  assert.equal(found.items.length, 2);
  assert.equal(searches, 1);
});

test('mixed artist, album and playlist cards are ignored without turning recognized results into a format failure', () => {
  const cards = ['Artist', 'Album', 'Playlist'].map(category => topCard({category}));
  assert.deepEqual(parseResults({contents: {sectionListRenderer: {contents: cards}}}, {filter: null}), []);
  assert.deepEqual(parseResults({responseContext: {serviceTrackingParams: []}}), []);
  assert.throws(() => parseResults({unexpected: 'must-not-be-suppressed'}), error => error.code === 'YOUTUBE_MUSIC_FORMAT_CHANGED');
});

test('pinned 2024 and 2026 captured catalogue rows preserve actual titles, artists and fixed-column durations', () => {
  const album = require('./fixtures/youtube-music/upstream-2024-03-album-rows.json');
  const videos = require('./fixtures/youtube-music/upstream-2026-05-album-video-rows.json');
  const artist = require('./fixtures/youtube-music/upstream-2026-05-artist-song-rows.json');
  // The rows are captured upstream data; the surrounding search envelope is
  // constructed here and is explicitly documented as synthetic in README.md.
  const albumItems = parseResults(result(album));
  assert.deepEqual(albumItems.map(item => [item.id, item.title, item.duration]), [['iKLU7z_xdYQ', 'Walk On Water (feat. Beyoncé)', 304], ['JrIoKNaM-8w', 'Believe', 316]]);
  const videoItems = parseResults(result(videos), {filter: 'videos'});
  assert.deepEqual(videoItems.map(item => [item.title, item.artists, item.duration]), [['The Explanation', ['XXXTENTACION'], 51], ['Jocelyn Flores', ['XXXTENTACION'], 120]]);
  const artistItems = parseResults(result(artist));
  assert.deepEqual(artistItems[0].artists, ['32ki', 'Hatsune Miku', 'Kasane Teto']);
  assert.equal(artistItems[0].title, 'メズマライザー - Mesmerizer (feat. Hatsune Miku&Kasane Teto)');
  assert.equal(artistItems[0].duration, null);
});

test('captured public playlistItemData IDs remain playable when the overlay and title navigation are absent', () => {
  const captured = require('./fixtures/youtube-music/upstream-2026-05-artist-song-rows.json');
  const reduced = structuredClone(captured);
  for (const entry of reduced) {
    delete entry.musicResponsiveListItemRenderer.overlay;
    for (const run of entry.musicResponsiveListItemRenderer.flexColumns[0].musicResponsiveListItemFlexColumnRenderer.text.runs) delete run.navigationEndpoint;
  }
  assert.deepEqual(parseResults(result(reduced)).map(item => item.id), ['ibjWftkJrd4', 'G2PDJTkFiA8']);
});

test('two-column search results and thumbnail-overlay top-card endpoints are recognized', () => {
  const card = topCard();
  card.musicCardShelfRenderer.thumbnailOverlay = {musicItemThumbnailOverlayRenderer: {content: {musicPlayButtonRenderer: {playNavigationEndpoint: card.musicCardShelfRenderer.onTap}}}};
  delete card.musicCardShelfRenderer.onTap;
  const data = {contents: {twoColumnSearchResultsRenderer: {primaryContents: {sectionListRenderer: {contents: [card]}}}}};
  assert.equal(parseResults(data)[0].id, 'ZrOKjDZOtkA');
});

test('a search format failure exposes only its stage and allowlisted structural names', async () => {
  const client = publicClient({fetch: async () => response(200, {responseContext: {visitorData: 'private-visitor'}, contents: {unknownUserShape: {secret: 'private-secret'}}, privateQuery: 'private-query'})});
  await assert.rejects(client.search('private query'), error => {
    assert.equal(error.details.stage, 'catalog-search');
    assert.equal(error.details.method, 'POST');
    assert.equal(error.details.upstreamStatus, 200);
    assert.deepEqual(error.details.responseShape, ['json', 'responseContext', 'contents']);
    assert.doesNotMatch(JSON.stringify(error.details), /private-visitor|private-secret|private-query|unknownUserShape|privateQuery/);
    assert.doesNotMatch(error.message, /mudou|incompleto/);
    return true;
  });
});

test('bootstrap connection failures retain the GET catalog-init stage and never send a search', async () => {
  let calls = 0;
  const client = createClient({fetch: async () => {calls++; throw new TypeError('private-network-details');}});
  await assert.rejects(client.search('Song'), error => error.details.stage === 'catalog-init' && error.details.method === 'GET' && error.code === 'PROVIDER_UNAVAILABLE');
  assert.equal(calls, 1);
});
