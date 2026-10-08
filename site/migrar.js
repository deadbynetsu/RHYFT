(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const store = window.RhyftMigrationStore;
  const matching = window.RhyftMusicMatching;
  let pacingStorage;
  try { pacingStorage = localStorage; } catch {}
  const pacing = window.RhyftProviderPacing.create({storage: pacingStorage});
  let searchStorage;
  try { searchStorage = sessionStorage; } catch {}
  const searchCache = window.RhyftSearchCache.create({storage: searchStorage});

  const ui = {
    spotifyStatus: $('#spotify-status'), spotifyDetail: $('#spotify-detail'), spotifyConnect: $('#spotify-connect'), spotifyLogout: $('#spotify-logout'),
    youtubeStatus: $('#youtube-status'), youtubeDetail: $('#youtube-detail'), youtubeConnect: $('#youtube-connect'), youtubeLogout: $('#youtube-logout'),
    directions: $$('.direction'), label: $('#playlist-label'), input: $('#playlist-input'), name: $('#playlist-name'),
    start: $('#start-migration'), pause: $('#pause-migration'), cancel: $('#cancel-migration'),
    progressPanel: $('#progress-panel'), progressTitle: $('#progress-title'), progressPercent: $('#progress-percent'), progressBar: $('#progress-bar'), progressCount: $('#progress-count'), progressCurrent: $('#progress-current'), log: $('#live-log'),
    pendingPanel: $('#pending-panel'), pendingTitle: $('#pending-title'), pendingList: $('#pending-list'), resultPanel: $('#result-panel'), resultTitle: $('#result-title'), resultSummary: $('#result-summary'), resultLink: $('#result-link'), toast: $('#toast')
  };

  const state = {
    direction: 'spotify-youtube', session: null, running: false, paused: false, cancelled: false,
    destination: null, added: 0, skipped: 0, pending: [], usedIds: new Set(),
    record: null, selectedRecord: null, processed: {}, inFlight: [], sourceInput: '', reviewing: false,
    failedSources: new Set(), sourceKeys: [], progressDone: 0
  };

  class Cancelled extends Error {}

  function showToast(message, error = false) {
    ui.toast.textContent = message;
    ui.toast.className = `toast show${error ? ' error' : ''}`;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => ui.toast.className = 'toast', 5000);
  }

  const operations = {
    search: 'buscar a música', 'track-details': 'consultar os detalhes das músicas',
    'playlist-read': 'ler a playlist', 'playlist-create': 'criar a playlist',
    'playlist-add': 'adicionar à playlist', auth: 'renovar a sessão',
    account: 'verificar a conta', session: 'verificar as conexões', request: 'consultar a plataforma'
  };

  function requestContext(path, method) {
    const route = path.split('?')[0];
    const provider = route === '/youtube/music/search' ? 'youtube-music'
      : route.startsWith('/youtube/') ? 'youtube' : route.startsWith('/spotify/') ? 'spotify' : 'site';
    const operation = route === '/session' ? 'session' : route.endsWith('/search') ? 'search'
      : /\/playlist\/items?$/.test(route) ? 'playlist-add'
      : route.endsWith('/playlist/state') || method === 'GET' && route.endsWith('/playlist') ? 'playlist-read'
      : route.endsWith('/playlist') ? 'playlist-create' : 'request';
    return {provider, operation};
  }

  function describeFailure(error, technical = true) {
    if (!error.context) return error.message;
    const details = error.details || {};
    const provider = {youtube: 'YouTube / Google', 'youtube-music': 'YouTube Music (busca pública)', spotify: 'Spotify', site: 'RHYFT'}[details.provider || error.context.provider] || 'RHYFT';
    const operation = operations[details.operation] || operations[error.context.operation] || operations.request;
    let message = `Falha ao ${operation} (${provider}): ${error.message}`;
    if (technical) {
      const codes = [`HTTP ${error.status}`, error.code];
      if (details.reason) codes.push(`motivo: ${details.reason}`);
      if (details.limitScope) codes.push(`limite: ${ {day: 'por dia', minute: 'por minuto', second: 'por segundo'}[details.limitScope] || details.limitScope}`);
      if (details.limitName) codes.push(`cota: ${details.limitName}`);
      const stage = {'catalog-init': 'inicialização do catálogo', 'catalog-search': 'resultados da busca'}[details.stage];
      if (stage) codes.push(`etapa: ${stage}`);
      if (['GET', 'POST'].includes(details.method)) codes.push(`método: ${details.method}`);
      const structuralNames = new Set(['html', 'json', 'text', 'ytcfg.set', 'ytcfg.data_', 'VISITOR_DATA',
        'INNERTUBE_CONTEXT', 'INNERTUBE_CLIENT_VERSION', 'INNERTUBE_CONTEXT_CLIENT_VERSION',
        'contents', 'responseContext', 'tabbedSearchResultsRenderer', 'singleColumnBrowseResultsRenderer',
        'twoColumnSearchResultsRenderer', 'twoColumnBrowseResultsRenderer', 'sectionListRenderer',
        'musicShelfRenderer', 'musicCardShelfRenderer', 'itemSectionRenderer', 'musicResponsiveListItemRenderer',
        'musicTwoRowItemRenderer', 'musicMultiRowListItemRenderer', 'playlistItemData', 'continuationContents',
        'musicShelfContinuation', 'messageRenderer', 'error']);
      const shape = Array.isArray(details.responseShape) ? details.responseShape.filter(name => structuralNames.has(name)).slice(0, 12) : [];
      if (shape.length) codes.push(`estrutura: ${shape.join(', ')}`);
      if (details.upstreamStatus && details.upstreamStatus !== error.status) codes.push(`plataforma: HTTP ${details.upstreamStatus}`);
      if (details.timeoutMs) codes.push(`prazo: ${details.timeoutMs / 1000}s`);
      if (details.attempts) codes.push(`tentativas no servidor: ${details.attempts}`);
      if (error.attempts) codes.push(`tentativas no navegador: ${error.attempts}`);
      message += ` [${codes.filter(Boolean).join(' · ')}]`;
    }
    return message;
  }

  async function api(path, options = {}) {
    const init = { method: (options.method || 'GET').toUpperCase(), credentials: 'same-origin', cache: 'no-store', headers: {} };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    const context = requestContext(path, init.method);
    let attempts = init.method === 'GET' ? context.operation === 'search' ? 3 : 2 : 1;
    for (let attempt = 0; attempt < attempts; attempt++) {
      if (state.running) await checkpoint();
      await reserveProvider(context.provider);
      let retryDelay = 500;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30000);
      try {
        const response = await fetch(`/api${path}`, { ...init, signal: controller.signal });
        let data;
        try { data = await response.json(); } catch (error) {
          if (controller.signal.aborted || ['AbortError', 'TimeoutError'].includes(error.name)) throw error;
          const err = new Error('O servidor não retornou uma resposta válida. Retome a migração para tentar novamente.');
          err.status = response.ok ? 502 : response.status;
          err.code = 'WEB_INVALID_RESPONSE';
          throw err;
        }
        if (!response.ok) {
          const err = new Error(data?.error || `Erro HTTP ${response.status}`);
          err.code = data?.code;
          err.status = response.status;
          err.details = data?.details;
          err.retryable = data?.retryable;
          if (isRateLimited(err)) err.message = 'A plataforma limitou temporariamente as requisições. É preciso aguardar antes de tentar novamente.';
          if (/^(?:(?:the )?(?:operation|request) (?:was |has been )?)?(?:aborted|cancelled|canceled)[.!]?$/i.test(err.message.trim())) {
            err.message = err.code === 'PROVIDER_TIMEOUT'
              ? 'A plataforma não respondeu dentro do prazo. Retome para tentar novamente.'
              : 'A plataforma interrompeu a operação; não informou o motivo da interrupção. Retome para tentar novamente.';
          }
          if (err.code === 'SPOTIFY_403' && /User Management/.test(err.message)) {
            document.dispatchEvent(new CustomEvent('rhyft:spotify-access-required', {detail: err.message}));
          }
          throw err;
        }
        return data;
      } catch (error) {
        if (!error.status) {
          const timeout = controller.signal.aborted || error.name === 'TimeoutError';
          const aborted = error.name === 'AbortError';
          error = new Error(timeout ? 'O servidor não respondeu dentro do prazo. Retome para tentar novamente.'
            : aborted ? 'A comunicação com o servidor foi interrompida. Retome para tentar novamente.'
            : 'A conexão com o servidor falhou. Retome para tentar novamente.');
          error.status = timeout ? 504 : 502;
          error.code = timeout ? 'WEB_TIMEOUT' : aborted ? 'WEB_ABORTED' : 'WEB_NETWORK';
          error.details = timeout ? {timeoutMs: 30000} : {};
        }
        error.context = context;
        error.attempts = attempt + 1;
        if (isRateLimited(error)) {
          retryDelay = await withProviderLock(context.provider, () => pacing.limited(context.provider, Number(error.details?.retryAfterMs) || 0));
          if (init.method === 'GET') attempts = 3;
          if (state.running || state.reviewing) log(`⚠ ${describeFailure(error)} Todas as chamadas a esta plataforma aguardam ${Math.ceil(retryDelay / 1000)}s antes de continuar.`, 'warn');
        }
        if (!isTemporary(error) || attempt + 1 === attempts) throw error;
        if (state.running || state.reviewing) log(`⚠ ${isRateLimited(error) ? '' : describeFailure(error)} Nova tentativa de leitura (${attempt + 2}/${attempts})…`, 'warn');
      } finally { clearTimeout(timer); }
      if (retryDelay === 500) await waitForRetry(retryDelay);
    }
  }

  function isRateLimited(error) {
    const details = error.details || {};
    if (details.cause === 'quota' || details.limitScope === 'day') return false;
    if (details.cause === 'rate-limit' || ['minute', 'second'].includes(details.limitScope)) return true;
    if (/quota|daily[_-]?Limit/i.test(details.reason || '')) return false;
    return /rate[_-]?Limit/i.test(details.reason || '') || error.status === 429;
  }

  function isTemporary(error) {
    if (isPublicSearchBlocked(error) || error.code === 'YOUTUBE_MUSIC_FORMAT_CHANGED') return false;
    return isRateLimited(error) || (typeof error.retryable === 'boolean' ? error.retryable : [500, 502, 503, 504].includes(error.status));
  }

  function isPublicSearchBlocked(error) {
    return (error.context?.provider === 'youtube-music' || error.details?.provider === 'youtube-music')
      && (error.status === 403 || ['catalog-blocked', 'blocked', 'captcha'].includes(error.details?.cause) || /CAPTCHA|BLOCKED/i.test(error.code || ''));
  }

  function withProviderLock(provider, action) {
    // Share reservations between tabs when Web Locks are available.
    return navigator.locks?.request ? navigator.locks.request(`rhyft:provider:${provider}`, action) : action();
  }

  async function reserveProvider(provider) {
    const at = await withProviderLock(provider, () => pacing.reserve(provider));
    const previousTitle = ui.progressTitle.textContent;
    let displayed = false;
    try {
      for (;;) {
        if (state.running) await checkpoint();
        const wait = pacing.remaining(provider, at);
        if (!wait.ms) break;
        if (wait.blocked && state.running) {
          ui.progressTitle.textContent = `${{youtube: 'YouTube', 'youtube-music': 'YouTube Music (busca pública)', spotify: 'Spotify'}[provider] || 'Servidor'} limitou as chamadas · aguardando ${Math.ceil(wait.ms / 1000)}s…`;
          displayed = true;
        }
        await sleep(Math.min(wait.ms, 200));
      }
    } finally {
      if (displayed) ui.progressTitle.textContent = previousTitle;
    }
  }

  async function waitForRetry(ms) {
    // Short waits keep pause/cancel responsive throughout the backoff.
    while (ms > 0) {
      if (state.running) await checkpoint();
      const step = Math.min(ms, 200);
      await sleep(step);
      ms -= step;
    }
    if (state.running) await checkpoint();
  }

  function canDeferSearch(error) {
    return error.context?.operation === 'search'
      && (!error.details?.operation || ['search', 'track-details'].includes(error.details.operation))
      && isTemporary(error) && !state.inFlight.length;
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

  async function loadSession(required = false) {
    try {
      const data = await api('/session');
      state.session = data;
      connectedCard('spotify', data.spotify);
      connectedCard('youtube', data.youtube);
      document.dispatchEvent(new CustomEvent('rhyft:session-loaded', {detail: data}));
      if (data.setup && !data.setup.ready) {
        showToast(`Configuração do site incompleta: ${data.setup.missing.join(', ')}`, true);
      }
      return true;
    } catch (error) {
      state.session = null;
      connectedCard('spotify', null); connectedCard('youtube', null);
      showToast(error.message, true);
      if (required) throw error;
      return false;
    }
  }

  function handleAuthQuery() {
    const p = new URLSearchParams(location.search);
    if (p.get('auth') === 'spotify-ok') showToast('Spotify conectado com sucesso.');
    if (p.get('auth') === 'google-ok') showToast('Google / YouTube conectado com sucesso.');
    if (p.get('auth_error')) {
      showToast(p.get('auth_error'), true);
      if (/User Management/.test(p.get('auth_error'))) document.dispatchEvent(new CustomEvent('rhyft:spotify-access-required', {detail: p.get('auth_error')}));
    }
    if ([...p.keys()].some(k => k.startsWith('auth'))) history.replaceState({}, '', location.pathname);
  }

  async function logout(provider) {
    if (state.running || state.reviewing) return;
    try {
      await api('/logout', { method: 'POST', body: { provider } });
      searchCache.clear(provider === 'google' ? 'youtube-music' : 'spotify');
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
    if (total) state.progressDone = Math.max(state.progressDone, Math.min(done, total));
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
    state.failedSources = new Set(); state.sourceKeys = []; state.progressDone = 0;
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
      inFlight: state.inFlight, matcherVersion: matching.VERSION
    });
    try { store.save(state.record); } catch {
      throw new Error('Não foi possível salvar o progresso neste navegador. Libere espaço no armazenamento antes de continuar.');
    }
  }

  function sourceKey(track) { return track.sourceKey || track.id || queryFor(track); }

  function destinationAccount(kind) { return state.session?.[kind]?.accountId || null; }

  function savedDestination(input = state.sourceInput) {
    const kind = state.direction === 'spotify-youtube' ? 'youtube' : 'spotify';
    return store.find(state.direction, input, destinationAccount(kind)) || store.load().find(record =>
      record.status === 'destination-unavailable' && record.direction === state.direction &&
      store.sourceId(record) === store.playlistId(state.direction, input));
  }

  function unavailableDestination(error) {
    if (error.context?.operation !== 'playlist-read') return false;
    if (error.code === 'DESTINATION_ACCOUNT_MISMATCH' && error.status === 409) return true;
    if (error.status !== 404) return false;
    if (error.code === 'DESTINATION_UNAVAILABLE') return error.details?.operation === 'playlist-read';
    // Older deployments used NOT_FOUND for both playlists and unknown API routes.
    if (error.code === 'NOT_FOUND') return /playlist.*(?:não encontrada|não acessível|removida|indisponível)/i.test(error.message);
    return ['GOOGLE_404', 'SPOTIFY_404'].includes(error.code) && error.details?.operation === 'playlist-read';
  }

  function checkStorage() {
    try { localStorage.setItem('rhyft.web.storage-check', '1'); localStorage.removeItem('rhyft.web.storage-check'); }
    catch { throw new Error('Habilite o armazenamento deste navegador para salvar e retomar a migração.'); }
  }

  async function destinationIds(kind) {
    const ids = new Set(), pages = new Set();
    let page = null;
    do {
      const suffix = page === null ? '' : `&page=${encodeURIComponent(page)}`;
      const data = await api(`/${kind}/playlist/state?input=${encodeURIComponent(state.destination.id)}${suffix}`);
      for (const id of data.ids || []) ids.add(id);
      if (page === null) {
        state.destination.name = data.name || state.destination.name;
        if (data.accountId) state.record.destinationAccountId = data.accountId;
      }
      page = data.nextPage ?? null;
      if (page !== null && pages.has(page)) throw new Error('Não foi possível conferir todas as faixas da playlist de destino.');
      pages.add(page);
    } while (page !== null);
    return ids;
  }

  async function prepareDestination(kind, source, name) {
    const accountId = destinationAccount(kind);
    const records = store.load();
    const currentTracks = new Map(source.tracks.map(track => [sourceKey(track), track]));
    const currentPending = items => (items || []).filter(item => currentTracks.has(sourceKey(item.source)) &&
      !state.processed[sourceKey(item.source)]).map(item => ({...item, source: currentTracks.get(sourceKey(item.source)), resolved: false}));
    let previous = (state.selectedRecord && records.find(record => record.id === state.selectedRecord.id && record.direction === state.direction &&
      store.sourceId(record) === source.id)) || savedDestination();
    state.selectedRecord = null;
    // An archived history item points to its successfully created replacement.
    const replacement = previous?.replacementId && records.find(record => record.id === previous.replacementId && record.status !== 'destination-unavailable');
    if (replacement && (!accountId || !replacement.destinationAccountId || replacement.destinationAccountId === accountId)) previous = replacement;
    if (previous?.status === 'destination-unavailable') {
      const active = store.find(state.direction, state.sourceInput, accountId);
      if (active && (!accountId || !active.destinationAccountId || active.destinationAccountId === accountId)) previous = active;
    }
    if (previous?.destinationAccountId && accountId && previous.destinationAccountId !== accountId) {
      const own = store.find(state.direction, state.sourceInput, accountId);
      if (own?.destinationAccountId === accountId) previous = own;
    }
    let recover = null;
    if (previous) {
      if (accountId && previous.destinationAccountId && previous.destinationAccountId !== accountId) {
        log('♻ O destino salvo pertence a outra conta. A migração usará uma nova playlist na conta conectada; o histórico anterior foi mantido.', 'warn');
        recover = previous;
      } else {
        state.record = { ...previous };
        const id = store.playlistId(state.direction, previous.destinationId || previous.destinationUrl, true);
        if (!id) throw new Error('O histórico não possui um destino válido para retomar.');
        state.destination = { id, url: previous.destinationUrl, name: previous.destinationName || name };
        // Preserve the checkpoint even if the destination cannot currently be read.
        state.processed = { ...previous.processed };
        state.inFlight = previous.inFlight || [];
        state.pending = currentPending(previous.pendingItems);
        state.added = previous.added || 0;
        try { state.usedIds = await destinationIds(kind); }
        catch (error) {
          if (!unavailableDestination(error)) throw error;
          log(`⚠ ${describeFailure(error)} O destino salvo não está disponível para esta conta. Criando uma nova playlist e mantendo o histórico anterior.`, 'warn');
          recover = previous;
          // Preserve the old checkpoint, even if creating its replacement fails.
          try { store.save({ ...previous, status: 'destination-unavailable', updatedAt: new Date().toISOString() }); }
          catch { throw new Error('Não foi possível salvar o progresso neste navegador. Libere espaço no armazenamento antes de continuar.'); }
          state.destination = null; state.record = null;
        }
        if (!recover) {
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
          state.pending = currentPending(previous.pendingItems);
          log(`♻ Playlist “${state.destination.name}” recuperada do histórico; ${state.usedIds.size} faixa(s) já presentes.`, 'ok');
        }
      }
    }
    if (!previous || recover) {
      // Check storage before creating anything remotely.
      checkStorage();
      state.destination = null; state.record = null; state.usedIds = new Set(); state.added = 0; state.inFlight = [];
      const sourceKeys = new Set(source.tracks.map(sourceKey));
      state.processed = Object.fromEntries(Object.entries(recover?.processed || {}).filter(([key, value]) => value === 'ignored' && sourceKeys.has(key)));
      state.pending = currentPending(recover?.pendingItems);
      name = name || recover?.destinationName;
      await checkpoint();
      const dest = await api(`/${kind}/playlist`, { method: 'POST', body: { name, expectedAccountId: accountId } });
      state.destination = { id: dest.id, url: dest.url, name: dest.name || name };
      state.record = {
        id: crypto.randomUUID(), createdAt: new Date().toISOString(),
        direction: state.direction, sourceInput: state.sourceInput, sourceId: source.id,
        destinationAccountId: dest.accountId || accountId
      };
      saveProgress('running');
      if (recover && store.load().find(record => record.id === recover.id)?.status === 'destination-unavailable') {
        store.save({...recover, status: 'destination-unavailable', replacementId: state.record.id, updatedAt: new Date().toISOString()});
      }
      log(`✓ Playlist “${name}” criada no ${kind === 'youtube' ? 'YouTube' : 'Spotify'}`, 'ok');
    }
    Object.assign(state.record, {sourceInput: state.sourceInput, sourceId: source.id,
      sourceTotal: source.tracks.length, sourceSnapshot: source.snapshotId || null});
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
          await api('/youtube/playlist/item', { method: 'POST', body: { playlistId: state.destination.id, videoId: fresh[0].candidate.id, expectedAccountId: state.record.destinationAccountId } });
        } else {
          await api('/spotify/playlist/items', { method: 'POST', body: { playlistId: state.destination.id, uris: fresh.map(entry => entry.candidate.uri), expectedAccountId: state.record.destinationAccountId } });
        }
        fresh.forEach(entry => state.usedIds.add(entry.candidate.id));
      } catch (error) {
        if (!isTemporary(error)) throw error;
        // Never repeat a POST blindly: it may have succeeded despite its error.
        log(`⚠ ${describeFailure(error)} Conferindo se o envio chegou à playlist…`, 'warn');
        await sleep(500);
        try { state.usedIds = await destinationIds(kind); }
        catch (verificationError) {
          verificationError.message += ' Não foi possível confirmar o envio anterior; retome pela mesma origem ou pelo histórico.';
          throw verificationError;
        }
        for (const entry of fresh) {
          if (state.usedIds.has(entry.candidate.id)) state.processed[sourceKey(entry.source)] = entry.candidate.id;
        }
        state.added = state.usedIds.size;
        state.inFlight = fresh.filter(entry => !state.usedIds.has(entry.candidate.id));
        saveProgress();
        if (state.inFlight.length) {
          error.message += ' Não foi possível confirmar o envio de todas as faixas. Retome pela mesma origem ou pelo histórico.';
          throw error;
        }
        log('✓ Envio confirmado na playlist após uma falha de resposta.', 'ok');
      }
    }
    entries.forEach(entry => {
      state.processed[sourceKey(entry.source)] = entry.candidate.id;
      resolveSource(entry.source);
    });
    state.added = state.usedIds.size;
    state.inFlight = [];
    saveProgress();
  }

  document.addEventListener('rhyft:resume', event => {
    if (state.running || state.reviewing) return;
    state.selectedRecord = event.detail;
    startMigration();
  });
  ui.input.addEventListener('input', () => state.selectedRecord = null);
  ui.directions.forEach(button => button.addEventListener('click', () => {
    if (!state.running) state.selectedRecord = null;
  }));

  function queryFor(track) {
    return `${track.name || ''} ${(track.artists || [])[0] || track.artist || ''}`.trim();
  }

  async function findCandidates(track, kind) {
    const cached = state.pending.find(item => !item.resolved && sourceKey(item.source) === sourceKey(track));
    const pool = new Map((cached?.candidates || []).filter(candidate => kind !== 'youtube' || candidate.catalogSource === 'youtube-music-public').map(candidate => [candidate.id, candidate]));
    let ranked = matching.rank(track, [...pool.values()], kind);
    // Reevaluate older pending matches with the new comparator before spending quota.
    if (ranked[0]?.auto) return ranked;
    for (const query of matching.queries(track, kind)) {
      await checkpoint();
      const provider = kind === 'youtube' ? 'youtube-music' : kind;
      let found = searchCache.get(provider, query);
      if (found) log(`♻ Reaproveitando busca salva: ${track.name || track.title}`, 'ok');
      else {
        found = await api(`${kind === 'youtube' ? '/youtube/music/search' : '/spotify/search'}?q=${encodeURIComponent(query)}`);
        searchCache.set(provider, query, found);
      }
      for (const candidate of found.items || []) pool.set(candidate.id, kind === 'youtube' ? {...candidate, catalogSource: 'youtube-music-public'} : candidate);
      ranked = matching.rank(track, [...pool.values()], kind);
      if (ranked[0]?.auto) break;
    }
    return ranked;
  }

  function resolveSource(track) {
    if (state.failedSources.delete(sourceKey(track))) state.skipped = Math.max(0, state.skipped - 1);
    state.pending.forEach(item => {
      if (sourceKey(item.source) === sourceKey(track)) item.resolved = true;
    });
  }

  function addPending(source, candidates, kind, searchFailed = false, persist = true) {
    const existing = state.pending.find(item => !item.resolved && sourceKey(item.source) === sourceKey(source));
    if (existing) { existing.candidates = candidates.slice(0, 4); existing.searchFailed = searchFailed; }
    else state.pending.push({ source, candidates: candidates.slice(0, 4), kind, resolved: false, searchFailed });
    if (persist) saveProgress();
  }

  function failedPublicSearch(track, persist = true) {
    if (!state.failedSources.has(sourceKey(track))) {state.failedSources.add(sourceKey(track)); state.skipped++;}
    addPending(track, [], 'youtube', true, persist);
  }

  async function spotifyToYoutube(sourceInput, name) {
    ui.progressTitle.textContent = 'Lendo playlist do Spotify…';
    const source = await api(`/spotify/playlist?input=${encodeURIComponent(sourceInput)}`);
    if (source.incomplete) {
      const error = new Error(`O Spotify informou ${source.sourceInfo.reportedTotal} itens, mas retornou apenas ${source.sourceInfo.rawItems} após uma nova leitura. Retome para atualizar quando o Spotify disponibilizar a playlist completa.`);
      error.context = {provider: 'spotify', operation: 'playlist-read'};
      error.status = 502; error.code = 'SPOTIFY_PLAYLIST_INCOMPLETE';
      throw error;
    }
    if (!source.tracks.length) throw new Error('A playlist do Spotify não possui faixas disponíveis para migrar.');
    state.sourceKeys = source.tracks.map(sourceKey);
    log(`✓ ${source.tracks.length} faixas carregadas do Spotify`, 'ok');
    if (source.sourceInfo) {
      const info = source.sourceInfo;
      if (info.readAttempts > 1) log('♻ O Spotify retornou uma leitura incompleta. A playlist foi consultada novamente antes de continuar.');
      if (info.localTracks) log(`✓ ${info.localTracks} faixa(s) local(is) incluída(s) na busca por título e artista.`, 'ok');
      const excluded = [
        [info.excluded?.nonMusic, 'item(ns) que não são músicas'],
        [info.excluded?.missingMetadata, 'item(ns) sem título disponível'],
        [info.excluded?.duplicates, 'música(s) repetida(s)']
      ].filter(([count]) => count > 0).map(([count, reason]) => `${count} ${reason}`);
      if (excluded.length) log(`ℹ ${info.rawItems} itens lidos do Spotify; ${source.tracks.length} músicas para migrar. Fora da migração: ${excluded.join('; ')}.`);
    }
    progress(0, source.tracks.length);
    if (source.truncated) log('⚠ Playlist muito grande: esta sessão processará apenas as primeiras 2.000 faixas.', 'warn');

    await prepareDestination('youtube', source, name);
    ui.progressTitle.textContent = 'Encontrando músicas no YouTube…';

    const total = source.tracks.length;
    for (let i = 0; i < total; i++) {
      await checkpoint();
      const track = source.tracks[i];
      if (state.processed[sourceKey(track)]) {
        progress(i + 1, total, track.name);
        continue;
      }
      progress(i, total, `${track.name} — ${(track.artists || []).join(', ')}`);
      log(`[${i + 1}/${total}] Procurando: ${track.name}`);
      try {
        const ranked = await findCandidates(track, 'youtube');
        const best = ranked[0];
        if (best?.auto) {
          await checkpoint();
          await addEntries('youtube', [{ source: track, candidate: best }]);
          log(`  ✓ ${best.title}`, 'ok');
        } else if (ranked.length) {
          addPending(track, ranked, 'youtube');
          log('  ? Correspondência incerta — deixei para sua escolha.', 'warn');
        } else {
          addPending(track, [], 'youtube');
          log('  ? Nenhum resultado útil encontrado — deixei para escolher pelo link.', 'warn');
        }
      } catch (error) {
        if (error.context?.provider === 'youtube-music' && error.context.operation === 'search' && !(error instanceof Cancelled)) {
          log(`  ✕ ${describeFailure(error)}`, 'error');
          failedPublicSearch(track);
          if (isPublicSearchBlocked(error) || error.code === 'YOUTUBE_MUSIC_FORMAT_CHANGED') {
            for (const remaining of source.tracks.slice(i + 1)) {
              if (!state.processed[sourceKey(remaining)]) failedPublicSearch(remaining, false);
            }
            saveProgress();
            const blocked = isPublicSearchBlocked(error);
            log(blocked
              ? '  ↳ O YouTube Music bloqueou a busca pública. O motor foi interrompido sem novas consultas; escolha os links das faixas pendentes abaixo.'
              : '  ↳ Não foi possível interpretar a resposta do YouTube Music. A busca foi interrompida; escolha os links das faixas pendentes abaixo.', 'warn');
            progress(i, total, `${blocked ? 'Busca pública bloqueada' : 'Formato da busca incompatível'} · revisão por link disponível`);
            return;
          }
          log(`  ↳ A busca não foi concluída${error.attempts === 3 ? ' após 3 tentativas' : ''}. Você pode adicionar esta faixa por link ou tentar ao retomar; seguindo com as próximas.`, 'warn');
          progress(i + 1, total, track.name);
          await sleep(120);
          continue;
        }
        if (!canDeferSearch(error) && (error instanceof Cancelled || isTemporary(error) || state.inFlight.length || error.status === 401 || error.status === 429 || /quota|cota|limit|salvar o progresso/i.test(error.message))) throw error;
        state.skipped++; log(`  ✕ ${describeFailure(error)}`, 'error');
        if (canDeferSearch(error)) log('  ↳ Busca falhou após 3 tentativas. Esta faixa ficou para tentar ao retomar; seguindo com as próximas.', 'warn');
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
    state.sourceKeys = source.tracks.map(sourceKey);
    log(`✓ ${source.tracks.length} itens carregados do YouTube`, 'ok');
    progress(0, source.tracks.length);
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
      if (state.processed[sourceKey(track)]) {
        progress(i + 1, total, track.name);
        continue;
      }
      progress(i, total, track.name);
      log(`[${i + 1}/${total}] Procurando: ${track.name}`);
      try {
        const ranked = await findCandidates(track, 'spotify');
        const best = ranked[0];
        if (best?.auto) {
          if (state.usedIds.has(best.id)) {
            state.processed[sourceKey(track)] = best.id;
            resolveSource(track);
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
        if (!canDeferSearch(error) && (error instanceof Cancelled || isTemporary(error) || state.inFlight.length || error.status === 401 || error.status === 429 || /salvar o progresso/i.test(error.message))) throw error;
        state.skipped++; log(`  ✕ ${describeFailure(error)}`, 'error');
        if (canDeferSearch(error)) log('  ↳ Busca falhou após 3 tentativas. Esta faixa ficou para tentar ao retomar; seguindo com as próximas.', 'warn');
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
      saveReviewProgress();
      renderPending(); updateResult();
      showToast(`Adicionada: ${candidate.title || candidate.name}`);
    } catch (error) {
      log(`✕ ${item.source.name || item.source.title}: ${describeFailure(error)}`, 'error');
      buttons.forEach(b => b.disabled = false); showToast(describeFailure(error, false), true);
    } finally {
      state.reviewing = false; ui.start.disabled = false;
    }
  }

  function saveReviewProgress() {
    const total = state.sourceKeys.length;
    const done = state.sourceKeys.filter(key => state.processed[key]).length;
    const complete = total > 0 && done === total && !state.inFlight.length && state.record?.status !== 'cancelled';
    saveProgress(complete ? 'completed' : state.record?.status);
    if (total) progress(Math.max(state.progressDone, done), total, complete ? 'Concluído' : ui.progressCurrent.textContent);
    if (complete) ui.progressTitle.textContent = 'Migração processada';
  }

  function youtubeVideoId(input) {
    let url;
    try { url = new URL(input.trim()); } catch { return null; }
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return null;
    const host = url.hostname.toLowerCase();
    let id;
    if (host === 'youtu.be') id = url.pathname.match(/^\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
    else if (['youtube.com', 'www.youtube.com', 'music.youtube.com', 'm.youtube.com'].includes(host)) {
      id = url.pathname === '/watch' ? url.searchParams.get('v')
        : url.pathname.match(/^\/(?:shorts|live|embed)\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
    }
    return /^[A-Za-z0-9_-]{11}$/.test(id || '') ? id : null;
  }

  function renderPending() {
    const active = state.pending.filter(p => !p.resolved);
    ui.pendingPanel.hidden = active.length === 0;
    ui.pendingTitle.textContent = active.length === 1 ? '1 faixa precisa de revisão' : `${active.length} faixas precisam de revisão`;
    ui.pendingList.textContent = '';
    active.forEach((item, index) => {
      const card = document.createElement('article'); card.className = 'pending-item';
      const source = document.createElement('div'); source.className = 'pending-source'; source.textContent = `${item.source.name} — ${(item.source.artists || []).join(', ')}`;
      card.appendChild(source);
      const list = document.createElement('div'); list.className = 'candidate-list';
      item.candidates.forEach(candidate => {
        const row = document.createElement('div'); row.className = 'candidate';
        const meta = document.createElement('div');
        const strong = document.createElement('strong'); strong.textContent = candidate.title || candidate.name;
        const span = document.createElement('span');
        const duration = candidate.duration ? ` · ${Math.floor(candidate.duration / 60)}:${String(candidate.duration % 60).padStart(2, '0')}` : '';
        span.textContent = `${candidate.channel || (candidate.artists || []).join(', ')}${duration} · ${candidate.reason || 'Confira esta correspondência'}`;
        meta.append(strong, span);
        if (candidate.url) {
          const listen = document.createElement('a');
          listen.href = candidate.url; listen.target = '_blank'; listen.rel = 'noreferrer';
          listen.textContent = 'Ouvir antes de escolher'; listen.className = 'candidate-listen';
          meta.appendChild(listen);
        }
        const choose = document.createElement('button'); choose.type = 'button'; choose.textContent = 'Escolher';
        choose.addEventListener('click', () => resolvePending(item, candidate, card));
        row.append(meta, choose); list.appendChild(row);
      });
      card.appendChild(list);
      if (item.kind === 'youtube') {
        if (item.searchFailed) {
          const failure = document.createElement('p'); failure.className = 'pending-search-failure';
          failure.textContent = 'A busca desta faixa não foi concluída. Escolha o vídeo pelo link ou tente novamente ao retomar.';
          card.appendChild(failure);
        }
        const manual = document.createElement('div'); manual.className = 'pending-manual-link';
        const search = document.createElement('a'); search.className = 'candidate-listen';
        search.href = `https://music.youtube.com/search?q=${encodeURIComponent(queryFor(item.source))}`;
        search.target = '_blank'; search.rel = 'noreferrer'; search.textContent = 'Buscar no YouTube Music';
        card.appendChild(search);
        const label = document.createElement('label'); label.textContent = 'Link da música no YouTube';
        const input = document.createElement('input'); input.type = 'url'; input.id = `manual-video-${index}`;
        input.placeholder = 'https://music.youtube.com/watch?v=…'; input.autocomplete = 'off'; label.htmlFor = input.id;
        const add = document.createElement('button'); add.type = 'button'; add.textContent = 'Adicionar pelo link';
        const submit = async () => {
          if (state.running || state.reviewing || item.resolved) return;
          const id = youtubeVideoId(input.value);
          if (!id) {showToast('Cole um link válido de uma música ou vídeo do YouTube; links somente de playlist não servem.', true); input.focus(); return;}
          await resolvePending(item, {id, title: item.source.name || item.source.title, channel: 'Link escolhido por você',
            url: `https://www.youtube.com/watch?v=${id}`, catalogSource: 'manual-link'}, card);
        };
        add.addEventListener('click', submit);
        input.addEventListener('keydown', event => {if (event.key === 'Enter') {event.preventDefault(); submit();}});
        manual.append(label, input, add); card.appendChild(manual);
      }
      const ignore = document.createElement('button'); ignore.className = 'pending-ignore'; ignore.type = 'button'; ignore.textContent = 'Ignorar esta faixa';
      ignore.addEventListener('click', () => {
        if (state.reviewing || state.running) return;
        item.resolved = true;
        if (!state.failedSources.delete(sourceKey(item.source))) state.skipped++;
        state.processed[sourceKey(item.source)] = 'ignored';
        try { saveReviewProgress(); } catch (error) { showToast(error.message, true); }
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
    if (!name && !savedDestination(sourceInput)) return showToast('Digite o nome da playlist de destino.', true);
    if (!state.session?.spotify?.connected || !state.session?.youtube?.connected) return showToast('Conecte Spotify e Google / YouTube antes de iniciar.', true);

    state.sourceInput = sourceInput;
    state.running = true; state.paused = false; state.cancelled = false; resetRun();
    ui.start.disabled = true; ui.pause.disabled = false; ui.cancel.disabled = false; ui.directions.forEach(x => x.disabled = true);
    try {
      await loadSession(true);
      if (!state.session?.spotify?.connected || !state.session?.youtube?.connected) throw new Error('Conecte Spotify e Google / YouTube antes de iniciar.');
      if (state.direction === 'spotify-youtube') await spotifyToYoutube(sourceInput, name);
      else await youtubeToSpotify(sourceInput, name);
      await checkpoint();
      ui.progressTitle.textContent = state.skipped ? 'Migração processada com falhas' : 'Migração processada';
      ui.progressCurrent.textContent = state.skipped ? 'Retome para tentar as faixas com erro' : 'Concluído';
      saveProgress(state.skipped ? 'interrupted' : 'completed');
      renderPending(); updateResult();
      log(`✓ Processo finalizado: ${state.added} item(ns) adicionado(s).`, 'ok');
      const unresolved = state.pending.filter(item => !item.resolved).length;
      if (unresolved) log(`? ${unresolved} item(ns) aguardam sua escolha.`, 'warn');
    } catch (error) {
      if (error instanceof Cancelled) {
        ui.progressTitle.textContent = 'Migração cancelada'; log('Migração cancelada. A playlist já criada não foi apagada.', 'warn');
      } else {
        ui.progressTitle.textContent = 'Migração interrompida'; log(`✕ ${describeFailure(error)}`, 'error');
        showToast(describeFailure(error, false), true);
      }
      if (state.destination) {
        try {
          saveProgress(error instanceof Cancelled ? 'cancelled' : 'interrupted');
          if (!(error instanceof Cancelled)) log('O progresso foi salvo. Clique em Iniciar migração com a mesma origem ou retome pelo histórico.', 'warn');
        }
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
