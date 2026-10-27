import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || 'playwright')); } catch { /* optional */ }
const web = fileURLToPath(new URL('../../src/alfrd/web/', import.meta.url));

test('live chat panel resizing, compact rows and accessible actions retain thread and drafts', { skip: !chromium && 'Playwright unavailable' }, async t => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const send = (data, type) => { res.writeHead(200, { 'Content-Type': type }); res.end(data); };
    if (url.pathname === '/') return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme") || "obsidian-orbit"}/theme.css"><body style="display:block;margin:0"><main id="home" style="height:100dvh"></main></body>`, 'text/html');
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { send(await readFile(path), extname(path) === '.js' ? 'text/javascript' : 'text/css'); } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(done => service.listen(0, '127.0.0.1', done));
  t.after(() => new Promise(done => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const open = async (width, theme) => {
    const page = await browser.newPage({ viewport: { width, height: 900 } }); t.after(() => page.close());
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async theme => {
      document.documentElement.dataset.theme = theme;
      const { server } = await import('/js/data/server.js');
      const { DEFAULTS } = await import('/js/data/chat.js');
      window.rows = [{ id: 's', title: 'A very long saved conversation title '.repeat(8), plan_status: 'running', active_requests: 1, pending_user: 0 }];
      window.checkPending = null;
      Object.assign(server, { session: { mutations_enabled: true }, request: async url => new Response(JSON.stringify(
        url.endsWith('/sessions') ? { sessions: rows } : url.endsWith('/settings') ? DEFAULTS : url.includes('/mailbox/') ? { messages: [], events: [] } : url.includes('/activity') ? { items: [] } : {
          session: { id: 's' }, requests: [], tasks: [], messages: [{ id: 'm1', role: 'user', text: 'Durable transcript' }],
          settings: DEFAULTS, graph: { nodes: [], edges: [] }, decisions: [], blockers: [], plan: { id: 'chat-s', status: 'running' }
        })), mutate: async url => { if (url.endsWith('/check')) return new Promise((resolve, reject) => { window.checkPending = { resolve, reject }; }); return {}; } });
      const mod = await import('/js/components/chat_view.js'), host = document.querySelector('#home');
      const ctx = { state: { mode: 'server', selectedProject: 'p', chatSession: 's', liveStatus: { state: 'live' } }, activeProject: () => 'p', projectName: () => 'Project', update() { mod.render(host, ctx); }, toast() {} };
      ctx.projects = () => [{ id: 'p' }]; ctx.chatOn = () => true;
      const { mountChatProjects } = await import('/js/data/chat_projects.js');
      const controller = mountChatProjects(ctx); controller.sync();
      window.ctx = ctx; mod.render(host, ctx);
    }, theme);
    await page.locator('.chat-session[data-sid=s]').waitFor({ state: 'attached' });
    return { page, errors };
  };
  for (const theme of ['obsidian-orbit', 'daylight-orbit']) for (const width of [360, 900, 1280, 1440]) {
    await t.test(`${theme} ${width}: resize boundaries and sheet behavior`, async () => {
      const { page, errors } = await open(width, theme);
      await page.locator('#chat-input').fill('Draft stays');
      assert.equal(await page.locator('.chat-sessions h2').count(), 0);
      assert.equal(await page.locator('.chat-list button,.chat-session time,.chat-session .badge').count(), 0);
      assert.equal(await page.locator('[data-chat=new]').getAttribute('aria-label'), 'Start a new chat session');
      for (const action of ['new', 'inspect', 'check']) {
        assert.equal(await page.locator(`[data-chat=${action}] svg`).count(), 1);
        if (width > 900 || action !== 'new') {
          const rect = await page.locator(`[data-chat=${action}]`).boundingBox();
          assert.ok(rect.width >= (width > 900 ? 32 : 44) && rect.height >= (width > 900 ? 32 : 44));
        }
      }
      if (width > 900) {
        const h = page.getByRole('separator', { name: 'Resize chat sessions' });
        await h.focus(); await page.keyboard.press('End'); assert.equal(await h.getAttribute('aria-valuenow'), '360');
        assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('alfrd-studio:ui:chat.sessionsWidth'))), 360);
        await page.keyboard.press('Home'); assert.equal(await h.getAttribute('aria-valuetext'), '160 pixels');
        await page.keyboard.press('Enter'); assert.equal(await h.getAttribute('aria-valuenow'), '220');
        const rect = await h.boundingBox();
        await page.mouse.move(rect.x + 6, rect.y + 120); await page.mouse.down(); await page.mouse.move(rect.x + 66, rect.y + 120); await page.mouse.up();
        assert.equal(await h.getAttribute('aria-valuenow'), '280');
        assert.equal(await page.locator('#chat-input').inputValue(), 'Draft stays');
        await h.focus(); await page.keyboard.press('ArrowRight');
        const before = await h.getAttribute('aria-valuenow');
        const r = await h.boundingBox(); await page.mouse.move(r.x + 6, r.y + 120); await page.mouse.down(); await page.mouse.move(r.x - 44, r.y + 120); await page.keyboard.press('Escape'); await page.mouse.up();
        assert.equal(await h.getAttribute('aria-valuenow'), before);
        await page.locator('.chat-session').focus();
        await page.evaluate(() => { window.focused = document.activeElement; rows[0].plan_status = 'finished'; rows[0].active_requests = 0; rows[0].title = 'Renamed while focused'; window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'p', events: [{ plan: 'chat-s' }] } })); });
        await page.waitForFunction(() => document.querySelector('.chat-session b')?.title === 'Renamed while focused');
        assert.equal(await page.evaluate(() => document.activeElement === window.focused), true);
        assert.equal(await page.locator('.chat-session .chat-running').count(), 0);
        assert.equal(await page.locator('[data-chat=delete]').isDisabled(), false);
        await page.locator('[data-chat=collapse]').click(); assert.equal(await h.isVisible(), false);
        await page.locator('[data-chat=sessions]').click(); assert.equal(await h.getAttribute('aria-valuenow'), before);
        await page.setViewportSize({ width: 900, height: 900 }); assert.equal(await h.isVisible(), false);
        await page.setViewportSize({ width, height: 900 }); await h.waitFor(); assert.equal(await h.getAttribute('aria-valuenow'), before);
      } else {
        assert.equal(await page.getByRole('separator', { name: 'Resize chat sessions', includeHidden: true }).isVisible(), false);
        await page.locator('[data-chat=sessions]').click();
        assert.equal(await page.locator('#chat-sessions').getAttribute('aria-modal'), 'true');
        await page.keyboard.press('Escape');
      }
      const check = page.locator('[data-chat=check]'); await check.click();
      await page.waitForFunction(() => window.checkPending);
      assert.equal(await check.getAttribute('aria-label'), 'Checking'); assert.equal(await check.getAttribute('aria-busy'), 'true');
      await page.evaluate(() => checkPending.reject(Error('offline')));
      await page.waitForFunction(() => document.querySelector('[data-chat=check]').getAttribute('aria-busy') === 'false');
      assert.equal(await check.locator('svg').count(), 1); assert.match(await page.locator('#chat-check-feedback').textContent(), /offline/);
      await check.click(); await page.evaluate(() => checkPending.resolve({ findings: [] }));
      await page.waitForFunction(() => document.querySelector('[data-chat=check]').getAttribute('aria-busy') === 'false');
      assert.equal(await check.locator('svg').count(), 1);
      await page.locator('[data-chat=inspect]').click();
      const inspector = page.locator('#chat-inspector');
      if (width <= 1280) assert.equal(await inspector.getAttribute('aria-modal'), 'true');
      else {
        const h = page.getByRole('separator', { name: 'Resize chat inspector' });
        await h.focus(); await page.keyboard.press('Home'); assert.equal(await h.getAttribute('aria-valuenow'), '280');
        await page.keyboard.press('ArrowLeft'); assert.equal(await h.getAttribute('aria-valuenow'), '300');
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.locator('#chat-input').inputValue(), 'Draft stays');
      if (width > 900) assert.ok(await page.locator('.chat-main').evaluate(e => e.getBoundingClientRect().width) >= 360);
      await page.screenshot({ path: `/tmp/cux-${theme}-${width}.png` });
      await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.deepEqual(errors, []);
      await page.close();
    });
  }
});
