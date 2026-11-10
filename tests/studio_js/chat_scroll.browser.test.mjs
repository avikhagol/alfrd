import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

test("chat fits its host with notices and console space reserved", { skip: !chromium && "Playwright not available" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (data, type) => { res.writeHead(200, { "Content-Type": type }); res.end(data); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme")}/theme.css"><link rel="stylesheet" href="/css/chat.css"><body class="console-open"><div class="app"><header class="topbar"></header><nav class="rail"></nav><main id="main"><div id="project-load-notice" style="height:200px;flex:none">Project notice</div><section id="home" class="view"></section></main><footer class="footbar"></footer></div><aside id="dock" hidden></aside></body>`, "text/html");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const open = async (theme, width) => {
    const page = await browser.newPage({ viewport: { width, height: 980 } });
    page.setDefaultTimeout(7000);
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async () => {
      const { server } = await import("/js/data/server.js");
      const { DEFAULTS } = await import("/js/data/chat.js");
      const messages = Array.from({ length: 8 }, (_, i) => ({ id: `m${i}`, request: "r", role: "user", text: `Message ${i}`, at: "2026-10-25T12:00:00Z" }));
      const fixture = { session: { id: "s", title: "Scroll regression" }, requests: [{ id: "r", text: "Check layout", status: "done" }],
        messages, tasks: [], settings: DEFAULTS, graph: { nodes: [], edges: [] }, blockers: [], decisions: [] };
      Object.assign(server, { session: { mutations_enabled: true }, request: async (url) => {
        const data = url.includes("/activity?") ? { items: [] } : url.endsWith("/sessions") ? { sessions: [fixture.session] }
          : url.endsWith("/settings") ? DEFAULTS : fixture;
        return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
      } });
      const host = document.getElementById("home"), mod = await import("/js/components/chat_view.js");
      const ctx = { state: { mode: "server", chatSession: "s", selectedProject: "p", liveStatus: { state: "live" } },
        activeProject: () => "p", toast() {}, update() { mod.render(host, ctx); } };
      mod.render(host, ctx);
    });
    await page.locator('.chat-message-slot').nth(7).waitFor();
    return page;
  };
  for (const width of [1440, 1024, 390]) await t.test(`${width}px`, async () => {
    const page = await open("daylight-orbit", width); t.after(() => page.close());
    const metrics = () => page.evaluate(() => {
      const view = document.querySelector('#home'), log = document.querySelector('#chat-log'), flow = log.querySelector('.chat-flow');
      const css = getComputedStyle(log);
      return { host: view.clientHeight, extent: view.scrollHeight, log: log.scrollHeight,
        content: Math.max(log.clientHeight, Math.round(flow.getBoundingClientRect().height + parseFloat(css.paddingTop) + parseFloat(css.paddingBottom))) };
    });
    const fixed = await metrics();
    assert.ok(fixed.extent <= fixed.host + 1, JSON.stringify(fixed));
    assert.ok(Math.abs(fixed.log - fixed.content) <= 1, JSON.stringify(fixed));
    // Reproduce the old viewport sizing in the very same loaded conversation.
    const old = await page.addStyleTag({ content: '.chat { height: calc(100vh - 120px); min-height: 480px; }' });
    const broken = await metrics();
    assert.ok(broken.extent - broken.host > 400, JSON.stringify(broken));
    await old.evaluate(el => el.remove());
    assert.ok((await metrics()).extent <= fixed.host + 1);
    console.log(JSON.stringify({ width, fixed, broken }));
    await page.evaluate(() => { document.body.classList.remove('console-open'); document.querySelector('#project-load-notice').hidden = true; });
    const normal = await metrics();
    assert.ok(normal.extent <= normal.host + 1, JSON.stringify(normal));
    assert.ok(Math.abs(normal.log - normal.content) <= 1, JSON.stringify(normal));
  });
});
