import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || 'playwright')); } catch { /* optional existing tooling */ }
const web = fileURLToPath(new URL('../../src/alfrd/web/', import.meta.url));

test('harness uses native Chat slots, task drawer and replay with drafts intact', { skip: !chromium && 'Playwright not available' }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/obsidian-orbit/theme.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/harness.css"><body style="display:block"><main id="home" style="height:100vh"></main>'); return; }
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); res.end(); return; }
    try { res.setHeader('Content-Type', ['.js', '.mjs'].includes(extname(path)) ? 'text/javascript' : 'text/css'); res.end(await readFile(path)); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, '127.0.0.1', done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = []; page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${service.address().port}/#/harness/s?p=p&request=r0007`);
  await page.evaluate(async () => {
    const { server } = await import('/js/data/server.js');
    const { DEFAULTS } = await import('/js/data/chat.js');
    let harness; try { harness = await import('/js/components/harness_view.js'); } catch (e) { console.log(e.stack); throw e; }
    window.calls = []; window.mutations = [];
    const from = '2026-11-01T10:00:00Z', to = '2026-11-01T10:10:00Z';
    const fixture = (replay, all) => {
      const task = { row: 'r0007.render', request: 'r0007', kind: 'task', owner: 'codex', status: replay ? 'running' : 'done', task: { title: 'Renderer', role: 'build' }, units: [], logs: [] };
      const request = { id: 'r0007', text: 'Render racing game', title: 'Racing game', status: replay ? 'unknown' : 'done', created: from, finished: replay ? null : to, usage: { total_cost_usd: 15 } };
      const usage = { total_cost_usd: 15, complete: false };
      return { schema: 'alfrd.harness/1', project: 'p', session: 's', scope: { kind: all ? 'all' : 'request', request: all ? null : 'r0007' },
        clock: { mode: replay ? 'replay' : 'live', at: replay || to, server_now: to }, settings: { poll_seconds: 60, role_order: ['build'] },
        coverage: { from, to, first_seq: 1, last_seq: 2, warnings: ['legacy_history_partial'] }, revision: { consistent: true },
        agents: [{ id: 'codex', label: 'GPT (Codex)', status: replay ? 'running' : 'done', tasks: [task.row], usage }], handoff_edges: [],
        timeline_segments: [{ id: 'one', unit: 'u1', task: task.row, request: request.id, agent: 'codex', state: task.status, start: from, end: replay || to, open: !!replay, attempt: 1 }],
        feed_events: [{ seq: 1, at: from, kind: 'task.started', agent: 'codex', task: task.row, severity: 'info' }, ...(!replay ? [{ seq: 2, at: to, kind: 'task.finished', agent: 'codex', task: task.row, severity: 'info' }] : [])], feed_page: { has_more: false },
        summary: { tasks: { total: 1, done: replay ? 0 : 1, failed: 0 }, usage, wall_seconds: 600, morning: { finished: !replay, handoffs: 0, dependency_edges: 0, errors: [] } },
        view: { attention: { count: 0, items: [], mailbox_known: true }, session: { id: 's', title: 'Racing game' }, requests: [request], tasks: [task], settings: DEFAULTS, messages: [{ id: 'm1', role: 'user', request: 'r0007', at: from, text: 'Build a racing game' }], blockers: [], decisions: [], graph: { nodes: [{ id: task.row, title: 'Renderer', owner: 'codex', request: 'r0007', status: task.status, kind: 'task' }], edges: [] } } };
    };
    server.session = { mutations_enabled: true };
    server.request = async (url, options) => {
      if (options?.method === "POST") { window.sentMessage = JSON.parse(options.body); return new Response("{}", { status: 202 }); }
      window.calls.push(url); if (url.includes("/harness/state") && window.offline) throw Error("Disconnected"); const q = new URL(url, location.origin).searchParams;
      const body = url.includes('/harness/state') ? fixture(q.get('at'), q.get('scope') === 'all') : url.endsWith('/sessions') ? { sessions: [{ id: 's', title: 'Racing game' }, { id: 'other', title: 'Other chat' }] } : url.endsWith('/settings') ? DEFAULTS : url.includes('/mailbox/') ? { messages: [], events: [] } : { items: [] };
      if (url.includes("/harness/state")) window.patchProjection?.(body);
      if (url.endsWith("/sessions") && window.homeSessions) body.sessions = window.homeSessions;
      return new Response(JSON.stringify(body), { status: 200 });
    };
    server.mutate = async (url, data) => { window.mutations.push({ url, data }); if (window.homeError) throw Error(window.homeError); return url.endsWith("/sessions") ? { session: { id: "new" } } : {}; };
    const ctx = window.ctx = { state: { mode: 'server', chatSession: 's', selectedProject: 'p', liveStatus: { state: 'live' } }, activeProject: () => ctx.state.selectedProject, projectName: (p) => p, toast() {}, update() { harness.render(document.getElementById('home'), ctx); }, modal() {} };
    window.addEventListener('hashchange', () => { ctx.state.chatSession = decodeURIComponent(location.hash.split('?')[0].split('/')[2] || ''); ctx.update(); });
    window.harness = harness; ctx.update();
  });
  await page.locator('[data-h-checklist]').waitFor();
  assert.equal(await page.locator('#chat-graph').count(), 0);
  assert.equal(await page.locator('.chat-side').count(), 0);
  assert.equal(await page.locator('.chat-live').count(), 0);
  assert.equal(await page.locator('.chat-main').evaluate((n) => n.parentElement.classList.contains('chat')), true);
  await page.locator('#chat-input').fill('Unsent follow-up');
  await page.locator('[data-h-checklist]').click();
  await page.locator('[data-h-task="r0007.render"]').click();
  await page.locator('#chat-drawer').waitFor();
  assert.match(await page.locator('#chat-task').innerText(), /Renderer/);
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#chat-drawer').isVisible(), false);
  assert.equal(await page.locator('[data-h-task="r0007.render"]').evaluate((n) => n === document.activeElement), true);
  await page.evaluate(() => { location.hash = '#/harness/s?p=p&at=2026-11-01T10:01:00Z'; });
  await page.waitForFunction(() => document.querySelector('#chat-form').inert);
  assert.match(await page.locator('[data-h-replay]').innerText(), /read-only/);
  assert.equal(await page.locator('#chat-input').inputValue(), 'Unsent follow-up');
  await page.locator('[data-h-now]').click();
  await page.waitForFunction(() => !document.querySelector('#chat-form').inert);
  assert.equal(await page.locator('#chat-input').inputValue(), 'Unsent follow-up');
  await page.setViewportSize({ width: 390, height: 600 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.locator('[data-chat=sessions]').click();
  assert.equal(await page.locator('.chat').evaluate((n) => n.classList.contains('chat-sessions-open')), true);
  await page.locator('[data-chat=close-sessions]').click();
  assert.equal(await page.locator('.chat').evaluate((n) => n.classList.contains('chat-sessions-open')), false);
  // Stress panels with twenty tasks and three long questions. Fresh mobile mount defaults closed.
  await page.evaluate(() => {
    window.patchProjection = (data) => {
      data.view.tasks = Array.from({ length: 20 }, (_, i) => ({ ...data.view.tasks[0], row: `r0007.task${i}`, status: i < 2 ? 'done' : i === 2 ? 'running' : i === 3 ? 'blocked' : 'pending', task: { title: `Build track ${i + 1}` }, depends_on: i > 3 ? ['r0007.task2'] : [], latest: { kind: 'task.created', at: '2026-11-01T10:00:00Z' } }));
      data.view.blockers = Array.from({ length: 3 }, (_, i) => ({ id: `d${i}`, task: 'r0007.task3', request: 'r0007', kind: 'question', needs: 'user', status: 'pending', question: 'Choose the storage approach. '.repeat(20) }));
      data.view.attention = { count: 3, mailbox_known: true, items: data.view.blockers.map(d => ({ source: 'decision', id: d.id, request: d.request })) };
    };
    location.hash = '#/harness/mobile?p=p';
  });
  await page.waitForFunction(() => document.querySelector('[data-h-count]')?.textContent.includes('3 need'));
  assert.equal(await page.locator('#h-checklist').isVisible(), false);
  assert.equal(await page.locator('#chat-decisions').isVisible(), false);
  for (const [width, height] of [[390, 600], [844, 390], [720, 450]]) {
    await page.setViewportSize({ width, height });
    assert.equal(await page.evaluate(() => {
      const status = document.querySelector('.h-connection').getBoundingClientRect();
      const chats = document.querySelector('[data-chat=sessions]').getBoundingClientRect();
      const top = document.querySelector('.h-top').getBoundingClientRect();
      return status.right <= chats.left && chats.top >= top.top && chats.bottom <= top.bottom;
    }), true, `Chats leaves connection status clear at ${width}×${height}`);
    const heightCheck = async () => assert.ok(await page.locator('#chat-log').evaluate((n, h) => n.getBoundingClientRect().height >= h * .4, height), `thread at ${width}×${height}`);
    await heightCheck();
    await page.locator('[data-h-checklist]').click();
    assert.equal(await page.locator('#h-checklist li').count(), 20);
    await heightCheck();
    await page.locator('[data-h-decisions]').click();
    assert.equal(await page.locator('#h-checklist').isVisible(), false);
    assert.equal(await page.locator('#chat-decisions').isVisible(), true);
    await heightCheck();
    await page.locator('[data-h-decisions]').click();
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator('[data-h-decisions]').click();
  await page.locator('[data-h-checklist]').click();
  assert.ok(await page.locator('#chat-log').evaluate(n => n.getBoundingClientRect().height >= innerHeight * .25), 'both desktop panels leave a usable thread');
  assert.equal(await page.evaluate(() => {
    const panel = document.querySelector('#chat-decisions').getBoundingClientRect();
    const action = document.querySelector('[data-decision=d0] [type=submit]').getBoundingClientRect();
    return action.top >= panel.top && action.bottom <= panel.bottom;
  }), true, 'first decision action is visible without scrolling');
  await page.locator('[data-h-checklist]').click();
  await page.locator('[data-decision=d0] [name=answer]').fill('Half-typed answer');
  await page.locator('#chat-input').fill('Keep this draft');
  const before = await page.locator('#chat-log').evaluate(n => { n.scrollTop = 5; return n.scrollTop; });
  for (let cycle = 0; cycle < 30; cycle++) {
    const count = await page.evaluate(() => calls.length);
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.waitForFunction(count => calls.length >= count + 2, count);
  }
  assert.equal(await page.locator('[data-decision=d0] [name=answer]').inputValue(), 'Half-typed answer');
  assert.equal(await page.locator('#chat-input').inputValue(), 'Keep this draft');
  assert.equal(await page.locator('#chat-log').evaluate(n => n.scrollTop), before);
  // Only B changes on request selection; the conversation stays intact.
  await page.evaluate(() => {
    const previous = window.patchProjection;
    window.patchProjection = data => { previous(data); data.view.requests.push({ id: 'r0008', title: 'Another request', status: 'running' }); if (data.scope.kind === 'request') data.scope.request = new URLSearchParams(location.hash.split('?')[1]).get('request') || 'r0007'; };
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.locator('[data-h-request] option[value=r0008]').waitFor({ state: 'attached' });
  const messages = await page.locator('#chat-log').innerText();
  const allBefore = await page.evaluate(() => calls.filter(u => u.includes('scope=all')).length);
  await page.locator('[data-h-request]').selectOption('r0008');
  await page.waitForFunction(() => calls.some(u => u.includes('request=r0008')));
  assert.equal(await page.locator('#chat-log').innerText(), messages);
  assert.equal(await page.evaluate(() => calls.filter(u => u.includes('scope=all')).length), allBefore);
  await page.evaluate(() => { window.offline = true; document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForFunction(() => document.querySelector('[data-h-connection]').textContent === 'Reconnecting…');
  await page.evaluate(() => { window.offline = false; document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForFunction(() => document.querySelector('[data-h-connection]').textContent.startsWith('Connected'));
  for (const status of ['planning', 'failed']) {
    await page.evaluate(status => {
      window.patchProjection = data => { data.view.tasks = []; data.view.requests[0].status = status; data.view.requests[0].title = 'Logo generator'; data.view.attention = { count: 0, items: [] }; };
      document.dispatchEvent(new Event('visibilitychange'));
    }, status);
    await page.waitForFunction(status => document.querySelector('.h-run-status').textContent === (status === 'planning' ? 'Planning' : 'Planning failed'), status);
    assert.equal(await page.locator('[data-h-checklist]').count(), 0);
  }
  await page.evaluate(() => {
    window.patchProjection = data => { data.view.requests[0].mode = 'direct'; delete data.view.requests[0].title; delete data.view.requests[0].max_cost_usd; delete data.view.attention; };
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.locator('[data-h-older]').waitFor();
  assert.match(await page.locator('.h-run-summary').innerText(), /Direct to @codex/);
  assert.equal(await page.locator('.h-run-cost meter').count(), 0);
  assert.equal(await page.locator('[data-h-request] option').first().innerText(), 'Build a racing game');
  await page.evaluate(() => { window.patchProjection = data => { data.view.requests = []; data.view.tasks = []; }; document.dispatchEvent(new Event('visibilitychange')); });
  await page.waitForFunction(() => !document.querySelector('.h-run'));
  await page.locator('#chat-log').focus(); await page.keyboard.press('c');
  assert.equal(await page.locator('.h-run').count(), 0);
  // Home precedence, sorting, row cost and failed-send recovery.
  await page.evaluate(() => { window.patchProjection = data => { data.summary.usage = {}; }; window.homeSessions = [{ id: 'a', title: 'Needs answer', pending_user: 1, active_requests: 1, created: '2026-11-02' }, { id: 'b', title: 'Running game', active_requests: 1, created: '2026-11-02' }, { id: 'c', title: 'Recent work', last_message: { at: '2026-11-02' } }]; location.hash = '#/harness?p=p'; });
  await page.locator('.h-home h1').waitFor();
  await page.locator('[data-h-open=a]').waitFor();
  assert.equal(await page.locator('[data-h-open=a]').count(), 1);
  assert.match(await page.locator('[data-h-sessions]').innerText(), /Needs you.*Running.*Recent/is);
  await page.waitForFunction(() => document.querySelector('[data-h-cost=a]').textContent.includes('Session cost:'));
  await page.waitForFunction(() => document.querySelector('[data-h-cost=a]').textContent === ' · Session cost: unavailable');
  assert.ok((await page.locator('.h-home time').first().innerText()).length <= 8);
  assert.match(await page.locator('.h-home time').first().getAttribute('title'), /2026/);
  await page.evaluate(() => { window.homeError = 'Stub cannot accept this goal'; });
  await page.locator('#h-goal').fill('Keep the failed goal');
  await page.locator('[data-h-goal] [type=submit]').click();
  await page.locator('[data-h-error]').filter({ hasText: 'Stub cannot accept this goal' }).waitFor();
  assert.equal(await page.locator('#h-goal').inputValue(), 'Keep the failed goal');
  assert.deepEqual(errors, []);
  await page.evaluate(() => harness.leave());
  assert.equal(await page.evaluate(() => document.body.classList.contains('shell-harness')), false);
});
