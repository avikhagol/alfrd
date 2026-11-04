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
  assert.equal(c.snapshot('b'), undefined, 'background GET does not load transcripts/list');
  c.select('b'); await flush();
  assert.equal(c.snapshot('b').sessions[0].pending_user, 1);
  c.select('empty'); await flush(); assert.deepEqual(c.snapshot('empty').sessions, []);
  data.b = [row('new', 1)]; await c.invalidate('b');
  assert.equal(c.snapshot('b').sessions[0].id, 'same', 'background updates only compact counts');
  c.select('b'); await flush(); assert.equal(c.snapshot('b').sessions[0].id, 'new');
  c.select('a'); await flush(); assert.equal(c.snapshot('a').sessions[0].id, 'same');
  assert.ok(seen.every(([p, rows]) => !rows || rows === data[p] || p === 'b'));
  c.stop();
});

test('selected-list deduplication and invalidation during GET coalesce a trailing refresh', async () => {
  let calls = 0; const deferred = [];
  const c = createChatProjects({ request: async () => {
    calls++; await new Promise((r) => deferred.push(r));
    return response({ sessions: [row('only')] });
  } });
  c.catalog(['a', 'b', 'c']); assert.equal(calls, 0);
  c.select('a'); c.load('a', true); assert.equal(calls, 1);
  c.invalidate('a'); c.invalidate('a');
  deferred.shift()(); await flush(); assert.equal(calls, 2);
  deferred.shift()(); await flush();
  assert.equal(c.snapshot('a').stale, false);
  c.refresh(); deferred.shift()(); await flush(); assert.equal(calls, 3); c.stop();
});

test('connection reset evicts late responses and failed lists remain errors', async () => {
  let finish; const c = createChatProjects({ request: () => new Promise((r) => { finish = r; }) });
  c.select('old'); c.stop(); finish(response({ sessions: [row('leaked', 1)] })); await flush();
  assert.equal(c.snapshot('old'), undefined);
  c.select('new'); finish(response({})); await flush();
  assert.equal(c.snapshot('new').sessions, null); assert.match(c.snapshot('new').error, /unavailable/);
  c.catalog([]); assert.equal(c.snapshot('new'), undefined); c.stop();
});

test('lazy shell fetches only selected lists, with no picker decoration or activity GETs', async () => {
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
    enabled = true; shell.sync(); await flush(); assert.equal(calls.length, 1);
    assert.equal(decorate, undefined); assert.equal(updates, 0);
    window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'b', events: [{ plan: 'workflow' }] } }));
    await flush(); assert.equal(calls.length, 1);
    window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'a', events: [{ plan: 'chat-a-only' }] } }));
    await flush(); assert.equal(calls.length, 2);
    ctx.state.liveStatus.state = 'disconnected'; shell.sync();
    ctx.state.liveStatus.state = 'live'; shell.sync(); await flush(); assert.equal(calls.length, 3);
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange')); await flush(); assert.equal(calls.length, 3);
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange')); await flush(); assert.equal(calls.length, 4);
    assert.ok(calls.every((url) => !url.includes("?")));
    shell.stop();
    window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'a', events: [{ plan: 'chat-a-only' }] } }));
    await flush(); assert.equal(calls.length, 4);
  } finally {
    shell.stop(); server.request = oldRequest; server.session = oldSession;
    Object.assign(globalThis, globals);
  }
});


test('fresh selected lists skip settled duplicate reads; TTL and background invalidation still refresh', async () => {
  let calls = 0, now = 0;
  const c = createChatProjects({ now: () => now, request: async () => {
    calls++; return response({ sessions: [row(String(calls))] });
  } });
  c.select('a'); await flush();
  await c.load('a'); assert.equal(calls, 1);
  c.select('b'); await flush(); c.select('a'); await flush(); assert.equal(calls, 2);
  now = 30000; await c.load('a'); assert.equal(calls, 3);
  c.select('b'); await flush(); await c.invalidate('a');
  c.select('a'); await flush(); assert.equal(calls, 5);
  c.refresh(); await flush(); assert.equal(calls, 6);
  c.stop();
});
