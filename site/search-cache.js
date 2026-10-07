(function (root, factory) {
  const cache = factory();
  if (typeof module === 'object' && module.exports) module.exports = cache;
  else root.RhyftSearchCache = cache;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const KEY = 'rhyft.web.search-cache.v1';
  const providers = ['youtube', 'spotify'];
  function create({storage, now = Date.now} = {}) {
    let memory = {};
    function read() {
      try {
        const saved = JSON.parse(storage?.getItem(KEY) || '{}');
        if (saved && typeof saved === 'object' && !Array.isArray(saved)) memory = {...memory, ...saved};
      } catch {}
      for (const [key, value] of Object.entries(memory)) if (!value || value.expiresAt <= now()) delete memory[key];
      return memory;
    }
    function write() { try { storage?.setItem(KEY, JSON.stringify(memory)); } catch {} }
    function key(provider, query) { return `${provider}:${query.trim().normalize('NFKC').toLowerCase().replace(/\s+/g, ' ')}`; }
    function get(provider, query) {
      if (!providers.includes(provider)) return null;
      const value = read()[key(provider, query)];
      return value && Number.isFinite(value.expiresAt) && value.expiresAt > now() && Array.isArray(value.items) ? {items: value.items} : null;
    }
    function set(provider, query, response) {
      if (!providers.includes(provider) || !Array.isArray(response?.items)) return;
      read();
      const items = response.items.slice(0, 10).filter(item => item && typeof item.id === 'string').map(item => {
        const clean = {id: item.id.slice(0, 100)};
        for (const field of ['title', 'name', 'channel', 'uri', 'url']) if (typeof item[field] === 'string') clean[field] = item[field].slice(0, 500);
        if (Array.isArray(item.artists)) clean.artists = item.artists.filter(artist => typeof artist === 'string').slice(0, 10).map(artist => artist.slice(0, 200));
        if (Number.isFinite(item.duration) && item.duration > 0) clean.duration = item.duration;
        return clean;
      });
      memory[key(provider, query)] = {items, savedAt: now(), expiresAt: now() + (items.length ? 21600000 : 300000)};
      const keys = Object.keys(memory).sort((a, b) => memory[b].savedAt - memory[a].savedAt);
      for (const key of keys.slice(100)) delete memory[key];
      write();
    }
    function clear(provider) {
      read();
      for (const key of Object.keys(memory)) if (key.startsWith(`${provider}:`)) delete memory[key];
      write();
    }
    return {get, set, clear};
  }
  return {create};
});
