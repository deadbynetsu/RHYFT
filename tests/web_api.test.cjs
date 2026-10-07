const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const path = require('node:path');

function backend(fetch, env = {}, runtime = {}) {
  const context = vm.createContext({
    require, exports: {}, fetch, AbortController, URL, URLSearchParams, Buffer, console,
    setTimeout, clearTimeout, process: { env: { SESSION_SECRET: crypto.randomBytes(32).toString('hex'), ...env } }, ...runtime
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
    error => error.status === 502 && error.code === 'PROVIDER_ABORTED' && error.details.cause === 'aborted' && !error.details.timeoutMs);
  assert.equal(calls, 1);
});

test('quota errors do not trigger retries', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; return response(403, {error: {errors: [{reason: 'quotaExceeded'}]}});});
  await assert.rejects(api.testing.providerFetch('https://example.invalid', {}, 'google'),
    error => error.status === 403 && /cota/.test(error.message));
  assert.equal(calls, 1);
});

test('an upstream ABORTED response preserves its reason and operation without inventing a timeout', async () => {
  let calls = 0;
  const api = backend(async () => {
    calls++;
    return response(409, {error: {message: 'The operation was aborted.', status: 'ABORTED'}});
  });
  const session = api.testing.seal({access_token: 'private-fixture-token', expires_at: Date.now() + 3600000});
  const result = await api.handler(event('youtube/search', {q: 'private-query'}, `g_session=${session}`));
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 409);
  assert.equal(calls, 2);
  assert.equal(data.code, 'GOOGLE_409');
  assert.equal(data.retryable, true);
  assert.deepEqual(data.details, {provider: 'youtube', operation: 'search', method: 'GET', cause: 'aborted', upstreamStatus: 409, reason: 'ABORTED', attempts: 2});
  assert.match(data.error, /interrompeu.*não informou o motivo/);
  assert.doesNotMatch(result.body, /private-fixture-token|private-query|googleapis\.com|cookie|timeoutMs/);
});

test('an abort with a specific provider explanation keeps that explanation', async () => {
  const api = backend(async () => response(409, {error: {message: 'The operation was aborted because the resource changed.', status: 'ABORTED'}}));
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
    assert.match(error.message, /resource changed/);
    assert.doesNotMatch(error.message, /não informou o motivo/);
    return true;
  });
});

test('Spotify plaintext development-mode rejections still explain account authorization', async () => {
  const api = backend(async () => new Response('The user is not registered for this application. Please check your settings.', {status: 403}));
  await assert.rejects(api.testing.providerFetch('https://api.spotify.com/v1/me', {}, 'spotify'), error => {
    assert.equal(error.code, 'SPOTIFY_403');
    assert.match(error.message, /User Management/);
    assert.equal(error.retryable, false);
    return true;
  });
});

test('a server deadline is reported separately from an unexplained AbortError', async () => {
  let calls = 0;
  const api = backend(async (_url, options) => {
    calls++;
    await new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {once: true}));
  }, {}, {setTimeout: (fn, ms) => setTimeout(fn, ms === 7000 ? 1 : ms)});
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/playlistItems', {method: 'POST'}, 'google'), error => {
    assert.equal(error.status, 504);
    assert.equal(error.code, 'PROVIDER_TIMEOUT');
    assert.equal(error.details.cause, 'timeout');
    assert.equal(error.details.timeoutMs, 7000);
    assert.equal(error.details.operation, 'playlist-add');
    assert.equal(error.details.attempts, 1);
    return true;
  });
  assert.equal(calls, 1);
});

test('video detail failures identify the actual failing call inside a search', async () => {
  const api = backend(async url => url.includes('/search?')
    ? response(200, {items: [{id: {videoId: 'Video123456'}}]})
    : response(503, {error: {message: 'Service unavailable', status: 'UNAVAILABLE'}}));
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const result = await api.handler(event('youtube/search', {q: 'Song'}, `g_session=${session}`));
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 503);
  assert.equal(data.details.operation, 'track-details');
  assert.equal(data.details.reason, 'UNAVAILABLE');
  assert.equal(data.details.attempts, 2);
});

test('a non-JSON provider response is retried without leaking the response body', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; return new Response('<html>private-proxy-details</html>', {status: 200});});
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search?q=private', {}, 'google'), error => {
    assert.equal(error.code, 'PROVIDER_INVALID_RESPONSE');
    assert.equal(error.details.attempts, 2);
    assert.doesNotMatch(error.message, /private/);
    return true;
  });
  assert.equal(calls, 2);
});

for (const reason of ['quotaExceeded', 'rateLimitExceeded']) {
  test(`${reason} stays actionable even if the provider message says aborted`, async () => {
    let calls = 0;
    const api = backend(async () => {calls++; return response(403, {error: {message: 'The operation was aborted.', errors: [{reason}]}});});
    await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
      assert.equal(error.retryable, false);
      assert.equal(error.details.reason, reason);
      assert.match(error.message, reason === 'quotaExceeded' ? /cota/ : /limitou temporariamente/);
      return true;
    });
    assert.equal(calls, 1);
  });
}

test('a refresh connection failure retains authentication context instead of becoming an internal error', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; throw new TypeError('private-network-internals');}, {SPOTIFY_CLIENT_ID: 'b'.repeat(32)});
  const session = api.testing.seal({access_token: 'old-token', refresh_token: 'private-refresh', expires_at: 0});
  const result = await api.handler(event('spotify/search', {q: 'Song'}, `sp_session=${session}`));
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 502);
  assert.equal(data.code, 'PROVIDER_UNAVAILABLE');
  assert.equal(data.details.operation, 'auth');
  assert.equal(data.details.provider, 'spotify');
  assert.equal(calls, 1);
  assert.doesNotMatch(result.body, /private-network|private-refresh|old-token/);
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
