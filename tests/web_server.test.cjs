const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {createServer} = require('../web-server.cjs');

async function fixture(options = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'rhyft-web-server-'));
  const siteRoot = path.join(directory, 'site');
  await fs.mkdir(siteRoot);
  await fs.writeFile(path.join(siteRoot, 'index.html'), '<!doctype html><title>RHYFT fixture</title>');
  await fs.writeFile(path.join(siteRoot, 'migrar.html'), '<h1>Migration</h1>');
  await fs.writeFile(path.join(siteRoot, 'app.js'), 'window.fixture = true;');
  await fs.writeFile(path.join(siteRoot, 'styles.css'), 'body { color: white; }');
  await fs.writeFile(path.join(directory, '.env'), 'PRIVATE_HOST_SECRET=must-not-leak');
  await fs.writeFile(path.join(directory, 'private.json'), '{"token":"must-not-leak"}');
  await fs.writeFile(path.join(siteRoot, '.env'), 'PRIVATE_STATIC_SECRET=must-not-leak');
  await fs.writeFile(path.join(siteRoot, 'key.pem'), 'must-not-leak');
  await fs.symlink(path.join(directory, 'private.json'), path.join(siteRoot, 'outside.json'));
  await fs.symlink(path.join(siteRoot, '.env'), path.join(siteRoot, 'hidden.txt'));
  const events = [];
  const server = createServer({siteRoot, handler: async event => {
    events.push(event);
    return {statusCode: 200, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ok: true})};
  }, ...options});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const request = (pathname, {method = 'GET', headers = {}, body, chunks} = {}) => new Promise((resolve, reject) => {
    const req = http.request({hostname: '127.0.0.1', port: server.address().port, path: pathname, method, headers}, res => {
      const values = [];
      res.on('data', chunk => values.push(chunk));
      res.on('end', () => resolve({status: res.statusCode, headers: res.headers, body: Buffer.concat(values)}));
      res.on('error', reject);
    });
    req.on('error', reject);
    for (const chunk of chunks || []) req.write(chunk);
    req.end(body);
  });
  const close = async () => {
    await new Promise(resolve => server.close(resolve));
    await fs.rm(directory, {recursive: true, force: true});
  };
  return {request, close, events, server};
}

test('serves the site entry and assets with Netlify security headers and MIME types', async () => {
  const f = await fixture();
  try {
    const response = await f.request('/');
    assert.equal(response.status, 200);
    assert.match(response.body.toString(), /RHYFT fixture/);
    assert.match(response.headers['content-type'], /^text\/html/);
    assert.equal(response.headers['x-content-type-options'], 'nosniff');
    assert.equal(response.headers['x-frame-options'], 'DENY');
    assert.match(response.headers['content-security-policy'], /connect-src 'self' https:\/\/api.github.com/);
    assert.match(response.headers['content-security-policy'], /upgrade-insecure-requests/);
    const js = await f.request('/app.js?v=2');
    assert.match(js.headers['content-type'], /^text\/javascript/);
    assert.equal(js.headers['cache-control'], 'public, max-age=300');
    assert.equal((await f.request('/styles.css')).headers['cache-control'], 'public, max-age=3600');
  } finally {await f.close();}
});

test('HEAD has the same static content length and headers without sending a body', async () => {
  const f = await fixture();
  try {
    const get = await f.request('/migrar.html');
    const head = await f.request('/migrar.html', {method: 'HEAD'});
    assert.equal(head.status, 200);
    assert.equal(head.body.length, 0);
    assert.equal(head.headers['content-length'], get.headers['content-length']);
    assert.equal(head.headers['content-type'], get.headers['content-type']);
  } finally {await f.close();}
});

test('health exposes no environment configuration and does not invoke the API handler', async () => {
  const f = await fixture();
  try {
    const response = await f.request('/health');
    assert.equal(response.status, 200);
    assert.deepEqual(JSON.parse(response.body), {status: 'ok'});
    assert.equal(response.headers['cache-control'], 'no-store');
    assert.equal((await f.request('/health', {method: 'HEAD'})).body.length, 0);
    assert.equal(f.events.length, 0);
    assert.equal((await f.request('/health', {method: 'POST'})).status, 405);
  } finally {await f.close();}
});

test('adapts real HTTP API requests and preserves body, query parameters and cookies', async () => {
  const f = await fixture();
  try {
    const body = JSON.stringify({name: 'Minhas músicas', uris: ['spotify:track:123']});
    const response = await f.request('/api/spotify/playlist?name=first&name=second&q=caf%C3%A9', {
      method: 'POST', headers: {'Content-Type': 'application/json; charset=utf-8', Cookie: 'g_session=fixture'}, body
    });
    assert.equal(response.status, 200);
    const [event] = f.events;
    assert.equal(event.path, '/api/spotify/playlist');
    assert.equal(event.httpMethod, 'POST');
    assert.equal(event.body, body);
    assert.equal(event.headers.cookie, 'g_session=fixture');
    assert.equal(event.queryStringParameters.name, 'second');
    assert.equal(event.queryStringParameters.q, 'café');
    assert.deepEqual(event.multiValueQueryStringParameters.name, ['first', 'second']);
    assert.equal(event.isBase64Encoded, false);
  } finally {await f.close();}
});

test('preserves multiple Set-Cookie values and OAuth redirects from Netlify responses', async () => {
  const f = await fixture({handler: async () => ({statusCode: 302,
    headers: {Location: 'https://accounts.example.test/authorize', 'Cache-Control': 'no-store'},
    multiValueHeaders: {'Set-Cookie': ['session=new; HttpOnly; Path=/', 'oauth=; Max-Age=0; Path=/']}, body: ''})});
  try {
    const response = await f.request('/api/google/callback');
    assert.equal(response.status, 302);
    assert.equal(response.headers.location, 'https://accounts.example.test/authorize');
    assert.deepEqual(response.headers['set-cookie'], ['session=new; HttpOnly; Path=/', 'oauth=; Max-Age=0; Path=/']);
    assert.equal(response.headers['cache-control'], 'no-store');
  } finally {await f.close();}
});

test('ignores forwarded protocol unless the reverse proxy is explicitly trusted', async () => {
  for (const trustProxy of [false, true]) {
    const f = await fixture({trustProxy});
    try {
      await f.request('/api/session', {headers: {'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'spoofed.example', Forwarded: 'proto=https'}});
      assert.equal(f.events[0].headers['x-forwarded-proto'], trustProxy ? 'https' : 'http');
      assert.equal(f.events[0].headers['x-forwarded-host'], undefined);
      assert.equal(f.events[0].headers.forwarded, undefined);
    } finally {await f.close();}
  }
});

test('rejects traversal, dotfiles, repository files and symlinks outside the public site', async () => {
  const f = await fixture();
  try {
    for (const pathname of ['/../.env', '/%2e%2e/private.json', '/%2e%2e%2fprivate.json', '/.%2f.env', '/%5c..%5c.env', '/%00index.html', '/.env', '/.git/config', '/outside.json', '/hidden.txt', '/key.pem', '/web-server.cjs', '/netlify/functions/api.js']) {
      const response = await f.request(pathname);
      assert.ok([403, 404].includes(response.status), `${pathname}: unexpected ${response.status}`);
      assert.doesNotMatch(response.body.toString(), /must-not-leak|PRIVATE_HOST_SECRET|PRIVATE_STATIC_SECRET/);
    }
    assert.equal(f.events.length, 0);
  } finally {await f.close();}
});

test('enforces the JSON body limit for declared and chunked requests without invoking the API', async () => {
  const f = await fixture();
  try {
    const valid = JSON.stringify({value: 'a'.repeat(65524)});
    assert.equal(Buffer.byteLength(valid), 65536);
    assert.equal((await f.request('/api/test', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: valid})).status, 200);
    const before = f.events.length;
    const oversized = JSON.stringify({value: 'a'.repeat(65525)});
    assert.equal((await f.request('/api/test', {method: 'POST', headers: {'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(oversized)}, body: oversized})).status, 413);
    assert.equal((await f.request('/api/test', {method: 'POST', headers: {'Content-Type': 'application/json'}, chunks: [oversized.slice(0, 32000), oversized.slice(32000)]})).status, 413);
    assert.equal(f.events.length, before);
    assert.equal((await f.request('/health')).status, 200);
  } finally {await f.close();}
});

test('rejects malformed JSON, non-JSON API bodies and static mutations', async () => {
  const f = await fixture();
  try {
    assert.equal((await f.request('/api/test', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{bad'})).status, 400);
    assert.equal((await f.request('/api/test', {method: 'POST', headers: {'Content-Type': 'text/plain'}, body: '{}'})).status, 415);
    assert.equal((await f.request('/migrar.html', {method: 'POST', body: '{}'})).status, 405);
    assert.equal(f.events.length, 0);
  } finally {await f.close();}
});

test('API failures do not expose exception messages or secrets; binary responses remain intact', async () => {
  const broken = await fixture({handler: async () => {throw new Error('private-token=must-not-leak');}});
  try {
    const response = await broken.request('/api/test');
    assert.equal(response.status, 500);
    assert.doesNotMatch(response.body.toString(), /must-not-leak|private-token/);
    assert.equal(response.headers['cache-control'], 'no-store');
  } finally {await broken.close();}
  const binary = await fixture({handler: async () => ({statusCode: 200, headers: {'Content-Type': 'application/octet-stream'}, body: Buffer.from([0, 255, 2]).toString('base64'), isBase64Encoded: true})});
  try {assert.deepEqual((await binary.request('/api/test')).body, Buffer.from([0, 255, 2]));}
  finally {await binary.close();}
});
