const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const path = require('node:path');

function backend(fetch) {
  const context = vm.createContext({
    require, exports: {}, fetch, AbortController, URL, URLSearchParams, Buffer, console,
    setTimeout, clearTimeout, process: { env: { SESSION_SECRET: crypto.randomBytes(32).toString('hex') } }
  });
  const code = fs.readFileSync(path.join(__dirname, '../netlify/functions/api.js'), 'utf8');
  vm.runInContext(`${code}\nexports.testing = {providerFetch, seal};`, context);
  return context.exports;
}
function response(status, body) {
  return new Response(JSON.stringify(body), { status, headers: {'Content-Type': 'application/json'} });
}

for (const failure of ['abort', 'body-abort', 'server']) {
  test(`retries a transient GET failure (${failure})`, async () => {
    let calls = 0;
    const api = backend(async () => {
      if (++calls === 1) {
        if (failure === 'abort') throw new DOMException('The operation was aborted.', 'AbortError');
        if (failure === 'body-abort') return {ok: true, text: async () => {throw new DOMException('Aborted', 'AbortError');}};
        return response(503, {error: {message: 'The operation was aborted.'}});
      }
      return response(200, {items: ['ok']});
    });
    const data = await api.testing.providerFetch('https://example.invalid', {}, 'google');
    assert.deepEqual(JSON.parse(JSON.stringify(data)), {items: ['ok']});
    assert.equal(calls, 2);
  });
}

test('a POST with a lost response is not blindly repeated', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; throw new DOMException('Aborted', 'AbortError');});
  await assert.rejects(api.testing.providerFetch('https://example.invalid', {method: 'POST'}, 'google'),
    error => error.status === 504 && error.code === 'PROVIDER_TIMEOUT');
  assert.equal(calls, 1);
});

test('quota errors do not trigger retries', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; return response(403, {error: {errors: [{reason: 'quotaExceeded'}]}});});
  await assert.rejects(api.testing.providerFetch('https://example.invalid', {}, 'google'),
    error => error.status === 403 && /cota/.test(error.message));
  assert.equal(calls, 1);
});

for (const provider of ['spotify', 'youtube']) {
  test(`${provider} destination IDs include pagination`, async () => {
    const calls = [];
    const api = backend(async url => {
      calls.push(url);
      if (url.includes('/playlists?')) return response(200, {items: [{snippet: {title: 'Destino'}}]});
      if (/\/playlists\/[^/?]+$/.test(url)) return response(200, {name: 'Destino'});
      if (provider === 'spotify') {
        return url.includes('offset=0')
          ? response(200, {items: [{item: {id: 'one'}}, {track: {id: 'two'}}], next: 'more'})
          : response(200, {items: [{item: {id: 'three'}}], next: null});
      }
      return url.includes('pageToken=next')
        ? response(200, {items: [{contentDetails: {videoId: 'three'}}]})
        : response(200, {items: [{contentDetails: {videoId: 'one'}}, {contentDetails: {videoId: 'two'}}], nextPageToken: 'next'});
    });
    const cookieName = provider === 'spotify' ? 'sp_session' : 'g_session';
    const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
    const event = {path: `/api/${provider}/playlist/state`, httpMethod: 'GET',
      headers: {cookie: `${cookieName}=${session}`}, queryStringParameters: {input: 'Playlist1234567890'}};
    const first = await api.handler(event);
    assert.equal(first.statusCode, 200);
    const data = JSON.parse(first.body);
    assert.deepEqual(data.ids, ['one', 'two']);
    assert.equal(data.name, 'Destino');
    const second = await api.handler({...event, queryStringParameters: {...event.queryStringParameters, page: data.nextPage}});
    assert.equal(second.statusCode, 200);
    assert.deepEqual(JSON.parse(second.body).ids, ['three']);
    assert.equal(JSON.parse(second.body).nextPage, null);
    assert.equal(calls.length, 3);
  });
}

test('inaccessible destinations fail instead of creating a replacement', async () => {
  const api = backend(async () => response(200, {items: []}));
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const result = await api.handler({path: '/api/youtube/playlist/state', httpMethod: 'GET',
    headers: {cookie: `g_session=${session}`}, queryStringParameters: {input: 'Playlist1234567890'}});
  assert.equal(result.statusCode, 404);
});

test('YouTube search requests ten candidates with their recording durations', async () => {
  const ids = Array.from({length: 10}, (_, i) => `Video12345${i}`);
  const api = backend(async url => {
    const parsed = new URL(url);
    if (parsed.pathname.endsWith('/search')) {
      assert.equal(parsed.searchParams.get('maxResults'), '10');
      return response(200, {items: ids.map(id => ({id: {videoId: id}}))});
    }
    assert.equal(parsed.searchParams.get('id'), ids.join(','));
    return response(200, {items: ids.map(id => ({id, snippet: {title: 'Song', channelTitle: 'Artist'}, contentDetails: {duration: 'PT3M2S'}}))});
  });
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const result = await api.handler({path: '/api/youtube/search', httpMethod: 'GET', headers: {cookie: `g_session=${session}`}, queryStringParameters: {q: 'Artist Song'}});
  assert.equal(result.statusCode, 200);
  const data = JSON.parse(result.body);
  assert.equal(data.items.length, 10);
  assert.equal(data.items[0].duration, 182);
});
