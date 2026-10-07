const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const path = require('node:path');

function backend(fetch, env = {}) {
  const context = vm.createContext({
    require, exports: {}, fetch, AbortController, URL, URLSearchParams, Buffer, console,
    setTimeout, clearTimeout, process: { env: { SESSION_SECRET: crypto.randomBytes(32).toString('hex'), ...env } }
  });
  const code = fs.readFileSync(path.join(__dirname, '../netlify/functions/api.js'), 'utf8');
  vm.runInContext(`${code}\nexports.testing = {providerFetch, seal, unseal, refreshSpotify};`, context);
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

const personalId = 'a'.repeat(32), sharedId = 'b'.repeat(32);
function event(route, query = {}, cookie = '') {
  return {path: `/api/${route}`, httpMethod: 'GET', headers: {host: 'rhyft.example.test', cookie}, queryStringParameters: query};
}
function savedCookie(result, name) {
  const value = result.multiValueHeaders?.['Set-Cookie']?.find(cookie => cookie.startsWith(`${name}=`));
  return value ? decodeURIComponent(value.split(';')[0].slice(name.length + 1)) : '';
}

for (const personal of [true, false]) {
  test(`Spotify login uses the ${personal ? 'personal' : 'shared'} Client ID with PKCE`, async () => {
    const api = backend(() => {throw new Error('login start should not fetch');}, {SPOTIFY_CLIENT_ID: sharedId});
    const result = await api.handler(event('spotify/start', personal ? {client_id: personalId} : {}));
    assert.equal(result.statusCode, 302);
    const location = new URL(result.headers.Location);
    assert.equal(location.hostname, 'accounts.spotify.com');
    assert.equal(location.searchParams.get('client_id'), personal ? personalId : sharedId);
    assert.equal(location.searchParams.get('code_challenge_method'), 'S256');
    const saved = api.testing.unseal(savedCookie(result, 'sp_oauth'));
    assert.equal(saved.clientId, personal ? personalId : sharedId);
    assert.equal(saved.state, location.searchParams.get('state'));
    assert.ok(saved.verifier);
  });
}

test('a personal Client ID works without a shared server Client ID', async () => {
  const api = backend(() => {throw new Error('unexpected fetch');});
  const result = await api.handler(event('spotify/start', {client_id: personalId}));
  assert.equal(result.statusCode, 302);
  const status = JSON.parse((await api.handler(event('session'))).body);
  assert.equal(status.spotify.sharedClientAvailable, false);
  assert.equal(status.spotify.callbackUrl, 'https://rhyft.example.test/api/spotify/callback');
  assert.ok(!status.setup.missing.includes('SPOTIFY_CLIENT_ID'));
});

test('an invalid pasted Client ID is rejected before contacting Spotify', async () => {
  const api = backend(() => {throw new Error('unexpected fetch');});
  const result = await api.handler(event('spotify/start', {client_id: '# SPOTIFY_CLIENT_ID | Value'}));
  assert.equal(result.statusCode, 400);
  assert.equal(JSON.parse(result.body).code, 'INVALID_CLIENT_ID');
});

test('token exchange and refresh stay bound to the selected personal app', async () => {
  const tokenBodies = [];
  const api = backend(async (url, options) => {
    if (url.endsWith('/api/token')) {
      tokenBodies.push(new URLSearchParams(options.body));
      return response(200, {access_token: 'fixture-token', refresh_token: 'fixture-refresh', expires_in: 3600});
    }
    assert.ok(url.endsWith('/me'));
    return response(200, {id: 'test-user', display_name: 'Test User'});
  }, {SPOTIFY_CLIENT_ID: sharedId});
  const start = await api.handler(event('spotify/start', {client_id: personalId}));
  const oauthCookie = savedCookie(start, 'sp_oauth');
  const oauth = api.testing.unseal(oauthCookie);
  const result = await api.handler(event('spotify/callback', {state: oauth.state, code: 'fixture-code', client_id: 'c'.repeat(32)}, `sp_oauth=${oauthCookie}`));
  assert.match(result.headers.Location, /auth=spotify-ok/);
  assert.equal(tokenBodies[0].get('client_id'), personalId);
  assert.equal(tokenBodies[0].get('code_verifier'), oauth.verifier);
  const session = api.testing.unseal(savedCookie(result, 'sp_session'));
  assert.equal(session.client_id, personalId);
  assert.equal(session.user_id, 'test-user');
  const refreshed = await api.testing.refreshSpotify(session);
  assert.equal(tokenBodies[1].get('client_id'), personalId);
  assert.equal(tokenBodies[1].get('grant_type'), 'refresh_token');
  assert.equal(refreshed.client_id, personalId);
});

test('legacy sessions continue refreshing with the configured shared app', async () => {
  const api = backend(async (url, options) => {
    assert.equal(new URLSearchParams(options.body).get('client_id'), sharedId);
    return response(200, {access_token: 'fixture-token', expires_in: 3600});
  }, {SPOTIFY_CLIENT_ID: sharedId});
  const refreshed = await api.testing.refreshSpotify({refresh_token: 'fixture-refresh'});
  assert.equal(refreshed.client_id, sharedId);
  assert.equal(refreshed.refresh_token, 'fixture-refresh');
});

test('a rejected account gets an actionable error instead of a connected session', async () => {
  const api = backend(async url => url.endsWith('/api/token')
    ? response(200, {access_token: 'fixture-token', expires_in: 3600})
    : response(403, {error: {message: 'The user is not registered for this application. Please check your settings on https://developer.spotify.com/dashboard.'}}), {SPOTIFY_CLIENT_ID: sharedId});
  const start = await api.handler(event('spotify/start'));
  const oauthCookie = savedCookie(start, 'sp_oauth');
  const oauth = api.testing.unseal(oauthCookie);
  const result = await api.handler(event('spotify/callback', {state: oauth.state, code: 'fixture-code'}, `sp_oauth=${oauthCookie}`));
  const url = new URL(result.headers.Location, 'https://rhyft.example.test');
  assert.match(url.searchParams.get('auth_error'), /User Management/);
  assert.match(url.searchParams.get('auth_error'), /próprio Client ID/);
  assert.equal(url.searchParams.has('auth'), false);
  assert.ok(result.multiValueHeaders['Set-Cookie'].some(cookie => cookie.startsWith('sp_session=;') && cookie.includes('Max-Age=0')));
});

test('a callback with an invalid OAuth state cannot exchange a token', async () => {
  const api = backend(() => {throw new Error('token exchange must not run');}, {SPOTIFY_CLIENT_ID: sharedId});
  const start = await api.handler(event('spotify/start', {client_id: personalId}));
  const result = await api.handler(event('spotify/callback', {state: 'wrong', code: 'fixture-code'}, `sp_oauth=${savedCookie(start, 'sp_oauth')}`));
  assert.match(new URL(result.headers.Location, 'https://rhyft.example.test').searchParams.get('auth_error'), /inválida/);
});
