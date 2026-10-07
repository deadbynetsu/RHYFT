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
  const virtualClock = options.virtualClock ?? true;
  if (virtualClock) await page.clock.install({time: Date.now()});
  const errors = [];
  page.on('pageerror', error => errors.push(error));
  const origin = direction === 'spotify-youtube' ? 'spotify' : 'youtube';
  const target = origin === 'spotify' ? 'youtube' : 'spotify';
  const sourceId = 'Source123456789012345';
  const destId = 'Destination123456789';
  const destUrl = target === 'youtube' ? `https://www.youtube.com/playlist?list=${destId}` : `https://open.spotify.com/playlist/${destId}`;
  const tracks = options.tracks || [
    {id: 'SourceTrack1234567890', name: 'Horizonte', artists: ['Artista'], duration: 180},
    {id: 'SourceTrack2345678901', name: 'Tempestade', artists: ['Artista'], duration: 180}
  ];
  const candidates = options.candidates || tracks.map((track, i) => ({id: `Music12345${i}`, title: track.name, name: track.name,
    channel: 'Artista', artists: ['Artista'], duration: 180, uri: `spotify:track:Music12345${i}`, url: `https://www.youtube.com/watch?v=Music12345${i}`}));
  const remote = new Set(options.existing ? [candidates[0].id] : []);
  const stats = {creates: 0, createIds: [], createBodies: [], replacementRequired: false, writes: [], writeDestinations: [], writeBodies: [], writeFailure: null, searches: [], searchTimes: [], reads: 0, readIds: [], missingDestinations: new Set(), readFailure: null, readFailureDestination: null,
    accounts: {spotify: 'SpotifyAccount123', youtube: 'YouTubeAccount123'}, sourceFailure: null,
    failSearch: false, loseWriteResponse: false, transientSearch: false, denyRead: false, dropWrite: false, pendingSearch: false, searchGate: null, searchFailure: null, denyReadAfterWrite: false, weakPrimary: false, loginQueries: [], forbiddenSource: false, loggedOut: []};
  const historyRecord = {id: 'legacy-history', createdAt: new Date().toISOString(), direction,
    sourceInput: sourceId, destinationName: 'Destino antigo', destinationUrl: destUrl, added: 1, pending: 0, skipped: 1, status: 'completed', ...options.savedRecord};
  if (options.legacyPending) {
    historyRecord.pending = 1;
    historyRecord.pendingItems = [{source: tracks[0], candidates: [{...candidates[0], score: .51}], kind: target, resolved: false}];
  }
  if (options.legacy || options.legacyPending || options.savedRecord) await context.addInitScript(({key, record}) => {
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify([record]));
  }, {key, record: historyRecord});
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url());
    const json = (data, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)});
    if (url.pathname === '/api/session') return json({setup: {ready: true}, spotify: {connected: !stats.loggedOut.includes('spotify'), accountId: stats.accounts.spotify, sharedClientAvailable: options.sharedAvailable ?? true, callbackUrl: options.callbackUrl || `${baseUrl}/api/spotify/callback`}, youtube: {connected: !stats.loggedOut.includes('google'), accountId: stats.accounts.youtube}});
    if (url.pathname === '/api/logout') {stats.loggedOut.push(req.postDataJSON().provider); return json({ok: true});}
    if (url.pathname === '/api/spotify/start') {
      stats.loginQueries.push(url.searchParams.get('client_id'));
      return json({authorizationFixture: true});
    }
    if (url.pathname === `/api/${origin}/playlist` && req.method() === 'GET') {
      if (stats.sourceFailure) return json(stats.sourceFailure.data, stats.sourceFailure.status);
      if (stats.forbiddenSource) return json({code: 'SPOTIFY_403', error: 'Esta conta não está autorizada no aplicativo Spotify. Adicione-a em User Management ou use seu próprio Client ID.'}, 403);
      return json({id: sourceId, tracks});
    }
    if (url.pathname === `/api/${target}/playlist` && req.method() === 'POST') {
      stats.creates++;
      stats.createBodies.push(req.postDataJSON());
      const id = stats.replacementRequired || stats.missingDestinations.has(destId) || stats.creates > 1 ? `Replacement12345678${stats.creates}` : destId;
      stats.createIds.push(id);
      if (id !== destId) remote.clear();
      const destinationUrl = target === 'youtube' ? `https://www.youtube.com/playlist?list=${id}` : `https://open.spotify.com/playlist/${id}`;
      return json({id, url: destinationUrl, name: req.postDataJSON().name || 'Destino novo'}, 201);
    }
    if (url.pathname === `/api/${target}/playlist/state`) {
      const id = url.searchParams.get('input');
      stats.reads++; stats.readIds.push(id);
      if (stats.missingDestinations.has(id)) return json({error: 'Playlist não encontrada ou não acessível.', code: 'NOT_FOUND'}, 404);
      if (stats.readFailure && (!stats.readFailureDestination || stats.readFailureDestination === id)) return json(stats.readFailure.data, stats.readFailure.status);
      if (stats.denyRead) return json({error: 'Destino inacessível'}, 403);
      // Exercise browser-side pagination, including an empty first page.
      return url.searchParams.has('page') ? json({id, ids: [...remote], nextPage: null})
        : json({id, name: 'Destino antigo', ids: [], nextPage: 'next'});
    }
    if (url.pathname === `/api/${target}/search`) {
      const query = url.searchParams.get('q'); stats.searches.push(query);
      stats.searchTimes.push(await page.evaluate(() => Date.now()));
      if (stats.searchGate) await stats.searchGate;
      if (stats.searchFailure && query.includes('Tempestade') && (stats.searchFailure.remaining === undefined || stats.searchFailure.remaining > 0)) {
        if (stats.searchFailure.remaining !== undefined) stats.searchFailure.remaining--;
        if (stats.searchFailure.html) return route.fulfill({status: 504, contentType: 'text/html', body: '<html>Gateway timeout</html>'});
        return json(stats.searchFailure.data, stats.searchFailure.status);
      }
      if ((stats.pendingSearch || (stats.weakPrimary && !query.includes('"'))) && query.includes('Horizonte')) return json({items: [{...candidates[0], name: 'Outra música', title: 'Outra música', artists: ['Outro'], channel: 'Outro'}]});
      if (stats.transientSearch) {stats.transientSearch = false; return json({error: 'The operation was aborted.', code: 'PROVIDER_TIMEOUT'}, 504);}
      if (stats.failSearch && query.includes('Tempestade')) return json({error: 'The operation was aborted.', code: 'PROVIDER_TIMEOUT'}, 504);
      return json({items: [candidates[Math.max(0, tracks.findIndex(track => query.includes(track.name)))]]});
    }
    if (url.pathname === `/api/${target}/playlist/item` || url.pathname === `/api/${target}/playlist/items`) {
      const body = req.postDataJSON();
      const ids = target === 'youtube' ? [body.videoId] : body.uris.map(uri => uri.split(':')[2]);
      stats.writes.push(ids); stats.writeDestinations.push(body.playlistId); stats.writeBodies.push(body);
      if (stats.writeFailure) return json(stats.writeFailure.data, stats.writeFailure.status);
      if (stats.dropWrite) {
        stats.dropWrite = false;
        if (stats.denyReadAfterWrite) stats.denyRead = true;
        return json({error: 'Timeout sem confirmação', code: 'PROVIDER_TIMEOUT'}, 504);
      }
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
  const start = async ({withoutDestination = false} = {}) => {
    await page.locator('#start-migration').click();
    let stopped = false;
    const advancing = virtualClock ? (async () => {
      while (!stopped) {
        await page.clock.runFor(1000);
        await new Promise(resolve => setTimeout(resolve, 20));
      }
    })() : null;
    try {
      await page.waitForFunction(withoutDestination => !document.querySelector('#start-migration').disabled &&
        !(withoutDestination ? document.querySelector('#progress-panel') : document.querySelector('#result-panel')).hidden, withoutDestination, {timeout: 60000});
    } finally {stopped = true; await advancing;}
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
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /Falha ao buscar a música \(YouTube \/ Google\)/);
    assert.match(log, /HTTP 504.*PROVIDER_TIMEOUT/);
    assert.match(log, /Nova tentativa de leitura \(2\/3\)/);
    assert.match(log, /Busca falhou após 3 tentativas/);
    assert.equal(saved.skipped, 1);
    assert.doesNotMatch(log, /The operation was aborted/);
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
      const log = await f.page.locator('#live-log').innerText();
      assert.match(log, /Falha ao adicionar à playlist/);
      assert.match(log, /HTTP 504.*PROVIDER_TIMEOUT/);
      assert.match(log, /Não foi possível confirmar o envio/);
      await f.start();
      assert.equal(f.stats.creates, 1);
      assert.equal((await f.history())[0].status, 'completed');
      assert.equal(f.remote.size, 2);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });
}

test('upstream aborts identify the failing detail request, exhaust three reads and defer the track', async () => {
  const f = await fixture();
  try {
    f.stats.searchFailure = {status: 409, data: {error: 'The operation was aborted.', code: 'GOOGLE_409', retryable: true,
      details: {provider: 'youtube', operation: 'track-details', cause: 'aborted', upstreamStatus: 409, reason: 'ABORTED', attempts: 2}}};
    await f.start();
    const saved = (await f.history())[0];
    assert.equal(saved.status, 'interrupted');
    assert.equal(saved.skipped, 1);
    assert.equal(Object.keys(saved.processed).length, 1);
    assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 3);
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /Falha ao consultar os detalhes das músicas \(YouTube \/ Google\)/);
    assert.match(log, /não informou o motivo/);
    assert.match(log, /HTTP 409.*GOOGLE_409.*motivo: ABORTED/);
    assert.match(log, /tentativas no servidor: 2.*tentativas no navegador: 3/);
    assert.doesNotMatch(log, /The operation was aborted|prazo:|cota/);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

function rateFailure(provider = 'youtube', retryAfterMs = undefined) {
  return {status: 429, data: {error: 'A cota do YouTube para este projeto foi atingida.', code: provider === 'youtube' ? 'GOOGLE_429' : 'SPOTIFY_429', retryable: false,
    details: {provider, operation: 'search', cause: 'rate-limit', reason: 'rateLimitExceeded', attempts: 1, retryAfterMs}}};
}

test('a temporary rate limit waits and recovers automatically on the third search attempt', async () => {
  const f = await fixture('spotify-youtube', {virtualClock: true});
  try {
    f.stats.searchFailure = {...rateFailure(), remaining: 2};
    await f.start();
    assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 3);
    const times = f.stats.searchTimes.slice(1);
    assert.ok(times[1] - times[0] >= 60000, 'the first retry must wait for the whole window');
    assert.ok(times[2] - times[1] >= 120000, 'the second retry must back off further');
    assert.equal((await f.history())[0].status, 'completed');
    assert.equal((await f.history())[0].skipped, 0);
    assert.equal(f.remote.size, 2);
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /Todas as chamadas.*aguardam 60s/);
    assert.match(log, /Todas as chamadas.*aguardam 120s/);
    assert.doesNotMatch(log, /cota.*atingida|Migração interrompida/);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

for (const direction of ['spotify-youtube', 'youtube-spotify']) {
  test(`${direction}: after three rate limits, continues with later tracks and retries the deferred one on resume`, async () => {
    const tracks = ['Horizonte', 'Tempestade', 'Aurora'].map((name, i) => ({id: `SourceTrack12345678${i}`, name, artists: ['Artista'], duration: 180}));
    const f = await fixture(direction, {tracks, virtualClock: true});
    try {
      f.stats.searchFailure = rateFailure(direction === 'spotify-youtube' ? 'youtube' : 'spotify');
      await f.start();
      assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 3);
      assert.ok(f.stats.searches.some(q => q.includes('Aurora')));
      const aurora = f.stats.searches.findIndex(q => q.includes('Aurora'));
      assert.ok(f.stats.searchTimes[aurora] - f.stats.searchTimes[aurora - 1] >= (direction === 'spotify-youtube' ? 240000 : 120000),
        'the next track must honor the last cooldown, without resetting it');
      assert.deepEqual(f.stats.writes.flat(), [f.candidates[0].id, f.candidates[2].id]);
      const saved = (await f.history())[0];
      assert.equal(saved.status, 'interrupted');
      assert.equal(saved.skipped, 1);
      assert.equal(saved.processed[tracks[1].id], undefined, 'a deferred track must remain eligible for retry');
      assert.match(await f.page.locator('#live-log').innerText(), /Busca falhou após 3 tentativas.*seguindo com as próximas/);
      f.stats.searchFailure = null;
      await f.start();
      assert.equal(f.stats.creates, 1);
      assert.deepEqual(f.stats.writes.flat(), [f.candidates[0].id, f.candidates[2].id, f.candidates[1].id]);
      assert.equal((await f.history())[0].status, 'completed');
      assert.equal((await f.history())[0].skipped, 0);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });
}

test('a longer provider Retry-After is respected before retrying a search', async () => {
  const f = await fixture('spotify-youtube', {virtualClock: true});
  try {
    f.stats.searchFailure = {...rateFailure('youtube', 90000), remaining: 1};
    await f.start();
    assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 2);
    assert.ok(f.stats.searchTimes[2] - f.stats.searchTimes[1] >= 90000);
    assert.match(await f.page.locator('#live-log').innerText(), /aguardam 90s/);
    assert.equal((await f.history())[0].status, 'completed');
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('pause and cancel stay responsive while waiting after a rate limit', async () => {
  const f = await fixture('spotify-youtube', {virtualClock: true});
  try {
    f.stats.searchFailure = rateFailure();
    await f.page.locator('#start-migration').click();
    for (let i = 0; i < 15 && !await f.page.locator('#live-log').textContent().then(text => text.includes('aguardam 60s')); i++) {
      await f.page.clock.runFor(1000);
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.match(await f.page.locator('#progress-title').innerText(), /YouTube limitou as chamadas.*aguardando/);
    await f.page.locator('#pause-migration').click();
    await f.page.clock.runFor(65000);
    assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 1);
    await f.page.locator('#cancel-migration').click();
    await f.page.clock.runFor(1000);
    await f.page.waitForFunction(() => !document.querySelector('#start-migration').disabled);
    assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 1);
    assert.equal((await f.history())[0].status, 'cancelled');
    assert.equal((await f.history())[0].added, 1);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('a reload cannot bypass the saved YouTube cooldown', async () => {
  const f = await fixture();
  try {
    f.stats.searchFailure = rateFailure();
    await f.page.locator('#start-migration').click();
    for (let i = 0; i < 15 && !await f.page.locator('#live-log').textContent().then(text => text.includes('aguardam 60s')); i++) {
      await f.page.clock.runFor(1000);
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.match(await f.page.locator('#progress-title').innerText(), /aguardando/);
    await f.page.locator('#cancel-migration').click();
    await f.page.clock.runFor(1000);
    await f.page.waitForFunction(() => !document.querySelector('#start-migration').disabled);
    f.stats.searchFailure = null;
    await f.page.reload();
    await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
    await f.page.locator('#playlist-input').fill(f.sourceId);
    await f.page.locator('#start-migration').click();
    for (let i = 0; i < 10; i++) {
      await f.page.clock.runFor(1000);
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(f.stats.reads, 0, 'destination checks also obey the platform cooldown after reload');
    assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 1);
    assert.match(await f.page.locator('#progress-title').innerText(), /YouTube limitou as chamadas.*aguardando/);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('normal YouTube operations are paced instead of sending a burst per track', async () => {
  const f = await fixture();
  try {
    await f.start();
    assert.ok(f.stats.searchTimes[1] - f.stats.searchTimes[0] >= 4000,
      'search, write and the next search must each reserve a platform slot');
    assert.equal((await f.history())[0].status, 'completed');
    assert.equal(f.stats.writes.length, 2);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

for (const direction of ['spotify-youtube', 'youtube-spotify']) {
  test(`${direction}: reload after an unconfirmed write reuses the successful search`, async () => {
    const f = await fixture(direction);
    try {
      f.stats.dropWrite = true;
      await f.start();
      assert.equal((await f.history())[0].status, 'interrupted');
      assert.equal(f.stats.searches.filter(q => q.includes('Horizonte')).length, 1);
      await f.page.reload();
      await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
      if (direction === 'youtube-spotify') await f.page.locator('[data-direction="youtube-spotify"]').click();
      await f.page.locator('#playlist-input').fill(f.sourceId);
      await f.start();
      assert.equal(f.stats.searches.filter(q => q.includes('Horizonte')).length, 1);
      assert.equal(f.stats.creates, 1);
      assert.equal(f.remote.size, 2);
      assert.equal((await f.history())[0].status, 'completed');
      assert.match(await f.page.locator('#live-log').innerText(), /Reaproveitando busca salva/);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });
}

test('a saved search can repair a missing target track without spending another search request', async () => {
  const f = await fixture();
  try {
    await f.start();
    f.remote.delete(f.candidates[1].id);
    f.stats.searchFailure = {status: 403, data: {error: 'Cota esgotada', code: 'GOOGLE_403', retryable: false, details: {cause: 'quota', reason: 'quotaExceeded'}}};
    await f.page.reload();
    await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
    await f.page.locator('#playlist-input').fill(f.sourceId);
    await f.start();
    assert.equal(f.stats.searches.length, 2, 'the quota-limited search endpoint should not be called again');
    assert.equal(f.remote.size, 2);
    assert.equal((await f.history())[0].status, 'completed');
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('disconnecting YouTube clears its cached search results', async () => {
  const f = await fixture();
  try {
    await f.start();
    assert.ok(await f.page.evaluate(() => Object.keys(JSON.parse(sessionStorage.getItem('rhyft.web.search-cache.v1'))).length));
    await f.page.locator('#youtube-logout').click();
    await f.page.waitForFunction(() => document.querySelector('#youtube-status').textContent === 'Não conectado');
    assert.deepEqual(await f.page.evaluate(() => JSON.parse(sessionStorage.getItem('rhyft.web.search-cache.v1'))), {});
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('a quota error reports its reason and stops without retrying or skipping the track', async () => {
  const f = await fixture();
  try {
    f.stats.searchFailure = {status: 403, data: {error: 'A cota do YouTube para este projeto foi atingida.', code: 'GOOGLE_403', retryable: false,
      details: {provider: 'youtube', operation: 'search', cause: 'http', reason: 'quotaExceeded', attempts: 1}}};
    await f.start();
    assert.equal((await f.history())[0].status, 'interrupted');
    assert.equal((await f.history())[0].skipped, 0);
    assert.equal(f.stats.searches.filter(q => q.includes('Tempestade')).length, 1);
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /Falha ao buscar a música.*cota/);
    assert.match(log, /HTTP 403.*GOOGLE_403.*quotaExceeded/);
    assert.doesNotMatch(log, /Nova tentativa/);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('a provider explanation accompanying an abort is kept in the visible log', async () => {
  const f = await fixture();
  try {
    f.stats.searchFailure = {status: 409, data: {error: 'O YouTube interrompeu a operação. Detalhe informado: The operation was aborted because the resource changed.', code: 'GOOGLE_409', retryable: true,
      details: {provider: 'youtube', operation: 'search', cause: 'aborted', reason: 'ABORTED', attempts: 2}}};
    await f.start();
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /resource changed/);
    assert.doesNotMatch(log, /não informou o motivo/);
    assert.equal((await f.history())[0].status, 'interrupted');
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('a non-JSON gateway timeout stays in the log with the failing operation', async () => {
  const f = await fixture();
  try {
    f.stats.searchFailure = {html: true};
    await f.start();
    assert.equal((await f.history())[0].status, 'interrupted');
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /Falha ao buscar a música.*resposta válida/);
    assert.match(log, /HTTP 504.*WEB_INVALID_RESPONSE/);
    assert.doesNotMatch(log, /<html>/);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('a browser response-body abort is distinguished from an invalid response or server timeout', async () => {
  const f = await fixture();
  try {
    await f.page.evaluate(() => {
      const realFetch = window.fetch;
      window.fetch = async (url, ...args) => {
        if (String(url).includes('/youtube/search')) return {ok: true, status: 200, json: async () => {throw new DOMException('The operation was aborted.', 'AbortError');}};
        return realFetch(url, ...args);
      };
    });
    await f.start();
    assert.equal((await f.history())[0].status, 'interrupted');
    assert.equal(f.stats.writes.length, 0);
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /Falha ao buscar a música.*comunicação com o servidor foi interrompida/);
    assert.match(log, /HTTP 502.*WEB_ABORTED/);
    assert.doesNotMatch(log, /WEB_INVALID_RESPONSE|WEB_TIMEOUT|The operation was aborted/);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('an unconfirmed write followed by a denied check logs both failures and keeps its checkpoint', async () => {
  const f = await fixture();
  try {
    f.stats.dropWrite = true; f.stats.denyReadAfterWrite = true;
    await f.start();
    const saved = (await f.history())[0];
    assert.equal(saved.status, 'interrupted');
    assert.equal(saved.inFlight.length, 1);
    assert.equal(f.stats.writes.length, 1);
    const log = await f.page.locator('#live-log').innerText();
    assert.match(log, /Falha ao adicionar à playlist.*HTTP 504.*PROVIDER_TIMEOUT/);
    assert.match(log, /Conferindo se o envio chegou/);
    assert.match(log, /Falha ao ler a playlist.*HTTP 403/);
    assert.match(log, /Não foi possível confirmar o envio anterior/);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

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

for (const direction of ['spotify-youtube', 'youtube-spotify']) {
  test(`${direction}: a deleted saved destination gets a replacement without reusing its old progress`, async () => {
    const f = await fixture(direction);
    try {
      await f.start();
      const original = (await f.history())[0];
      const previousWrites = f.stats.writes.length;
      f.stats.missingDestinations.add(original.destinationId);
      await f.page.reload();
      await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
      if (direction === 'youtube-spotify') await f.page.locator('[data-direction="youtube-spotify"]').click();
      await f.page.locator('#playlist-input').fill(f.sourceId);
      // A recovered migration must not require entering the previous playlist name again.
      await f.start();
      const records = await f.history();
      const archived = records.find(record => record.id === original.id);
      const replacement = records.find(record => record.id !== original.id);
      assert.equal(records.length, 2);
      assert.equal(archived.status, 'destination-unavailable');
      assert.equal(archived.destinationId, original.destinationId);
      assert.equal(archived.replacementId, replacement.id);
      assert.deepEqual(archived.processed, original.processed);
      assert.equal(archived.added, original.added);
      assert.equal(replacement.status, 'completed');
      assert.notEqual(replacement.destinationId, original.destinationId);
      assert.ok(replacement.destinationUrl.includes(replacement.destinationId));
      assert.equal(replacement.added, f.candidates.length);
      assert.deepEqual(replacement.inFlight, []);
      assert.deepEqual(new Set(f.stats.writes.slice(previousWrites).flat()), new Set(f.candidates.map(candidate => candidate.id)));
      assert.ok(f.stats.writeDestinations.slice(previousWrites).every(id => id === replacement.destinationId));
      assert.equal(f.stats.creates, 2);
      const accountId = f.stats.accounts[direction === 'spotify-youtube' ? 'youtube' : 'spotify'];
      assert.ok(f.stats.createBodies.every(body => body.expectedAccountId === accountId));
      assert.ok(f.stats.writeBodies.every(body => body.expectedAccountId === accountId));
      assert.equal(await f.page.locator('#progress-count').innerText(), '2 de 2');
      assert.doesNotMatch(await f.page.locator('#progress-title').innerText(), /interrompida/);
      const confirmedWrites = f.stats.writes.length;
      await f.start();
      assert.equal(f.stats.creates, 2, 'automatic lookup must ignore the archived destination');
      assert.equal(f.stats.writes.length, confirmedWrites, 'the new destination must be reused without duplicate tracks');
      assert.equal((await f.history()).length, 2);
      await f.page.locator('#history-toggle').click();
      await f.page.locator('.history-item').filter({hasText: 'Destino indisponível'}).getByRole('button', {name: 'Retomar / atualizar'}).click();
      await f.start();
      assert.equal(f.stats.creates, 2, 'explicitly resuming the archived history must follow its replacement');
      assert.equal(f.stats.writes.length, confirmedWrites);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });

  test(`${direction}: changing the destination account preserves its previous playlist and starts a separate migration`, async () => {
    const f = await fixture(direction);
    try {
      await f.start();
      const original = (await f.history())[0];
      const previousWrites = f.stats.writes.length;
      const destinationProvider = direction === 'spotify-youtube' ? 'youtube' : 'spotify';
      f.stats.accounts[destinationProvider] = 'AnotherAccount456';
      f.stats.missingDestinations.add(original.destinationId);
      const readsBefore = f.stats.readIds.length;
      await f.page.reload();
      await f.page.waitForFunction(() => document.querySelector('#spotify-status').textContent === 'Conectado');
      if (direction === 'youtube-spotify') await f.page.locator('[data-direction="youtube-spotify"]').click();
      await f.page.locator('#playlist-input').fill(f.sourceId);
      await f.page.locator('#playlist-name').fill('Destino da outra conta');
      await f.start();
      const records = await f.history();
      const preserved = records.find(record => record.id === original.id);
      const current = records.find(record => record.id !== original.id);
      assert.equal(records.length, 2);
      assert.equal(preserved.destinationId, original.destinationId);
      assert.deepEqual(preserved.processed, original.processed);
      assert.equal(preserved.added, original.added);
      assert.equal(preserved.status, original.status, 'a different account must not mark the old account playlist as deleted');
      assert.equal(current.status, 'completed');
      assert.notEqual(current.destinationId, original.destinationId);
      assert.equal(f.stats.creates, 2);
      assert.deepEqual(f.stats.readIds.slice(readsBefore), [], 'a destination bound to another account must not be probed or modified');
      assert.deepEqual(new Set(f.stats.writes.slice(previousWrites).flat()), new Set(f.candidates.map(candidate => candidate.id)));
      assert.ok(f.stats.writeDestinations.slice(previousWrites).every(id => id === current.destinationId));
      assert.equal(f.stats.createBodies.at(-1).expectedAccountId, 'AnotherAccount456');
      assert.ok(f.stats.writeBodies.slice(previousWrites).every(body => body.expectedAccountId === 'AnotherAccount456'));
      assert.equal(f.remote.size, 2);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });

  test(`${direction}: an inaccessible legacy destination preserves its pending and unconfirmed checkpoint in the archive`, async () => {
    const source = {id: 'SourceTrack2345678901', name: 'Tempestade', artists: ['Artista'], duration: 180};
    const candidate = {id: 'Music123451', title: 'Tempestade', name: 'Tempestade', artists: ['Artista'], channel: 'Artista', duration: 180,
      uri: 'spotify:track:Music123451', url: 'https://www.youtube.com/watch?v=Music123451'};
    const kind = direction === 'spotify-youtube' ? 'youtube' : 'spotify';
    const savedRecord = {destinationId: 'Destination123456789', added: 1, skipped: 0, status: 'interrupted',
      processed: {SourceTrack1234567890: 'Music123450'}, inFlight: [{source, candidate}], pending: 1,
      pendingItems: [{source, candidates: [candidate], kind, resolved: false}], matcherVersion: 2};
    const f = await fixture(direction, {savedRecord});
    try {
      f.stats.accounts[kind] = 'AnotherAccount456';
      f.stats.missingDestinations.add(savedRecord.destinationId);
      await f.start();
      const records = await f.history();
      const archived = records.find(record => record.id === 'legacy-history');
      const replacement = records.find(record => record.id !== 'legacy-history');
      assert.equal(records.length, 2);
      assert.equal(archived.status, 'destination-unavailable');
      assert.deepEqual(archived.processed, savedRecord.processed);
      assert.deepEqual(archived.inFlight, savedRecord.inFlight);
      assert.deepEqual(archived.pendingItems, savedRecord.pendingItems);
      assert.equal(replacement.status, 'completed');
      assert.equal(replacement.added, 2);
      assert.equal(replacement.pending, 0);
      assert.deepEqual(replacement.inFlight, []);
      assert.deepEqual(new Set(f.stats.writes.flat()), new Set(f.candidates.map(item => item.id)));
      assert.ok(f.stats.writeDestinations.every(id => id === replacement.destinationId));
      assert.equal(f.stats.creates, 1);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });

  test(`${direction}: a readable legacy playlist owned by another account gets a separate destination`, async () => {
    const savedRecord = {destinationId: 'Destination123456789', processed: {SourceTrack1234567890: 'Music123450'}, inFlight: []};
    const f = await fixture(direction, {savedRecord});
    try {
      f.stats.readFailure = {status: 409, data: {error: 'Esta playlist pertence a outra conta.', code: 'DESTINATION_ACCOUNT_MISMATCH'}};
      f.stats.readFailureDestination = savedRecord.destinationId;
      f.stats.replacementRequired = true;
      await f.start();
      const records = await f.history();
      const archived = records.find(record => record.id === 'legacy-history');
      const current = records.find(record => record.id !== 'legacy-history');
      assert.equal(records.length, 2);
      assert.equal(archived.status, 'destination-unavailable');
      assert.deepEqual(archived.processed, savedRecord.processed);
      assert.equal(current.status, 'completed');
      assert.notEqual(current.destinationId, savedRecord.destinationId);
      assert.equal(f.stats.creates, 1);
      assert.ok(f.stats.writeDestinations.every(id => id === current.destinationId));
      const accountId = f.stats.accounts[direction === 'spotify-youtube' ? 'youtube' : 'spotify'];
      assert.equal(f.stats.createBodies[0].expectedAccountId, accountId);
      assert.ok(f.stats.writeBodies.every(body => body.expectedAccountId === accountId));
      assert.equal(f.remote.size, 2);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });

  test(`${direction}: a generic endpoint HTTP 404 does not replace the saved playlist`, async () => {
    const savedRecord = {destinationId: 'Destination123456789', processed: {SourceTrack1234567890: 'Music123450'}, inFlight: []};
    const f = await fixture(direction, {savedRecord});
    try {
      f.stats.readFailure = {status: 404, data: {code: 'NOT_FOUND', error: 'Endpoint não encontrado.'}};
      await f.start();
      const records = await f.history();
      assert.equal(records.length, 1);
      assert.equal(records[0].id, 'legacy-history');
      assert.equal(records[0].destinationId, savedRecord.destinationId);
      assert.deepEqual(records[0].processed, savedRecord.processed);
      assert.equal(records[0].status, 'interrupted');
      assert.equal(f.stats.creates, 0);
      assert.equal(f.stats.writes.length, 0);
      assert.match(await f.page.locator('#live-log').innerText(), /HTTP 404/);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });

  test(`${direction}: an account change during a write interrupts without recreating or losing its checkpoint`, async () => {
    const f = await fixture(direction);
    try {
      f.stats.writeFailure = {status: 409, data: {error: 'A conta conectada mudou. Atualize a página.', code: 'ACCOUNT_CHANGED'}};
      await f.start();
      const records = await f.history();
      assert.equal(records.length, 1);
      assert.equal(records[0].status, 'interrupted');
      assert.equal(records[0].destinationId, f.stats.createIds[0]);
      assert.equal(records[0].added, 0);
      assert.ok(records[0].inFlight.length > 0);
      assert.deepEqual(records[0].processed, {});
      assert.equal(f.stats.creates, 1);
      assert.equal(f.stats.writes.length, 1, 'an account guard must not be retried as a transient write failure');
      assert.equal(f.remote.size, 0);
      const accountId = f.stats.accounts[direction === 'spotify-youtube' ? 'youtube' : 'spotify'];
      assert.equal(f.stats.createBodies[0].expectedAccountId, accountId);
      assert.equal(f.stats.writeBodies[0].expectedAccountId, accountId);
      assert.match(await f.page.locator('#live-log').innerText(), /HTTP 409.*ACCOUNT_CHANGED/);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });

  for (const status of [403, 429, 504]) {
    test(`${direction}: a destination HTTP ${status} error preserves its checkpoint and never creates a replacement`, async () => {
      const savedRecord = {destinationId: 'Destination123456789', added: 1, skipped: 0,
        processed: {SourceTrack1234567890: 'Music123450'}, inFlight: [], pending: 0, pendingItems: []};
      const f = await fixture(direction, {savedRecord});
      try {
        f.stats.readFailure = {status, data: {error: 'A leitura da playlist falhou.', code: `READ_${status}`,
          details: status === 429 ? {cause: 'quota', reason: 'quotaExceeded'} : {cause: 'http'}}};
        await f.start();
        const records = await f.history();
        assert.equal(records.length, 1);
        assert.equal(records[0].id, 'legacy-history');
        assert.equal(records[0].destinationId, savedRecord.destinationId);
        assert.deepEqual(records[0].processed, savedRecord.processed);
        assert.equal(records[0].added, savedRecord.added);
        assert.equal(records[0].status, 'interrupted');
        assert.equal(f.stats.creates, 0);
        assert.equal(f.stats.writes.length, 0);
        assert.match(await f.page.locator('#live-log').innerText(), new RegExp(`HTTP ${status}`));
        assert.equal(await f.page.locator('#progress-count').innerText(), '0 de 2');
        assert.deepEqual(f.errors, []);
      } finally {await f.context.close();}
    });
  }

  test(`${direction}: a user-supplied source playlist HTTP 404 stops without creating a destination`, async () => {
    const f = await fixture(direction);
    try {
      f.stats.sourceFailure = {status: 404, data: {code: 'NOT_FOUND', error: 'Playlist de origem não encontrada ou não acessível.'}};
      await f.start({withoutDestination: true});
      assert.equal(f.stats.creates, 0);
      assert.equal(f.stats.writes.length, 0);
      assert.equal(await f.history(), null);
      assert.match(await f.page.locator('#live-log').innerText(), /HTTP 404.*NOT_FOUND/);
      assert.match(await f.page.locator('#progress-title').innerText(), /interrompida/);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });
}

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


test('rechecks a previously uncertain Last Fall match without another search or manual click', async () => {
  const track = {id: 'SourceTrack1234567890', name: 'Last Fall', artists: ['Lil Peep', 'Lil Tracy', 'Horse Head'], duration: 180};
  const candidate = {id: 'Music123450', title: 'Lil Peep w/ Lil Tracy & Horse Head - Last Fall (Official Audio)', channel: 'Lil Peep', duration: 182};
  const f = await fixture('spotify-youtube', {tracks: [track], candidates: [candidate], legacyPending: true});
  try {
    await f.start();
    assert.equal(f.stats.creates, 0);
    assert.equal(f.stats.searches.length, 0);
    assert.deepEqual(f.stats.writes.flat(), [candidate.id]);
    assert.equal((await f.history())[0].pending, 0);
    assert.deepEqual((await f.history())[0].pendingItems, []);
    assert.equal((await f.history())[0].matcherVersion, 2);
    assert.equal(await f.page.locator('#pending-panel').isHidden(), true);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('uses a targeted second search only when the first result is weak', async () => {
  const f = await fixture();
  try {
    f.stats.weakPrimary = true;
    await f.start();
    assert.deepEqual(f.stats.searches, ['Artista Horizonte', '"Artista" "Horizonte"', 'Artista Tempestade']);
    assert.equal((await f.history())[0].pending, 0);
    assert.equal(f.remote.size, 2);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});


test('Spotify connect opens a tutorial, rejects invalid input and remembers a personal Client ID', async () => {
  const f = await fixture('spotify-youtube', {sharedAvailable: false});
  const clientId = 'a'.repeat(32);
  try {
    await f.page.locator('#spotify-connect').click();
    assert.equal(await f.page.locator('#spotify-setup').isVisible(), true);
    assert.equal(await f.page.locator('#spotify-tutorial').isHidden(), true);
    await f.page.locator('#spotify-have-id').click();
    assert.equal(await f.page.locator('#spotify-shared-option').isHidden(), true);
    await f.page.locator('#spotify-client-id').fill('not-a-client-id');
    await f.page.getByRole('button', {name: 'Conectar ao Spotify'}).click();
    assert.match(await f.page.locator('#spotify-setup-error').innerText(), /32 caracteres/);
    assert.equal(f.stats.loginQueries.length, 0);
    await f.page.locator('#spotify-client-id').fill(clientId);
    await f.page.getByRole('button', {name: 'Conectar ao Spotify'}).click();
    await f.page.waitForURL(/api\/spotify\/start/);
    assert.deepEqual(f.stats.loginQueries, [clientId]);
    await f.page.goto(`${baseUrl}/migrar.html`);
    await f.page.locator('#spotify-connect').click();
    assert.equal(await f.page.locator('#spotify-client-id').inputValue(), clientId);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('the shared Spotify app remains an explicit alternative', async () => {
  const f = await fixture();
  try {
    await f.page.locator('#spotify-connect').click();
    await f.page.locator('#spotify-shared-connect').click();
    await f.page.waitForURL(/api\/spotify\/start/);
    assert.deepEqual(f.stats.loginQueries, [null]);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('the tutorial copies the canonical callback URI from server configuration', async () => {
  const callbackUrl = 'https://rhyft.netlify.app/api/spotify/callback';
  const f = await fixture('spotify-youtube', {callbackUrl});
  try {
    await f.page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: async value => window.copiedRedirect = value}});
    });
    await f.page.locator('#spotify-connect').click();
    await f.page.locator('#spotify-setup-begin').click();
    await f.page.locator('#spotify-created-app').click();
    assert.equal(await f.page.locator('#spotify-redirect').inputValue(), callbackUrl);
    await f.page.locator('#spotify-copy-redirect').click();
    await f.page.getByRole('button', {name: 'Copiado', exact: true}).waitFor();
    assert.equal(await f.page.evaluate(() => window.copiedRedirect), callbackUrl);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});

test('a development-mode rejection opens actionable Spotify setup', async () => {
  const f = await fixture();
  try {
    f.stats.forbiddenSource = true;
    await f.page.locator('#start-migration').click();
    await f.page.locator('#spotify-setup').waitFor({state: 'visible'});
    assert.match(await f.page.locator('#spotify-setup-error').innerText(), /Spotify ainda não autorizou/);
    await f.page.locator('#spotify-access-help summary').click();
    assert.match(await f.page.locator('#spotify-access-help').innerText(), /User Management/);
    assert.equal(f.stats.creates, 0);
    assert.deepEqual(f.errors, []);
  } finally {await f.context.close();}
});


for (const viewport of [{name: 'desktop', width: 1280, height: 720}, {name: 'mobile', width: 390, height: 844}]) {
  test(`Spotify connection stays compact and styled on ${viewport.name}, with one tutorial step at a time`, async () => {
    const f = await fixture();
    try {
      await f.page.setViewportSize({width: viewport.width, height: viewport.height});
      await f.page.locator('#spotify-connect').click();
      const box = await f.page.locator('#spotify-setup').boundingBox();
      assert.ok(box.width <= 440 && box.width < viewport.width);
      assert.ok(box.height < 440 && box.y >= 0 && box.y + box.height <= viewport.height);
      const style = await f.page.locator('#spotify-setup').evaluate(element => ({
        background: getComputedStyle(element).backgroundColor,
        radius: parseFloat(getComputedStyle(element).borderRadius),
        overflow: element.scrollWidth > element.clientWidth
      }));
      assert.equal(style.background, 'rgb(17, 21, 28)');
      assert.ok(style.radius >= 18);
      assert.equal(style.overflow, false);
      assert.equal(await f.page.locator('#spotify-personal-form').isHidden(), true);
      if (process.env.RHYFT_SCREENSHOTS) {
        fs.mkdirSync(process.env.RHYFT_SCREENSHOTS, {recursive: true});
        await f.page.screenshot({path: path.join(process.env.RHYFT_SCREENSHOTS, `spotify-${viewport.name}-welcome.png`)});
      }
      await f.page.locator('#spotify-setup-begin').click();
      assert.equal(await f.page.locator('#spotify-step-create').isVisible(), true);
      assert.equal(await f.page.locator('#spotify-step-redirect').isHidden(), true);
      assert.equal(await f.page.locator('#spotify-step-client').isHidden(), true);
      await f.page.locator('#spotify-created-app').click();
      assert.equal(await f.page.locator('#spotify-step-create').isHidden(), true);
      assert.equal(await f.page.locator('#spotify-step-label').innerText(), 'Passo 2 de 3');
      await f.page.locator('#spotify-saved-redirect').click();
      assert.equal(await f.page.locator('#spotify-step-client').isVisible(), true);
      assert.equal(await f.page.locator('#spotify-step-redirect').isHidden(), true);
      assert.equal(await f.page.locator('#spotify-step-label').innerText(), 'Passo 3 de 3');
      await f.page.locator('#spotify-step-back').click();
      assert.equal(await f.page.locator('#spotify-step-redirect').isVisible(), true);
      await f.page.locator('#spotify-setup-close').click();
      assert.equal(await f.page.locator('#spotify-setup').isVisible(), false);
      assert.deepEqual(f.errors, []);
    } finally {await f.context.close();}
  });
}
