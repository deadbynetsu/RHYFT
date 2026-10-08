const assert = require('node:assert/strict');
const test = require('node:test');
const {create} = require('../site/provider-pacing.js');

function fixture() {
  let time = 1000000;
  const data = new Map();
  const storage = {getItem: key => data.get(key), setItem: (key, value) => data.set(key, value)};
  const clock = {storage, now: () => time};
  return {pacing: create(clock), reload: () => create(clock), advance: ms => time += ms, now: () => time};
}

test('YouTube reservations pace searches and writes on the same platform', () => {
  const f = fixture();
  assert.equal(f.pacing.reserve('youtube'), f.now());
  assert.equal(f.pacing.reserve('youtube'), f.now() + 2000);
  assert.equal(f.pacing.reserve('youtube'), f.now() + 4000);
  assert.equal(f.pacing.reserve('spotify'), f.now());
});

test('a rate limit applies to later requests and survives a new page instance', () => {
  const f = fixture();
  f.pacing.reserve('youtube');
  assert.equal(f.pacing.limited('youtube'), 60000);
  f.advance(20000);
  const reload = f.reload();
  const at = reload.reserve('youtube');
  assert.equal(at, f.now() + 40000);
  assert.equal(reload.remaining('youtube', at).ms, 40000);
  assert.equal(reload.remaining('youtube', at).blocked, true);
  assert.equal(reload.reserve('spotify'), f.now());
});

test('consecutive limits increase cooldown across tracks rather than restarting at a short delay', () => {
  const f = fixture();
  assert.equal(f.pacing.limited('youtube'), 60000);
  f.advance(60000);
  assert.equal(f.pacing.limited('youtube'), 120000);
  f.advance(120000);
  assert.equal(f.pacing.limited('youtube'), 240000);
  f.advance(240000);
  assert.equal(f.pacing.limited('youtube'), 240000);
});

test('new limits extend waits already reserved by another tab', () => {
  const f = fixture();
  const other = f.reload();
  const at = f.pacing.reserve('youtube');
  other.limited('youtube', 180000);
  assert.equal(f.pacing.remaining('youtube', at).ms, 180000);
  assert.equal(f.pacing.remaining('youtube', at).blocked, true);
});

test('after a limit clears the platform keeps a slower pace', () => {
  const f = fixture();
  f.pacing.limited('youtube');
  f.advance(60000);
  assert.equal(f.pacing.reserve('youtube'), f.now());
  assert.equal(f.pacing.reserve('youtube'), f.now() + 4000);
  f.advance(1800000 + 10000);
  assert.equal(f.pacing.reserve('youtube'), f.now());
  assert.equal(f.pacing.reserve('youtube'), f.now() + 2000);
});

test('Retry-After periods longer than the fallback are respected', () => {
  const f = fixture();
  assert.equal(f.pacing.limited('youtube', 300000), 300000);
  f.advance(120000);
  assert.equal(f.pacing.remaining('youtube', f.now()).ms, 180000);
});

test('public YouTube Music catalog calls are paced separately from official writes', () => {
  const f = fixture();
  f.pacing.limited('youtube', 300000);
  assert.equal(f.pacing.reserve('youtube-music'), f.now());
  assert.equal(f.pacing.reserve('youtube-music'), f.now() + 1000);
  assert.equal(f.pacing.remaining('youtube-music', f.now()).blocked, false);
  assert.equal(f.pacing.reserve('youtube'), f.now() + 300000);
});

test('public catalog limits do not delay official writes and persist across reload', () => {
  const f = fixture();
  assert.equal(f.pacing.limited('youtube-music'), 15000);
  assert.equal(f.pacing.reserve('youtube'), f.now());
  f.advance(15000);
  assert.equal(f.reload().limited('youtube-music'), 30000);
  f.advance(30000);
  assert.equal(f.reload().limited('youtube-music', 90000), 90000);
  assert.equal(f.pacing.reserve('youtube'), f.now());
});
