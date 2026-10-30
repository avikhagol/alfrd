import test from 'node:test';
import assert from 'node:assert/strict';
import { createChatState, createLRU } from '../../src/alfrd/web/js/data/chat_state.js';
const response = (body) => ({ ok: true, json: async () => body });
test('warm snapshots resolve synchronously through peek, remain representation/project scoped and coalesce', async () => {
  let calls = 0;
  const store = createChatState({ request: async () => { calls++; return response({ rev: 1, messages: ['cached'] }); } });
  await Promise.all([store.load('p', 'a'), store.load('p', 'a')]);
  assert.deepEqual(store.peek('p', 'a').data.messages, ['cached']);
  await store.load('p', 'a'); assert.equal(calls, 1);
  assert.equal(store.peek('other', 'a'), undefined); assert.equal(store.peek('p', 'a', 'messages=200'), undefined);
});
test('cancelled late A response cannot overwrite B or populate A after cancellation', async () => {
  let finish;
  const store = createChatState({ request: async (url) => url.endsWith('/a') ? new Promise((r) => { finish = r; }) : response({ session: { id: 'b' } }) });
  const pending = store.load('p', 'a'); store.cancel('p', 'a');
  await store.load('p', 'b'); finish(response({ session: { id: 'a' } }));
  assert.equal(await pending, null); assert.equal(store.peek('p', 'a').data, null);
  assert.equal(store.peek('p', 'b').data.session.id, 'b');
});
test('snapshot count and byte bounds evict LRU and rendered view eviction disposes controllers', async () => {
  const store = createChatState({ limit: 2, request: async () => response({ messages: ['hello'] }) });
  await store.load('p', 'a'); await store.load('p', 'b'); store.peek('p', 'a'); await store.load('p', 'c');
  assert.equal(store.cache.size, 2); assert.equal(store.peek('p', 'b'), undefined);
  const bytes = createChatState({ maxBytes: 10, request: async () => response({ messages: ['too large'] }) });
  await bytes.load('p', 'a'); assert.equal(bytes.cache.size, 0);
  const disposed = [], views = createLRU(3, (v) => disposed.push(v.id));
  for (let id = 0; id < 4; id++) views.set(id, { id });
  assert.equal(views.entries.size, 3); assert.deepEqual(disposed, [0]);
});
test('invalidation during a request follows up once; unchanged retains data and full refresh ceiling', async () => {
  let finish, calls = [], time = 1000;
  const store = createChatState({ now: () => time, request: async (url) => { calls.push(url); if (calls.length === 1) return new Promise((r) => { finish = r; }); return response(url.includes('since_rev') ? { unchanged: true, rev: 1 } : { rev: 1, messages: ['full'] }); } });
  const pending = store.load('p', 'a'); store.invalidate('p', 'a'); finish(response({ rev: 1, messages: ['full'] })); await pending;
  assert.equal(calls.length, 2); assert.ok(calls[1].includes('since_rev=1')); assert.deepEqual(store.peek('p', 'a').data.messages, ['full']);
  time += 61000; await store.load('p', 'a'); assert.equal(calls.at(-1).includes('since_rev'), false);
});

test('resync resets revision assumptions and deletion removes every representation', async () => {
  let rev = 9;
  const store = createChatState({ request: async () => response({ rev, messages: [String(rev)] }) });
  await store.load('p', 'a'); rev = 1;
  store.invalidate('p', null, { reset: true }); await store.load('p', 'a');
  assert.equal(store.peek('p', 'a').data.rev, 1);
  await store.load('p', 'a', 'messages=200'); store.remove('p', 'a'); assert.equal(store.cache.size, 0);
});
