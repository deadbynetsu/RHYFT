(function (root, factory) {
  const pacing = factory();
  if (typeof module === 'object' && module.exports) module.exports = pacing;
  else root.RhyftProviderPacing = pacing;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const KEY = 'rhyft.web.provider-pacing.v1';
  const defaults = {youtube: 2000, spotify: 0};

  function create({storage, now = Date.now} = {}) {
    const memory = {};
    function read(provider) {
      let saved;
      try { saved = JSON.parse(storage?.getItem(KEY) || '{}')[provider]; } catch {}
      const candidates = [memory[provider], saved].filter(item => item && item.expiresAt > now());
      const state = {nextAt: 0, blockedUntil: 0, strikes: 0, intervalMs: defaults[provider] || 0, expiresAt: now() + 1800000};
      for (const item of candidates) {
        for (const key of Object.keys(state)) {
          if (Number.isFinite(item[key]) && item[key] >= 0) state[key] = Math.max(state[key], item[key]);
        }
      }
      return state;
    }
    function write(provider, state) {
      memory[provider] = state;
      try {
        const saved = JSON.parse(storage?.getItem(KEY) || '{}');
        saved[provider] = state;
        storage?.setItem(KEY, JSON.stringify(saved));
      } catch {}
    }
    function reserve(provider) {
      if (!(provider in defaults)) return now();
      const state = read(provider);
      const at = Math.max(now(), state.nextAt, state.blockedUntil);
      state.nextAt = at + state.intervalMs;
      state.expiresAt = Math.max(state.expiresAt, state.nextAt + 1800000);
      write(provider, state);
      return at;
    }
    function limited(provider, retryAfterMs = 0) {
      const state = read(provider);
      state.strikes = Math.min(state.strikes + 1, 8);
      const base = provider === 'youtube' ? 60000 : 30000;
      const delay = Math.max(base * 2 ** Math.min(state.strikes - 1, 2), Number.isFinite(retryAfterMs) ? retryAfterMs : 0);
      state.blockedUntil = Math.max(state.blockedUntil, now() + delay);
      state.intervalMs = provider === 'youtube' ? Math.min(2000 * 2 ** state.strikes, 10000) : 2000;
      state.expiresAt = Math.max(state.expiresAt, state.blockedUntil + 1800000);
      write(provider, state);
      return state.blockedUntil - now();
    }
    function remaining(provider, at) {
      const state = read(provider);
      return {ms: Math.max(0, Math.max(at, state.blockedUntil) - now()), blocked: state.blockedUntil > now()};
    }
    return {reserve, limited, remaining};
  }
  return {create};
});
