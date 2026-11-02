import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || 'playwright')); } catch { /* optional */ }
const web = fileURLToPath(new URL('../../src/alfrd/web/', import.meta.url));

for (const layout of ['default', 'harness']) test(`Chat ${layout}: native landmarks, delegated decisions and task actions`, { skip: !chromium && 'Playwright not available' }, async (t) => {
  const service = createServer(async (req, res) => {
    if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); return res.end('<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/harness.css"><body style="display:block"><main id="home" style="height:900px"></main>'); }
    const path = resolve(web, req.url.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { res.setHeader('Content-Type', ['.js', '.mjs'].includes(extname(path)) ? 'text/javascript' : 'text/css'); res.end(await readFile(path)); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise(done => service.listen(0, '127.0.0.1', done));
  t.after(() => new Promise(done => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch(); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  page.setDefaultTimeout(7000);
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${service.address().port}/`);
  await page.evaluate(async layout => {
    const { server } = await import('/js/data/server.js');
    const { DEFAULTS } = await import('/js/data/chat.js');
    const { createChatView } = await import('/js/components/chat_view.js');
    window.calls = []; window.posts = []; window.closeCount = 0; window.selected = [];
    const task = { row: 'r1.build', request: 'r1', kind: 'task', owner: 'codex', status: 'failed', attempts: 2, task: { title: 'Build track', description: 'A detailed task description', acceptance_criteria: ['Keyboard works'], expected_outputs: [{ path: 'index.html', description: 'Game' }], budget: { max_retries: 3 } }, result: { summary: 'Partial result' }, error: 'Test error', logs: [] };
    const blockers = [{ id: 'd1', task: task.row, request: 'r1', kind: 'question', needs: 'user', question: 'Where should scores be saved?', status: 'pending' }];
    window.state = { summary: { usage: {}, wall_seconds: 0, morning: {} }, scope: { kind: 'all' }, clock: { mode: 'live' }, view: { session: { id: 's', title: 'Game' }, settings: DEFAULTS, requests: [{ id: 'r1', status: 'needs_decision', text: 'Build game' }], messages: [{ id: 'm1', role: 'user', request: 'r1', text: 'Build game' }], tasks: [task], graph: { nodes: [{ id: task.row, title: task.task.title, owner: 'codex', request: 'r1', status: 'failed', kind: 'task' }], edges: [] }, blockers, decisions: [] } };
    server.session = { mutations_enabled: true };
    server.request = async url => { calls.push(url); const body = url.endsWith('/settings') ? DEFAULTS : url.endsWith('/sessions') ? { sessions: [{ id: 's', title: 'Game' }] } : url.includes('/transcript') ? { attempts: [] } : { items: [], messages: [], events: [] }; return new Response(JSON.stringify(body)); };
    server.mutate = async (url, body) => { posts.push({ url, body }); return {}; };
    const host = document.getElementById('home');
    const view = window.view = createChatView({ projected: true, ...(layout === 'harness' ? { layout: 'harness' } : {}), onTask(row) { selected.push(row); if (view.slots()) view.slots().drawer.hidden = false; }, onTaskClose() { closeCount++; }, refresh() {} });
    host.append(view.root);
    const ctx = { state: { mode: 'server', selectedProject: 'p', chatSession: 's', liveStatus: { state: 'live' } }, activeProject: () => 'p', toast() {}, update() { view.render(view.root, ctx); }, modal() {} };
    view.render(view.root, ctx);
    await new Promise(resolve => setTimeout(resolve, 100));
    view.setProjection(state);
    if (layout === 'harness') document.body.classList.add('shell-harness');
  }, layout);
  assert.equal(await page.locator('.chat-side').count(), layout === 'default' ? 1 : 0);
  for (const selector of ['#chat-progress', '#chat-graph', '[data-chat=inspect]', '.chat-live']) assert.equal(await page.locator(selector).count(), layout === 'default' ? 1 : 0);
  assert.equal(await page.evaluate(() => view.slots() === null), layout === 'default');
  if (layout === 'default') await page.locator('[data-chat=inspect]').click();
  else {
    assert.equal(await page.evaluate(() => Object.values(view.slots()).every(n => view.root.contains(n))), true);
    assert.equal(await page.evaluate(() => Object.keys(view.slots()).length), 5);
    assert.equal(await page.locator('#chat-decisions').evaluate(n => !!(n.compareDocumentPosition(document.getElementById('chat-form')) & Node.DOCUMENT_POSITION_FOLLOWING)), true);
  }
  await page.locator('[name=answer]').fill('Local storage');
  await page.locator('[data-decision=d1] [type=submit]').click();
  await page.waitForFunction(() => posts.length === 1);
  assert.match(await page.evaluate(() => posts[0].url), /decisions\/d1$/);
  assert.equal(await page.evaluate(() => posts[0].body.answer), 'Local storage');
  await page.locator('[data-delegate=d1]').click();
  await page.waitForFunction(() => posts.length === 2);
  assert.deepEqual(await page.evaluate(() => posts[1].body), { delegate: true });
  await page.evaluate(() => {
    state.view.blockers = [{ ...state.view.blockers[0], id: 'linked', mailbox_message_id: 'mail1', delegated: true }, { ...state.view.blockers[0], id: 'repeated', repeat: 2 }];
    view.setProjection(state);
    const card = document.createElement('div'); card.dataset.mailId = 'mail1';
    card.scrollIntoView = () => { window.jumped = true; };
    document.getElementById('chat-log').append(card);
  });
  assert.equal(await page.locator('[data-decision=linked]').count(), 0);
  assert.equal(await page.locator('[data-delegate=linked], [data-delegate=repeated]').count(), 0);
  await page.locator('[data-mail-jump=mail1]').click();
  assert.equal(await page.evaluate(() => jumped), true);
  if (layout === 'default') await page.locator('[data-chat=inspect]').click();
  await page.evaluate(() => view.openTask('r1.build'));
  assert.match(await page.locator('#chat-task').innerText(), /A detailed task description/);
  await page.locator('#chat-task [data-transcript]').click();
  await page.waitForFunction(() => calls.some(url => url.endsWith('/tasks/r1.build/transcript')));
  await page.locator('#chat-task [data-task-act=retry]').click();
  await page.waitForFunction(() => posts.some(p => p.url.endsWith('/tasks/r1.build/retry')));
  await page.evaluate(() => { state.view.tasks[0].status = 'running'; view.setProjection(state); });
  await page.locator('#chat-task [data-task-act=cancel]').click();
  await page.waitForFunction(() => posts.some(p => p.url.endsWith('/tasks/r1.build/cancel')));
  await page.locator('#chat-task [data-chat=close-task]').click();
  assert.equal(await page.evaluate(() => closeCount), 1);
  if (layout === 'harness') {
    await page.setViewportSize({ width: 800, height: 900 });
    assert.equal(await page.locator('.chat').evaluate(n => n.classList.contains('chat-sessions-sheet')), true);
    await page.setViewportSize({ width: 1200, height: 900 });
    await page.waitForFunction(() => !document.querySelector('.chat').classList.contains('chat-sessions-sheet'));
    await page.evaluate(() => { state.clock.mode = 'replay'; view.setProjection(state); });
    assert.equal(await page.locator('#chat-decisions').evaluate(n => n.inert), true);
    assert.equal(await page.locator('#chat-form').evaluate(n => n.inert), true);
    assert.equal(await page.locator('#chat-task').evaluate(n => n.inert), true);
    const count = await page.evaluate(() => posts.length);
    await page.evaluate(() => { const form = document.querySelector('[data-decision=repeated]'); form.querySelector('textarea').value = 'Replay cannot submit'; form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(() => posts.length), count);
  }
  assert.deepEqual(errors, []);
  await page.evaluate(() => view.destroy());
});
