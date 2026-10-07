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
  vm.runInContext(`${code}\nexports.testing = {providerFetch, seal, unseal, refreshSpotify, refreshGoogle};`, context);
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
  assert.equal(calls, 1);
  assert.equal(data.code, 'GOOGLE_409');
  assert.equal(data.retryable, true);
  assert.deepEqual(data.details, {provider: 'youtube', operation: 'search', method: 'GET', cause: 'aborted', upstreamStatus: 409, reason: 'ABORTED', attempts: 1});
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

test('a non-JSON search failure is returned to the browser without leaking or retrying on the server', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; return new Response('<html>private-proxy-details</html>', {status: 200});});
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search?q=private', {}, 'google'), error => {
    assert.equal(error.code, 'PROVIDER_INVALID_RESPONSE');
    assert.equal(error.details.attempts, 1);
    assert.doesNotMatch(error.message, /private/);
    return true;
  });
  assert.equal(calls, 1);
});

for (const reason of ['quotaExceeded', 'rateLimitExceeded']) {
  test(`${reason} stays actionable even if the provider message says aborted`, async () => {
    let calls = 0;
    const api = backend(async () => {calls++; return response(403, {error: {message: 'The operation was aborted.', errors: [{reason}]}});});
    await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
      assert.equal(error.retryable, reason === 'rateLimitExceeded');
      assert.equal(error.details.reason, reason);
      assert.match(error.message, reason === 'quotaExceeded' ? /cota/ : /limitou temporariamente/);
      return true;
    });
    assert.equal(calls, 1);
  });
}

for (const status of [403, 429]) {
  test(`a ${status} rate limit takes precedence over a generic quota message and returns Retry-After`, async () => {
    let calls = 0;
    const api = backend(async () => {
      calls++;
      return new Response(JSON.stringify({error: {message: 'Quota exceeded for requests per minute.', errors: [{reason: 'rateLimitExceeded'}]}}),
        {status, headers: {'Retry-After': '5'}});
    });
    await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
      assert.equal(error.status, status);
      assert.equal(error.retryable, true);
      assert.equal(error.details.cause, 'rate-limit');
      assert.equal(error.details.retryAfterMs, 5000);
      assert.match(error.message, /limitou temporariamente/);
      assert.doesNotMatch(error.message, /cota/);
      return true;
    });
    assert.equal(calls, 1, 'the browser schedules the retries; the server must not double them');
  });
}

test('a Retry-After date is forwarded as a waiting period', async () => {
  const date = new Date(Date.now() + 15000).toUTCString();
  const api = backend(async () => new Response(JSON.stringify({error: {status: 'RESOURCE_EXHAUSTED'}}), {status: 429, headers: {'Retry-After': date}}));
  await assert.rejects(api.testing.providerFetch('https://api.spotify.com/v1/search', {}, 'spotify'), error => {
    assert.equal(error.details.cause, 'rate-limit');
    assert.ok(error.details.retryAfterMs > 12000 && error.details.retryAfterMs <= 15000);
    return true;
  });
});

for (const retryDelay of ['90s', {seconds: '90', nanos: 500000000}]) {
  test(`Google RetryInfo (${typeof retryDelay}) is honored along with Retry-After`, async () => {
    const api = backend(async () => new Response(JSON.stringify({error: {status: 'RESOURCE_EXHAUSTED', details: [
      {'@type': 'type.googleapis.com/google.rpc.ErrorInfo', reason: 'RATE_LIMIT_EXCEEDED'},
      {'@type': 'type.googleapis.com/google.rpc.RetryInfo', retryDelay}
    ]}}), {status: 429, headers: {'Retry-After': '30'}}));
    await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
      assert.equal(error.details.reason, 'RATE_LIMIT_EXCEEDED');
      assert.equal(error.details.cause, 'rate-limit');
      assert.equal(error.details.retryAfterMs, typeof retryDelay === 'string' ? 90000 : 90500);
      return true;
    });
  });
}

for (const scope of ['Day', 'Minute']) {
  test(`an explicitly named per-${scope.toLowerCase()} Google quota is classified by its actual limit`, async () => {
    let calls = 0;
    const api = backend(async () => {
      calls++;
      return response(429, {error: {message: 'Quota exceeded.', details: [{
        '@type': 'type.googleapis.com/google.rpc.ErrorInfo', reason: 'RATE_LIMIT_EXCEEDED',
        metadata: {quota_limit: `QueriesPer${scope}PerProject`, consumer: 'private-project-identifier'}
      }]}});
    });
    const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
    const result = await api.handler(event('youtube/search', {q: 'Song'}, `g_session=${session}`));
    const data = JSON.parse(result.body);
    assert.equal(data.retryable, scope === 'Minute');
    assert.equal(data.details.limitScope, scope.toLowerCase());
    assert.equal(data.details.cause, scope === 'Day' ? 'quota' : 'rate-limit');
    assert.match(data.error, scope === 'Day' ? /cota.*esgotada/ : /limitou temporariamente/);
    assert.doesNotMatch(result.body, /private-project-identifier/);
    assert.equal(calls, 1);
  });
}

test('a 429 with an explicit exhausted quota remains final', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; return response(429, {error: {errors: [{reason: 'quotaExceeded'}]}});});
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
    assert.equal(error.retryable, false);
    assert.equal(error.details.cause, 'quota');
    assert.match(error.message, /cota/);
    return true;
  });
  assert.equal(calls, 1);
});

test('a per-minute quota name outranks incidental daily wording in the message', async () => {
  const api = backend(async () => response(429, {error: {message: 'Rate limit exceeded. The daily quota resets once per day.', errors: [{reason: 'rateLimitExceeded'}], details: [{
    '@type': 'type.googleapis.com/google.rpc.ErrorInfo', metadata: {quota_limit: 'QueriesPerMinutePerProject'}
  }]}}));
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
    assert.equal(error.details.cause, 'rate-limit');
    assert.equal(error.details.limitScope, 'minute');
    assert.equal(error.details.limitName, 'QueriesPerMinutePerProject');
    assert.match(error.message, /limitou temporariamente/);
    assert.doesNotMatch(error.message, /cota.*esgotada/);
    return true;
  });
});

test('mentioning daily quota does not prove it is exhausted', async () => {
  const api = backend(async () => response(429, {error: {message: 'Rate limit exceeded. Daily quota resets once per day.', errors: [{reason: 'rateLimitExceeded'}]}}));
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => error.details.cause === 'rate-limit' && !error.details.limitScope);
});

test('unknown quota-related reasons are not asserted to be an exhausted quota', async () => {
  const api = backend(async () => response(503, {error: {message: 'Quota service temporarily unavailable.', errors: [{reason: 'quotaServiceUnavailable'}]}}));
  await assert.rejects(api.testing.providerFetch('https://www.googleapis.com/youtube/v3/search', {}, 'google'), error => {
    assert.equal(error.details.cause, 'http');
    assert.equal(error.retryable, true);
    assert.doesNotMatch(error.message, /esgotada|atingida/);
    return true;
  });
});

test('successful YouTube searches are reused in a warm function and isolated by account', async () => {
  let searches = 0, details = 0;
  const api = backend(async url => {
    if (url.includes('/search?')) {searches++; return response(200, {items: [{id: {videoId: 'Video123456'}}]});}
    details++;
    return response(200, {items: [{id: 'Video123456', snippet: {title: 'Song', channelTitle: 'Artist'}, contentDetails: {duration: 'PT3M'}}]});
  });
  const session = api.testing.seal({access_token: 'test-only', refresh_token: 'account-one', expires_at: Date.now() + 3600000});
  const request = event('youtube/search', {q: 'Artist Song'}, `g_session=${session}`);
  assert.equal((await api.handler(request)).statusCode, 200);
  assert.equal((await api.handler({...request, queryStringParameters: {q: 'artist   song'}})).statusCode, 200);
  assert.equal(searches, 1);
  assert.equal(details, 1);
  const other = api.testing.seal({access_token: 'test-two', refresh_token: 'account-two', expires_at: Date.now() + 3600000});
  assert.equal((await api.handler({...request, headers: {cookie: `g_session=${other}`}})).statusCode, 200);
  assert.equal(searches, 2);
});

test('a failed video-details response does not repeat the paid search on retry', async () => {
  let searches = 0, details = 0;
  const api = backend(async url => {
    if (url.includes('/search?')) {searches++; return response(200, {items: [{id: {videoId: 'Video123456'}}]});}
    if (++details <= 2) return response(503, {error: {message: 'Unavailable'}});
    return response(200, {items: [{id: 'Video123456', snippet: {title: 'Song'}, contentDetails: {duration: 'PT3M'}}]});
  });
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const request = event('youtube/search', {q: 'Song'}, `g_session=${session}`);
  assert.equal((await api.handler(request)).statusCode, 503);
  assert.equal((await api.handler(request)).statusCode, 200);
  assert.equal(searches, 1);
  assert.equal(details, 3);
});

test('a failed search is tried once per server invocation', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; return response(503, {error: {message: 'Unavailable'}});});
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const request = event('youtube/search', {q: 'Song'}, `g_session=${session}`);
  assert.equal((await api.handler(request)).statusCode, 503);
  assert.equal((await api.handler(request)).statusCode, 503);
  assert.equal(calls, 2);
});

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

test('an inaccessible destination identifies its scope for safe frontend recovery', async () => {
  const api = backend(async () => response(200, {items: []}));
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const result = await api.handler({path: '/api/youtube/playlist/state', httpMethod: 'GET',
    headers: {cookie: `g_session=${session}`}, queryStringParameters: {input: 'Playlist1234567890'}});
  assert.equal(result.statusCode, 404);
  assert.equal(JSON.parse(result.body).code, 'DESTINATION_UNAVAILABLE');
  assert.equal(JSON.parse(result.body).details.operation, 'playlist-read');
  assert.equal(JSON.parse(result.body).details.upstreamStatus, 200, 'YouTube returned an empty listing, not an upstream HTTP 404');
});

test('YouTube search requests ten candidates with their recording durations', async () => {
  const ids = Array.from({length: 10}, (_, i) => `Video12345${i}`);
  let searchAt;
  const api = backend(async url => {
    const parsed = new URL(url);
    if (parsed.pathname.endsWith('/search')) {
      searchAt = Date.now();
      assert.equal(parsed.searchParams.get('maxResults'), '10');
      return response(200, {items: ids.map(id => ({id: {videoId: id}}))});
    }
    assert.equal(parsed.searchParams.get('id'), ids.join(','));
    assert.ok(Date.now() - searchAt >= 990, 'the metadata request must not immediately follow the search');
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

const googleEnv = {GOOGLE_CLIENT_ID: 'fixture-google-client', GOOGLE_CLIENT_SECRET: 'fixture-google-secret'};
async function googleLogin(api, previous) {
  const start = await api.handler(event('google/start'));
  const oauthCookie = savedCookie(start, 'g_oauth');
  const oauth = api.testing.unseal(oauthCookie);
  const cookies = [`g_oauth=${oauthCookie}`];
  if (previous) cookies.push(`g_session=${api.testing.seal(previous)}`);
  return api.handler(event('google/callback', {state: oauth.state, code: 'fixture-code'}, cookies.join('; ')));
}

test('Google reconnect keeps a refresh token only for the same confirmed channel', async () => {
  const api = backend(async url => url.endsWith('/token')
    ? response(200, {access_token: 'new-access', expires_in: 3600})
    : response(200, {items: [{id: 'channel-one', snippet: {title: 'One'}}]}), googleEnv);
  const result = await googleLogin(api, {channel_id: 'channel-one', refresh_token: 'same-account-refresh'});
  assert.match(result.headers.Location, /auth=google-ok/);
  const session = api.testing.unseal(savedCookie(result, 'g_session'));
  assert.equal(session.channel_id, 'channel-one');
  assert.equal(session.refresh_token, 'same-account-refresh');
  assert.equal(session.access_token, 'new-access');
});

for (const previousChannel of ['different-channel', undefined]) {
  test(`Google never combines a new account with a ${previousChannel ? 'different' : 'legacy unknown'} account refresh token`, async () => {
    const api = backend(async url => url.endsWith('/token')
      ? response(200, {access_token: 'new-account-access', expires_in: 3600})
      : response(200, {items: [{id: 'new-channel', snippet: {title: 'New'}}]}), googleEnv);
    const result = await googleLogin(api, {channel_id: previousChannel, refresh_token: 'old-account-refresh'});
    const url = new URL(result.headers.Location, 'https://rhyft.example.test');
    assert.match(url.searchParams.get('auth_error'), /acesso offline para esta conta/);
    assert.equal(url.searchParams.has('auth'), false);
    assert.ok(result.multiValueHeaders['Set-Cookie'].some(value => value.startsWith('g_session=;') && value.includes('Max-Age=0')));
    assert.doesNotMatch(result.body, /old-account-refresh|new-account-access/);
  });
}

test('Google account switch uses the new account refresh token and stable channel identity', async () => {
  const api = backend(async url => url.endsWith('/token')
    ? response(200, {access_token: 'new-access', refresh_token: 'new-refresh', expires_in: 3600})
    : response(200, {items: [{id: 'new-channel', snippet: {title: 'New'}}]}), googleEnv);
  const result = await googleLogin(api, {channel_id: 'old-channel', refresh_token: 'old-refresh'});
  const session = api.testing.unseal(savedCookie(result, 'g_session'));
  assert.equal(session.channel_id, 'new-channel');
  assert.equal(session.refresh_token, 'new-refresh');
});

test('an unavailable channel identity cannot authorize reuse of an old refresh token', async () => {
  const api = backend(async url => url.endsWith('/token')
    ? response(200, {access_token: 'new-access', expires_in: 3600})
    : response(403, {error: {errors: [{reason: 'quotaExceeded'}]}}), googleEnv);
  const result = await googleLogin(api, {channel_id: 'old-channel', refresh_token: 'old-refresh'});
  assert.match(result.headers.Location, /auth_error=/);
  assert.ok(result.multiValueHeaders['Set-Cookie'].some(value => value.startsWith('g_session=;')));
});

test('session identity backfill refreshes legacy credentials and persists a channel ID without exposing tokens', async () => {
  const calls = [];
  const api = backend(async (url, options) => {
    calls.push(url);
    if (url.endsWith('/token')) return response(200, {access_token: 'refreshed-private-access', expires_in: 3600});
    assert.equal(options.headers.Authorization, 'Bearer refreshed-private-access');
    return response(200, {items: [{id: 'backfilled-channel', snippet: {title: 'My channel'}}]});
  }, googleEnv);
  const old = api.testing.seal({access_token: 'old-private-access', refresh_token: 'private-refresh', expires_at: 0});
  const result = await api.handler(event('session', {}, `g_session=${old}`));
  const data = JSON.parse(result.body);
  assert.equal(data.youtube.connected, true);
  assert.equal(data.youtube.accountId, 'backfilled-channel');
  assert.equal(data.youtube.name, 'My channel');
  assert.doesNotMatch(result.body, /private-access|private-refresh/);
  const cookieValues = result.multiValueHeaders['Set-Cookie'].filter(value => value.startsWith('g_session='));
  assert.equal(cookieValues.length, 1, 'refresh and identity backfill must persist one final cookie');
  const stored = api.testing.unseal(decodeURIComponent(cookieValues.at(-1).split(';')[0].slice('g_session='.length)));
  assert.equal(stored.channel_id, 'backfilled-channel');
  assert.equal(stored.refresh_token, 'private-refresh');
  assert.equal(calls.length, 2);
});

test('a legacy identity lookup quota failure keeps the valid login connected with unknown identity', async () => {
  const api = backend(async () => response(403, {error: {errors: [{reason: 'quotaExceeded'}]}}));
  const old = api.testing.seal({access_token: 'private-access', expires_at: Date.now() + 3600000});
  const result = await api.handler(event('session', {}, `g_session=${old}`));
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 200);
  assert.equal(data.youtube.connected, true);
  assert.equal(data.youtube.accountId, null);
});

test('a new offline token remains usable when the channel identity lookup is quota-limited', async () => {
  const api = backend(async url => url.endsWith('/token')
    ? response(200, {access_token: 'new-access', refresh_token: 'new-refresh', expires_in: 3600})
    : response(403, {error: {errors: [{reason: 'quotaExceeded'}]}}), googleEnv);
  const result = await googleLogin(api, {channel_id: 'old-channel', refresh_token: 'old-refresh'});
  assert.match(result.headers.Location, /auth=google-ok/);
  const session = api.testing.unseal(savedCookie(result, 'g_session'));
  assert.equal(session.channel_id, null);
  assert.equal(session.refresh_token, 'new-refresh');
});

test('known session account IDs are public and do not trigger extra identity requests', async () => {
  const api = backend(() => {throw new Error('unnecessary account request');});
  const sp = api.testing.seal({user_id: 'spotify-user', access_token: 'private-sp', expires_at: Date.now() + 3600000});
  const yt = api.testing.seal({channel_id: 'youtube-channel', access_token: 'private-yt', expires_at: Date.now() + 3600000});
  const result = await api.handler(event('session', {}, `sp_session=${sp}; g_session=${yt}`));
  const data = JSON.parse(result.body);
  assert.equal(data.spotify.accountId, 'spotify-user');
  assert.equal(data.youtube.accountId, 'youtube-channel');
  assert.doesNotMatch(result.body, /private-sp|private-yt/);
});

for (const provider of ['spotify', 'youtube']) {
  test(`${provider} detects a readable destination belonging to a different account before reading its tracks`, async () => {
    let calls = 0;
    const api = backend(async () => {
      calls++;
      return response(200, provider === 'youtube'
        ? {items: [{snippet: {title: 'Public other playlist', channelId: 'other-owner'}}]}
        : {name: 'Public other playlist', owner: {id: 'other-owner'}, collaborative: false});
    });
    const key = provider === 'youtube' ? 'channel_id' : 'user_id';
    const session = api.testing.seal({[key]: 'connected-owner', access_token: 'test-only', expires_at: Date.now() + 3600000});
    const cookie = `${provider === 'youtube' ? 'g' : 'sp'}_session=${session}`;
    const result = await api.handler(event(`${provider}/playlist/state`, {input: 'Playlist1234567890'}, cookie));
    const data = JSON.parse(result.body);
    assert.equal(result.statusCode, 409);
    assert.equal(data.code, 'DESTINATION_ACCOUNT_MISMATCH');
    assert.equal(data.details.provider, provider);
    assert.equal(data.details.operation, 'playlist-read');
    assert.equal(calls, 1);
  });

  test(`${provider} destination state includes the authenticated account ID`, async () => {
    const api = backend(async url => {
      if (url.includes('/playlists?')) return response(200, {items: [{snippet: {title: 'Mine', channelId: 'connected-owner'}}]});
      if (/\/playlists\/[^/?]+$/.test(url)) return response(200, {name: 'Mine', owner: {id: 'connected-owner'}});
      return response(200, {items: []});
    });
    const key = provider === 'youtube' ? 'channel_id' : 'user_id';
    const session = api.testing.seal({[key]: 'connected-owner', access_token: 'test-only', expires_at: Date.now() + 3600000});
    const result = await api.handler(event(`${provider}/playlist/state`, {input: 'Playlist1234567890'}, `${provider === 'youtube' ? 'g' : 'sp'}_session=${session}`));
    assert.equal(result.statusCode, 200);
    assert.equal(JSON.parse(result.body).accountId, 'connected-owner');
  });

  test(`${provider} provider destination 404s are scoped for recovery`, async () => {
    const api = backend(async () => response(404, {error: {message: 'Not found', errors: [{reason: 'playlistNotFound'}]}}));
    const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
    const result = await api.handler(event(`${provider}/playlist/state`, {input: 'Playlist1234567890'}, `${provider === 'youtube' ? 'g' : 'sp'}_session=${session}`));
    assert.equal(result.statusCode, 404);
    const data = JSON.parse(result.body);
    assert.equal(data.code, 'DESTINATION_UNAVAILABLE');
    assert.equal(data.details.operation, 'playlist-read');
    assert.equal(data.details.upstreamStatus, 404);
  });
}

test('a collaborative Spotify destination remains readable for a different authenticated user', async () => {
  const api = backend(async url => /\/playlists\/[^/?]+$/.test(url)
    ? response(200, {name: 'Shared', owner: {id: 'other-owner'}, collaborative: true})
    : response(200, {items: []}));
  const session = api.testing.seal({user_id: 'collaborator', access_token: 'test-only', expires_at: Date.now() + 3600000});
  const result = await api.handler(event('spotify/playlist/state', {input: 'Playlist1234567890'}, `sp_session=${session}`));
  assert.equal(result.statusCode, 200);
  assert.equal(JSON.parse(result.body).accountId, 'collaborator');
});

test('source 404s are not reclassified as missing destinations', async () => {
  const api = backend(async () => response(404, {error: {message: 'Source missing', errors: [{reason: 'playlistNotFound'}]}}));
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const result = await api.handler(event('youtube/playlist', {input: 'Playlist1234567890'}, `g_session=${session}`));
  assert.equal(result.statusCode, 404);
  assert.equal(JSON.parse(result.body).code, 'GOOGLE_404');
});

for (const [status, reason, cause] of [[403, 'quotaExceeded', 'quota'], [429, 'rateLimitExceeded', 'rate-limit'], [401, 'authError', 'auth']]) {
  test(`destination reads preserve ${cause} errors without suggesting replacement`, async () => {
    const api = backend(async () => response(status, {error: {message: 'Provider failure', errors: [{reason}]}}));
    const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
    const result = await api.handler(event('youtube/playlist/state', {input: 'Playlist1234567890'}, `g_session=${session}`));
    assert.equal(result.statusCode, status);
    assert.equal(JSON.parse(result.body).code, `GOOGLE_${status}`);
    assert.notEqual(JSON.parse(result.body).code, 'DESTINATION_UNAVAILABLE');
  });
}

for (const provider of ['spotify', 'youtube']) {
  for (const operation of ['create', 'add']) {
    for (const expectedMatches of [false, true]) {
      test(`${provider} ${operation} ${expectedMatches ? 'accepts the expected account' : 'blocks account switches before mutation'}`, async () => {
        let calls = 0;
        const api = backend(async (_url, options) => {
          calls++;
          assert.equal(options.method, 'POST');
          return response(201, {id: 'Created123456789', name: 'Fixture', snippet: {title: 'Fixture'}});
        });
        const key = provider === 'youtube' ? 'channel_id' : 'user_id';
        const session = api.testing.seal({[key]: 'current-account', access_token: 'test-only', expires_at: Date.now() + 3600000});
        const route = operation === 'create' ? `${provider}/playlist` : provider === 'youtube' ? 'youtube/playlist/item' : 'spotify/playlist/items';
        const body = {name: 'Fixture', playlistId: 'Playlist1234567890', videoId: 'Video123456', uris: ['spotify:track:Track12345'], expectedAccountId: expectedMatches ? 'current-account' : 'previous-account'};
        const result = await api.handler({...event(route, {}, `${provider === 'youtube' ? 'g' : 'sp'}_session=${session}`), httpMethod: 'POST', body: JSON.stringify(body)});
        assert.equal(result.statusCode, expectedMatches ? 201 : 409);
        assert.equal(calls, expectedMatches ? 1 : 0);
        if (!expectedMatches) assert.equal(JSON.parse(result.body).code, 'ACCOUNT_CHANGED');
        else if (operation === 'create') assert.equal(JSON.parse(result.body).accountId, 'current-account');
      });
    }
  }
}

test('a missing session identity cannot satisfy an expected mutation account', async () => {
  let calls = 0;
  const api = backend(async () => {calls++; return response(201, {id: 'unexpected'});});
  const session = api.testing.seal({access_token: 'test-only', expires_at: Date.now() + 3600000});
  const result = await api.handler({...event('youtube/playlist', {}, `g_session=${session}`), httpMethod: 'POST', body: JSON.stringify({name: 'Fixture', expectedAccountId: 'expected-channel'})});
  assert.equal(result.statusCode, 409);
  assert.equal(JSON.parse(result.body).code, 'ACCOUNT_CHANGED');
  assert.equal(calls, 0);
});

test('a legacy mixed Google cookie cannot keep the old channel identity after refreshing another account', async () => {
  let mutations = 0;
  const api = backend(async (url, options) => {
    if (url.endsWith('/token')) return response(200, {access_token: 'account-two-access', expires_in: 3600});
    if (url.includes('/channels?')) {
      assert.equal(options.headers.Authorization, 'Bearer account-two-access');
      return response(200, {items: [{id: 'account-two-channel', snippet: {title: 'Two'}}]});
    }
    mutations++;
    return response(201, {id: 'unexpected-mutation'});
  }, googleEnv);
  const mixed = {channel_id: 'account-one-channel', access_token: 'account-one-access', refresh_token: 'account-two-refresh', expires_at: 0};
  const session = api.testing.seal(mixed);
  const result = await api.handler({...event('youtube/playlist', {}, `g_session=${session}`), httpMethod: 'POST', body: JSON.stringify({name: 'Must not create', expectedAccountId: 'account-one-channel'})});
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 401);
  assert.equal(data.code, 'AUTH_REQUIRED');
  assert.equal(data.details.cause, 'account-mismatch');
  assert.match(data.error, /credenciais de contas diferentes.*Conecte/);
  assert.equal(mutations, 0);
  assert.doesNotMatch(result.body, /account-two-access|account-two-refresh|account-one-access/);
  const optional = await api.handler(event('session', {}, `g_session=${session}`));
  assert.equal(optional.statusCode, 200);
  assert.equal(JSON.parse(optional.body).youtube.connected, false);
});

test('a same-channel Google refresh verifies the new token and preserves the confirmed identity', async () => {
  const api = backend(async (url, options) => {
    if (url.endsWith('/token')) return response(200, {access_token: 'new-access', expires_in: 3600});
    assert.equal(options.headers.Authorization, 'Bearer new-access');
    return response(200, {items: [{id: 'same-channel', snippet: {title: 'Current channel title'}}]});
  }, googleEnv);
  const refreshed = await api.testing.refreshGoogle({channel_id: 'same-channel', name: 'Old title', refresh_token: 'same-refresh'});
  assert.equal(refreshed.channel_id, 'same-channel');
  assert.equal(refreshed.access_token, 'new-access');
  assert.equal(refreshed.refresh_token, 'same-refresh');
  assert.equal(refreshed.name, 'Current channel title');
});

test('a known account refresh preserves quota verification failures instead of falsely reporting disconnected', async () => {
  const api = backend(async url => url.endsWith('/token')
    ? response(200, {access_token: 'new-access', expires_in: 3600})
    : response(403, {error: {errors: [{reason: 'quotaExceeded'}]}}), googleEnv);
  const session = api.testing.seal({channel_id: 'known-channel', access_token: 'old-access', refresh_token: 'same-refresh', expires_at: 0});
  const result = await api.handler(event('session', {}, `g_session=${session}`));
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 403);
  assert.equal(data.code, 'GOOGLE_403');
  assert.equal(data.details.cause, 'quota');
  assert.equal(data.details.operation, 'account');
  assert.equal(result.multiValueHeaders, undefined, 'unverified refreshed credentials must not be persisted');
});

test('a known account refresh preserves identity connection failures instead of reporting disconnected', async () => {
  const api = backend(async url => {
    if (url.endsWith('/token')) return response(200, {access_token: 'new-access', expires_in: 3600});
    throw new TypeError('private-connection-details');
  }, googleEnv);
  const session = api.testing.seal({channel_id: 'known-channel', access_token: 'old-access', refresh_token: 'same-refresh', expires_at: 0});
  const result = await api.handler(event('session', {}, `g_session=${session}`));
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 502);
  assert.equal(data.code, 'PROVIDER_UNAVAILABLE');
  assert.equal(data.details.operation, 'account');
  assert.equal(result.multiValueHeaders, undefined);
  assert.doesNotMatch(result.body, /private-connection-details|new-access|same-refresh/);
});

test('an unknown legacy account remains connected if refresh identity lookup is quota-limited and is checked only once', async () => {
  let calls = 0;
  const api = backend(async url => {
    calls++;
    return url.endsWith('/token')
      ? response(200, {access_token: 'new-access', expires_in: 3600})
      : response(403, {error: {errors: [{reason: 'quotaExceeded'}]}});
  }, googleEnv);
  const session = api.testing.seal({access_token: 'old-access', refresh_token: 'same-refresh', expires_at: 0});
  const result = await api.handler(event('session', {}, `g_session=${session}`));
  const data = JSON.parse(result.body);
  assert.equal(result.statusCode, 200);
  assert.equal(data.youtube.connected, true);
  assert.equal(data.youtube.accountId, null);
  assert.equal(calls, 2, 'the same identity lookup must not be repeated by session backfill');
  const stored = api.testing.unseal(savedCookie(result, 'g_session'));
  assert.equal(stored.access_token, 'new-access');
});
