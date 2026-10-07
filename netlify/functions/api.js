const crypto = require('crypto');

const SPOTIFY_API = 'https://api.spotify.com/v1';
const SPOTIFY_ACCOUNTS = 'https://accounts.spotify.com';
const GOOGLE_ACCOUNTS = 'https://accounts.google.com';
const GOOGLE_TOKEN = 'https://oauth2.googleapis.com/token';
const YOUTUBE_API = 'https://www.googleapis.com/youtube/v3';
const GOOGLE_SCOPE = 'https://www.googleapis.com/auth/youtube';
const SPOTIFY_SCOPE = [
  'playlist-read-private',
  'playlist-read-collaborative',
  'playlist-modify-private',
  'playlist-modify-public',
  'user-read-private'
].join(' ');

class HttpError extends Error {
  constructor(status, message, code = 'API_ERROR', details = undefined) {
    super(message); this.status = status; this.code = code; this.details = details;
  }
}

function setupStatus() {
  const missing = [];
  if (!process.env.GOOGLE_CLIENT_ID) missing.push('GOOGLE_CLIENT_ID');
  if (!process.env.GOOGLE_CLIENT_SECRET) missing.push('GOOGLE_CLIENT_SECRET');
  if (!process.env.SESSION_SECRET || process.env.SESSION_SECRET.length < 32) missing.push('SESSION_SECRET (32+ caracteres)');
  return { ready: missing.length === 0, missing };
}

function requireEnv(...names) {
  const values = {};
  for (const name of names) {
    const value = process.env[name];
    if (!value || (name === 'SESSION_SECRET' && value.length < 32)) throw new HttpError(500, `Configuração ausente no servidor: ${name}.`, 'SETUP_REQUIRED');
    values[name] = value;
  }
  return values;
}

function origin(event) {
  if (process.env.SITE_URL) return process.env.SITE_URL.replace(/\/$/, '');
  const proto = event.headers['x-forwarded-proto'] || 'https';
  const host = event.headers.host;
  return `${proto}://${host}`;
}

function safeReturnTo(value) {
  const v = String(value || '/migrar.html');
  return /^\/[A-Za-z0-9_./?=&%-]*$/.test(v) && !v.startsWith('//') ? v : '/migrar.html';
}

function routeOf(path = '') {
  const parts = path.split('/').filter(Boolean);
  const idx = parts.lastIndexOf('api');
  return idx >= 0 ? parts.slice(idx + 1).join('/') : '';
}

function parseCookies(event) {
  const out = {};
  const raw = event.headers.cookie || event.headers.Cookie || '';
  raw.split(';').forEach(part => {
    const i = part.indexOf('=');
    if (i < 0) return;
    const key = part.slice(0, i).trim();
    try { out[key] = decodeURIComponent(part.slice(i + 1).trim()); } catch { out[key] = part.slice(i + 1).trim(); }
  });
  return out;
}

function secureAttr() {
  return process.env.NETLIFY_DEV === 'true' ? '' : '; Secure';
}

function cookie(name, value, maxAge) {
  return `${name}=${encodeURIComponent(value)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${secureAttr()}`;
}
function clearCookie(name) { return `${name}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${secureAttr()}`; }

function key() {
  const { SESSION_SECRET } = requireEnv('SESSION_SECRET');
  return crypto.createHash('sha256').update(SESSION_SECRET).digest();
}

function seal(value) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key(), iv);
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(value), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${iv.toString('base64url')}.${tag.toString('base64url')}.${encrypted.toString('base64url')}`;
}

function unseal(value) {
  if (!value) return null;
  try {
    const [iv64, tag64, data64] = value.split('.');
    if (!iv64 || !tag64 || !data64) return null;
    const decipher = crypto.createDecipheriv('aes-256-gcm', key(), Buffer.from(iv64, 'base64url'));
    decipher.setAuthTag(Buffer.from(tag64, 'base64url'));
    const plain = Buffer.concat([decipher.update(Buffer.from(data64, 'base64url')), decipher.final()]).toString('utf8');
    return JSON.parse(plain);
  } catch { return null; }
}

function json(statusCode, body, cookies = []) {
  const response = { statusCode, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }, body: JSON.stringify(body) };
  if (cookies.length) response.multiValueHeaders = { 'Set-Cookie': cookies };
  return response;
}

function redirect(location, cookies = []) {
  const response = { statusCode: 302, headers: { Location: location, 'Cache-Control': 'no-store' }, body: '' };
  if (cookies.length) response.multiValueHeaders = { 'Set-Cookie': cookies };
  return response;
}

function readBody(event) {
  if (!event.body) return {};
  try { return JSON.parse(event.body); } catch { throw new HttpError(400, 'Corpo JSON inválido.', 'BAD_JSON'); }
}

function assertSameOrigin(event) {
  const requestOrigin = event.headers.origin;
  if (!requestOrigin) return;
  try {
    if (new URL(requestOrigin).origin !== new URL(origin(event)).origin) throw new HttpError(403, 'Origem da solicitação não permitida.', 'CSRF_BLOCKED');
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(403, 'Origem da solicitação não permitida.', 'CSRF_BLOCKED');
  }
}

async function readJson(response) {
  const text = await response.text();
  if (!text) return {};
  try { return JSON.parse(text); } catch { return { raw: text.slice(0, 500) }; }
}

function providerMessage(provider, status, data) {
  if (provider === 'google') {
    const reason = data?.error?.errors?.[0]?.reason || data?.error?.status || '';
    const message = data?.error?.message || data?.error_description || (typeof data?.error === 'string' ? data.error : '');
    if (/quota|dailyLimit/i.test(`${reason} ${message || ''}`)) return 'A cota do YouTube para este projeto foi atingida. Tente novamente mais tarde.';
    if (status === 429 || /rateLimit/i.test(reason)) return 'O YouTube limitou temporariamente as requisições. Aguarde um pouco e tente novamente.';
    if (status === 401) return 'A sessão do Google expirou ou foi revogada. Conecte o Google / YouTube novamente.';
    if (status === 403) return message || 'O Google recusou esta operação. Verifique as permissões concedidas ao aplicativo.';
    return message || `O Google respondeu com erro ${status}.`;
  }
  const message = data?.error?.message || data?.error_description || data?.error?.status;
  if (status === 403 && /user (?:is )?not registered|not registered for this application/i.test(`${message || ''} ${data?.raw || ''}`)) {
    return 'Esta conta não está autorizada no aplicativo Spotify usado no login. O dono do app precisa adicioná-la em User Management no Spotify Developers, ou você pode conectar com seu próprio Client ID no RHYFT.';
  }
  if (status === 429) return 'O Spotify limitou temporariamente as requisições. Aguarde um pouco e tente novamente.';
  if (status === 401) return 'A sessão do Spotify expirou ou foi revogada. Conecte o Spotify novamente.';
  if (status === 403) return message || 'O Spotify recusou a operação. Playlists de origem precisam estar disponíveis para a sua conta.';
  return message || `O Spotify respondeu com erro ${status}.`;
}

function providerContext(url, options, provider) {
  // Only an operation name leaves the server; URLs, queries and credentials don't.
  const path = new URL(url).pathname;
  const method = (options?.method || 'GET').toUpperCase();
  let operation = 'request';
  if (path.endsWith('/search')) operation = 'search';
  else if (path.endsWith('/videos')) operation = 'track-details';
  else if (path.endsWith('/token')) operation = 'auth';
  else if (/\/(playlistItems|playlists\/[^/]+\/items)$/.test(path)) operation = method === 'GET' ? 'playlist-read' : 'playlist-add';
  else if (/\/playlists(?:\/[^/]+)?$/.test(path)) operation = method === 'GET' ? 'playlist-read' : 'playlist-create';
  else if (/\/(me|channels)$/.test(path)) operation = 'account';
  return { provider: provider === 'google' ? 'youtube' : provider, operation, method };
}

function providerReason(data) {
  const value = data?.error?.errors?.[0]?.reason || data?.error?.status || data?.error;
  return typeof value === 'string' && /^[A-Za-z0-9_.-]{1,80}$/.test(value) ? value : undefined;
}

async function providerFetch(url, options, provider) {
  // Only reads can be repeated safely. A failed POST may already have been applied.
  const context = providerContext(url, options, provider);
  const attempts = context.method === 'GET' ? 2 : 1;
  const label = provider === 'google' ? 'YouTube / Google' : 'Spotify';
  for (let attempt = 0; attempt < attempts; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 7000);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal });
      const data = await readJson(response);
      if (!response.ok) {
        const reason = providerReason(data);
        const originalMessage = typeof data?.error?.message === 'string' ? data.error.message : typeof data?.error === 'string' ? data.error : '';
        const accessOrLimit = [401, 403, 429].includes(response.status) || /quota|dailyLimit|rateLimit/i.test(reason || '');
        const aborted = !accessOrLimit && (reason === 'ABORTED' || /\b(?:aborted|cancelled|canceled)\b/i.test(originalMessage || data?.raw || ''));
        const timeout = !accessOrLimit && reason === 'DEADLINE_EXCEEDED';
        const genericAbort = /^(?:(?:the )?(?:operation|request) (?:was |has been )?)?(?:aborted|cancelled|canceled)[.!]?$/i;
        const message = timeout ? `O ${label} informou que o tempo de resposta foi excedido.`
          : aborted ? originalMessage && !genericAbort.test(originalMessage.trim())
            ? `O ${label} interrompeu a operação. Detalhe informado: ${originalMessage}`
            : `O ${label} interrompeu a operação antes de concluir; não informou o motivo da interrupção.`
          : providerMessage(provider, response.status, data);
        const error = new HttpError(response.status, message, `${provider.toUpperCase()}_${response.status}`,
          { ...context, cause: timeout ? 'timeout' : aborted ? 'aborted' : 'http', upstreamStatus: response.status, reason, attempts: attempt + 1 });
        // Access/quota errors must remain final even if their message mentions an abort.
        error.retryable = !accessOrLimit && ([500, 502, 503, 504].includes(response.status) || aborted || timeout);
        throw error;
      }
      if (!data || typeof data !== 'object' || Array.isArray(data) || data.raw !== undefined) {
        const error = new HttpError(502, `O ${label} devolveu uma resposta inválida.`, 'PROVIDER_INVALID_RESPONSE',
          { ...context, cause: 'invalid-response', upstreamStatus: response.status, attempts: attempt + 1 });
        error.retryable = true;
        throw error;
      }
      return data;
    } catch (error) {
      const temporary = !(error instanceof HttpError) || error.retryable;
      if (!temporary) throw error;
      if (attempt + 1 === attempts) {
        if (error instanceof HttpError) throw error;
        const timeout = controller.signal.aborted || error.name === 'TimeoutError';
        const aborted = !timeout && error.name === 'AbortError';
        const failure = new HttpError(timeout ? 504 : 502,
          timeout ? `O ${label} não respondeu dentro do prazo. Retome a migração para tentar novamente.`
            : aborted ? `A comunicação com o ${label} foi interrompida antes de receber a resposta. Retome para tentar novamente.`
            : `A conexão com o ${label} falhou. Retome a migração para tentar novamente.`,
          timeout ? 'PROVIDER_TIMEOUT' : aborted ? 'PROVIDER_ABORTED' : 'PROVIDER_UNAVAILABLE',
          { ...context, cause: timeout ? 'timeout' : aborted ? 'aborted' : 'network', attempts: attempt + 1,
            ...(controller.signal.aborted ? { timeoutMs: 7000 } : {}) });
        failure.retryable = true;
        throw failure;
      }
    } finally {
      clearTimeout(timer);
    }
    await new Promise(resolve => setTimeout(resolve, 300));
  }
}

function randomString(bytes = 32) { return crypto.randomBytes(bytes).toString('base64url'); }

function spotifyId(input) {
  const text = String(input || '').trim();
  const match = text.match(/open\.spotify\.com\/playlist\/([A-Za-z0-9]{10,30})/i) || text.match(/^spotify:playlist:([A-Za-z0-9]{10,30})$/i) || text.match(/^([A-Za-z0-9]{10,30})$/);
  if (!match) throw new HttpError(400, 'Não entendi o link ou ID da playlist do Spotify.', 'INVALID_PLAYLIST');
  return match[1];
}

function youtubePlaylistId(input) {
  const text = String(input || '').trim();
  const match = text.match(/[?&]list=([A-Za-z0-9_-]{10,})/) || text.match(/^([A-Za-z0-9_-]{10,})$/);
  if (!match) throw new HttpError(400, 'Não entendi o link ou ID da playlist do YouTube.', 'INVALID_PLAYLIST');
  return match[1];
}

function parseDuration(value) {
  const m = String(value || '').match(/^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/);
  if (!m) return null;
  return (Number(m[1] || 0) * 86400) + (Number(m[2] || 0) * 3600) + (Number(m[3] || 0) * 60) + Number(m[4] || 0);
}

async function refreshSpotify(session) {
  const clientId = spotifyClientId(session?.client_id || process.env.SPOTIFY_CLIENT_ID);
  if (!session?.refresh_token) throw new HttpError(401, 'Spotify não conectado.', 'AUTH_REQUIRED');
  const body = new URLSearchParams({ grant_type: 'refresh_token', refresh_token: session.refresh_token, client_id: clientId });
  let data;
  try {
    data = await providerFetch(`${SPOTIFY_ACCOUNTS}/api/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body }, 'spotify');
  } catch (error) {
    if ([400, 401, 403].includes(error.status)) throw new HttpError(401, 'Não consegui renovar a sessão do Spotify. Conecte novamente.', 'AUTH_REQUIRED', error.details);
    throw error;
  }
  return { ...session, ...data, client_id: clientId, refresh_token: data.refresh_token || session.refresh_token, expires_at: Date.now() + Number(data.expires_in || 3600) * 1000 };
}

async function spotifyAuth(event, optional = false) {
  const session = unseal(parseCookies(event).sp_session);
  if (!session) {
    if (optional) return null;
    throw new HttpError(401, 'Spotify não conectado.', 'AUTH_REQUIRED');
  }
  let current = session, setCookies = [];
  if (Number(current.expires_at || 0) < Date.now() + 90000) {
    try {
      current = await refreshSpotify(current);
      setCookies.push(cookie('sp_session', seal(current), 60 * 60 * 24 * 30));
    } catch (error) {
      if (optional) return null;
      throw error;
    }
  }
  return { accessToken: current.access_token, session: current, setCookies };
}

async function refreshGoogle(session) {
  const { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET } = requireEnv('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET');
  if (!session?.refresh_token) throw new HttpError(401, 'Google / YouTube não conectado.', 'AUTH_REQUIRED');
  const body = new URLSearchParams({ client_id: GOOGLE_CLIENT_ID, client_secret: GOOGLE_CLIENT_SECRET, refresh_token: session.refresh_token, grant_type: 'refresh_token' });
  let data;
  try {
    data = await providerFetch(GOOGLE_TOKEN, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body }, 'google');
  } catch (error) {
    if ([400, 401, 403].includes(error.status)) throw new HttpError(401, 'Não consegui renovar a sessão do Google. Conecte novamente.', 'AUTH_REQUIRED', error.details);
    throw error;
  }
  return { ...session, ...data, refresh_token: session.refresh_token, expires_at: Date.now() + Number(data.expires_in || 3600) * 1000 };
}

async function googleAuth(event, optional = false) {
  const session = unseal(parseCookies(event).g_session);
  if (!session) {
    if (optional) return null;
    throw new HttpError(401, 'Google / YouTube não conectado.', 'AUTH_REQUIRED');
  }
  let current = session, setCookies = [];
  if (Number(current.expires_at || 0) < Date.now() + 90000) {
    try {
      current = await refreshGoogle(current);
      setCookies.push(cookie('g_session', seal(current), 60 * 60 * 24 * 30));
    } catch (error) {
      if (optional) return null;
      throw error;
    }
  }
  return { accessToken: current.access_token, session: current, setCookies };
}

function bearer(token) { return { Authorization: `Bearer ${token}` }; }

function spotifyClientId(value) {
  const id = String(value || '').trim();
  if (!/^[0-9a-f]{32}$/i.test(id)) throw new HttpError(400, 'Informe um Client ID válido do Spotify (32 caracteres, somente o ID). O Client Secret não é necessário.', 'INVALID_CLIENT_ID');
  return id;
}

async function spotifyStart(event) {
  requireEnv('SESSION_SECRET');
  const clientId = spotifyClientId(event.queryStringParameters?.client_id ?? process.env.SPOTIFY_CLIENT_ID);
  const state = randomString(24), verifier = randomString(48);
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  const callback = `${origin(event)}/api/spotify/callback`;
  const returnTo = safeReturnTo(event.queryStringParameters?.return);
  const params = new URLSearchParams({ client_id: clientId, response_type: 'code', redirect_uri: callback, scope: SPOTIFY_SCOPE, state, code_challenge_method: 'S256', code_challenge: challenge, show_dialog: 'true' });
  return redirect(`${SPOTIFY_ACCOUNTS}/authorize?${params}`, [cookie('sp_oauth', seal({ state, verifier, clientId, returnTo, created: Date.now() }), 600)]);
}

async function spotifyCallback(event) {
  requireEnv('SESSION_SECRET');
  const saved = unseal(parseCookies(event).sp_oauth);
  const returnTo = safeReturnTo(saved?.returnTo);
  if (!saved || Date.now() - Number(saved.created || 0) > 10 * 60 * 1000) return redirect(`${returnTo}?auth_error=${encodeURIComponent('A tentativa de login do Spotify expirou. Tente novamente.')}`, [clearCookie('sp_oauth')]);
  if (event.queryStringParameters?.error) return redirect(`${returnTo}?auth_error=${encodeURIComponent('A autorização do Spotify foi cancelada.')}`, [clearCookie('sp_oauth')]);
  if (event.queryStringParameters?.state !== saved.state || !event.queryStringParameters?.code) return redirect(`${returnTo}?auth_error=${encodeURIComponent('Resposta de login do Spotify inválida.')}`, [clearCookie('sp_oauth')]);
  // Bind token exchange to the signed login request, never to callback input.
  const clientId = spotifyClientId(saved.clientId || process.env.SPOTIFY_CLIENT_ID);
  const callback = `${origin(event)}/api/spotify/callback`;
  const body = new URLSearchParams({ client_id: clientId, grant_type: 'authorization_code', code: event.queryStringParameters.code, redirect_uri: callback, code_verifier: saved.verifier });
  const response = await fetch(`${SPOTIFY_ACCOUNTS}/api/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
  const token = await readJson(response);
  if (!response.ok) return redirect(`${returnTo}?auth_error=${encodeURIComponent('O Spotify recusou a autorização. Tente novamente.')}`, [clearCookie('sp_oauth')]);
  let me;
  try { me = await providerFetch(`${SPOTIFY_API}/me`, { headers: bearer(token.access_token) }, 'spotify'); }
  catch (error) {
    const message = error instanceof HttpError ? error.message : 'Não consegui verificar sua conta Spotify. Tente conectar novamente.';
    return redirect(`${returnTo}?auth_error=${encodeURIComponent(message)}`, [clearCookie('sp_session'), clearCookie('sp_oauth')]);
  }
  const session = { ...token, client_id: clientId, user_id: me.id, name: me.display_name || me.id || 'Conta Spotify', expires_at: Date.now() + Number(token.expires_in || 3600) * 1000 };
  return redirect(`${returnTo}?auth=spotify-ok`, [cookie('sp_session', seal(session), 60 * 60 * 24 * 30), clearCookie('sp_oauth')]);
}

async function googleStart(event) {
  const { GOOGLE_CLIENT_ID } = requireEnv('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'SESSION_SECRET');
  const state = randomString(24), callback = `${origin(event)}/api/google/callback`;
  const returnTo = safeReturnTo(event.queryStringParameters?.return);
  const params = new URLSearchParams({ client_id: GOOGLE_CLIENT_ID, redirect_uri: callback, response_type: 'code', scope: GOOGLE_SCOPE, access_type: 'offline', include_granted_scopes: 'true', prompt: 'consent', state });
  return redirect(`${GOOGLE_ACCOUNTS}/o/oauth2/v2/auth?${params}`, [cookie('g_oauth', seal({ state, returnTo, created: Date.now() }), 600)]);
}

async function googleCallback(event) {
  const { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET } = requireEnv('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'SESSION_SECRET');
  const saved = unseal(parseCookies(event).g_oauth);
  const returnTo = safeReturnTo(saved?.returnTo);
  if (!saved || Date.now() - Number(saved.created || 0) > 10 * 60 * 1000) return redirect(`${returnTo}?auth_error=${encodeURIComponent('A tentativa de login do Google expirou. Tente novamente.')}`, [clearCookie('g_oauth')]);
  if (event.queryStringParameters?.error) return redirect(`${returnTo}?auth_error=${encodeURIComponent('A autorização do Google foi cancelada.')}`, [clearCookie('g_oauth')]);
  if (event.queryStringParameters?.state !== saved.state || !event.queryStringParameters?.code) return redirect(`${returnTo}?auth_error=${encodeURIComponent('Resposta de login do Google inválida.')}`, [clearCookie('g_oauth')]);
  const callback = `${origin(event)}/api/google/callback`;
  const body = new URLSearchParams({ client_id: GOOGLE_CLIENT_ID, client_secret: GOOGLE_CLIENT_SECRET, code: event.queryStringParameters.code, grant_type: 'authorization_code', redirect_uri: callback });
  const response = await fetch(GOOGLE_TOKEN, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
  const token = await readJson(response);
  if (!response.ok || !token.access_token) return redirect(`${returnTo}?auth_error=${encodeURIComponent('O Google recusou a autorização. Tente novamente.')}`, [clearCookie('g_oauth')]);
  let name = 'Conta Google / YouTube';
  try {
    const channels = await providerFetch(`${YOUTUBE_API}/channels?part=snippet&mine=true&maxResults=1`, { headers: bearer(token.access_token) }, 'google');
    name = channels.items?.[0]?.snippet?.title || name;
  } catch {}
  const previous = unseal(parseCookies(event).g_session);
  const session = { ...token, refresh_token: token.refresh_token || previous?.refresh_token, name, expires_at: Date.now() + Number(token.expires_in || 3600) * 1000 };
  if (!session.refresh_token) return redirect(`${returnTo}?auth_error=${encodeURIComponent('O Google não forneceu acesso offline. Desconecte o app na Conta Google e conecte novamente.')}`, [clearCookie('g_oauth')]);
  return redirect(`${returnTo}?auth=google-ok`, [cookie('g_session', seal(session), 60 * 60 * 24 * 30), clearCookie('g_oauth')]);
}

async function sessionRoute(event) {
  const setup = setupStatus();
  const spotifySetup = { sharedClientAvailable: /^[0-9a-f]{32}$/i.test(process.env.SPOTIFY_CLIENT_ID || ''), callbackUrl: `${origin(event)}/api/spotify/callback` };
  if (!process.env.SESSION_SECRET || process.env.SESSION_SECRET.length < 32) return json(200, { setup, spotify: { ...spotifySetup, connected: false }, youtube: { connected: false } });
  const cookies = [];
  const sp = await spotifyAuth(event, true); const yt = await googleAuth(event, true);
  if (sp?.setCookies) cookies.push(...sp.setCookies); if (yt?.setCookies) cookies.push(...yt.setCookies);
  return json(200, { setup, spotify: { ...spotifySetup, connected: !!sp, name: sp?.session?.name || null }, youtube: { connected: !!yt, name: yt?.session?.name || null } }, cookies);
}

async function logoutRoute(event) {
  assertSameOrigin(event);
  const provider = readBody(event).provider;
  const cookies = [];
  if (!provider || provider === 'spotify' || provider === 'all') cookies.push(clearCookie('sp_session'));
  if (!provider || provider === 'google' || provider === 'all') cookies.push(clearCookie('g_session'));
  return json(200, { ok: true }, cookies);
}

async function spotifyPlaylistRoute(event) {
  const auth = await spotifyAuth(event); const id = spotifyId(event.queryStringParameters?.input);
  let name = 'Playlist do Spotify', url = `https://open.spotify.com/playlist/${id}`;
  try {
    const info = await providerFetch(`${SPOTIFY_API}/playlists/${id}`, { headers: bearer(auth.accessToken) }, 'spotify');
    name = info.name || name; url = info.external_urls?.spotify || url;
  } catch {}
  const tracks = []; let offset = 0, truncated = false;
  while (tracks.length < 2000) {
    const data = await providerFetch(`${SPOTIFY_API}/playlists/${id}/items?limit=50&offset=${offset}`, { headers: bearer(auth.accessToken) }, 'spotify');
    const items = data.items || [];
    for (const entry of items) {
      const t = entry.item || entry.track || entry;
      if (!t || t.type !== 'track' || !t.id || !t.name) continue;
      tracks.push({ id: t.id, name: t.name, artists: (t.artists || []).map(a => a.name).filter(Boolean), duration: t.duration_ms ? Math.round(t.duration_ms / 1000) : null, uri: t.uri || `spotify:track:${t.id}`, url: t.external_urls?.spotify || null });
      if (tracks.length >= 2000) break;
    }
    offset += items.length;
    if (!items.length || !data.next) break;
  }
  if (tracks.length >= 2000) truncated = true;
  return json(200, { id, name, url, tracks, truncated }, auth.setCookies);
}

async function spotifySearchRoute(event) {
  const auth = await spotifyAuth(event); const q = String(event.queryStringParameters?.q || '').trim().slice(0, 200);
  if (!q) throw new HttpError(400, 'Busca vazia.', 'BAD_QUERY');
  const data = await providerFetch(`${SPOTIFY_API}/search?type=track&limit=10&q=${encodeURIComponent(q)}`, { headers: bearer(auth.accessToken) }, 'spotify');
  const items = (data.tracks?.items || []).filter(Boolean).map(t => ({ id: t.id, name: t.name, artists: (t.artists || []).map(a => a.name).filter(Boolean), duration: t.duration_ms ? Math.round(t.duration_ms / 1000) : null, uri: t.uri || `spotify:track:${t.id}`, url: t.external_urls?.spotify || null }));
  return json(200, { items }, auth.setCookies);
}

async function spotifyCreateRoute(event) {
  assertSameOrigin(event); const auth = await spotifyAuth(event); const body = readBody(event); const name = String(body.name || '').trim().slice(0, 100);
  if (!name) throw new HttpError(400, 'Nome da playlist vazio.', 'BAD_NAME');
  const data = await providerFetch(`${SPOTIFY_API}/me/playlists`, { method: 'POST', headers: { ...bearer(auth.accessToken), 'Content-Type': 'application/json' }, body: JSON.stringify({ name, public: false, description: 'Criada pelo RHYFT' }) }, 'spotify');
  return json(201, { id: data.id, name: data.name || name, url: data.external_urls?.spotify || `https://open.spotify.com/playlist/${data.id}` }, auth.setCookies);
}

async function spotifyAddItemsRoute(event) {
  assertSameOrigin(event); const auth = await spotifyAuth(event); const body = readBody(event); const playlistId = spotifyId(body.playlistId);
  const uris = Array.isArray(body.uris) ? body.uris.map(String).filter(x => /^spotify:track:[A-Za-z0-9]+$/.test(x)).slice(0, 100) : [];
  if (!uris.length) throw new HttpError(400, 'Nenhuma faixa válida para adicionar.', 'BAD_ITEMS');
  const data = await providerFetch(`${SPOTIFY_API}/playlists/${playlistId}/items`, { method: 'POST', headers: { ...bearer(auth.accessToken), 'Content-Type': 'application/json' }, body: JSON.stringify({ uris }) }, 'spotify');
  return json(201, { ok: true, snapshotId: data.snapshot_id || null }, auth.setCookies);
}

async function youtubePlaylistRoute(event) {
  const auth = await googleAuth(event); const id = youtubePlaylistId(event.queryStringParameters?.input);
  const info = await providerFetch(`${YOUTUBE_API}/playlists?part=snippet&id=${encodeURIComponent(id)}&maxResults=1`, { headers: bearer(auth.accessToken) }, 'google');
  const first = info.items?.[0];
  if (!first) throw new HttpError(404, 'Playlist do YouTube não encontrada ou não acessível.', 'NOT_FOUND');
  const raw = []; let pageToken = '', truncated = false;
  while (raw.length < 2000) {
    const params = new URLSearchParams({ part: 'snippet,contentDetails', playlistId: id, maxResults: '50' });
    if (pageToken) params.set('pageToken', pageToken);
    const data = await providerFetch(`${YOUTUBE_API}/playlistItems?${params}`, { headers: bearer(auth.accessToken) }, 'google');
    for (const item of data.items || []) {
      const videoId = item.contentDetails?.videoId || item.snippet?.resourceId?.videoId;
      if (!videoId) continue;
      raw.push({ id: videoId, title: item.snippet?.title || '', channel: item.snippet?.videoOwnerChannelTitle || item.snippet?.channelTitle || '' });
      if (raw.length >= 2000) break;
    }
    pageToken = data.nextPageToken || '';
    if (!pageToken) break;
  }
  if (raw.length >= 2000) truncated = true;
  const details = new Map();
  for (let i = 0; i < raw.length; i += 50) {
    const ids = raw.slice(i, i + 50).map(x => x.id).join(',');
    const data = await providerFetch(`${YOUTUBE_API}/videos?part=snippet,contentDetails&id=${encodeURIComponent(ids)}`, { headers: bearer(auth.accessToken) }, 'google');
    (data.items || []).forEach(v => details.set(v.id, v));
  }
  const tracks = raw.map(item => {
    const v = details.get(item.id); const title = v?.snippet?.title || item.title; const channel = v?.snippet?.channelTitle || item.channel;
    const dash = title.match(/^(.+?)\s+-\s+(.+)$/); const artists = dash ? [dash[1].trim()] : (channel ? [channel.replace(/\s*-\s*Topic$/i, '')] : []);
    return { id: item.id, name: dash ? dash[2].trim() : title, title, artists, artist: artists.join(', '), channel, duration: parseDuration(v?.contentDetails?.duration), url: `https://www.youtube.com/watch?v=${item.id}` };
  }).filter(t => t.name && !/^deleted video$|^private video$/i.test(t.name));
  return json(200, { id, name: first.snippet?.title || 'Playlist do YouTube', url: `https://www.youtube.com/playlist?list=${id}`, tracks, truncated }, auth.setCookies);
}

async function youtubeSearchRoute(event) {
  const auth = await googleAuth(event); const q = String(event.queryStringParameters?.q || '').trim().slice(0, 180);
  if (!q) throw new HttpError(400, 'Busca vazia.', 'BAD_QUERY');
  const params = new URLSearchParams({ part: 'snippet', type: 'video', maxResults: '10', q });
  const search = await providerFetch(`${YOUTUBE_API}/search?${params}`, { headers: bearer(auth.accessToken) }, 'google');
  const ids = (search.items || []).map(x => x.id?.videoId).filter(Boolean);
  if (!ids.length) return json(200, { items: [] }, auth.setCookies);
  const details = await providerFetch(`${YOUTUBE_API}/videos?part=snippet,contentDetails&id=${encodeURIComponent(ids.join(','))}`, { headers: bearer(auth.accessToken) }, 'google');
  const map = new Map((details.items || []).map(v => [v.id, v]));
  const items = ids.map(id => map.get(id)).filter(Boolean).map(v => ({ id: v.id, title: v.snippet?.title || '', channel: v.snippet?.channelTitle || '', duration: parseDuration(v.contentDetails?.duration), url: `https://www.youtube.com/watch?v=${v.id}` }));
  return json(200, { items }, auth.setCookies);
}

async function youtubeCreateRoute(event) {
  assertSameOrigin(event); const auth = await googleAuth(event); const body = readBody(event); const name = String(body.name || '').trim().slice(0, 150);
  if (!name) throw new HttpError(400, 'Nome da playlist vazio.', 'BAD_NAME');
  const data = await providerFetch(`${YOUTUBE_API}/playlists?part=snippet,status`, { method: 'POST', headers: { ...bearer(auth.accessToken), 'Content-Type': 'application/json' }, body: JSON.stringify({ snippet: { title: name, description: 'Criada pelo RHYFT' }, status: { privacyStatus: 'private' } }) }, 'google');
  return json(201, { id: data.id, name: data.snippet?.title || name, url: `https://www.youtube.com/playlist?list=${data.id}` }, auth.setCookies);
}

async function youtubeAddItemRoute(event) {
  assertSameOrigin(event); const auth = await googleAuth(event); const body = readBody(event); const playlistId = youtubePlaylistId(body.playlistId); const videoId = String(body.videoId || '').trim();
  if (!/^[A-Za-z0-9_-]{6,20}$/.test(videoId)) throw new HttpError(400, 'ID do vídeo inválido.', 'BAD_VIDEO');
  const data = await providerFetch(`${YOUTUBE_API}/playlistItems?part=snippet`, { method: 'POST', headers: { ...bearer(auth.accessToken), 'Content-Type': 'application/json' }, body: JSON.stringify({ snippet: { playlistId, resourceId: { kind: 'youtube#video', videoId } } }) }, 'google');
  return json(201, { ok: true, id: data.id || null }, auth.setCookies);
}

// Read the destination in pages without fetching music metadata or truncating IDs.
// The browser uses these IDs to reconcile uncertain writes and avoid duplicates.
async function destinationStateRoute(event, provider) {
  const query = event.queryStringParameters || {};
  const isSpotify = provider === 'spotify';
  const auth = isSpotify ? await spotifyAuth(event) : await googleAuth(event);
  const id = isSpotify ? spotifyId(query.input) : youtubePlaylistId(query.input);
  const headers = { headers: bearer(auth.accessToken) };
  let name, ids, nextPage = null;
  if (isSpotify) {
    const offset = query.page === undefined ? 0 : Number(query.page);
    if (!Number.isSafeInteger(offset) || offset < 0) throw new HttpError(400, 'Página inválida.', 'BAD_PAGE');
    if (!offset) {
      const info = await providerFetch(`${SPOTIFY_API}/playlists/${id}`, headers, 'spotify');
      name = info.name;
    }
    const data = await providerFetch(`${SPOTIFY_API}/playlists/${id}/items?limit=50&offset=${offset}`, headers, 'spotify');
    const items = data.items || [];
    ids = items.map(entry => (entry.item || entry.track || entry)?.id).filter(Boolean);
    if (data.next && items.length) nextPage = String(offset + items.length);
  } else {
    if (!query.page) {
      const info = await providerFetch(`${YOUTUBE_API}/playlists?part=snippet&id=${encodeURIComponent(id)}&maxResults=1`, headers, 'google');
      if (!info.items?.[0]) throw new HttpError(404, 'Playlist do YouTube não encontrada ou não acessível.', 'NOT_FOUND');
      name = info.items[0].snippet?.title;
    }
    const params = new URLSearchParams({ part: 'contentDetails', playlistId: id, maxResults: '50' });
    if (query.page) params.set('pageToken', query.page);
    const data = await providerFetch(`${YOUTUBE_API}/playlistItems?${params}`, headers, 'google');
    ids = (data.items || []).map(item => item.contentDetails?.videoId).filter(Boolean);
    nextPage = data.nextPageToken || null;
  }
  const url = isSpotify ? `https://open.spotify.com/playlist/${id}` : `https://www.youtube.com/playlist?list=${id}`;
  return json(200, { id, name, url, ids, nextPage }, auth.setCookies);
}

exports.handler = async (event) => {
  try {
    const route = routeOf(event.path), method = event.httpMethod || 'GET';
    if (method === 'OPTIONS') return { statusCode: 204, body: '' };
    if (route === 'session' && method === 'GET') return await sessionRoute(event);
    if (route === 'spotify/start' && method === 'GET') return await spotifyStart(event);
    if (route === 'spotify/callback' && method === 'GET') return await spotifyCallback(event);
    if (route === 'google/start' && method === 'GET') return await googleStart(event);
    if (route === 'google/callback' && method === 'GET') return await googleCallback(event);
    if (route === 'logout' && method === 'POST') return await logoutRoute(event);
    if (route === 'spotify/playlist' && method === 'GET') return await spotifyPlaylistRoute(event);
    if (route === 'spotify/playlist/state' && method === 'GET') return await destinationStateRoute(event, 'spotify');
    if (route === 'spotify/search' && method === 'GET') return await spotifySearchRoute(event);
    if (route === 'spotify/playlist' && method === 'POST') return await spotifyCreateRoute(event);
    if (route === 'spotify/playlist/items' && method === 'POST') return await spotifyAddItemsRoute(event);
    if (route === 'youtube/playlist' && method === 'GET') return await youtubePlaylistRoute(event);
    if (route === 'youtube/playlist/state' && method === 'GET') return await destinationStateRoute(event, 'youtube');
    if (route === 'youtube/search' && method === 'GET') return await youtubeSearchRoute(event);
    if (route === 'youtube/playlist' && method === 'POST') return await youtubeCreateRoute(event);
    if (route === 'youtube/playlist/item' && method === 'POST') return await youtubeAddItemRoute(event);
    return json(404, { error: 'Endpoint não encontrado.', code: 'NOT_FOUND' });
  } catch (error) {
    const status = error instanceof HttpError ? error.status : 500;
    const message = error instanceof HttpError ? error.message : 'O servidor encontrou um erro inesperado.';
    if (!(error instanceof HttpError)) console.error(error);
    return json(status, { error: message, code: error.code || 'INTERNAL_ERROR',
      ...(error instanceof HttpError ? { details: error.details, retryable: error.retryable } : {}) });
  }
};
