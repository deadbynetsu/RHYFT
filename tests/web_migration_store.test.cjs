const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const key = 'rhyft.web.history.v1';
const sourceId = 'Source1234567890123456';

function record(id, options = {}) {
  return {
    id, createdAt: '2026-10-07T12:00:00.000Z', direction: 'spotify-youtube',
    sourceInput: `https://open.spotify.com/playlist/${sourceId}`,
    destinationId: 'Destination123456789', status: 'interrupted', ...options
  };
}

function fixture(records) {
  const values = new Map([[key, JSON.stringify(records)]]);
  const context = vm.createContext({
    window: {}, document: {dispatchEvent() {}},
    CustomEvent: class {constructor(type) {this.type = type;}},
    localStorage: {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../site/migration-store.js'), 'utf8'), context);
  return context.window.RhyftMigrationStore;
}

test('the same source resumes the destination belonging to the connected account', () => {
  const store = fixture([
    record('account-b', {destinationAccountId: 'channel-b'}),
    record('legacy'),
    record('account-a', {destinationAccountId: 'channel-a'})
  ]);
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-a').id, 'account-a');
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-b').id, 'account-b');
});

test('an unavailable destination is excluded from automatic resumption', () => {
  const archived = record('unavailable', {destinationAccountId: 'channel-a', status: 'destination-unavailable'});
  const store = fixture([archived, record('replacement', {destinationAccountId: 'channel-a'})]);
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-a').id, 'replacement');
  assert.equal(fixture([archived]).find('spotify-youtube', sourceId, 'channel-a'), null);
  assert.equal(store.load()[0].id, 'unavailable', 'the old checkpoint remains available in history');
});

test('an unbound legacy checkpoint is preferred to a destination from a different account', () => {
  const store = fixture([
    record('other-account', {destinationAccountId: 'channel-b'}),
    record('legacy')
  ]);
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-a').id, 'legacy');
});

test('a different account checkpoint remains a fallback for replacement naming', () => {
  const store = fixture([record('other-account', {destinationAccountId: 'channel-b', destinationName: 'My playlist'})]);
  const match = store.find('spotify-youtube', sourceId, 'channel-a');
  assert.equal(match.id, 'other-account');
  assert.equal(match.destinationAccountId, 'channel-b');
  assert.equal(match.destinationName, 'My playlist');
});

test('canonical Spotify source IDs match URLs with tracking parameters and Spotify URIs', () => {
  const store = fixture([record('bound', {destinationAccountId: 'channel-a'})]);
  for (const input of [sourceId, `spotify:playlist:${sourceId}`, `https://open.spotify.com/playlist/${sourceId}?si=tracking`]) {
    assert.equal(store.find('spotify-youtube', input, 'channel-a').id, 'bound');
  }
});

test('opposite migration directions keep their account histories separate', () => {
  const store = fixture([
    record('youtube-source', {direction: 'youtube-spotify', sourceInput: `https://music.youtube.com/playlist?list=${sourceId}`, destinationAccountId: 'account-a'}),
    record('spotify-source', {destinationAccountId: 'account-a'})
  ]);
  assert.equal(store.find('spotify-youtube', sourceId, 'account-a').id, 'spotify-source');
  assert.equal(store.find('youtube-spotify', `https://www.youtube.com/playlist?list=${sourceId}&feature=share`, 'account-a').id, 'youtube-source');
});

test('lookups without account identity retain the latest usable legacy behavior', () => {
  const store = fixture([
    record('unavailable', {status: 'destination-unavailable'}),
    record('latest', {destinationAccountId: 'channel-b'}),
    record('older')
  ]);
  assert.equal(store.find('spotify-youtube', sourceId).id, 'latest');
  assert.equal(store.find('spotify-youtube', 'not a playlist', 'channel-a'), null);
});
