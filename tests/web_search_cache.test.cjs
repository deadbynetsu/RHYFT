const assert = require('node:assert/strict');
const test = require('node:test');
const {create} = require('../site/search-cache.js');

function fixture() {
  let time = 1000000;
  const values = new Map();
  const storage = {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value)};
  const options = {storage, now: () => time};
  return {cache: create(options), reload: () => create(options), advance: ms => time += ms, values};
}
const song = {id: 'Video123456', title: 'Artist - Song', channel: 'Artist', duration: 180, url: 'https://www.youtube.com/watch?v=Video123456'};
const publicResults = items => ({items, source: 'youtube-music-public'});

test('successful search results survive a page reload and normalize equivalent queries', () => {
  const f = fixture();
  f.cache.set('youtube-music', 'Artist   Song', publicResults([song]));
  assert.deepEqual(f.reload().get('youtube-music', 'artist song'), publicResults([song]));
  assert.equal(f.reload().get('spotify', 'artist song'), null);
});

test('failed requests cannot replace usable results or become cache hits', () => {
  const f = fixture();
  f.cache.set('youtube-music', 'Song', publicResults([song]));
  f.cache.set('youtube-music', 'Song', {error: 'Timeout'});
  assert.deepEqual(f.cache.get('youtube-music', 'Song'), publicResults([song]));
  f.cache.set('youtube-music', 'Other', {error: 'Timeout'});
  assert.equal(f.cache.get('youtube-music', 'Other'), null);
});

test('cached results expire and empty searches can be tried again sooner', () => {
  const f = fixture();
  f.cache.set('youtube-music', 'Song', publicResults([song]));
  f.cache.set('youtube-music', 'Other', publicResults([]));
  f.advance(300000);
  assert.equal(f.cache.get('youtube-music', 'Other'), null);
  assert.deepEqual(f.cache.get('youtube-music', 'Song'), publicResults([song]));
  f.advance(21600000);
  assert.equal(f.reload().get('youtube-music', 'Song'), null);
});

test('the cache keeps only song fields and is bounded', () => {
  const f = fixture();
  for (let i = 0; i < 110; i++) {
    f.cache.set('youtube-music', `Song ${i}`, publicResults([{...song, access_token: 'private-fixture', debug: 'private-response'}]));
    f.advance(1);
  }
  assert.equal(f.cache.get('youtube-music', 'Song 0'), null);
  assert.deepEqual(f.reload().get('youtube-music', 'Song 109'), publicResults([song]));
  assert.doesNotMatch([...f.values.values()].join(''), /private-fixture|private-response/);
});

test('logout removes only the disconnected provider search results', () => {
  const f = fixture();
  f.cache.set('youtube-music', 'Song', publicResults([song]));
  f.cache.set('spotify', 'Song', {items: [{id: 'Track123456', name: 'Song'}]});
  f.cache.clear('youtube-music');
  assert.equal(f.reload().get('youtube-music', 'Song'), null);
  assert.deepEqual(f.reload().get('spotify', 'Song'), {items: [{id: 'Track123456', name: 'Song'}]});
});

test('old official search caches and results without public provenance are not reused', () => {
  const f = fixture();
  f.values.set('rhyft.web.search-cache.v1', JSON.stringify({'youtube:song': {items: [song], expiresAt: 9999999999}}));
  assert.equal(f.reload().get('youtube-music', 'Song'), null);
  f.cache.set('youtube-music', 'Song', {items: [song]});
  assert.equal(f.cache.get('youtube-music', 'Song'), null);
  f.cache.set('youtube', 'Song', publicResults([song]));
  assert.equal(f.cache.get('youtube', 'Song'), null);
});

test('public result provenance and recording identifiers survive reload', () => {
  const f = fixture();
  const candidate = {...song, isrc: 'USABC2600001', catalogSource: 'youtube-music-public'};
  f.cache.set('youtube-music', 'Song', publicResults([candidate]));
  assert.deepEqual(f.reload().get('youtube-music', 'Song'), publicResults([candidate]));
});
