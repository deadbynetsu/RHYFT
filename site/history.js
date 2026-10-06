(() => {
  const STORAGE_KEY = 'rhyft.web.history.v1';
  const MAX_ITEMS = 100;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const ui = {
    toggle: $('#history-toggle'),
    count: $('#history-count'),
    panel: $('#history-panel'),
    list: $('#history-list'),
    empty: $('#history-empty'),
    search: $('#history-search'),
    filters: $$('.history-filter'),
    clear: $('#history-clear'),
    export: $('#history-export'),
    resultPanel: $('#result-panel'),
    resultTitle: $('#result-title'),
    resultSummary: $('#result-summary'),
    resultLink: $('#result-link'),
    progressTitle: $('#progress-title'),
    sourceInput: $('#playlist-input'),
    playlistName: $('#playlist-name'),
    migrator: $('.migrator-panel')
  };

  if (!ui.toggle || !ui.panel || !ui.list || !ui.resultPanel) return;

  const state = {
    currentRecordId: null,
    filter: 'all',
    query: ''
  };

  function loadHistory() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(item => item && item.id && item.createdAt);
    } catch {
      return [];
    }
  }

  function writeHistory(items) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, MAX_ITEMS)));
    } catch {
      // Histórico é um recurso auxiliar; falha de storage nunca deve quebrar a migração.
    }
  }

  function makeId() {
    if (crypto?.randomUUID) return crypto.randomUUID();
    return `rhyft-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function directionInfo(direction) {
    if (direction === 'youtube-spotify') {
      return { label: 'YouTube Music → Spotify', from: 'YouTube Music', to: 'Spotify', cls: 'yt-sp' };
    }
    return { label: 'Spotify → YouTube Music', from: 'Spotify', to: 'YouTube Music', cls: 'sp-yt' };
  }

  function currentDirection() {
    return $('.direction.active')?.dataset.direction || 'spotify-youtube';
  }

  function statusFromProgress() {
    const text = (ui.progressTitle?.textContent || '').toLowerCase();
    if (text.includes('cancelada')) return 'cancelled';
    if (text.includes('interrompida')) return 'interrupted';
    return 'completed';
  }

  function statusLabel(status, pending = 0) {
    if (status === 'cancelled') return 'Cancelada';
    if (status === 'interrupted') return 'Interrompida';
    if (pending > 0) return 'Revisão pendente';
    return 'Concluída';
  }

  function parseSummary() {
    const text = ui.resultSummary?.textContent || '';
    const numbers = [...text.matchAll(/(\d+)/g)].map(match => Number(match[1]));
    return {
      added: numbers[0] || 0,
      pending: numbers[1] || 0,
      skipped: numbers[2] || 0
    };
  }

  function captureResult() {
    if (ui.resultPanel.hidden) return;
    const href = ui.resultLink?.getAttribute('href') || '';
    const name = (ui.resultTitle?.textContent || ui.playlistName?.value || 'Playlist criada').trim();
    if (!href || href === '#') return;

    const stats = parseSummary();
    const now = new Date().toISOString();
    const items = loadHistory();
    let record = state.currentRecordId ? items.find(item => item.id === state.currentRecordId) : null;

    if (!record) {
      record = {
        id: makeId(),
        createdAt: now,
        updatedAt: now,
        direction: currentDirection(),
        sourceInput: ui.sourceInput?.value?.trim() || '',
        destinationName: name,
        destinationUrl: href,
        status: statusFromProgress(),
        added: stats.added,
        pending: stats.pending,
        skipped: stats.skipped
      };
      state.currentRecordId = record.id;
      items.unshift(record);
    } else {
      record.updatedAt = now;
      record.destinationName = name;
      record.destinationUrl = href;
      record.status = statusFromProgress();
      record.added = stats.added;
      record.pending = stats.pending;
      record.skipped = stats.skipped;
      record.sourceInput = record.sourceInput || ui.sourceInput?.value?.trim() || '';
    }

    writeHistory(items);
    renderHistory();
  }

  function formatDate(value) {
    try {
      return new Intl.DateTimeFormat('pt-BR', {
        dateStyle: 'short',
        timeStyle: 'short'
      }).format(new Date(value));
    } catch {
      return value;
    }
  }

  function matchesFilter(item) {
    if (state.filter === 'completed') return item.status === 'completed';
    if (state.filter === 'attention') return item.status !== 'completed' || Number(item.pending) > 0;
    return true;
  }

  function matchesSearch(item) {
    if (!state.query) return true;
    const haystack = [item.destinationName, item.sourceInput, directionInfo(item.direction).label]
      .join(' ').toLowerCase();
    return haystack.includes(state.query);
  }

  function removeItem(id) {
    const items = loadHistory().filter(item => item.id !== id);
    writeHistory(items);
    if (state.currentRecordId === id) state.currentRecordId = null;
    renderHistory();
  }

  function repeatItem(item) {
    const target = $(`.direction[data-direction="${item.direction}"]`);
    if (target && !target.disabled) target.click();
    if (ui.sourceInput) ui.sourceInput.value = item.sourceInput || '';
    if (ui.playlistName) ui.playlistName.value = item.destinationName || '';
    closeHistory();
    ui.migrator?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    ui.sourceInput?.focus({ preventScroll: true });
  }

  function openHistory() {
    ui.panel.hidden = false;
    ui.toggle.setAttribute('aria-expanded', 'true');
    ui.toggle.classList.add('active');
    renderHistory();
    ui.panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function closeHistory() {
    ui.panel.hidden = true;
    ui.toggle.setAttribute('aria-expanded', 'false');
    ui.toggle.classList.remove('active');
  }

  function renderHistory() {
    const items = loadHistory();
    if (ui.count) ui.count.textContent = String(items.length);
    if (ui.export) ui.export.disabled = items.length === 0;
    if (ui.clear) ui.clear.disabled = items.length === 0;

    const visible = items.filter(matchesFilter).filter(matchesSearch);
    ui.list.textContent = '';
    ui.empty.hidden = visible.length !== 0;

    visible.forEach(item => {
      const info = directionInfo(item.direction);
      const card = document.createElement('article');
      card.className = 'history-item';

      const top = document.createElement('div');
      top.className = 'history-item-top';

      const badges = document.createElement('div');
      badges.className = 'history-badges';
      const direction = document.createElement('span');
      direction.className = `history-direction ${info.cls}`;
      direction.textContent = info.label;
      const status = document.createElement('span');
      status.className = `history-status ${item.status}${Number(item.pending) > 0 ? ' pending' : ''}`;
      status.textContent = statusLabel(item.status, item.pending);
      badges.append(direction, status);

      const date = document.createElement('time');
      date.dateTime = item.createdAt;
      date.textContent = formatDate(item.createdAt);
      top.append(badges, date);

      const body = document.createElement('div');
      body.className = 'history-item-body';
      const title = document.createElement('div');
      title.className = 'history-item-title';
      title.textContent = item.destinationName || 'Playlist criada';
      const source = document.createElement('div');
      source.className = 'history-source';
      source.textContent = item.sourceInput ? `Origem: ${item.sourceInput}` : `Destino: ${info.to}`;
      body.append(title, source);

      const stats = document.createElement('div');
      stats.className = 'history-stats';
      const statAdded = document.createElement('span');
      statAdded.innerHTML = `<b>${Number(item.added) || 0}</b><small>adicionadas</small>`;
      const statPending = document.createElement('span');
      statPending.innerHTML = `<b>${Number(item.pending) || 0}</b><small>pendentes</small>`;
      const statSkipped = document.createElement('span');
      statSkipped.innerHTML = `<b>${Number(item.skipped) || 0}</b><small>ignoradas/erro</small>`;
      stats.append(statAdded, statPending, statSkipped);

      const actions = document.createElement('div');
      actions.className = 'history-actions';
      if (item.destinationUrl) {
        const open = document.createElement('a');
        open.className = 'btn btn-small btn-primary';
        open.href = item.destinationUrl;
        open.target = '_blank';
        open.rel = 'noreferrer';
        open.textContent = 'Abrir playlist';
        actions.appendChild(open);
      }
      const repeat = document.createElement('button');
      repeat.className = 'btn btn-small btn-secondary';
      repeat.type = 'button';
      repeat.textContent = 'Usar novamente';
      repeat.addEventListener('click', () => repeatItem(item));
      const remove = document.createElement('button');
      remove.className = 'history-delete';
      remove.type = 'button';
      remove.textContent = 'Excluir';
      remove.addEventListener('click', () => removeItem(item.id));
      actions.append(repeat, remove);

      card.append(top, body, stats, actions);
      ui.list.appendChild(card);
    });
  }

  function exportHistory() {
    const items = loadHistory();
    if (!items.length) return;
    const blob = new Blob([JSON.stringify({ exportedAt: new Date().toISOString(), migrations: items }, null, 2)], {
      type: 'application/json;charset=utf-8'
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `rhyft-historico-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  ui.toggle.addEventListener('click', () => {
    if (ui.panel.hidden) openHistory(); else closeHistory();
  });

  ui.search?.addEventListener('input', event => {
    state.query = event.target.value.trim().toLowerCase();
    renderHistory();
  });

  ui.filters.forEach(button => button.addEventListener('click', () => {
    state.filter = button.dataset.filter || 'all';
    ui.filters.forEach(item => item.classList.toggle('active', item === button));
    renderHistory();
  }));

  ui.clear?.addEventListener('click', () => {
    if (!loadHistory().length) return;
    if (!confirm('Apagar todo o histórico de migrações salvo neste navegador?')) return;
    localStorage.removeItem(STORAGE_KEY);
    state.currentRecordId = null;
    renderHistory();
  });

  ui.export?.addEventListener('click', exportHistory);

  const resultObserver = new MutationObserver(() => {
    if (ui.resultPanel.hidden) {
      state.currentRecordId = null;
      return;
    }
    queueMicrotask(captureResult);
  });

  resultObserver.observe(ui.resultPanel, { attributes: true, attributeFilter: ['hidden'] });
  if (ui.resultSummary) resultObserver.observe(ui.resultSummary, { childList: true, characterData: true, subtree: true });
  if (ui.resultTitle) resultObserver.observe(ui.resultTitle, { childList: true, characterData: true, subtree: true });
  if (ui.resultLink) resultObserver.observe(ui.resultLink, { attributes: true, attributeFilter: ['href'] });
  if (ui.progressTitle) resultObserver.observe(ui.progressTitle, { childList: true, characterData: true, subtree: true });

  renderHistory();
})();