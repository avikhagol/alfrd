import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
globalThis.document = { hidden: false, addEventListener() {} };
const { server } = await import('../../src/alfrd/web/js/data/server.js');
const { loadPlan, forgetPlan, syncPlanCatalog, activeJobs, activePlan } = await import('../../src/alfrd/web/js/components/plans.js');
const { jobRows } = await import('../../src/alfrd/web/js/components/jobs_tray.js');
const app = await readFile(new URL('../../src/alfrd/web/js/app.js', import.meta.url), 'utf8');
const jobLabel = new Function(`${app.slice(app.indexOf('function jobLabel('), app.indexOf('function renderJobs('))}; return jobLabel;`)();
const plan = (id, status = 'running', extra = {}) => ({ id, status, ...extra });

test('Jobs includes all workflow/chat summaries across roots, deduplicates selection and excludes terminal plans', async () => {
  const old = server.planStatus, plugin = server.pluginJob;
  const ctx = { state: { mode: 'server', trees: { a: { provider: 'server' }, b: { provider: 'server' } }, scans: {} }, update() {}, log() {}, projectName: p => p, jobLabel };
  let data = { plan: plan('history', 'finished'), plans: [plan('one'), plan('two', 'paused'), plan('done', 'finished')],
    chat_plans: [plan('chat-s', 'running', { chat: { session: 's' }, title: 'Saved title', phase: 'decision' }), plan('chat-t'), plan('idle', 'finished')] };
  server.planStatus = async () => data;
  try {
    await loadPlan(ctx, 'a', { id: 'history' });
    assert.equal(activeJobs().length, 4);
    assert.equal(activePlan('a').plan.chat, undefined);
    data = { plan: plan('one'), units: [{ log: 'unit.log', status: 'running' }], plans: [plan('one'), plan('two', 'interrupted')], chat_plans: [] };
    await loadPlan(ctx, 'b');
    assert.equal(activeJobs().length, 6, 'same ID in two project roots stays separate');
    let rows = jobRows(ctx);
    assert.equal(rows.find(r => r.session === 's').name, 'a');
    assert.equal(rows.find(r => r.session === 's').title, 'Saved title');
    assert.match(rows.find(r => r.session === 's').log, /chat-s\/runner.log$/);
    assert.equal(rows.find(r => r.project === 'b' && r.run === 'one').log, 'unit.log');
    server.pluginJob = { id: 'plugin', status: 'running', action: 'install', target: 'x' };
    assert.equal(jobRows(ctx).filter(r => r.plugin).length, 1);
    data = { plan: null, plans: [], chat_plans: [plan('chat-only')] };
    await loadPlan(ctx, 'a'); assert.equal(activeJobs().filter(j => j.project === 'a').length, 1);
    data.chat_plans = []; await loadPlan(ctx, 'a');
    assert.equal(activeJobs().filter(j => j.project === 'a').length, 0);
    syncPlanCatalog(['a']); assert.equal(activeJobs().length, 0);
  } finally { server.planStatus = old; server.pluginJob = plugin; forgetPlan('a'); forgetPlan('b'); }
});

test('shared Jobs labels prioritize paused/interrupted and recognize summary waits', () => {
  for (const phase of ['manual', 'review', 'scheduled', 'decision']) assert.equal(jobLabel({ plan: plan('p', 'running', { phase }) }), 'Waiting');
  assert.equal(jobLabel({ plan: plan('p', 'running', { phase: 'waiting' }) }), 'Running');
  assert.equal(jobLabel({ plan: plan('p', 'paused', { start_at: 'future' }) }), 'Paused');
  assert.equal(jobLabel({ plan: plan('p', 'interrupted') }), 'Interrupted');
  assert.equal(jobLabel({ plan: plan('p'), waiting: [{}] }), 'Waiting');
  assert.equal(jobLabel({ plan: plan('p', 'running', { phase: 'running' }), waiting: [{}] }), 'Running');
});

test('in-flight events produce a trailing summary refresh and removed identities reject stale results', async () => {
  const old = server.planStatus, pending = []; let calls = 0;
  const ctx = { state: { mode: 'server', trees: { a: { provider: 'server' } } }, update() {}, log() {} };
  server.planStatus = () => { calls++; return new Promise(r => pending.push(r)); };
  try {
    const one = loadPlan(ctx, 'a'); const two = loadPlan(ctx, 'a'); loadPlan(ctx, 'a');
    pending.shift()({ plan: null, chat_plans: [plan('chat-s')] });
    await new Promise(r => setImmediate(r)); assert.equal(calls, 2);
    pending.shift()({ plan: null, chat_plans: [] }); await Promise.all([one, two]); assert.equal(activeJobs().length, 0);
    const stale = loadPlan(ctx, 'a'); forgetPlan('a'); pending.shift()({ plan: plan('stale') }); await stale;
    assert.equal(activeJobs().length, 0);
  } finally { server.planStatus = old; forgetPlan('a'); }
});
