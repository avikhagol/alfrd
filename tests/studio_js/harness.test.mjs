import test from 'node:test';
import assert from 'node:assert/strict';
import { bounds, filterFeed, feedSentence, requestTitle, defaultRequest, runRows, waitingFor, activitySentence, elapsed, requestStatusWord, attentionFor, costText, warningSentences, homeSections } from '../../src/alfrd/web/js/data/harness.js';

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

test('request titles and selection tolerate older v1 fields', () => {
  const requests = [{ id: 'r0001', status: 'running' }, { id: 'r0002', status: 'done' }];
  assert.equal(requestTitle(requests[0], [{ role: 'user', request: 'r0001', text: 'Build game\nDetails' }]), 'Build game');
  assert.equal(requestTitle(requests[1]), 'Request 2');
  assert.equal(requestTitle({ title: 'Title' }), 'Title');
  assert.equal(defaultRequest(requests, 'all'), requests[0]);
  assert.equal(defaultRequest(requests, 'r0002'), requests[1]);
  assert.equal(defaultRequest([]), null);
  assert.equal(defaultRequest([{ status: 'done' }]).status, 'done');
});
test('specialists, phases, dependencies and activity use titles', () => {
  const a = { row: 'r1.a', status: 'running', task: { title: 'Track editor' } };
  const b = { row: 'r1.b', kind: 'task', request: 'r1', depends_on: ['a'], status: 'pending' };
  const byRow = new Map([[a.row, a], [b.row, b]]);
  assert.deepEqual(waitingFor(b, byRow), [a]);
  assert.equal(activitySentence(b, byRow), 'Waiting for Track editor');
  assert.deepEqual(runRows({ view: { tasks: [b, { kind: 'plan' }] } }).tasks, [b]);
  for (const [kind, text] of Object.entries({ 'task.created': 'Queued', 'task.started': 'Started attempt 2', 'turn.started': 'Started attempt 2', 'turn.retrying': 'Retrying', 'task.blocked': 'Blocked', 'task.failed': 'Failed', 'turn.failed': 'Failed', 'task.finished': 'Finished', 'turn.finished': 'Finished', 'handoff.published': 'Published its output', 'task.reassigned': 'Reassigned', 'unknown.event': 'unknown event' })) {
    assert.equal(activitySentence({ latest: { kind, attempt: 2 } }), text);
  }
  assert.match(activitySentence({ latest: { kind: 'task.started', at: '2026-11-01T12:00:00Z' } }), /Started attempt 1 ·/);
  assert.equal(elapsed(a, [{ task: a.row, open: true, start: '2026-11-01T12:00:00Z' }], { at: '2026-11-01T12:04:00Z' }), 'running 4m (this attempt)');
  assert.equal(elapsed(a, [], {}), '');
});
test('request state never borrows a live status in replay', () => {
  assert.equal(requestStatusWord({ status: 'failed' }, []), 'Planning failed');
  assert.equal(requestStatusWord({ status: 'planning' }, []), 'Planning');
  assert.equal(requestStatusWord({ status: 'needs_decision' }, []), 'Needs you');
  assert.equal(requestStatusWord({ status: 'done' }, [{ status: 'running' }], true), 'Running at this time');
  assert.equal(requestStatusWord({}, [{ status: 'done' }], true), 'Done at this time');
  assert.equal(requestStatusWord({}, [{ status: 'pending' }], true), '0/1 done at this time');
  assert.equal(attentionFor({ items: [{ request: 'a' }, { request: 'b' }] }, 'a').other.length, 1);
});
test('cost distinguishes unknown, zero, complete and lower bounds', () => {
  assert.equal(costText({}).text, 'Cost unavailable');
  assert.equal(costText({ total_cost_usd: 0, complete: true }).text, '$0.00');
  assert.equal(costText({ total_cost_usd: 25, complete: true }, 30).text, '$25.00 / $30');
  assert.equal(costText({ total_cost_usd: 25, complete: false }, 30).text, 'At least $25.00 of $30');
  assert.equal(costText({ total_cost_usd: 25, complete: false }).text, 'At least $25.00 reported');
  assert.equal(costText({ total_cost_usd: 25 }, 30).tone, 'warn');
  assert.equal(costText({ total_cost_usd: 30 }, 30).tone, 'fail');
  assert.equal(costText({ total_cost_usd: 1 }, -1).budget, null);
  assert.equal(costText({ total_cost_usd: 1 }, null, ['legacy_usage_not_sampled']).partial, true);
  const warnings = warningSentences(['legacy_usage_not_sampled', 'legacy_history_partial', 'event_sequence_gap', 'before_first_event', 'unknown']);
  assert.equal(warnings.cost.length, 1); assert.equal(warnings.checklist.length, 2); assert.equal(warnings.summary.length, 2);
});
test('Home membership is exclusive, ordered by activity and capped after sorting', () => {
  const sessions = [{ id: 'a', pending_user: 1, active_requests: 1 }, { id: 'b', active_requests: 1 }, { id: 'c', created: '2020-01-01', last_message: { at: '2026-11-02' } }, { id: 'd', created: '2026-11-01' }, ...Array.from({ length: 22 }, (_, i) => ({ id: `old${i}`, created: '2025-01-01' }))];
  const groups = homeSections(sessions);
  assert.deepEqual(groups.needs.map((s) => s.id), ['a']); assert.deepEqual(groups.running.map((s) => s.id), ['b']);
  assert.deepEqual(groups.recent.slice(0, 2).map((s) => s.id), ['c', 'd']); assert.equal(groups.recent.length, 20);
});
