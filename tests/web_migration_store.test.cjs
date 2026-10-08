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
  const store = fixture([record('bound', {destinationAccountId: 'channel-a',
    sourceInput: `https://open.spotify.com/playlist/${sourceId}?si=old-tracking`})]);
  for (const input of [sourceId, `spotify:playlist:${sourceId}`, `https://open.spotify.com/playlist/${sourceId}?si=new-tracking`]) {
    assert.equal(store.find('spotify-youtube', input, 'channel-a').id, 'bound');
  }
});

test('the saved source ID takes precedence over a contradictory legacy source link', () => {
  const otherSourceId = 'Other12345678901234567';
  const store = fixture([record('canonical', {
    sourceId, sourceInput: `https://open.spotify.com/playlist/${otherSourceId}`,
    destinationAccountId: 'channel-a', status: 'completed'
  })]);
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-a').id, 'canonical');
  assert.equal(store.find('spotify-youtube', otherSourceId, 'channel-a'), null,
    'a stale display link must not bind the checkpoint to a different playlist');
});

test('a completed checkpoint remains discoverable by its saved source ID when its old link cannot be parsed', () => {
  for (const sourceInput of [undefined, '', 'old shared link', `https://open.spotify.com/intl-pt/playlist/${sourceId}`]) {
    const store = fixture([record('canonical', {
      sourceId, sourceInput, destinationAccountId: 'channel-a', status: 'completed', added: 41
    })]);
    assert.equal(store.find('spotify-youtube', sourceId, 'channel-a')?.id, 'canonical');
    assert.equal(store.find('spotify-youtube', `https://open.spotify.com/playlist/${sourceId}?si=updated`, 'channel-a')?.id, 'canonical');
  }
});

test('invalid saved source IDs fall back to the valid legacy source link', () => {
  for (const invalid of ['short', 'not a playlist', 'Bad_Source_12345678901', 'A'.repeat(31),
    `https://open.spotify.com/playlist/${sourceId}`, `spotify:playlist:${sourceId}`, 1234567890123, {id: sourceId}]) {
    const store = fixture([record('legacy', {sourceId: invalid, destinationAccountId: 'channel-a'})]);
    assert.equal(store.find('spotify-youtube', sourceId, 'channel-a')?.id, 'legacy');
  }
});

test('invalid saved source IDs cannot create a binding without a valid legacy source link', () => {
  const store = fixture([
    record('url', {sourceId: `https://open.spotify.com/playlist/${sourceId}`, sourceInput: ''}),
    record('uri', {sourceId: `spotify:playlist:${sourceId}`, sourceInput: ''}),
    record('numeric', {sourceId: 1234567890123, sourceInput: ''})
  ]);
  assert.equal(store.find('spotify-youtube', sourceId), null);
  assert.equal(store.find('spotify-youtube', '1234567890123'), null);
});

test('saved source IDs preserve destination account priority and archived exclusion', () => {
  const options = {sourceId, sourceInput: 'old shared link', status: 'completed'};
  const store = fixture([
    record('archived-a', {...options, destinationAccountId: 'channel-a', status: 'destination-unavailable'}),
    record('account-b', {...options, destinationAccountId: 'channel-b'}),
    record('legacy', options),
    record('account-a', {...options, destinationAccountId: 'channel-a'})
  ]);
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-a')?.id, 'account-a');
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-b')?.id, 'account-b');
  assert.equal(store.find('spotify-youtube', sourceId, 'channel-c')?.id, 'legacy');
});

test('saved YouTube source IDs accept platform characters and stay separate from Spotify histories', () => {
  const youtubeSourceId = 'PL_Public-Source_12345';
  const store = fixture([
    record('youtube', {direction: 'youtube-spotify', sourceId: youtubeSourceId, sourceInput: 'old shared link',
      destinationAccountId: 'account-a'}),
    record('spotify', {sourceId, sourceInput: 'old shared link', destinationAccountId: 'account-a'}),
    record('invalid-spotify', {sourceId: youtubeSourceId, sourceInput: 'old shared link', destinationAccountId: 'account-a'})
  ]);
  assert.equal(store.find('youtube-spotify', `https://music.youtube.com/playlist?list=${youtubeSourceId}&si=new`, 'account-a')?.id, 'youtube');
  assert.equal(store.find('spotify-youtube', sourceId, 'account-a')?.id, 'spotify');
  assert.equal(store.find('spotify-youtube', youtubeSourceId, 'account-a'), null);
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
