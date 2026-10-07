((root, factory) => {
  const matching = factory();
  if (typeof module === 'object' && module.exports) module.exports = matching;
  else root.RhyftMusicMatching = matching;
})(typeof window === 'object' ? window : globalThis, () => {
  const VERSION = 2;
  const modifiers = [
    /\b(?:live|ao vivo|en vivo)\b/u, /\bremix\b/u, /\bcover\b/u,
    /\b(?:acoustic|acustic[oa])\b/u, /\binstrumental\b/u, /\bkaraoke\b/u,
    /\b(?:slowed|slow and reverb)\b/u, /\b(?:sped up|speed up|speedup)\b/u, /\bnightcore\b/u,
    /\breverb\b/u, /\b(?:acapella|a cappella)\b/u, /\b(?:lofi|lo fi)\b/u,
    /\b8d\b/u, /\bmashup\b/u, /\btribute\b/u, /\bdemo\b/u
  ];

  function plain(value) {
    return String(value || '').replace(/&amp;/gi, '&').replace(/&quot;/gi, '"').replace(/&#0?39;|&apos;/gi, "'")
      .normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim();
  }

  function cleanTitle(value) {
    return String(value || '').replace(/\(([^)]*)\)|\[([^\]]*)\]/g, (whole, a, b) => {
      const text = plain(a || b);
      return /^(?:feat|ft|featuring|prod|produced|official|oficial|music video|audio|lyrics?|lyric video|visualizer|video oficial|hd|4k|sub|subtitulos|subtitles|remaster(?:ed)?)(?:\b|$)/u.test(text) ? ' ' : ` ${a || b} `;
    }).replace(/\b(?:feat|ft|featuring)\.?\s+.*$/i, '')
      .replace(/\b(?:official|oficial|music video|audio|lyrics?|visualizer|hd|4k)\b/gi, ' ')
      .replace(/\s*[-–—]\s*remaster(?:ed)?(?:\s+\d{4})?.*$/i, ' ').trim();
  }

  function artistName(value) {
    return plain(value).replace(/\s+(?:topic|official|oficial)$/u, '').replace(/vevo$/u, '').trim();
  }

  function artistVariants(value) {
    const firstCredit = String(value || '').split(/\s*[&,]\s*|\s+(?:x|w\/|with|feat\.?)\s+/i)[0];
    return [...new Set([value, firstCredit].filter(Boolean))];
  }

  function includesArtist(text, artist) {
    return !!artist && ` ${text} `.includes(` ${artist} `);
  }

  function similarity(a, b) {
    if (!a || !b) return 0;
    if (a === b) return 1;
    if (a.replace(/ /g, '') === b.replace(/ /g, '')) return .97;
    // Compare code points so non-Latin titles survive normalization and comparison.
    const aa = Array.from(a), bb = Array.from(b);
    const row = Array.from({length: bb.length + 1}, (_, i) => i);
    for (let i = 1; i <= aa.length; i++) {
      let diag = row[0]; row[0] = i;
      for (let j = 1; j <= bb.length; j++) {
        const up = row[j];
        row[j] = aa[i - 1] === bb[j - 1] ? diag : Math.min(diag, up, row[j - 1]) + 1;
        diag = up;
      }
    }
    const edit = 1 - row[bb.length] / Math.max(aa.length, bb.length);
    const at = new Set(a.split(' ')), bt = new Set(b.split(' '));
    const overlap = [...at].filter(token => bt.has(token)).length;
    return edit * .65 + (2 * overlap / (at.size + bt.size)) * .35;
  }

  function titleVariants(title, artists) {
    const full = plain(cleanTitle(title));
    const names = [...new Set(artists.map(artistName).filter(Boolean))].sort((a, b) => b.length - a.length);
    const variants = new Set([full]);
    const segments = String(title || '').split(/\s+[-–—|]\s+/u);
    if (segments.length > 1) {
      const first = plain(segments[0]), last = plain(segments.at(-1));
      if (names.some(name => includesArtist(first, name))) variants.add(plain(cleanTitle(segments.slice(1).join(' - '))));
      if (names.some(name => includesArtist(last, name))) variants.add(plain(cleanTitle(segments.slice(0, -1).join(' - '))));
    }
    // Handle credits without a dash: "Lil Peep x Lil Tracy white tee".
    let stripped = full;
    for (let i = 0; i < names.length + 1; i++) {
      const prefix = names.find(name => stripped.startsWith(`${name} `));
      if (!prefix) break;
      stripped = stripped.slice(prefix.length).trim().replace(/^(?:x|w|with|and|e)\s+/u, '');
      if (stripped) variants.add(stripped);
    }
    return [...variants].filter(Boolean);
  }

  function sourceInfo(track) {
    const artists = (track.artists?.length ? track.artists : [track.artist || track.channel || '']).filter(Boolean);
    return {artists, title: track.name || track.title || '', duration: Number(track.duration) || null};
  }

  function primaryCredits(track, source, kind) {
    // YouTube playlist titles can supply one combined artist credit. Spotify
    // supplies actual artist names, including bands with an ampersand in the name.
    const combinedVideoCredit = kind === 'spotify' && source.artists.length === 1 &&
      track.title && plain(track.title).startsWith(`${plain(source.artists[0])} `);
    return combinedVideoCredit ? artistVariants(source.artists[0]) : [source.artists[0]].filter(Boolean);
  }

  function rank(track, candidates, kind) {
    const source = sourceInfo(track);
    return candidates.map(candidate => {
      const candidateTitle = candidate.title || candidate.name || '';
      const candidateArtists = (candidate.artists?.length ? candidate.artists : [candidate.channel || '']).filter(Boolean);
      const knownArtists = [...source.artists, ...candidateArtists].flatMap(artistVariants);
      const sourceTitles = titleVariants(source.title, knownArtists);
      const targetTitles = titleVariants(candidateTitle, knownArtists);
      const titleScore = Math.max(0, ...sourceTitles.flatMap(a => targetTitles.map(b => similarity(a, b))));
      const primaryNames = primaryCredits(track, source, kind).map(artistName);
      const channelArtist = Math.max(0, ...primaryNames.flatMap(primary => candidateArtists.flatMap(artistVariants).map(artist => similarity(primary, artistName(artist)))));
      const credited = kind === 'youtube' && primaryNames.some(primary => includesArtist(plain(candidateTitle), primary));
      const artistScore = Math.max(channelArtist, credited ? .95 : 0);
      const sourceRaw = plain(track.title || source.title), targetRaw = plain(candidateTitle);
      const versionMismatch = modifiers.some(pattern => pattern.test(sourceRaw) !== pattern.test(targetRaw));
      const targetDuration = Number(candidate.duration) || null;
      const durationKnown = !!source.duration && !!targetDuration;
      const difference = durationKnown ? Math.abs(source.duration - targetDuration) : null;
      const tolerance = Math.max(12, Math.min(20, source.duration * .08));
      const durationMismatch = durationKnown && difference > tolerance;
      const durationScore = durationKnown ? Math.max(0, 1 - difference / Math.max(tolerance * 2, 1)) : .65;
      let score = titleScore * .65 + artistScore * .25 + durationScore * .10;
      if (versionMismatch) score = Math.min(score, .45);
      if (durationMismatch || artistScore < .75 || titleScore < .72) score = Math.min(score, .60);
      const shortTitle = plain(cleanTitle(source.title)).length <= 4;
      const auto = titleScore >= (shortTitle || !durationKnown ? .97 : .90) &&
        artistScore >= (durationKnown ? .88 : .95) && !versionMismatch && !durationMismatch;
      const reason = versionMismatch ? 'Versão diferente (cover, remix, ao vivo ou outra edição)'
        : durationMismatch ? 'Duração diferente da faixa original'
        : artistScore < .88 ? 'Artista não confirmado'
        : titleScore < .90 ? 'Título não corresponde com clareza'
        : !auto ? 'Confira esta gravação; a correspondência ainda é incerta'
        : 'Título, artista e versão correspondem';
      const official = channelArtist >= .95 || /\b(?:official|oficial)\b/u.test(targetRaw);
      return {...candidate, score, auto, reason, titleScore, artistScore, official};
    }).sort((a, b) => Number(b.auto) - Number(a.auto) || b.score - a.score || Number(b.official) - Number(a.official));
  }

  function queries(track, kind) {
    const source = sourceInfo(track);
    const title = cleanTitle(source.title).replace(/["“”]/g, '').replace(/\s+/g, ' ').trim();
    const credits = primaryCredits(track, source, kind);
    const artist = String(credits.at(-1) || '').replace(/\s*-\s*Topic$|VEVO$/i, '').replace(/["“”]/g, '').trim();
    const primary = kind === 'spotify' && artist ? `track:"${title}" artist:"${artist}"` : `${artist} ${title}`.trim();
    const alternative = kind === 'spotify' ? `${title} ${artist}`.trim() : `${artist ? `"${artist}" ` : ''}"${title}"`;
    return [...new Set([primary, alternative].filter(Boolean).map(query => query.slice(0, 180)))];
  }

  return {VERSION, plain, rank, queries};
});
