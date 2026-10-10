// Exercise the app's lazy folder entry points with the real module and modal.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));

test("Lazy folder browser: picker, Import, Open project and cancellation", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const picker = app.slice(app.indexOf("  browseFolder(host"), app.indexOf("  navigate(view"));
  const importDialog = app.slice(app.indexOf('function openImport(tab'), app.indexOf('async function importEntries('));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") return send('<!doctype html><body><div id="home"><input id="path"><div id="folders" hidden></div></div><div id="modal-host" hidden></div></body>');
    if (url.pathname === "/js/fixture.js") return send(`
      import { $, on, esc, icon } from "./utils/dom.js";
      import { server } from "./data/server.js";
      let clearModalKeys;
      ${shell}
      const state = { mode: "server" };
      const canPickDirectory = () => false;
      const folderMod = async () => {};
      const loadServer = async () => {};
      export const ctx = { ${picker} modal, toast: (message) => window.errors.push(message), log() {} };
      export ${importDialog}
      window.errors = []; window.opened = []; window.created = [];
      server.session = { mutations_enabled: true, default_manifest: true };
      server.canBrowse = () => true;
      server.listFolders = async (path) => ({ path: path || "/demo", parent: "/", writable: false, is_project: false,
        entries: [{ name: "project", path: "/demo/project", is_project: true }, { name: "plain", path: "/demo/plain" }] });
      server.connect = async (path) => { window.opened.push(path); return { name: "project", root_path: path }; };
    `, "text/javascript");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web + sep)) return send("missing", "text/plain", 404);
    try { return send(await readFile(path), extname(path) === ".js" ? "text/javascript" : "text/plain"); }
    catch { return send("missing", "text/plain", 404); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const open = async () => {
    const page = await browser.newPage(); t.after(() => page.close());
    page.setDefaultTimeout(5000);
    await page.goto(`http://127.0.0.1:${service.address().port}/`);
    await page.evaluate(async () => { window.fixture = await import("/js/fixture.js"); });
    return page;
  };

  await t.test("Create/quickstart picker keeps its synchronous destroy handle and selects a path", async () => {
    const page = await open();
    let requests = 0;
    page.on("request", (r) => { if (r.url().endsWith("/folder_browser.js")) requests++; });
    assert.equal(requests, 0);
    await page.evaluate(() => { window.picker = fixture.ctx.browseFolder(document.querySelector('#folders'), document.querySelector('#path')); });
    await page.locator('[data-fsb-use]').click();
    assert.equal(await page.locator('#path').inputValue(), "/demo");
    assert.equal(await page.locator('#folders').isVisible(), false);
    assert.equal(requests, 1);
    assert.deepEqual(await page.evaluate(() => errors), []);
  });
  await t.test("Destroy while import is pending prevents mounting", async () => {
    const page = await open();
    let release, requested;
    const waiting = new Promise((done) => { requested = done; });
    const ready = new Promise((done) => { release = done; });
    await page.route('**/components/folder_browser.js', async (route) => { requested(); await ready; await route.continue(); });
    await page.evaluate(() => { window.picker = fixture.ctx.browseFolder(document.querySelector('#folders'), document.querySelector('#path')); });
    await waiting;
    await page.evaluate(() => picker.destroy());
    const loaded = page.waitForResponse('**/components/folder_browser.js');
    release(); await loaded;
    // Flush the import's continuation before checking that nothing mounted.
    await page.evaluate(async () => { await import('/js/components/folder_browser.js'); });
    assert.equal(await page.locator('#folders').innerHTML(), "");
    assert.equal(await page.locator('#folders').isVisible(), false);
  });
  await t.test("Import Browse toggles, reopens and connects the selected project", async () => {
    const page = await open();
    await page.evaluate(() => fixture.openImport());
    const browse = page.locator('[data-act=browse-server]');
    await browse.click(); await page.locator('[data-fsb-connect="0"]').waitFor();
    assert.equal(await browse.getAttribute('aria-expanded'), "true");
    await browse.click(); assert.equal(await page.locator('#imp-browse').isVisible(), false);
    await browse.click(); await page.locator('[data-fsb-connect="0"]').click();
    assert.deepEqual(await page.evaluate(() => opened), ["/demo/project"]);
    assert.deepEqual(await page.evaluate(() => errors), ["Project project connected"]);
  });
  await t.test("Open project preserves Open and Create project here actions", async () => {
    const page = await open();
    await page.evaluate(async () => {
      const { openProject } = await import('/js/components/agent_dialog.js');
      await openProject(fixture.ctx, () => {}, "", (path) => created.push(path));
    });
    await page.locator('[data-fsb-create]').click();
    assert.deepEqual(await page.evaluate(() => created), ["/demo"]);
    assert.deepEqual(await page.evaluate(() => opened), []);
  });
});
