(() => {
  const key = 'rhyft.web.history.v1';

  function playlistId(direction, input, destination = false) {
    const spotify = (direction === 'spotify-youtube') !== destination;
    const text = String(input || '').trim();
    const match = spotify
      ? text.match(/open\.spotify\.com\/playlist\/([A-Za-z0-9]{10,30})/i) || text.match(/^spotify:playlist:([A-Za-z0-9]{10,30})$/i) || text.match(/^([A-Za-z0-9]{10,30})$/)
      : text.match(/[?&]list=([A-Za-z0-9_-]{10,})/) || text.match(/^([A-Za-z0-9_-]{10,})$/);
    return match?.[1] || null;
  }

  function load() {
    try {
      const items = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(items) ? items.filter(item => item?.id && item.createdAt) : [];
    } catch { return []; }
  }

  function save(record) {
    const items = load().filter(item => item.id !== record.id);
    // Keep a migration checkpoint as soon as a destination exists, not just at completion.
    localStorage.setItem(key, JSON.stringify([record, ...items].slice(0, 100)));
    document.dispatchEvent(new CustomEvent('rhyft:history-updated'));
  }

  function find(direction, input) {
    const id = playlistId(direction, input);
    if (!id) return null;
    return load().find(item => item.direction === direction &&
      playlistId(direction, item.sourceInput) === id &&
      playlistId(direction, item.destinationId || item.destinationUrl, true)) || null;
  }

  window.RhyftMigrationStore = { load, save, find, playlistId };
})();
