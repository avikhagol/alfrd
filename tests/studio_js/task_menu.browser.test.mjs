// Exercise real graph triggers, task API wiring and the Markdown viewer in Chromium.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));
const plugin = resolve(fileURLToPath(new URL("../../examples/plugins/alfrd-markdown/alfrd_markdown/web/", import.meta.url)));

test("graph menu: every item, shared triggers, keyboard, viewport and Markdown", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const requests = [];
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme")}/theme.css"><link rel="stylesheet" href="/plugin/index.css"><body style="display:block;height:auto"><div id="graph" style="height:550px"></div><button id="outside">Outside</button><div id="modal-host" hidden></div></body>`);
    if (url.pathname === "/shell.js") return send(`import { $ } from "/js/utils/dom.js"; let clearModalKeys; export ${shell}`, "text/javascript");
    if (url.pathname.startsWith("/api/")) {
      requests.push([req.method, url.pathname]);
      let body = {};
      if (url.pathname.endsWith("retry-preview")) body = { eligible: true, current_status: "failed", current_unit: "u1", affected_rows: ["a"] };
      else if (url.pathname.endsWith("/transcript")) body = { attempts: [{ unit: "u1", prompt_bytes: 30, response_bytes: 30 }] };
      else if (/\/transcript\/u1\/(prompt|response)$/.test(url.pathname)) body = { content: "# Archived document\n\n**Markdown content**", eof: true };
      return send(JSON.stringify(body), "application/json");
    }
    const [root, rel] = url.pathname.startsWith("/plugin/") ? [plugin, url.pathname.slice(8)] : [web, url.pathname.slice(1)];
    const path = resolve(root, rel);
    if (!path.startsWith(root + sep)) return send("missing", "text/plain", 404);
    try { return send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { return send("missing", "text/plain", 404); }
  });
  await new Promise(done => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise(done => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  for (const theme of ["obsidian-orbit", "daylight-orbit"]) for (const width of [1280, 390]) await t.test(`${theme} ${width}px`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 844 }, permissions: ["clipboard-read", "clipboard-write"] });
    t.after(() => page.close());
    page.on("dialog", dialog => dialog.accept());
    const errors = []; page.on("pageerror", e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async () => {
      const { modal } = await import('/shell.js');
      const { api } = await import('/js/components/plugin_api.js');
      const ctx = { modal, state: {}, toast() {}, update() {} };
      (await import('/plugin/index.js')).activate(api(ctx, (await import('/js/vendor/purify.es.mjs')).default));
      const { mountGraph } = await import('/js/components/graph_view.js');
      const { taskMenuApi } = await import('/js/components/task_menu_api.js');
      const { server } = await import('/js/data/server.js');
      server.session = { mutations_enabled: true, csrf_token: 'test' };
      window.calls = [];
      window.task = { row: 'a', status: 'failed', units: ['u1'], logs: [{ unit: 'u1', log: '.alfrd/plans/chat-test/logs/u1.log' }], result: { artifacts: ['answer.txt'] } };
      const menuApi = taskMenuApi({ project: () => 'p', sid: () => 's', state: () => ({ tasks: [window.task] }), refresh: async () => {},
        openLog: async rel => window.calls.push(['log', rel]), files: async row => window.calls.push(['files', row]),
        openMarkdown: async (path, content) => (await import('/js/components/files_tab.js')).openBlob(ctx, path, new Blob([content], { type: 'text/markdown' })) });
      // Preserve actual API calls while allowing the running-state branch to be exercised.
      const preview = menuApi.preview;
      menuApi.preview = async row => ({ ...(await preview(row)), current_status: window.task.status, eligible: window.task.status === 'failed' });
      window.graph = mountGraph(document.querySelector('#graph'), { taskMenu: menuApi });
      window.graph.update({ rev: 1, nodes: [{ id: 'a', title: 'Task A', status: 'failed', st: 'failed', tone: 'fail', label: 'Failed' }, { id: 'plan', orch: true, kind: 'plan', title: 'Planner', st: 'done', label: 'Done', tone: 'ok' }], edges: [] });
    });
    const trigger = page.locator('[data-gv-menu-for="a"]'), menu = page.getByRole('menu');
    const open = async () => { await trigger.click(); await menu.locator('[data-action="prompt"]').waitFor(); };
    await open();
    const labels = await menu.getByRole('menuitem').allTextContents();
    assert.deepEqual(labels, ['Open log', 'View prompt', 'View reply', 'View files', 'Retry', 'Copy ID']);
    assert.equal(await trigger.getAttribute('aria-expanded'), 'true');
    await page.keyboard.press('End'); assert.equal(await page.locator(':focus').getAttribute('data-action'), 'copy_id');
    await page.keyboard.press('ArrowDown'); assert.equal(await page.locator(':focus').getAttribute('data-action'), 'log');
    await page.keyboard.press('ArrowUp'); assert.equal(await page.locator(':focus').getAttribute('data-action'), 'copy_id');
    await page.keyboard.press('Home'); assert.equal(await page.locator(':focus').getAttribute('data-action'), 'log');
    await page.keyboard.press('Escape'); assert.equal(await menu.isVisible(), false);
    assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
    await page.locator('.node[data-key="a"]').click({ button: 'right' });
    await menu.locator('[data-action="prompt"]').waitFor();
    assert.deepEqual(await menu.getByRole('menuitem').allTextContents(), labels);
    await page.locator('#outside').click(); assert.equal(await menu.isVisible(), false);
    // Pointer coordinates at all viewport corners, including negative/overflow coordinates.
    for (const [x, y] of [[1, 1], [width - 1, 843], [-20, -20]]) {
      await page.locator('.node[data-key="a"]').dispatchEvent('contextmenu', { clientX: x, clientY: y });
      await menu.locator('[data-action="prompt"]').waitFor();
      const box = await menu.boundingBox();
      assert.ok(box.x >= 11 && box.y >= 11 && box.x + box.width <= width - 11 && box.y + box.height <= 833, JSON.stringify(box));
      await page.keyboard.press('Escape');
    }
    for (const action of ['log', 'files', 'copy_id', 'retry', 'prompt', 'reply']) {
      await open();
      if (action === 'copy_id') { await page.keyboard.press('End'); await page.keyboard.press('Enter'); }
      else await menu.locator(`[data-action="${action}"]`).click();
      if (action === 'prompt' || action === 'reply') {
        await page.locator('.file-markdown h1').waitFor();
        assert.equal(await page.locator('.file-markdown h1').innerText(), 'Archived document');
        assert.equal(await page.locator('.file-markdown strong').innerText(), 'Markdown content');
        await page.getByRole('button', { name: 'Close', exact: true }).click();
      } else await page.waitForFunction(() => document.querySelector('[data-gv-menu]').hidden);
    }
    assert.deepEqual(await page.evaluate(() => window.calls), [['log', '.alfrd/plans/chat-test/logs/u1.log'], ['files', 'a']]);
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'a');
    await page.evaluate(() => { window.task.status = 'running'; });
    await open();
    assert.equal(await menu.locator('[data-action="retry"]').count(), 0);
    assert.equal(await menu.locator('[data-action="cancel"]').getAttribute('class'), 'danger');
    await menu.locator('[data-action="cancel"]').click();
    await page.waitForFunction(() => document.querySelector('[data-gv-menu]').hidden);
    await page.locator('[data-gv-menu-for="plan"]').click();
    assert.deepEqual(await menu.getByRole('menuitem').allTextContents(), ['Copy ID']);
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    for (const action of ['retry', 'cancel']) assert.ok(requests.some(([method, url]) => method === 'POST' && url.endsWith('/' + action)), action);
    if (process.env.ALFRD_MENU_SCREENSHOTS) {
      await open();
      await page.screenshot({ path: `${process.env.ALFRD_MENU_SCREENSHOTS}/${theme}-${width}.png` });
    }
  });
});
