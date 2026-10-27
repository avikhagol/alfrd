import test from 'node:test';
import assert from 'node:assert/strict';
import { createChatProjects } from '../../src/alfrd/web/js/data/chat_projects.js';
import { activeSession, destinationSession, filterSessions } from '../../src/alfrd/web/js/data/chat_sessions.js';
const flush = () => new Promise((r) => setImmediate(r));
const response = (body) => ({ ok: true, json: async () => body });
const row = (id, active_requests = 0, pending_user = 0) => ({ id, title: id, active_requests, pending_user });

test('active-first destination preserves remembered and explicit project-qualified identity', () => {
  const rows = [row('idle'), row('running', 1), row('question', 0, 1)];
  assert.deepEqual(rows.map(activeSession), [false, true, true]);
  assert.equal(destinationSession(rows), 'running');
  assert.equal(destinationSession(rows, 'idle'), 'idle');
  assert.equal(destinationSession(rows, 'missing'), 'running');
  assert.equal(destinationSession(rows, 'idle', 'missing-link'), 'missing-link');
  assert.equal(destinationSession([]), null);
  assert.equal(destinationSession([row('idle')]), 'idle');
  assert.deepEqual(filterSessions(rows, 'question', true).map((r) => r.id), ['question']);
});

test('switch A/B/A, empty project and live snapshots never leak sessions across projects', async () => {
  const data = { a: [row('same', 1)], b: [row('same', 0, 1)], empty: [] }, seen = [];
  const c = createChatProjects({ request: async (url) => {
    const p = url.split('/projects/')[1].split('/')[0];
    return response(url.includes('?') ? { activity: { active_sessions: data[p].filter(activeSession).length, needs_user_sessions: 0 } } : { sessions: data[p] });
  } });
  c.subscribe((e) => seen.push([e.project, e.sessions]));
  c.select('a'); c.catalog(['a', 'b', 'empty']); await flush();
  assert.equal(c.snapshot('a').sessions[0].active_requests, 1);
  assert.equal(c.snapshot('b').sessions, null, 'background GET does not load transcripts/list');
  c.select('b'); await flush();
  assert.equal(c.snapshot('b').sessions[0].pending_user, 1);
  c.select('empty'); await flush(); assert.deepEqual(c.snapshot('empty').sessions, []);
  assert.deepEqual(c.snapshot('empty').activity, { active_sessions: 0, needs_user_sessions: 0 });
  data.b = [row('new', 1)]; await c.invalidate('b');
  assert.equal(c.snapshot('b').sessions[0].id, 'same', 'background updates only compact counts');
  c.select('b'); await flush(); assert.equal(c.snapshot('b').sessions[0].id, 'new');
  c.select('a'); await flush(); assert.equal(c.snapshot('a').sessions[0].id, 'same');
  assert.ok(seen.every(([p, rows]) => !rows || rows === data[p] || p === 'b'));
  c.stop();
});

test('bounded fanout, deduplication and invalidation during GET cause exactly one trailing refresh', async () => {
  let running = 0, max = 0, calls = 0; const deferred = [];
  const c = createChatProjects({ request: async () => {
    running++; calls++; max = Math.max(max, running);
    await new Promise((r) => deferred.push(r)); running--;
    return response({ activity: { active_sessions: 0, needs_user_sessions: 0 } });
  } });
  c.catalog(['a', 'b', 'c', 'd', 'e', 'f']);
  assert.equal(calls, 4);
  c.invalidate('a'); c.invalidate('a'); c.load('a', false);
  for (let i = 0; i < 6; i++) { deferred.splice(0).forEach((r) => r()); await flush(); }
  assert.equal(max, 4); assert.equal(calls, 7);
  assert.equal(c.snapshot('a').stale, false);
  await flush(); assert.equal(calls, 7, 'no recurring poll'); c.stop();
});

test('catalog/server reset evicts late responses; partial/failed counts remain unknown or stale', async () => {
  let finish; const c = createChatProjects({ request: () => new Promise((r) => { finish = r; }) });
  c.select('old'); c.stop(); finish(response({ sessions: [row('leaked', 1)] })); await flush();
  assert.equal(c.snapshot('old'), undefined);
  c.catalog(['new']); finish(response({ activity: {} })); await flush();
  assert.equal(c.snapshot('new').activity, null); assert.match(c.snapshot('new').error, /unavailable/);
  c.catalog([]); assert.equal(c.snapshot('new'), undefined); c.stop();
});

test('full list activity uses entire project, with unknown fields never reported as zero', async () => {
  let rows = [row('idle'), row('work', 2, 3), row('question', 0, 1)];
  const c = createChatProjects({ request: async () => response({ sessions: rows }) });
  c.select('a'); await flush();
  assert.deepEqual(c.snapshot('a').activity, { active_sessions: 2, needs_user_sessions: 2 });
  rows = [{ id: 'older-server' }]; await c.invalidate('a');
  assert.equal(c.snapshot('a').activity, null); c.stop();
});

test('lazy shell composes picker badges, refreshes live events and visibility, and does no work disabled', async () => {
  const { mountChatProjects } = await import('../../src/alfrd/web/js/data/chat_projects.js');
  const { server } = await import('../../src/alfrd/web/js/data/server.js');
  const globals = { window: globalThis.window, document: globalThis.document, location: globalThis.location };
  const oldRequest = server.request, oldSession = server.session;
  globalThis.window = new EventTarget();
  globalThis.document = Object.assign(new EventTarget(), { hidden: false, querySelector: () => null });
  globalThis.location = { origin: 'http://test' };
  let enabled = false, decorate, calls = [], updates = 0;
  const ctx = { state: { mode: 'server', liveStatus: { state: 'live' } }, chatOn: () => enabled,
    activeProject: () => 'a', projects: () => [{ id: 'a' }, { id: 'b' }] };
  server.session = { csrf_token: 'connection' };
  server.request = async (url) => {
    calls.push(url);
    return response(url.includes('?') ? { activity: { active_sessions: 2, needs_user_sessions: 1 } }
      : { sessions: [row('a-only', 1, 2)] });
  };
  const shell = mountChatProjects(ctx, { decorate(fn) { decorate = fn; }, redecorate() { updates++; } });
  try {
    shell.sync(); await flush(); assert.equal(calls.length, 0);
    enabled = true; shell.sync(); await flush(); assert.equal(calls.length, 2);
    assert.match(decorate({ value: 'a', badge: 'unsaved · Loading…', detail: '/root/a' }).badge, /^unsaved · Loading… · 1 active chat · 1 need you$/);
    assert.equal(decorate({ value: 'a', detail: '/root/a' }).detail, '/root/a');
    assert.match(decorate({ value: 'all' }).badge, /3 active chats · 2 need you/);
    window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'b', events: [{ plan: 'workflow' }] } }));
    await flush(); assert.equal(calls.length, 2);
    window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'a', events: [{ plan: 'chat-a-only' }] } }));
    await flush(); assert.equal(calls.length, 3);
    ctx.state.liveStatus.state = 'disconnected'; shell.sync();
    assert.match(decorate({ value: 'a' }).badge, /stale/);
    ctx.state.liveStatus.state = 'live'; shell.sync(); await flush(); assert.equal(calls.length, 5);
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange')); await flush(); assert.equal(calls.length, 5);
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); await flush(); assert.equal(calls.length, 7);
    assert.ok(updates > 0);
    shell.stop();
    window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'a', events: [{ plan: 'chat-a-only' }] } }));
    await flush(); assert.equal(calls.length, 7);
  } finally {
    shell.stop(); server.request = oldRequest; server.session = oldSession;
    Object.assign(globalThis, globals);
  }
});
