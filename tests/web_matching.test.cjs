const assert = require('node:assert/strict');
const test = require('node:test');
const matching = require('../site/music-matching.js');

// Real titles shown in the user's recording; durations are controlled fixtures.
const examples = [
  ['OMFG', ['Lil Peep'], 'Lil Peep - OMFG (Sub. Español)', 'Lyrics Channel'],
  ['pain', ['Lil Peep', 'Lil Tracy', 'Slug † Christ'], 'Lil Peep & Lil Tracy - pain (feat. Slug Christ) (Official Audio)', 'Lil Peep'],
  ['white tee', ['Lil Peep', 'Lil Tracy'], 'Lil Peep x Lil Tracy - white tee (Official Audio)', 'Lil Peep'],
  ['Fingers', ['Lil Peep'], 'Fingers (lyrics) - Lil Peep', 'Lyrics Channel'],
  ['cobain', ['Lil Peep', 'Lil Tracy'], 'Lil Peep - cobain (feat. Lil Tracy) (Official Audio)', 'Lil Peep'],
  ['drugz', ['Lil Peep'], 'Lil Peep - Drugz (Official Video)', 'Lil Peep'],
  ['girls', ['Lil Peep', 'Horse Head'], 'Lil Peep - girls (feat. horse head) (Official Audio)', 'Lil Peep'],
  ['Last Fall', ['Lil Peep', 'Lil Tracy', 'Horse Head'], 'Lil Peep w/ Lil Tracy & Horse Head - Last Fall (Official Audio)', 'Lil Peep'],
  ['Lose My Mind', ['Lil Peep', 'Meeting by Chance'], 'Lil Peep x Meeting by Chance - Lose My Mind (Official Audio)', 'Lil Peep'],
  ['sex [last nite]', ['Lil Peep'], 'Lil Peep - sex (last nite) (Official Audio)', 'Lil Peep']
];
for (const [title, artists, videoTitle, channel] of examples) {
  test(`recognizes ${title} with artist credits and video decorations`, () => {
    const [best] = matching.rank({name: title, artists, duration: 180},
      [{id: 'candidate', title: videoTitle, channel, duration: 182}], 'youtube');
    assert.equal(best.auto, true, `${videoTitle}: ${best.reason}, score=${best.score}`);
    assert.ok(best.titleScore >= .97);
  });
}

for (const version of ['(Live)', '(Cover)', '(Acoustic)', '(Remix)', '(Slowed + Reverb)', '(Sped Up)', '(Instrumental)', '(Karaoke)']) {
  test(`keeps a different ${version} version for review`, () => {
    const [best] = matching.rank({name: 'Last Fall', artists: ['Lil Peep'], duration: 180},
      [{title: `Lil Peep - Last Fall ${version}`, channel: 'Lil Peep', duration: 180}], 'youtube');
    assert.equal(best.auto, false);
    assert.match(best.reason, /Versão diferente/);
  });
}

test('accepts the requested live version and rejects the studio version', () => {
  const ranked = matching.rank({name: 'Last Fall (Live)', artists: ['Lil Peep'], duration: 180}, [
    {id: 'studio', title: 'Lil Peep - Last Fall', channel: 'Lil Peep', duration: 180},
    {id: 'live', title: 'Lil Peep - Last Fall (Live)', channel: 'Lil Peep', duration: 182}
  ], 'youtube');
  assert.equal(ranked[0].id, 'live');
  assert.equal(ranked[0].auto, true);
  assert.equal(ranked[1].auto, false);
});

test('duration prevents accepting a different recording or long compilation', () => {
  const [best] = matching.rank({name: 'Last Fall', artists: ['Lil Peep'], duration: 180},
    [{title: 'Lil Peep - Last Fall', channel: 'Lil Peep', duration: 600}], 'youtube');
  assert.equal(best.auto, false);
  assert.match(best.reason, /Duração diferente/);
});

test('a shared short title does not excuse a different artist', () => {
  const [best] = matching.rank({name: 'pain', artists: ['Lil Peep'], duration: 180},
    [{title: 'pain', channel: 'Other Artist', duration: 180}], 'youtube');
  assert.equal(best.auto, false);
  assert.match(best.reason, /Artista não confirmado/);
});

test('distinct meaningful subtitles remain distinct', () => {
  const [best] = matching.rank({name: 'Song (Part 2)', artists: ['Artist'], duration: 180},
    [{title: 'Artist - Song (Part 1)', channel: 'Artist', duration: 180}], 'youtube');
  assert.equal(best.auto, false);
});

test('does not erase non-Latin song or artist names', () => {
  const ranked = matching.rank({name: '夜に駆ける', artists: ['YOASOBI'], duration: 180}, [
    {id: 'wrong', title: 'YOASOBI - 群青', channel: 'YOASOBI', duration: 180},
    {id: 'right', title: 'YOASOBI - 夜に駆ける (Official Audio)', channel: 'YOASOBI', duration: 180}
  ], 'youtube');
  assert.equal(ranked[0].id, 'right');
  assert.equal(ranked[0].auto, true);
  assert.equal(ranked[1].auto, false);
});

test('reverse direction compares Spotify artists and cleaned video titles', () => {
  const [best] = matching.rank({name: 'Last Fall (Official Audio)', title: 'Lil Peep - Last Fall (Official Audio)', artists: ['Lil Peep'], duration: 180},
    [{name: 'Last Fall', artists: ['Lil Peep', 'Lil Tracy'], duration: 181}], 'spotify');
  assert.equal(best.auto, true);
  assert.deepEqual(matching.queries({name: 'Last Fall (Official Audio)', artists: ['Lil Peep']}, 'spotify'),
    ['track:"Last Fall" artist:"Lil Peep"', 'Last Fall Lil Peep']);
});

test('queries preserve last nite and reserve the alternate search for weak results', () => {
  assert.deepEqual(matching.queries({name: 'sex [last nite]', artists: ['Lil Peep', 'Lil Tracy']}, 'youtube'),
    ['Lil Peep sex last nite', '"Lil Peep" "sex last nite"']);
});

test('reverse direction handles multiple artist credits parsed from a video title', () => {
  const source = {name: 'Last Fall', title: 'Lil Peep & Lil Tracy - Last Fall', artists: ['Lil Peep & Lil Tracy'], duration: 180};
  const [best] = matching.rank(source, [{name: 'Last Fall', artists: ['Lil Peep', 'Lil Tracy'], duration: 180}], 'spotify');
  assert.equal(best.auto, true);
  assert.equal(matching.queries(source, 'spotify')[0], 'track:"Last Fall" artist:"Lil Peep"');
});

test('does not remove a meaningful subtitle beginning with sub', () => {
  const [best] = matching.rank({name: 'Song (Submarine)', artists: ['Artist'], duration: 180},
    [{title: 'Artist - Song (Sunrise)', channel: 'Artist', duration: 180}], 'youtube');
  assert.equal(best.auto, false);
});

test('a band name containing an ampersand is not replaced with a partial artist', () => {
  const source = {name: 'Shake It Out', artists: ['Florence & The Machine'], duration: 180};
  const [best] = matching.rank(source, [{title: 'Florence - Shake It Out', channel: 'Florence', duration: 180}], 'youtube');
  assert.equal(best.auto, false);
  assert.equal(matching.queries(source, 'youtube')[0], 'Florence & The Machine Shake It Out');
});
