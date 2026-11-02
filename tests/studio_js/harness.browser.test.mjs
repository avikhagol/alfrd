import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || 'playwright')); } catch { /* optional existing tooling */ }
const web = fileURLToPath(new URL('../../src/alfrd/web/', import.meta.url));

test('harness mounts shared chat/graph and replays observer state with drafts intact', { skip: !chromium && 'Playwright not available' }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') { res.setHeader('Content-Type', 'text/html'); res.end('<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/obsidian-orbit/theme.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/harness.css"><body style="display:block"><main id="home"></main>'); return; }
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); res.end(); return; }
    try { res.setHeader('Content-Type', extname(path) === '.js' ? 'text/javascript' : 'text/css'); res.end(await readFile(path)); }
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
    const harness = await import('/js/components/harness_view.js');
    window.calls = []; window.mutations = [];
    const from = '2026-11-01T10:00:00Z', to = '2026-11-01T10:10:00Z';
    const fixture = (replay, all) => {
      const task = { row: 'r0007.render', request: 'r0007', kind: 'task', owner: 'codex', status: replay ? 'running' : 'done', task: { title: 'Renderer', role: 'build' }, units: [], logs: [] };
      const request = { id: 'r0007', text: 'Render racing game', status: replay ? 'running' : 'done', created: from, finished: replay ? null : to, usage: { total_cost_usd: 15 } };
      const usage = { total_cost_usd: 15, complete: false };
      return { schema: 'alfrd.harness/1', project: 'p', session: 's', scope: { kind: all ? 'all' : 'request', request: all ? null : 'r0007' },
        clock: { mode: replay ? 'replay' : 'live', at: replay || to, server_now: to }, settings: { poll_seconds: 60, role_order: ['build'] },
        coverage: { from, to, first_seq: 1, last_seq: 2, warnings: ['legacy_history_partial'] }, revision: { consistent: true },
        agents: [{ id: 'codex', label: 'GPT (Codex)', status: replay ? 'running' : 'done', tasks: [task.row], usage }], handoff_edges: [],
        timeline_segments: [{ id: 'one', unit: 'u1', task: task.row, request: request.id, agent: 'codex', state: task.status, start: from, end: replay || to, open: !!replay, attempt: 1 }],
        feed_events: [{ seq: 1, at: from, kind: 'task.started', agent: 'codex', task: task.row, severity: 'info' }, ...(!replay ? [{ seq: 2, at: to, kind: 'task.finished', agent: 'codex', task: task.row, severity: 'info' }] : [])], feed_page: { has_more: false },
        summary: { tasks: { total: 1, done: replay ? 0 : 1, failed: 0 }, usage, wall_seconds: 600, morning: { finished: !replay, handoffs: 0, dependency_edges: 0, errors: [] } },
        view: { session: { id: 's', title: 'Racing game' }, requests: [request], tasks: [task], settings: DEFAULTS, messages: [{ id: 'm1', role: 'user', request: 'r0007', at: from, text: 'Build a racing game' }], blockers: [], decisions: [], graph: { nodes: [{ id: task.row, title: 'Renderer', owner: 'codex', request: 'r0007', status: task.status, kind: 'task' }], edges: [] } } };
    };
    server.session = { mutations_enabled: true };
    server.request = async (url, options) => {
      if (options?.method === "POST") { window.sentMessage = JSON.parse(options.body); return new Response("{}", { status: 202 }); }
      window.calls.push(url); const q = new URL(url, location.origin).searchParams;
      const body = url.includes('/harness/state') ? fixture(q.get('at'), q.get('scope') === 'all') : url.endsWith('/sessions') ? { sessions: [{ id: 's', title: 'Racing game' }, { id: 'other', title: 'Other chat' }] } : url.endsWith('/settings') ? DEFAULTS : url.includes('/mailbox/') ? { messages: [], events: [] } : { items: [] };
      return new Response(JSON.stringify(body), { status: 200 });
    };
    server.mutate = async (url, data) => { window.mutations.push({ url, data }); return {}; };
    const ctx = window.ctx = { state: { mode: 'server', chatSession: 's', selectedProject: 'p', liveStatus: { state: 'live' } }, activeProject: () => ctx.state.selectedProject, projectName: (p) => p, toast() {}, update() { harness.render(document.getElementById('home'), ctx); }, modal() {} };
    window.addEventListener('hashchange', () => { ctx.state.chatSession = decodeURIComponent(location.hash.split('?')[0].split('/')[2] || ''); ctx.update(); });
    window.harness = harness; ctx.update();
  });
  await page.locator('[data-h-agent=codex]').waitFor();
  assert.equal(await page.locator('#chat-graph').isVisible(), false);
  assert.match(await page.locator('[data-h-totals]').innerText(), /r0007.*15/);
  await page.locator('#chat-input').fill('Unsent follow-up');
  await page.getByRole('tab', { name: 'Tasks', exact: true }).click();
  await page.locator('#chat-graph .node').first().waitFor();
  assert.equal(await page.locator('#chat-graph .node').count(), 1);
  await page.locator('#chat-graph .node').first().click();
  await page.locator('#chat-task').waitFor();
  await page.getByRole('tab', { name: 'Timeline', exact: true }).click();
  await page.locator('[data-h-seek]').evaluate((el) => { el.value = Number(el.min) + 60; el.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.waitForFunction(() => document.querySelector('[data-h-time]').textContent.includes('Replay'));
  await page.getByRole('tab', { name: 'Agents', exact: true }).click();
  assert.match(await page.locator('[data-h-agent]').innerText(), /working/);
  await page.locator('[data-h-agent]').click();
  assert.equal(await page.locator('[data-h-agent]').getAttribute('aria-pressed'), 'true');
  await page.getByRole('tab', { name: 'Feed', exact: true }).click();
  assert.equal(await page.locator('.harness-feed li').count(), 1);
  assert.equal(await page.locator('[data-h-agent-filter]').inputValue(), 'codex');
  await page.locator('[data-h-errors]').check();
  assert.equal(await page.locator('.harness-feed li').count(), 0);
  await page.locator('[data-h-errors]').uncheck();
  await page.locator('[data-h-speed]').selectOption('60');
  await page.locator('[data-h-play]').click();
  await page.waitForFunction(() => new URLSearchParams(location.hash.split('?')[1]).get('at') > '2026-11-01T10:01:00.000Z');
  await page.locator('[data-h-play]').click();
  await page.getByRole('tab', { name: 'Chat', exact: true }).click();
  assert.equal(await page.locator('#chat-form').evaluate((el) => el.inert), true);
  assert.equal(await page.locator('#chat-input').inputValue(), 'Unsent follow-up');
  await page.locator('[data-h-live]').click();
  await page.waitForFunction(() => !document.querySelector('#chat-form').inert);
  assert.equal(await page.locator('#chat-input').inputValue(), 'Unsent follow-up');
  await page.getByRole('tab', { name: 'Stats', exact: true }).click();
  await page.getByRole('heading', { name: /Morning summary/ }).waitFor();
  const beforeWidth = await page.locator('.harness-room').evaluate((el) => el.getBoundingClientRect().width);
  await page.locator('.harness-resize').focus();
  await page.keyboard.press('ArrowLeft');
  assert.ok(await page.locator('.harness-room').evaluate((el) => el.getBoundingClientRect().width) > beforeWidth);
  await page.locator('[data-h-expand]').click();
  assert.equal(await page.locator('.harness').evaluate((el) => el.classList.contains('harness-expanded')), true);
  await page.keyboard.press('Escape');
  await page.locator('[data-h-scope]').selectOption('all');
  await page.waitForFunction(() => document.querySelector('[data-h-totals]').textContent.startsWith('All requests'));
  assert.equal(await page.evaluate(() => calls.some((u) => u.includes('/harness/state?') && u.includes('at='))), true);
  assert.deepEqual(await page.evaluate(() => mutations), []);
  assert.deepEqual(errors, []);
  await page.getByRole('tab', { name: 'Chat', exact: true }).click();
  await page.locator('#chat-send').click();
  assert.equal(await page.evaluate(() => sentMessage.text), 'Unsent follow-up');
  await page.getByRole('tab', { name: 'Stats', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 850 });
  assert.equal(await page.locator('#chat-sessions').isVisible(), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: '/tmp/harness-mobile.png' });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.screenshot({ path: '/tmp/harness-desktop.png' });
  await page.evaluate(() => { ctx.state.selectedProject = 'other-project'; ctx.state.chatSession = 's'; ctx.update(); });
  await page.waitForFunction(() => calls.some((u) => u.includes('/projects/other-project/chat/sessions/s/harness/state')));
  assert.equal(await page.locator('.harness').count(), 1);
  assert.equal(await page.locator('#chat-sessions').count(), 1);
  await page.evaluate(() => harness.leave());
});
