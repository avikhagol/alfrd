import test from 'node:test';
import assert from 'node:assert/strict';
import { bounds, stations, handoffs, filterFeed, feedSentence } from '../../src/alfrd/web/js/data/harness.js';

test('stations derive role layers from task metadata without arranging by time', () => {
  const rows = stations({ agents: [{ id: 'builder' }, { id: 'researcher' }, { id: 'unknown' }],
    settings: { role_order: ['research', 'build'] }, view: { tasks: [
      { owner: 'builder', task: { role: 'build' } }, { owner: 'researcher', task: { role: 'research' } }] } });
  assert.deepEqual(rows.map((a) => [a.id, a.layer]), [['researcher', 0], ['builder', 1], ['unknown', 2]]);
});
test('dependency hints do not inflate published handoff thickness', () => {
  const edges = handoffs(['depends_on', 'published', 'published', 'planned'].map((kind) => ({ from_agent: 'a', to_agent: 'b', kind })));
  assert.equal(edges.find((e) => e.kind === 'published').count, 2);
  assert.equal(edges.find((e) => e.kind === 'depends_on').count, 0);
  assert.equal(edges.find((e) => e.kind === 'planned').count, 0);
});
test('feed filters compose, newest first, with readable fallback sentences', () => {
  const events = [{ seq: 1, agent: 'a', kind: 'task.started', task: 'Renderer', severity: 'info' },
    { seq: 3, agent: 'a', kind: 'task.failed', task: 'Renderer', severity: 'error' },
    { seq: 2, agent: 'b', kind: 'turn.failed', severity: 'error' }];
  assert.deepEqual(filterFeed(events).map((e) => e.seq), [3, 2, 1]);
  assert.deepEqual(filterFeed(events, { agent: 'a', type: 'task', errors: true, search: 'renderer' }).map((e) => e.seq), [3]);
  assert.equal(feedSentence(events[0]), 'a started Renderer.');
  assert.equal(feedSentence({ text: '<unsafe>' }), '<unsafe>');
});
test('replay bounds stay tied to retained history, live bounds extend to now', () => {
  const state = { coverage: { from: '2026-01-01T00:00:00Z', to: '2026-01-01T00:10:00Z' }, clock: { at: '2026-01-01T00:05:00Z', server_now: '2026-01-01T01:00:00Z', mode: 'replay' } };
  assert.equal(bounds(state)[1] - bounds(state)[0], 600000);
  assert.equal(bounds({ ...state, clock: { ...state.clock, mode: 'live' } })[1] - bounds(state)[0], 3600000);
});
