(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const store = window.RhyftMigrationStore;

  const ui = {
    spotifyStatus: $('#spotify-status'), spotifyDetail: $('#spotify-detail'), spotifyConnect: $('#spotify-connect'), spotifyLogout: $('#spotify-logout'),
    youtubeStatus: $('#youtube-status'), youtubeDetail: $('#youtube-detail'), youtubeConnect: $('#youtube-connect'), youtubeLogout: $('#youtube-logout'),
    directions: $$('.direction'), label: $('#playlist-label'), input: $('#playlist-input'), name: $('#playlist-name'),
    start: $('#start-migration'), pause: $('#pause-migration'), cancel: $('#cancel-migration'),
    progressPanel: $('#progress-panel'), progressTitle: $('#progress-title'), progressPercent: $('#progress-percent'), progressBar: $('#progress-bar'), progressCount: $('#progress-count'), progressCurrent: $('#progress-current'), log: $('#live-log'),
    pendingPanel: $('#pending-panel'), pendingList: $('#pending-list'), resultPanel: $('#result-panel'), resultTitle: $('#result-title'), resultSummary: $('#result-summary'), resultLink: $('#result-link'), toast: $('#toast')
  };

  const state = {
    direction: 'spotify-youtube', session: null, running: false, paused: false, cancelled: false,
    destination: null, added: 0, skipped: 0, pending: [], usedIds: new Set(),
    record: null, selectedRecord: null, processed: {}, inFlight: [], sourceInput: '', reviewing: false
  };

  class Cancelled extends Error {}

  function showToast(message, error = false) {
    ui.toast.textContent = message;
    ui.toast.className = `toast show${error ? ' error' : ''}`;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => ui.toast.className = 'toast', 5000);
  }

  async function api(path, options = {}) {
    const init = { method: options.method || 'GET', credentials: 'same-origin', headers: {} };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    const attempts = init.method === 'GET' ? 2 : 1;
    for (let attempt = 0; attempt < attempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30000);
      try {
        const response = await fetch(`/api${path}`, { ...init, signal: controller.signal });
        let data;
        try { data = await response.json(); } catch {
          const err = new Error('O servidor não retornou uma resposta válida. Retome a migração para tentar novamente.');
          err.status = response.ok ? 502 : response.status;
          throw err;
        }
        if (!response.ok) {
          const err = new Error(data?.error || `Erro HTTP ${response.status}`);
          err.code = data?.code;
          err.status = response.status;
          throw err;
        }
        return data;
      } catch (error) {
        if (!error.status) {
          error = new Error(error.name === 'AbortError'
            ? 'A requisição demorou para responder. O progresso foi preservado; retome para tentar novamente.'
            : 'A conexão falhou temporariamente. O progresso foi preservado; retome para tentar novamente.');
          error.status = 502;
        }
        if (!isTemporary(error) || attempt + 1 === attempts) throw error;
      } finally { clearTimeout(timer); }
      await sleep(500);
      if (state.running) await checkpoint();
    }
  }

  function isTemporary(error) {
    return [500, 502, 503, 504].includes(error.status);
  }

  function connectedCard(provider, info) {
    const isSpotify = provider === 'spotify';
    const status = isSpotify ? ui.spotifyStatus : ui.youtubeStatus;
    const detail = isSpotify ? ui.spotifyDetail : ui.youtubeDetail;
    const connect = isSpotify ? ui.spotifyConnect : ui.youtubeConnect;
    const logout = isSpotify ? ui.spotifyLogout : ui.youtubeLogout;
    if (info?.connected) {
      status.textContent = 'Conectado';
      status.style.color = 'var(--green)';
      detail.textContent = info.name || 'Conta autorizada';
      connect.textContent = 'Reconectar';
      logout.hidden = false;
    } else {
      status.textContent = 'Não conectado';
      status.style.color = '';
      detail.textContent = 'Conecte para migrar';
      connect.textContent = 'Conectar';
      logout.hidden = true;
    }
  }

  async function loadSession() {
    try {
      const data = await api('/session');
      state.session = data;
      connectedCard('spotify', data.spotify);
      connectedCard('youtube', data.youtube);
      if (data.setup && !data.setup.ready) {
        showToast(`Configuração do site incompleta: ${data.setup.missing.join(', ')}`, true);
      }
    } catch (error) {
      connectedCard('spotify', null); connectedCard('youtube', null);
      showToast(error.message, true);
    }
  }

  function handleAuthQuery() {
    const p = new URLSearchParams(location.search);
    if (p.get('auth') === 'spotify-ok') showToast('Spotify conectado com sucesso.');
    if (p.get('auth') === 'google-ok') showToast('Google / YouTube conectado com sucesso.');
    if (p.get('auth_error')) showToast(p.get('auth_error'), true);
    if ([...p.keys()].some(k => k.startsWith('auth'))) history.replaceState({}, '', location.pathname);
  }

  async function logout(provider) {
    try {
      await api('/logout', { method: 'POST', body: { provider } });
      showToast(`${provider === 'spotify' ? 'Spotify' : 'Google / YouTube'} desconectado.`);
      await loadSession();
    } catch (error) { showToast(error.message, true); }
  }

  ui.spotifyLogout.addEventListener('click', () => logout('spotify'));
  ui.youtubeLogout.addEventListener('click', () => logout('google'));

  function setDirection(direction) {
    if (state.running || state.reviewing) return;
    state.direction = direction;
    ui.directions.forEach(btn => {
      const active = btn.dataset.direction === direction;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', String(active));
    });
    if (direction === 'spotify-youtube') {
      ui.label.textContent = 'Link ou ID da playlist do Spotify';
      ui.input.placeholder = 'https://open.spotify.com/playlist/…';
    } else {
      ui.label.textContent = 'Link ou ID da playlist do YouTube Music';
      ui.input.placeholder = 'https://music.youtube.com/playlist?list=…';
    }
  }
  ui.directions.forEach(btn => btn.addEventListener('click', () => setDirection(btn.dataset.direction)));

  function log(message, type = '') {
    const line = document.createElement('div');
    line.className = `log-line ${type}`.trim();
    line.textContent = message;
    ui.log.appendChild(line);
    ui.log.scrollTop = ui.log.scrollHeight;
  }

  function progress(done, total, current = '') {
    const pct = total ? Math.round((done / total) * 100) : 0;
    ui.progressPercent.textContent = `${pct}%`;
    ui.progressBar.style.width = `${pct}%`;
    ui.progressCount.textContent = `${done} de ${total}`;
    ui.progressCurrent.textContent = current || '—';
  }

  async function checkpoint() {
    if (state.cancelled) throw new Cancelled('Migração cancelada.');
    while (state.paused) {
      await sleep(200);
      if (state.cancelled) throw new Cancelled('Migração cancelada.');
    }
  }

  function resetRun() {
    state.destination = null; state.added = 0; state.skipped = 0; state.pending = []; state.usedIds = new Set();
    state.record = null; state.processed = {}; state.inFlight = [];
    ui.log.textContent = ''; ui.pendingList.textContent = ''; ui.pendingPanel.hidden = true; ui.resultPanel.hidden = true;
    ui.progressPanel.hidden = false; ui.progressTitle.textContent = 'Preparando migração…'; progress(0, 0);
  }

  function saveProgress(status = state.record?.status || 'running') {
    if (!state.destination || !state.record) return;
    Object.assign(state.record, {
      updatedAt: new Date().toISOString(), destinationId: state.destination.id,
      destinationName: state.destination.name, destinationUrl: state.destination.url,
      status, added: state.added, skipped: state.skipped,
      pending: state.pending.filter(p => !p.resolved).length,
      pendingItems: state.pending.filter(p => !p.resolved), processed: state.processed,
      inFlight: state.inFlight
    });
    try { store.save(state.record); } catch {
      throw new Error('Não foi possível salvar o progresso neste navegador. Libere espaço no armazenamento antes de continuar.');
    }
  }

  function sourceKey(track) { return track.id || queryFor(track); }

  async function destinationIds(kind) {
    const ids = new Set(), pages = new Set();
    let page = null;
    do {
      const suffix = page === null ? '' : `&page=${encodeURIComponent(page)}`;
      const data = await api(`/${kind}/playlist/state?input=${encodeURIComponent(state.destination.id)}${suffix}`);
      for (const id of data.ids || []) ids.add(id);
      if (page === null) state.destination.name = data.name || state.destination.name;
      page = data.nextPage ?? null;
      if (page !== null && pages.has(page)) throw new Error('Não foi possível conferir todas as faixas da playlist de destino.');
      pages.add(page);
    } while (page !== null);
    return ids;
  }

  async function prepareDestination(kind, source, name) {
    const previous = (state.selectedRecord && store.load().find(record => record.id === state.selectedRecord.id)) || store.find(state.direction, state.sourceInput);
    state.selectedRecord = null;
    if (previous) {
      state.record = { ...previous };
      const id = store.playlistId(state.direction, previous.destinationId || previous.destinationUrl, true);
      if (!id) throw new Error('O histórico não possui um destino válido para retomar.');
      state.destination = { id, url: previous.destinationUrl, name: previous.destinationName || name };
      // Preserve the checkpoint even if the destination cannot currently be read.
      state.processed = { ...previous.processed };
      state.inFlight = previous.inFlight || [];
      state.pending = previous.pendingItems || [];
      state.added = previous.added || 0;
      state.usedIds = await destinationIds(kind);
      state.added = state.usedIds.size;
      state.processed = { ...previous.processed };
      for (const [key, value] of Object.entries(state.processed)) {
        if (value !== 'ignored' && !state.usedIds.has(value)) delete state.processed[key];
      }
      // A previous response can have been lost after the platform accepted the write.
      for (const entry of previous.inFlight || []) {
        if (state.usedIds.has(entry.candidate.id)) state.processed[sourceKey(entry.source)] = entry.candidate.id;
      }
      state.inFlight = [];
      state.pending = (previous.pendingItems || []).filter(item => !state.processed[sourceKey(item.source)]);
      log(`♻ Playlist “${state.destination.name}” recuperada do histórico; ${state.usedIds.size} faixa(s) já presentes.`, 'ok');
    } else {
      // Check storage before creating anything remotely.
      try { localStorage.setItem('rhyft.web.storage-check', '1'); localStorage.removeItem('rhyft.web.storage-check'); }
      catch { throw new Error('Habilite o armazenamento deste navegador para salvar e retomar a migração.'); }
      await checkpoint();
      const dest = await api(`/${kind}/playlist`, { method: 'POST', body: { name } });
      state.destination = { id: dest.id, url: dest.url, name: dest.name || name };
      state.record = {
        id: crypto.randomUUID(), createdAt: new Date().toISOString(),
        direction: state.direction, sourceInput: state.sourceInput, sourceId: source.id
      };
      log(`✓ Playlist “${name}” criada no ${kind === 'youtube' ? 'YouTube' : 'Spotify'}`, 'ok');
    }
    saveProgress('running');
    return state.destination;
  }

  async function addEntries(kind, entries) {
    if (state.inFlight.length) state.usedIds = await destinationIds(kind);
    const fresh = entries.filter(entry => !state.usedIds.has(entry.candidate.id));
    if (fresh.length) {
      // Persist the intended write before sending it so a reload can reconcile it.
      state.inFlight = fresh;
      saveProgress();
      try {
        if (kind === 'youtube') {
          await api('/youtube/playlist/item', { method: 'POST', body: { playlistId: state.destination.id, videoId: fresh[0].candidate.id } });
        } else {
          await api('/spotify/playlist/items', { method: 'POST', body: { playlistId: state.destination.id, uris: fresh.map(entry => entry.candidate.uri) } });
        }
        fresh.forEach(entry => state.usedIds.add(entry.candidate.id));
      } catch (error) {
        if (!isTemporary(error)) throw error;
        // Never repeat a POST blindly: it may have succeeded despite its error.
        await sleep(500);
        state.usedIds = await destinationIds(kind);
        for (const entry of fresh) {
          if (state.usedIds.has(entry.candidate.id)) state.processed[sourceKey(entry.source)] = entry.candidate.id;
        }
        state.added = state.usedIds.size;
        state.inFlight = fresh.filter(entry => !state.usedIds.has(entry.candidate.id));
        saveProgress();
        if (state.inFlight.length) throw new Error(`${error.message} Não foi possível confirmar o envio de todas as faixas. Retome pela mesma origem ou pelo histórico.`);
        log('✓ Envio confirmado na playlist após uma falha de resposta.', 'ok');
      }
    }
    entries.forEach(entry => state.processed[sourceKey(entry.source)] = entry.candidate.id);
    state.added = state.usedIds.size;
    state.inFlight = [];
    saveProgress();
  }

  document.addEventListener('rhyft:resume', event => {
    if (state.running) return;
    state.selectedRecord = event.detail;
    showToast('Ao iniciar, a migração continuará na playlist do histórico.');
  });
  ui.input.addEventListener('input', () => state.selectedRecord = null);
  ui.directions.forEach(button => button.addEventListener('click', () => {
    if (!state.running) state.selectedRecord = null;
  }));

  function normalize(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/\([^)]*\)|\[[^\]]*\]/g, ' ')
      .replace(/\b(official|oficial|video|audio|lyrics|lyric|visualizer|hd|4k)\b/g, ' ')
      .replace(/\b(feat|ft|featuring)\.?\s+.*$/g, ' ')
      .replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function levenshtein(a, b) {
    a = normalize(a); b = normalize(b);
    if (!a) return b.length; if (!b) return a.length;
    const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      let left = i, diag = i - 1;
      for (let j = 1; j <= b.length; j++) {
        const up = prev[j];
        const cur = a[i - 1] === b[j - 1] ? diag : Math.min(diag, up, left) + 1;
        prev[j] = cur; diag = up; left = cur;
      }
    }
    return prev[b.length];
  }

  function textScore(a, b) {
    const na = normalize(a), nb = normalize(b);
    if (!na || !nb) return 0;
    const lev = 1 - levenshtein(na, nb) / Math.max(na.length, nb.length, 1);
    const aa = new Set(na.split(' ')), bb = new Set(nb.split(' '));
    const common = [...aa].filter(x => bb.has(x)).length;
    const token = common / Math.max(aa.size, bb.size, 1);
    return Math.max(0, Math.min(1, lev * .58 + token * .42));
  }

  function durationScore(a, b) {
    if (!a || !b) return .55;
    const diff = Math.abs(Number(a) - Number(b));
    return Math.max(0, 1 - diff / 28);
  }

  function rankYoutube(track, candidates) {
    const artist = (track.artists || [])[0] || track.artist || '';
    return candidates.map(c => {
      const channel = String(c.channel || '').replace(/\s*-\s*Topic$/i, '').replace(/VEVO$/i, '');
      const score = textScore(track.name, c.title) * .64 + textScore(artist, channel) * .21 + durationScore(track.duration, c.duration) * .15;
      return { ...c, score };
    }).sort((a, b) => b.score - a.score);
  }

  function rankSpotify(track, candidates) {
    const artist = (track.artists || [])[0] || track.artist || '';
    return candidates.map(c => {
      const candArtist = (c.artists || [])[0] || '';
      const score = textScore(track.name, c.name) * .56 + textScore(artist, candArtist) * .29 + durationScore(track.duration, c.duration) * .15;
      return { ...c, score };
    }).sort((a, b) => b.score - a.score);
  }

  function queryFor(track) {
    const artist = (track.artists || [])[0] || track.artist || '';
    return `${track.name || ''} ${artist}`.trim().slice(0, 180);
  }

  function addPending(source, candidates, kind) {
    if (state.pending.some(item => !item.resolved && sourceKey(item.source) === sourceKey(source))) return;
    state.pending.push({ source, candidates: candidates.slice(0, 4), kind, resolved: false });
    saveProgress();
  }

  async function spotifyToYoutube(sourceInput, name) {
    ui.progressTitle.textContent = 'Lendo playlist do Spotify…';
    const source = await api(`/spotify/playlist?input=${encodeURIComponent(sourceInput)}`);
    if (!source.tracks.length) throw new Error('A playlist do Spotify não possui faixas disponíveis para migrar.');
    log(`✓ ${source.tracks.length} faixas carregadas do Spotify`, 'ok');
    if (source.truncated) log('⚠ Playlist muito grande: esta sessão processará apenas as primeiras 2.000 faixas.', 'warn');

    await prepareDestination('youtube', source, name);
    ui.progressTitle.textContent = 'Encontrando músicas no YouTube…';

    const total = source.tracks.length;
    for (let i = 0; i < total; i++) {
      await checkpoint();
      const track = source.tracks[i];
      if (state.processed[sourceKey(track)] || state.pending.some(p => !p.resolved && sourceKey(p.source) === sourceKey(track))) {
        progress(i + 1, total, track.name);
        continue;
      }
      progress(i, total, `${track.name} — ${(track.artists || []).join(', ')}`);
      log(`[${i + 1}/${total}] Procurando: ${track.name}`);
      try {
        const found = await api(`/youtube/search?q=${encodeURIComponent(queryFor(track))}`);
        const ranked = rankYoutube(track, found.items || []);
        const best = ranked[0];
        if (best && best.score >= .61) {
          await checkpoint();
          await addEntries('youtube', [{ source: track, candidate: best }]);
          log(`  ✓ ${best.title}`, 'ok');
        } else if (ranked.length) {
          addPending(track, ranked, 'youtube');
          log('  ? Correspondência incerta — deixei para sua escolha.', 'warn');
        } else {
          state.skipped++; log('  ! Nenhum resultado útil encontrado.', 'warn');
        }
      } catch (error) {
        if (error instanceof Cancelled || isTemporary(error) || state.inFlight.length || error.status === 401 || error.status === 429 || /quota|cota|limit|salvar o progresso/i.test(error.message)) throw error;
        state.skipped++; log(`  ✕ ${error.message}`, 'error');
        saveProgress();
      }
      progress(i + 1, total, track.name);
      await sleep(120);
    }
  }

  async function youtubeToSpotify(sourceInput, name) {
    ui.progressTitle.textContent = 'Lendo playlist do YouTube…';
    const source = await api(`/youtube/playlist?input=${encodeURIComponent(sourceInput)}`);
    if (!source.tracks.length) throw new Error('A playlist do YouTube não possui vídeos disponíveis para migrar.');
    log(`✓ ${source.tracks.length} itens carregados do YouTube`, 'ok');
    if (source.truncated) log('⚠ Playlist muito grande: esta sessão processará apenas as primeiras 2.000 faixas.', 'warn');

    await prepareDestination('spotify', source, name);
    ui.progressTitle.textContent = 'Encontrando músicas no Spotify…';

    const total = source.tracks.length;
    let batch = [];
    const queuedIds = new Set();
    const flush = async () => {
      if (!batch.length) return;
      await checkpoint();
      await addEntries('spotify', batch);
      batch = [];
      queuedIds.clear();
    };

    for (let i = 0; i < total; i++) {
      await checkpoint();
      const track = source.tracks[i];
      if (state.processed[sourceKey(track)] || state.pending.some(p => !p.resolved && sourceKey(p.source) === sourceKey(track))) {
        progress(i + 1, total, track.name);
        continue;
      }
      progress(i, total, track.name);
      log(`[${i + 1}/${total}] Procurando: ${track.name}`);
      try {
        const found = await api(`/spotify/search?q=${encodeURIComponent(queryFor(track))}`);
        const ranked = rankSpotify(track, found.items || []);
        const best = ranked[0];
        if (best && best.score >= .63) {
          if (state.usedIds.has(best.id)) {
            state.processed[sourceKey(track)] = best.id;
            saveProgress();
            log(`  ✓ Já presente: ${best.name}`, 'ok');
          } else if (!queuedIds.has(best.id)) {
            batch.push({ source: track, candidate: best }); queuedIds.add(best.id);
            log(`  • Na fila: ${best.name} — ${(best.artists || []).join(', ')}`);
          }
          if (batch.length >= 50) await flush();
        } else if (ranked.length) {
          addPending(track, ranked, 'spotify');
          log('  ? Correspondência incerta — deixei para sua escolha.', 'warn');
        } else {
          state.skipped++; log('  ! Nenhum resultado útil encontrado.', 'warn');
        }
      } catch (error) {
        if (error instanceof Cancelled || isTemporary(error) || state.inFlight.length || error.status === 401 || error.status === 429 || /salvar o progresso/i.test(error.message)) throw error;
        state.skipped++; log(`  ✕ ${error.message}`, 'error');
        saveProgress();
      }
      progress(i + 1, total, track.name);
      await sleep(120);
    }
    await flush();
  }

  function updateResult() {
    if (!state.destination) return;
    const unresolved = state.pending.filter(p => !p.resolved).length;
    ui.resultPanel.hidden = false;
    ui.resultTitle.textContent = state.destination.name || 'Playlist criada';
    ui.resultSummary.textContent = `${state.added} adicionada(s) · ${unresolved} aguardando sua escolha · ${state.skipped} ignorada(s)/com erro.`;
    ui.resultLink.href = state.destination.url;
  }

  async function resolvePending(item, candidate, card) {
    if (item.resolved || state.running || state.reviewing) return;
    state.reviewing = true; ui.start.disabled = true;
    const buttons = $$('button', card); buttons.forEach(b => b.disabled = true);
    try {
      await addEntries(item.kind, [{ source: item.source, candidate }]);
      item.resolved = true;
      saveProgress();
      card.classList.add('resolved');
      card.querySelector('.candidate-list').textContent = '';
      const done = document.createElement('div'); done.className = 'log-line ok'; done.textContent = `✓ Adicionada: ${candidate.title || candidate.name}`;
      card.appendChild(done); updateResult();
    } catch (error) {
      buttons.forEach(b => b.disabled = false); showToast(error.message, true);
    } finally {
      state.reviewing = false; ui.start.disabled = false;
    }
  }

  function renderPending() {
    const active = state.pending.filter(p => !p.resolved);
    ui.pendingPanel.hidden = active.length === 0;
    ui.pendingList.textContent = '';
    active.forEach(item => {
      const card = document.createElement('article'); card.className = 'pending-item';
      const source = document.createElement('div'); source.className = 'pending-source'; source.textContent = `${item.source.name} — ${(item.source.artists || []).join(', ')}`;
      card.appendChild(source);
      const list = document.createElement('div'); list.className = 'candidate-list';
      item.candidates.forEach(candidate => {
        const row = document.createElement('div'); row.className = 'candidate';
        const meta = document.createElement('div');
        const strong = document.createElement('strong'); strong.textContent = candidate.title || candidate.name;
        const span = document.createElement('span');
        span.textContent = `${candidate.channel || (candidate.artists || []).join(', ')} · confiança ${Math.round(candidate.score * 100)}%`;
        meta.append(strong, span);
        const choose = document.createElement('button'); choose.type = 'button'; choose.textContent = 'Escolher';
        choose.addEventListener('click', () => resolvePending(item, candidate, card));
        row.append(meta, choose); list.appendChild(row);
      });
      card.appendChild(list);
      const ignore = document.createElement('button'); ignore.className = 'pending-ignore'; ignore.type = 'button'; ignore.textContent = 'Ignorar esta faixa';
      ignore.addEventListener('click', () => {
        if (state.reviewing || state.running) return;
        item.resolved = true; state.skipped++; state.processed[sourceKey(item.source)] = 'ignored';
        try { saveProgress(); } catch (error) { showToast(error.message, true); }
        card.remove(); renderPending(); updateResult();
      });
      card.appendChild(ignore); ui.pendingList.appendChild(card);
    });
  }

  async function startMigration() {
    if (state.running || state.reviewing) return;
    const sourceInput = ui.input.value.trim();
    const name = ui.name.value.trim();
    if (!sourceInput) return showToast('Cole o link ou ID da playlist.', true);
    if (!name && !store.find(state.direction, sourceInput)) return showToast('Digite o nome da playlist de destino.', true);
    if (!state.session?.spotify?.connected || !state.session?.youtube?.connected) return showToast('Conecte Spotify e Google / YouTube antes de iniciar.', true);

    state.sourceInput = sourceInput;
    state.running = true; state.paused = false; state.cancelled = false; resetRun();
    ui.start.disabled = true; ui.pause.disabled = false; ui.cancel.disabled = false; ui.directions.forEach(x => x.disabled = true);
    try {
      if (state.direction === 'spotify-youtube') await spotifyToYoutube(sourceInput, name);
      else await youtubeToSpotify(sourceInput, name);
      await checkpoint();
      ui.progressTitle.textContent = state.skipped ? 'Migração processada com falhas' : 'Migração processada';
      ui.progressCurrent.textContent = state.skipped ? 'Retome para tentar as faixas com erro' : 'Concluído';
      saveProgress(state.skipped ? 'interrupted' : 'completed');
      renderPending(); updateResult();
      log(`✓ Processo finalizado: ${state.added} item(ns) adicionado(s).`, 'ok');
      if (state.pending.length) log(`? ${state.pending.length} item(ns) aguardam sua escolha.`, 'warn');
    } catch (error) {
      if (error instanceof Cancelled) {
        ui.progressTitle.textContent = 'Migração cancelada'; log('Migração cancelada. A playlist já criada não foi apagada.', 'warn');
      } else {
        ui.progressTitle.textContent = 'Migração interrompida'; log(`✕ ${error.message}`, 'error'); showToast(error.message, true);
      }
      if (state.destination) {
        try { saveProgress(error instanceof Cancelled ? 'cancelled' : 'interrupted'); }
        catch (storageError) { log(storageError.message, 'error'); }
        renderPending(); updateResult();
      }
    } finally {
      state.running = false; state.paused = false;
      ui.start.disabled = false; ui.pause.disabled = true; ui.pause.textContent = 'Ⅱ Pausar'; ui.cancel.disabled = true; ui.directions.forEach(x => x.disabled = false);
    }
  }

  ui.start.addEventListener('click', startMigration);
  ui.pause.addEventListener('click', () => {
    if (!state.running) return;
    state.paused = !state.paused;
    ui.pause.textContent = state.paused ? '▶ Continuar' : 'Ⅱ Pausar';
    log(state.paused ? 'Migração pausada.' : 'Migração retomada.', 'warn');
  });
  ui.cancel.addEventListener('click', () => { if (state.running) state.cancelled = true; });

  handleAuthQuery();
  loadSession();
})();
