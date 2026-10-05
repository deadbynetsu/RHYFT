(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];

  const header = $('.site-header');
  const navToggle = $('.nav-toggle');
  const navLinks = $('.nav-links');
  const onScroll = () => header?.classList.toggle('scrolled', window.scrollY > 20);
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  navToggle?.addEventListener('click', () => {
    const open = navLinks.classList.toggle('open');
    navToggle.setAttribute('aria-expanded', String(open));
    navToggle.textContent = open ? '×' : '☰';
  });
  $$('.nav-links a').forEach(a => a.addEventListener('click', () => navLinks?.classList.remove('open')));

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduced && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.14 });
    $$('.reveal').forEach(el => io.observe(el));
  } else {
    $$('.reveal').forEach(el => el.classList.add('visible'));
  }

  async function loadRelease() {
    const fallback = 'https://github.com/deadbynetsu/Spotify-Youtube-Music-Playlists-Migrator/releases/latest';
    try {
      const response = await fetch('https://api.github.com/repos/deadbynetsu/Spotify-Youtube-Music-Playlists-Migrator/releases/latest', {
        headers: { Accept: 'application/vnd.github+json' }
      });
      if (!response.ok) throw new Error(`GitHub ${response.status}`);
      const release = await response.json();
      const assets = release.assets || [];
      const windows = assets.find(a => /windows.*\.zip$/i.test(a.name)) || assets.find(a => /\.zip$/i.test(a.name) && /migrador/i.test(a.name));
      const android = assets.find(a => /android.*\.apk$/i.test(a.name)) || assets.find(a => /\.apk$/i.test(a.name));
      const version = release.tag_name || 'Release mais recente';
      const date = release.published_at ? new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium' }).format(new Date(release.published_at)) : 'GitHub Releases';
      $$('.release-version').forEach(el => el.textContent = version);
      $$('.release-date').forEach(el => el.textContent = date);
      $$('.download-windows').forEach(el => el.href = windows?.browser_download_url || release.html_url || fallback);
      $$('.download-android').forEach(el => el.href = android?.browser_download_url || release.html_url || fallback);
    } catch (error) {
      $$('.release-version').forEach(el => el.textContent = 'Release mais recente');
      $$('.release-date').forEach(el => el.textContent = 'GitHub');
      $$('.download-windows, .download-android').forEach(el => el.href = fallback);
    }
  }
  loadRelease();

  const demoButton = $('#demo-start');
  const demoLog = $('#demo-log');
  const demoProgress = $('#demo-progress');
  let demoRunning = false;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const line = (text, cls = '') => {
    const p = document.createElement('p');
    if (cls) p.className = cls;
    p.textContent = text;
    demoLog.appendChild(p);
    demoLog.scrollTop = demoLog.scrollHeight;
  };

  demoButton?.addEventListener('click', async () => {
    if (demoRunning) return;
    demoRunning = true;
    demoButton.disabled = true;
    demoLog.textContent = '';
    demoProgress.style.width = '0%';
    const steps = [
      ['Conectando às plataformas…', 8],
      ['✓ Playlist encontrada: “Noite”', 20],
      ['✓ 42 músicas carregadas', 34],
      ['♫ Procurando versões correspondentes…', 52],
      ['✓ 18/42 adicionadas', 66],
      ['✓ 31/42 adicionadas', 82],
      ['✓ 42/42 processadas', 96],
      ['Migração concluída ✓', 100]
    ];
    for (const [text, pct] of steps) {
      line(text, pct === 100 ? 'success' : '');
      demoProgress.style.width = `${pct}%`;
      await wait(reduced ? 80 : 520);
    }
    demoButton.textContent = 'Executar novamente';
    demoButton.disabled = false;
    demoRunning = false;
  });
})();