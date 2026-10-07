const assert = require('node:assert/strict');
const {test, before, after} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');

let browser, server, baseUrl;
const key = 'rhyft.web.history.v1';
before(async () => {
  server = http.createServer((req, res) => {
    const filename = path.basename(new URL(req.url, 'http://localhost').pathname);
    const file = path.join(__dirname, '../site', filename || 'migrar.html');
    if (!fs.existsSync(file)) {res.writeHead(404); res.end(); return;}
    const ext = path.extname(file);
    res.setHeader('Content-Type', ext === '.js' ? 'text/javascript' : ext === '.html' ? 'text/html' : ext === '.css' ? 'text/css' : 'image/svg+xml');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({headless: true,
    executablePath: process.env.CHROMIUM_PATH || (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined)});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});

async function fixture(direction = 'spotify-youtube', options = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error));
  const origin = direction === 'spotify-youtube' ? 'spotify' : 'youtube';
  const target = origin === 'spotify' ? 'youtube' : 'spotify';
  const sourceId = 'Source123456789012345';
  const destId = 'Destination123456789';
  const destUrl = target === 'youtube' ? `https://www.youtube.com/playlist?list=${destId}` : `https://open.spotify.com/playlist/${destId}`;
  const tracks = [
    {id: 'SourceTrack1234567890', name: 'Horizonte', artists: ['Artista'], duration: 180},
    {id: 'SourceTrack2345678901', name: 'Tempestade', artists: ['Artista'], duration: 180}
  ];
  const candidates = tracks.map((track, i) => ({id: `Music12345${i}`, title: track.name, name: track.name,
    channel: 'Artista', artists: ['Artista'], duration: 180, uri: `spotify:track:Music12345${i}`}));
  const remote = new Set(options.existing ? [candidates[0].id] : []);
  const stats = {creates: 0, writes: [], searches: [], reads: 0, failSearch: false, loseWriteResponse: false, transientSearch: false, denyRead: false, dropWrite: false, pendingSearch: false, searchGate: null};
  const historyRecord = {id: 'legacy-history', createdAt: new Date().toISOString(), direction,
    sourceInput: sourceId, destinationName: 'Destino antigo', destinationUrl: destUrl, added: 1, pending: 0, skipped: 1, status: 'completed'};
  if (options.legacy) await context.addInitScript(({key, record}) => {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify([record]));
  }, {key, record: historyRecord});
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url());
    const json = (data, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)});
    if (url.pathname === '/api/session') return json({setup: {ready: true}, spotify: {connected: true}, youtube: {connected: true}});
    if (url.pathname === `/api/${origin}/playlist` && req.method() === 'GET') return json({id: sourceId, tracks});
    if (url.pathname === `/api/${target}/playlist` && req.method() === 'POST') {
      stats.creates++; return json({id: destId, url: destUrl, name: 'Destino novo'}, 201);
    }
    if (url.pathname === `/api/${target}/playlist/state`) {
      stats.reads++;
      if (stats.denyRead) return json({error: 'Destino inacessível'}, 403);
      // Exercise browser-side pagination, including an empty first page.
      return url.searchParams.has('page') ? json({id: destId, ids: [...remote], nextPage: null})
        : json({id: destId, name: 'Destino antigo', ids: [], nextPage: 'next'});
    }
    if (url.pathname === `/api/${target}/search`) {
      const query = url.searchParams.get('q'); stats.searches.push(query);
      if (stats.searchGate) await stats.searchGate;
      if (stats.pendingSearch && query.includes('Horizonte')) return json({items: [{...candidates[0], name: 'Outra música', title: 'Outra música', artists: ['Outro'], channel: 'Outro'}]});
      if (stats.transientSearch) {stats.transientSearch = false; return json({error: 'The operation was aborted.', code: 'PROVIDER_TIMEOUT'}, 504);}
      if (stats.failSearch && query.includes('Tempestade')) return json({error: 'The operation was aborted.', code: 'PROVIDER_TIMEOUT'}, 504);
      return json({items: [candidates[query.includes('Tempestade') ? 1 : 0]]});
    }
    if (url.pathname === `/api/${target}/playlist/item` || url.pathname === `/api/${target}/playlist/items`) {
      const body = req.postDataJSON();
      const ids = target === 'youtube' ? [body.videoId] : body.uris.map(uri => uri.split(':')[2]);
      stats.writes.push(ids);
      if (stats.dropWrite) {stats.dropWrite = false; return json({error: 'Timeout sem confirmação', code: 'PROVIDER_TIMEOUT'}, 504);}
      ids.forEach(id => remote.add(id));
      if (stats.loseWriteResponse) {stats.loseWriteResponse = false; return json({error: 'The operation was aborted.', code: 'PROVIDER_TIMEOUT'}, 504);}
      return json({ok: true}, 201);
    }
    throw new Error(`Unexpected API request ${req.method()} ${url.pathname}`);
  });
  await page.goto(`${baseUrl}/migrar.html`);
  await page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
  if (direction === 'youtube-spotify') await page.locator('[data-direction="youtube-spotify"]').click();
  await page.locator('#playlist-input').fill(sourceId);
  await page.locator('#playlist-name').fill('Destino novo');
  const start = async () => {
    await page.locator('#start-migration').click();
    await page.waitForFunction(() => !document.querySelector('#start-migration').disabled && !document.querySelector('#result-panel').hidden);
  };
  const history = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), key);
  return {page, context, stats, remote, candidates, sourceId, history, start, errors};
}

for (const direction of ['spotify-youtube', 'youtube-spotify']) {
  test(`${direction}: old history and equivalent source links reuse the destination without duplicates`, async () => {
    const f = await fixture(direction, {existing: true, legacy: true});
    try {
      await f.page.locator('#history-toggle').click();
      await f.page.getByRole('button', {name: 'Retomar / atualizar'}).click();
      await f.start();
      assert.equal(f.stats.creates, 0);
      assert.deepEqual(f.stats.writes.flat(), [f.candidates[1].id]);
      assert.equal(f.stats.reads, 2);
      assert.equal((await f.history()).length, 1);
      await f.page.reload();
      await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
      if (direction === 'youtube-spotify') await f.page.locator('[data-direction="youtube-spotify"]').click();
      const link = direction === 'spotify-youtube' ? `https://open.spotify.com/playlist/${f.sourceId}?si=other` : `https://music.youtube.com/playlist?list=${f.sourceId}&si=other`;
      await f.page.locator('#playlist-input').fill(link);
      // The saved playlist is sufficient: no need to reenter its name.
      await f.start();
      assert.equal(f.stats.creates, 0);
      assert.deepEqual(f.stats.writes.flat(), [f.candidates[1].id]);
      assert.equal((await f.history()).length, 1);
      assert.match(await f.page.locator('#live-log').innerText(), /recuperada do histórico/);
      assert.equal(await f.page.locator('#progress-count').innerText(), '2 de 2');
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });

  test(`${direction}: a lost POST response is reconciled, not repeated`, async () => {
    const f = await fixture(direction);
    try {
      f.stats.loseWriteResponse = true;
      await f.start();
      assert.equal(f.stats.creates, 1);
      assert.equal(f.stats.writes.flat().length, 2);
      assert.equal(new Set(f.stats.writes.flat()).size, 2);
      assert.equal((await f.history())[0].status, 'completed');
      assert.deepEqual((await f.history())[0].inFlight, []);
      assert.match(await f.page.locator('#live-log').innerText(), /Envio confirmado/);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });
}

test('an exhausted read failure persists progress and a reload resumes the same playlist', async () => {
  const f = await fixture();
  try {
    f.stats.failSearch = true;
    await f.start();
    const saved = (await f.history())[0];
    assert.equal(saved.status, 'interrupted');
    assert.equal(saved.destinationId, 'Destination123456789');
    assert.equal(Object.keys(saved.processed).length, 1);
    assert.equal(f.stats.creates, 1);
    f.stats.failSearch = false;
    await f.page.reload();
    await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
    await f.page.locator('#playlist-input').fill(f.sourceId);
    await f.start();
    assert.equal(f.stats.creates, 1);
    assert.deepEqual(f.stats.writes.flat(), f.candidates.map(c => c.id));
    assert.equal((await f.history()).length, 1);
    assert.equal((await f.history())[0].status, 'completed');
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('a temporary read error recovers without skipping the source track', async () => {
  const f = await fixture();
  try {
    f.stats.transientSearch = true;
    await f.start();
    assert.equal((await f.history())[0].skipped, 0);
    assert.equal(f.stats.searches.length, 3);
    assert.equal(f.remote.size, 2);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

for (const direction of ['spotify-youtube', 'youtube-spotify']) {
  test(`${direction}: an unconfirmed write stops and retries only after checking the destination`, async () => {
    const f = await fixture(direction);
    try {
      f.stats.dropWrite = true;
      await f.start();
      assert.equal((await f.history())[0].status, 'interrupted');
      assert.ok((await f.history())[0].inFlight.length > 0);
      assert.equal(f.stats.creates, 1);
      await f.start();
      assert.equal(f.stats.creates, 1);
      assert.equal((await f.history())[0].status, 'completed');
      assert.equal(f.remote.size, 2);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });
}

test('a failed destination check preserves the earlier checkpoint', async () => {
  const f = await fixture();
  try {
    f.stats.failSearch = true;
    await f.start();
    const before = (await f.history())[0];
    f.stats.failSearch = false; f.stats.denyRead = true;
    await f.start();
    const after = (await f.history())[0];
    assert.deepEqual(after.processed, before.processed);
    assert.equal(after.added, before.added);
    assert.equal(f.stats.creates, 1);
    assert.equal(after.status, 'interrupted');
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('manual review survives reload and updates the same history entry', async () => {
  const f = await fixture();
  try {
    f.stats.pendingSearch = true;
    await f.start();
    assert.equal((await f.history())[0].pending, 1);
    await f.page.reload();
    await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
    await f.page.locator('#playlist-input').fill(f.sourceId);
    await f.start();
    assert.equal(f.stats.creates, 1);
    await f.page.getByRole('button', {name: 'Escolher', exact: true}).click();
    await f.page.waitForFunction(key => JSON.parse(localStorage.getItem(key))[0].pending === 0, key);
    assert.equal((await f.history()).length, 1);
    assert.equal(f.remote.size, 2);
    assert.equal(f.stats.writes.flat().length, 2);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('cancel during a search prevents the next write and preserves the destination', async () => {
  const f = await fixture();
  let release;
  try {
    f.stats.searchGate = new Promise(resolve => release = resolve);
    await f.page.locator('#start-migration').click();
    await f.page.waitForFunction(() => document.querySelector('#live-log').textContent.includes('Procurando:'));
    await f.page.locator('#cancel-migration').click();
    release();
    await f.page.waitForFunction(() => !document.querySelector('#start-migration').disabled);
    assert.equal((await f.history())[0].status, 'cancelled');
    assert.equal(f.stats.creates, 1);
    assert.equal(f.stats.writes.length, 0);
    assert.deepEqual(f.errors, []);
  } finally {release?.(); await f.context.close();}
});
