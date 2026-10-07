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

test('successful search results survive a page reload and normalize equivalent queries', () => {
  const f = fixture();
  f.cache.set('youtube', 'Artist   Song', {items: [song]});
  assert.deepEqual(f.reload().get('youtube', 'artist song'), {items: [song]});
  assert.equal(f.reload().get('spotify', 'artist song'), null);
});

test('failed requests cannot replace usable results or become cache hits', () => {
  const f = fixture();
  f.cache.set('youtube', 'Song', {items: [song]});
  f.cache.set('youtube', 'Song', {error: 'quotaExceeded'});
  assert.deepEqual(f.cache.get('youtube', 'Song'), {items: [song]});
  f.cache.set('youtube', 'Other', {error: 'Timeout'});
  assert.equal(f.cache.get('youtube', 'Other'), null);
});

test('cached results expire and empty searches can be tried again sooner', () => {
  const f = fixture();
  f.cache.set('youtube', 'Song', {items: [song]});
  f.cache.set('youtube', 'Other', {items: []});
  f.advance(300000);
  assert.equal(f.cache.get('youtube', 'Other'), null);
  assert.deepEqual(f.cache.get('youtube', 'Song'), {items: [song]});
  f.advance(21600000);
  assert.equal(f.reload().get('youtube', 'Song'), null);
});

test('the cache keeps only song fields and is bounded', () => {
  const f = fixture();
  for (let i = 0; i < 110; i++) {
    f.cache.set('youtube', `Song ${i}`, {items: [{...song, access_token: 'private-fixture', debug: 'private-response'}]});
    f.advance(1);
  }
  assert.equal(f.cache.get('youtube', 'Song 0'), null);
  assert.deepEqual(f.reload().get('youtube', 'Song 109'), {items: [song]});
  assert.doesNotMatch([...f.values.values()].join(''), /private-fixture|private-response/);
});

test('logout removes only the disconnected provider search results', () => {
  const f = fixture();
  f.cache.set('youtube', 'Song', {items: [song]});
  f.cache.set('spotify', 'Song', {items: [{id: 'Track123456', name: 'Song'}]});
  f.cache.clear('youtube');
  assert.equal(f.reload().get('youtube', 'Song'), null);
  assert.deepEqual(f.reload().get('spotify', 'Song'), {items: [{id: 'Track123456', name: 'Song'}]});
});
