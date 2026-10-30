import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname } from "node:path";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch {}
const web = new URL("../../src/alfrd/web/", import.meta.url);

test("HTML outlines precede data, replace each region in place, and defer closed panels", { skip: !chromium && "Playwright not available" }, async (t) => {
  const service = createServer(async (req, res) => {
    const path = new URL(req.url, "http://local").pathname;
    const file = new URL(path === "/" ? "index.html" : path === "/theme.css" ? "css/themes/obsidian-orbit/theme.css" : path.slice(1), web);
    try {
      const body = await readFile(file);
      res.writeHead(200, { "Content-Type": ({ ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml" })[extname(file.pathname)] || "text/plain" }); res.end(body);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => service.listen(0, "127.0.0.1", r));
  t.after(() => new Promise((r) => { service.close(r); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  for (const width of [1440, 390]) await t.test(`${width}px, reduced motion`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 1000 }, reducedMotion: "reduce" });
    page.setDefaultTimeout(8000);
    const errors = [], paths = [], held = new Map();
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => paths.push(new URL(r.url()).pathname));
    await page.addInitScript(() => { localStorage.setItem("alfrd-studio:prefs.v2", JSON.stringify({ version: 2, values: { "ui.experimental_chat": true } })); window.EventSource = class { addEventListener() {} close() {} }; });
    const json = (route, data) => route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
    const settings = { orchestrator: "codex", agents: { codex: { label: "Codex" } } };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/studio/session" || path.endsWith("/chat/sessions") || path.endsWith("/chat/sessions/s")) { held.set(path, route); return; }
      if (path === "/api/projects") return json(route, { projects: [{ name: "p", identifier: "p", display_name: "Project" }] });
      if (path.endsWith("/scan")) return json(route, { alfrd_avica_scan: 1, root: "/tmp/p", root_name: "p", files: [], ms_paths: [] });
      if (path.endsWith("/settings")) return json(route, settings);
      if (path.endsWith("/schedule")) return json(route, { items: [], schedule: null, schedule_service: { available: true } });
      return json(route, {});
    });
    const release = async (path, body) => {
      for (let i = 0; !held.has(path) && i < 200; i++) await new Promise((r) => setTimeout(r, 10));
      assert.ok(held.has(path), path); await json(held.get(path), body); held.delete(path);
    };
    await page.goto(`http://127.0.0.1:${service.address().port}/#/chat/s?p=p`, { waitUntil: "domcontentloaded" });
    await page.locator("#startup-outline .loading-layout").waitFor();
    assert.equal(await page.locator(".outline-composer").isVisible(), true);
    assert.equal(await page.locator(".skeleton-block").first().evaluate((e) => getComputedStyle(e).animationName), "none");
    await page.evaluate(() => { window.roots = [document.querySelector(".topbar"), document.querySelector("#workspace-rail"), document.querySelector("#main"), document.querySelector(".footbar")]; });
    await release("/api/studio/session", { app: "alfrd", projects: ["p"], default_project: "p", runtime_enabled: true, mutations_enabled: true, experimental: true, ui_flags: { "app.experimental": true } });
    await page.locator("#chat-list .skeleton-sessions").waitFor({ state: "attached" });
    await page.locator("#chat-log .skeleton-messages").waitFor();
    await release("/api/studio/projects/p/chat/sessions", { sessions: [{ id: "s", title: "Loaded conversation" }] });
    await page.locator('a[data-sid="s"]').waitFor({ state: "attached" });
    assert.equal(await page.locator("#chat-list .skeleton-block").count(), 0);
    assert.equal(await page.locator("#chat-log").getAttribute("aria-busy"), "true");
    const before = await page.locator("#chat-log").boundingBox();
    await page.evaluate(() => { window.logNode = document.querySelector("#chat-log"); window.listNode = document.querySelector("#chat-list"); });
    await release("/api/studio/projects/p/chat/sessions/s", { rev: 1, session: { id: "s", title: "Loaded conversation" }, settings, requests: [], tasks: [], graph: { nodes: [], edges: [] }, messages: [{ id: "m", role: "assistant", text: "Content has arrived" }] });
    await page.locator('[data-key="m:m"]').waitFor();
    assert.equal(await page.locator("#chat-log .skeleton-block").count(), 0);
    assert.equal(await page.locator("#chat-log").getAttribute("aria-busy"), "false");
    const after = await page.locator("#chat-log").boundingBox();
    for (const key of ["x", "y", "width", "height"]) assert.ok(Math.abs(after[key] - before[key]) <= 1, `${key} stays fixed: ${before[key]} → ${after[key]}`);
    assert.equal(await page.evaluate(() => logNode === document.querySelector("#chat-log") && listNode === document.querySelector("#chat-list") && roots.every((el) => el.isConnected)), true);
    const deferred = ["metadata.js", "results.js", "alfrd_config.js", "skills_explorer.js", "goals_tab.js", "graph_view.js"];
    for (const name of deferred) assert.equal(paths.some((p) => p.endsWith(`/${name}`)), false, `${name} stays deferred`);
    assert.equal(paths.some((p) => /\/(goals|overview)$/.test(p)), false, "no hidden inspector data");
    await page.getByRole("button", { name: "Show or hide the inspector", exact: true }).click();
    await page.locator("#chat-graph .gv-canvas").waitFor();
    assert.ok(paths.some((p) => p.endsWith("/graph_view.js")));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    await page.close();
  });
});
