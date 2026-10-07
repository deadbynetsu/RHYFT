(() => {
  const dialog = document.querySelector('#spotify-setup');
  const connect = document.querySelector('#spotify-connect');
  const input = document.querySelector('#spotify-client-id');
  const redirect = document.querySelector('#spotify-redirect');
  const error = document.querySelector('#spotify-setup-error');
  const preferenceKey = 'rhyft.web.spotify-client-id.v1';
  if (!dialog || !connect || !input) return;

  // Client IDs are public app identifiers. Tokens stay in HttpOnly cookies.
  try { input.value = localStorage.getItem(preferenceKey) || ''; } catch {}
  redirect.value = `${location.origin}/api/spotify/callback`;
  document.querySelector('#spotify-tutorial').open = !input.value;

  function open(message = '') {
    error.textContent = message;
    error.hidden = !message;
    if (!dialog.open) dialog.showModal();
    input.focus();
  }

  connect.addEventListener('click', event => {
    event.preventDefault();
    open();
  });
  document.querySelector('#spotify-setup-close').addEventListener('click', () => dialog.close());
  document.addEventListener('rhyft:session-loaded', event => {
    const spotify = event.detail.spotify;
    if (spotify?.callbackUrl) redirect.value = spotify.callbackUrl;
    document.querySelector('#spotify-shared-option').hidden = spotify?.sharedClientAvailable === false;
  });
  document.addEventListener('rhyft:spotify-access-required', event => {
    document.querySelector('#spotify-tutorial').open = true;
    open(event.detail);
  });
  document.querySelector('#spotify-personal-form').addEventListener('submit', event => {
    event.preventDefault();
    const clientId = input.value.trim();
    if (!/^[0-9a-f]{32}$/i.test(clientId)) return open('Cole somente o Client ID do Spotify: 32 caracteres. Não use o Client Secret.');
    try { localStorage.setItem(preferenceKey, clientId); } catch {}
    const params = new URLSearchParams({client_id: clientId});
    location.assign(`/api/spotify/start?${params}`);
  });
  document.querySelector('#spotify-copy-redirect').addEventListener('click', async event => {
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(redirect.value);
      button.textContent = 'Copiado';
    } catch {
      redirect.focus(); redirect.select();
      error.textContent = 'Selecionei o endereço. Copie e cole em Redirect URIs no Spotify Developers.';
      error.hidden = false;
    }
  });
})();
