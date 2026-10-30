import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch {}
const web = new URL("../../src/alfrd/web/", import.meta.url);


test('retained switches reuse nodes, preserve drafts/dropdowns/scroll, reject late fetches, refresh away and evict', { skip: !chromium && 'Playwright not available' }, async (t) => {
  const service = createServer(async (req, res) => {
    try { const path = new URL(req.url, 'http://local').pathname; const file = new URL(path === '/' ? 'index.html' : path.slice(1), web);
      res.writeHead(200, { 'Content-Type': path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html' }); res.end(await readFile(file));
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => service.listen(0, '127.0.0.1', r));
  t.after(() => new Promise((r) => { service.close(r); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) }); t.after(() => browser.close());
  const page = await browser.newPage(); const errors = []; page.on('pageerror', (e) => errors.push(e.message));
  // Avoid booting the app: load a bare document under the same static server.
  await page.route('**/', (r) => r.fulfill({ contentType: 'text/html', body: '<div id="home" style="height:700px"></div>' }));
  await page.goto(`http://127.0.0.1:${service.address().port}/`);
  await page.evaluate(async () => {
    const { server } = await import('/js/data/server.js'); const mod = await import('/js/components/chat_view.js');
    window.held = []; window.hold = false; window.version = 1; window.calls = 0;
    const settings = { orchestrator: 'codex', agents: { codex: { label: 'Codex' } } };
    server.session = { mutations_enabled: true };
    server.request = async (url) => {
      const path = new URL(url, location.origin).pathname; let data = {};
      if (path.endsWith('/sessions')) data = { sessions: ['a','b','c','d'].map((id) => ({ id, title: id })) };
      else if (path.endsWith('/settings')) data = settings;
      else if (/\/sessions\/[a-d]$/.test(path)) {
        window.calls++; const sid = path.split('/').at(-1), rev = window.version;
        data = { rev, session: { id: sid }, settings, requests: [], tasks: [], graph: { nodes: [], edges: [] }, messages: Array.from({length:30}, (_, i) => ({id: `${sid}-${i}`, role:'user', text:`${sid} revision ${rev} message ${i}`})) };
        if (window.hold) await new Promise((r) => window.held.push(r));
      } else if (path.endsWith('/schedule')) data = { schedule: null, items: [], schedule_service: {available:true} };
      return new Response(JSON.stringify(data), {status:200});
    };
    const host = document.querySelector('#home');
    const ctx = window.ctx = { state: {mode:'server', selectedProject:'p', chatSession:'a'}, activeProject: () => ctx.state.selectedProject, projects: () => [{id:'p'},{id:'q'}], update: () => mod.render(host, ctx), toast: () => {} };
    window.go = (sid, project = 'p') => { ctx.state.chatSession = sid; ctx.state.selectedProject = project; ctx.update(); };
    ctx.update();
  });
  await page.locator('[data-key="m:a-29"]').waitFor();
  await page.locator('#chat-input').fill('draft A');
  await page.evaluate(() => { window.nodeA = document.querySelector('#chat-log'); nodeA.scrollTop = 40; window.scrollA = nodeA.scrollTop; window.menuA = document.querySelector('.chat-session-menu'); menuA.open = true; });
  await page.evaluate(() => go('b')); await page.locator('[data-key="m:b-29"]').waitFor();
  const instant = await page.evaluate(() => { window.hold = true; go('a'); return {same: nodeA === document.querySelector('#chat-log'), text: document.querySelector('#chat-log').textContent, draft: document.querySelector('#chat-input').value, menu: menuA.open, scroll: scrollA > 0 && nodeA.scrollTop === scrollA}; });
  assert.equal(instant.same, true); assert.ok(instant.text.includes('a revision 1')); assert.equal(instant.draft, 'draft A'); assert.equal(instant.menu, true); assert.equal(instant.scroll, true);
  // Force a GET for A, switch to B, then let A resolve despite its aborted signal.
  await page.evaluate(() => { window.dispatchEvent(new CustomEvent('alfrd-events', {detail:{project:'p', events:[{plan:'chat-a'}]}})); });
  await page.waitForFunction(() => held.length > 0);
  await page.evaluate(() => { go('b'); window.hold = false; for (const r of held.splice(0)) r(); });
  assert.ok((await page.locator('#chat-log').innerText()).includes('b revision 1'));
  assert.equal(await page.locator('[data-key="m:a-29"]').count(), 0);
  await page.evaluate(() => { window.version = 2; window.dispatchEvent(new CustomEvent('alfrd-events', {detail:{project:'p', events:[{plan:'chat-a'}]}})); go('a'); });
  await page.getByText('a revision 2 message 29', {exact:true}).waitFor();
  await page.evaluate(() => go('a', 'q')); await page.locator('[data-key="m:a-29"]').waitFor();
  assert.equal(await page.evaluate(() => nodeA === document.querySelector('#chat-log')), false, 'same SID in a different project has a separate view');
  await page.evaluate(() => go('c')); await page.locator('[data-key="m:c-29"]').waitFor();
  await page.evaluate(() => go('d')); await page.locator('[data-key="m:d-29"]').waitFor();
  await page.evaluate(() => go('a')); await page.locator('[data-key="m:a-29"]').waitFor();
  assert.equal(await page.evaluate(() => nodeA === document.querySelector('#chat-log')), false, 'fourth retained destination evicts the old DOM');
  assert.equal(await page.locator('#chat-input').inputValue(), 'draft A', 'eviction preserves model draft');
  assert.deepEqual(errors, []);
});
