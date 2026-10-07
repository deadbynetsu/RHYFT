(() => {
  const dialog = document.querySelector('#spotify-setup');
  const connect = document.querySelector('#spotify-connect');
  const input = document.querySelector('#spotify-client-id');
  const redirect = document.querySelector('#spotify-redirect');
  const error = document.querySelector('#spotify-setup-error');
  const welcome = document.querySelector('#spotify-welcome');
  const tutorial = document.querySelector('#spotify-tutorial');
  const progress = document.querySelector('#spotify-step-progress');
  const accessHelp = document.querySelector('#spotify-access-help');
  const preferenceKey = 'rhyft.web.spotify-client-id.v1';
  if (!dialog || !connect || !input) return;
  let step = 0, quick = false;

  // Client IDs are public app identifiers. Tokens stay in HttpOnly cookies.
  try { input.value = localStorage.getItem(preferenceKey) || ''; } catch {}
  redirect.value = `${location.origin}/api/spotify/callback`;

  function focusStep() {
    const target = step === 3 ? input : step === 2 ? redirect : step === 1
      ? document.querySelector('.spotify-dashboard-link') : document.querySelector('#spotify-setup-begin');
    target.focus();
  }

  function showStep(next, quickEntry = false) {
    step = next; quick = quickEntry;
    welcome.hidden = step !== 0;
    tutorial.hidden = step === 0;
    progress.hidden = quick;
    document.querySelectorAll('#spotify-tutorial [data-step]').forEach(panel => {
      panel.hidden = Number(panel.dataset.step) !== step;
    });
    document.querySelector('#spotify-step-label').textContent = `Passo ${step} de 3`;
    document.querySelectorAll('.spotify-step-dots i').forEach((dot, index) => dot.classList.toggle('active', index < step));
    document.querySelector('#spotify-need-help').hidden = !quick;
    error.hidden = true;
    dialog.scrollTop = 0;
    if (dialog.open) focusStep();
  }

  function open(accessError = false) {
    showStep(accessError ? 0 : input.value ? 3 : 0, !accessError && !!input.value);
    accessHelp.hidden = !accessError; accessHelp.open = false;
    if (accessError) {
      error.textContent = 'O Spotify ainda não autorizou esta conta no aplicativo usado no login.';
      error.hidden = false;
    }
    if (!dialog.open) dialog.showModal();
    focusStep();
  }

  connect.addEventListener('click', event => {event.preventDefault(); open();});
  document.querySelector('#spotify-setup-close').addEventListener('click', () => dialog.close());
  document.querySelector('#spotify-setup-begin').addEventListener('click', () => showStep(1));
  document.querySelector('#spotify-have-id').addEventListener('click', () => showStep(3, true));
  document.querySelector('#spotify-created-app').addEventListener('click', () => showStep(2));
  document.querySelector('#spotify-saved-redirect').addEventListener('click', () => showStep(3));
  document.querySelector('#spotify-step-back').addEventListener('click', () => showStep(quick ? 0 : step - 1));
  document.querySelector('#spotify-need-help').addEventListener('click', () => showStep(1));
  document.addEventListener('rhyft:session-loaded', event => {
    const spotify = event.detail.spotify;
    if (spotify?.callbackUrl) redirect.value = spotify.callbackUrl;
    document.querySelector('#spotify-shared-option').hidden = spotify?.sharedClientAvailable === false;
  });
  document.addEventListener('rhyft:spotify-access-required', () => open(true));
  document.querySelector('#spotify-personal-form').addEventListener('submit', event => {
    event.preventDefault();
    const clientId = input.value.trim();
    if (!/^[0-9a-f]{32}$/i.test(clientId)) {
      error.textContent = 'Cole o Client ID de 32 caracteres que aparece no seu aplicativo Spotify.';
      error.hidden = false; input.focus();
      return;
    }
    try { localStorage.setItem(preferenceKey, clientId); } catch {}
    location.assign(`/api/spotify/start?${new URLSearchParams({client_id: clientId})}`);
  });
  document.querySelector('#spotify-copy-redirect').addEventListener('click', async event => {
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(redirect.value);
      button.textContent = 'Copiado';
    } catch {
      redirect.focus(); redirect.select();
      error.textContent = 'Endereço selecionado. Copie e cole em Redirect URIs.';
      error.hidden = false;
    }
  });
})();
