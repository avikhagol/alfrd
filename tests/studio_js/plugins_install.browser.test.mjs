// Optional real Chromium checks; ports 5270+ and all catalog/jobs are offline fixtures.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname, sep } from "node:path";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));
const plugin = (id, more = {}) => ({ id, title: id === "markdown" ? "Markdown" : id, description: "Read project notes with a trusted local plugin.", latest: "1.0", kinds: ["viewer"], compatible: true,
  versions: [{ version: "1.0", wheel: "file:///tmp/markdown-1.0.whl", sha256: "a".repeat(64), requirements: ["dependency==1 --hash=sha256:" + "b".repeat(64)] }], ...more });

async function fixture(t) {
  const state = { catalog: { catalog_url: "file:///tmp/catalog.json", name: "Offline fixture catalog", fetched_at: new Date().toISOString(), user: "studio-test", gui_install: true, plugins: [plugin("markdown"), plugin("current", { installed: "1.0" }), plugin("update", { installed: "0.9", update_available: true, missing_bin: ["gs"] }), plugin("future", { compatible: false })] }, calls: [], logs: [], status: "running", busy: false, job: null };
  const http = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "application/json", status = 200, headers = {}) => { res.writeHead(status, { "Content-Type": type, ...headers }); res.end(type === "application/json" ? JSON.stringify(body) : body); };
    if (url.pathname === "/") return send('<!doctype html><html><head><link rel="stylesheet" href="/studio/css/studio.css"><link rel="stylesheet" href="/studio/css/lazy.css"><link rel="stylesheet" href="/studio/css/themes/obsidian-orbit/theme.css" id="theme"></head><body><button id="btn-settings">Settings</button><div id="modal-host"></div></body></html>', "text/html");
    if (url.pathname === "/api/studio/plugins/catalog") return send(state.catalog);
    if (url.pathname === "/api/studio/plugins") return send({ plugins: [{ ...plugin("markdown"), version: "1.0", managed: true, status: "ok", enabled: true, active: true }], themes: [], gui_install: state.catalog.gui_install, job: state.job });
    if (url.pathname.includes("/jobs/")) {
      state.logs.push(Number(url.searchParams.get("offset")));
      const text = state.status === "failed" ? "Failed: package provides other, not markdown\n" : state.status === "done" ? "Installed markdown 1.0\n" : "Downloading local wheel…\n";
      const offset = Number(url.searchParams.get("offset"));
      return send(offset ? "" : text, "text/plain", 200, { "X-Offset": String(Buffer.byteLength(text)), "X-Job-Status": state.status, "X-Restart-Required": state.status === "done" ? "1" : "0" });
    }
    if (req.method === "POST") {
      let body = ""; for await (const chunk of req) body += chunk;
      state.calls.push({ path: url.pathname, payload: JSON.parse(body) });
      if (state.busy) return send({ error: { message: "busy", job: { action: "install", target: "other" } } }, "application/json", 409);
      state.job = { id: "job-1", action: "install", target: "markdown", status: state.status, started: new Date().toISOString() };
      return send({ job: state.job }, "application/json", 202);
    }
    if (!url.pathname.startsWith("/studio/")) return send({}, "application/json", 404);
    const path = resolve(web, url.pathname.slice(8));
    if (!path.startsWith(resolve(web) + sep)) return send({}, "application/json", 404);
    try { send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); } catch { send({}, "application/json", 404); }
  });
  let port;
  for (port = 5270; port < 5300; port++) {
    try { await new Promise((ok, fail) => { http.once("error", fail); http.listen(port, "127.0.0.1", () => { http.removeListener("error", fail); ok(); }); }); break; }
    catch (e) { if (e.code !== "EADDRINUSE") throw e; }
  }
  assert.ok(port < 5300);
  t.after(() => new Promise(ok => { http.close(ok); http.closeAllConnections(); }));
  return { ...state, state, origin: `http://127.0.0.1:${port}` };
}

async function initialize(page, origin) {
  await page.goto(origin);
  await page.evaluate(async () => {
    const { server } = await import("/studio/js/data/server.js");
    server.session = { mutations_enabled: true, can_restart: true, csrf_token: "fixture" };
    const settings = await import("/studio/js/components/settings_plugins.js");
    window.toasts = [];
    window.ctx = { state: { mode: "server" }, toast: (...a) => window.toasts.push(a), log() {},
      menu(b, items) { const el = document.createElement("div"); el.id = "fixture-menu"; el.style.cssText = "position:fixed;z-index:9999;top:20px;left:20px"; el.setAttribute("role", "menu"); items.forEach(item => { const btn = document.createElement("button"); btn.textContent = item.label; btn.onclick = () => { el.remove(); item.run(); }; el.append(btn); }); document.body.append(el); },
      modal(html, mount) {
        const host = document.querySelector("#modal-host"); host.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
        const root = host.firstElementChild;
        const close = () => { root.dispatchEvent(new Event("beforeclose")); host.innerHTML = ""; };
        mount(root, close);
      } };
    window.showSettings = async () => {
      document.querySelector("#modal-host").innerHTML = '<div class="modal settings-modal" role="dialog"><header class="modal-h"><h2>Settings → Plugins</h2></header><div class="modal-b set"><section id="set-panel-plugins"></section></div></div>';
      await settings.mountPlugins(window.ctx, document.querySelector("#modal-host"));
    };
    document.querySelector("#btn-settings").onclick = window.showSettings;
    await window.showSettings();
  });
}

const screenshot = async (page, name) => { if (process.env.ALFRD_PLUGIN_SCREENSHOTS) await page.screenshot({ path: `/tmp/alfrd-p4-${name}.png`, fullPage: true }); };

test("Studio install UI in Chromium", { skip: chromium ? false : "Playwright not available" }, async t => {
  const f = await fixture(t);
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const pageFor = async t => { const page = await browser.newPage({ viewport: { width: 1100, height: 820 } }); t.after(() => page.close()); await initialize(page, f.origin); return page; };

  await t.test("tabs switch with arrows, load Browse once, search and kind filter", async t => {
    const page = await pageFor(t); let loads = 0;
    page.on("request", r => { if (r.url().endsWith("/plugins_browse.js")) loads++; });
    await page.getByRole("tab", { name: "Installed", exact: true }).focus(); await page.keyboard.press("ArrowRight");
    await page.getByText("Offline fixture catalog", { exact: false }).waitFor();
    await screenshot(page, "browse-dark");
    await page.getByRole("searchbox").fill("MARKDOWN");
    await page.waitForTimeout(200); assert.equal(await page.locator("[data-install]").count(), 1);
    await page.getByLabel("Plugin kind").selectOption("theme"); await page.getByText("No plugins match", { exact: false }).waitFor();
    await page.getByRole("button", { name: "Clear search" }).click();
    await page.getByRole("tab", { name: "Browse", exact: true }).focus(); await page.keyboard.press("Home");
    assert.equal(await page.locator("#plug-panel-installed").isVisible(), true);
    await page.keyboard.press("End"); assert.equal(loads, 1);
  });

  await t.test("catalog install names user, copies full hash, posts version and tails to done", async t => {
    f.state.calls.length = 0; f.state.job = null; f.state.status = "running";
    const page = await pageFor(t); await page.getByRole("tab", { name: "Browse", exact: true }).click();
    await page.locator('[data-install="markdown"]').click();
    await page.getByText("Plugins run as studio-test", { exact: false }).waitFor();
    await page.evaluate(() => document.querySelector("#theme").href = "/studio/css/themes/daylight-orbit/theme.css");
    await screenshot(page, "install-light");
    assert.equal(await page.locator("[data-cancel]").evaluate(el => el === document.activeElement), true);
    await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { value: { writeText: async text => { window.copiedHash = text; } }, configurable: true }));
    await page.locator('[data-copy="sha256"]').click();
    assert.equal(await page.evaluate(() => window.copiedHash), "a".repeat(64));
    await page.getByRole("button", { name: "Install", exact: true }).click();
    await page.getByText("Installing markdown…", { exact: true }).waitFor();
    assert.deepEqual(f.state.calls[0], { path: "/api/studio/plugins/install", payload: { id: "markdown", version: "1.0" } });
    f.state.status = "done";
    await page.getByRole("button", { name: "Restart now" }).waitFor({ timeout: 5000 });
    await screenshot(page, "restart-banner");
    assert.ok(f.state.logs.some(offset => offset > 0));
    const count = f.state.logs.length; await page.waitForTimeout(1300); assert.equal(f.state.logs.length, count);
  });

  await t.test("disabled Studio installs expose a command and never POST", async t => {
    f.state.job = null; f.state.catalog.gui_install = false; const before = f.state.calls.length;
    const page = await pageFor(t); await page.getByRole("tab", { name: "Browse", exact: true }).click(); await page.locator('[data-install="markdown"]').click();
    await page.getByRole("button", { name: "Copy command" }).waitFor();
    assert.match(await page.locator("[data-policy]").innerText(), /alfrd plugin install/);
    assert.equal(f.state.calls.length, before); f.state.catalog.gui_install = true;
  });

  await t.test("advanced requires typed id, posts both fields and displays failed log verbatim", async t => {
    f.state.job = null; f.state.status = "failed";
    const page = await pageFor(t); await page.getByRole("tab", { name: "Browse", exact: true }).click(); await page.getByRole("button", { name: "Install from source…" }).click();
    const submit = page.getByRole("button", { name: "Install", exact: true }); assert.equal(await submit.isDisabled(), true);
    await page.locator("[data-source-input]").fill("/tmp/package.whl"); await page.locator("[data-id]").fill("Markdown"); assert.equal(await submit.isDisabled(), true);
    await page.locator("[data-id]").fill("markdown"); assert.equal(await submit.isDisabled(), false);
    await screenshot(page, "advanced"); await submit.click();
    await page.getByText("Failed: package provides other, not markdown", { exact: true }).first().waitFor();
    assert.deepEqual(f.state.calls.at(-1).payload, { source: "/tmp/package.whl", confirm_id: "markdown" });
    await screenshot(page, "failed-progress");
  });

  await t.test("409 keeps dialog open with the running job explained", async t => {
    f.state.job = null; f.state.busy = true;
    const page = await pageFor(t); await page.getByRole("tab", { name: "Browse", exact: true }).click(); await page.locator('[data-install="markdown"]').click(); await page.getByRole("button", { name: "Install", exact: true }).click();
    await page.getByText("Another plugin job is running (install other). Wait for it to finish.").waitFor();
    assert.equal(await page.getByRole("button", { name: "Install", exact: true }).isEnabled(), true); f.state.busy = false;
  });

  await t.test("managed Update and Remove confirm before posting; Cancel restores focus", async t => {
    f.state.job = null; f.state.status = "running";
    const page = await pageFor(t);
    await page.getByRole("button", { name: "More actions for Markdown" }).click();
    await page.getByRole("button", { name: "Remove…", exact: true }).click();
    await page.getByText("Its files are deleted. Projects keep their data.").waitFor();
    const before = f.state.calls.length;
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "More actions for Markdown" }).waitFor();
    assert.equal(f.state.calls.length, before);
    await page.getByRole("button", { name: "More actions for Markdown" }).click();
    await page.getByRole("button", { name: "Update", exact: true }).click();
    await page.getByRole("heading", { name: "Update Markdown to 1.0" }).waitFor();
    await page.getByRole("button", { name: "Update", exact: true }).click();
    await page.getByText("Installing markdown…", { exact: true }).waitFor();
    assert.equal(f.state.calls.at(-1).path, "/api/studio/plugins/markdown/update");
  });

  await t.test("narrow light dialog keeps trust, confirmation and actions in the viewport", async t => {
    f.state.job = null;
    const page = await pageFor(t); await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("tab", { name: "Browse", exact: true }).click(); await page.locator('[data-install="markdown"]').click();
    await page.evaluate(() => document.querySelector("#theme").href = "/studio/css/themes/daylight-orbit/theme.css");
    await screenshot(page, "install-narrow");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.locator(".plug-trust").isVisible(), true);
    assert.equal(await page.getByRole("button", { name: "Install", exact: true }).isVisible(), true);
  });

  await t.test("no catalog shows configuration guidance and advanced remains reachable", async t => {
    f.state.catalog.catalog_url = null; f.state.job = null;
    const page = await pageFor(t); await page.getByRole("tab", { name: "Browse", exact: true }).click(); await page.getByText("No plugin catalog is set.").waitFor();
    assert.match(await page.locator("[data-browse] pre").innerText(), /catalog_url/);
    await page.getByRole("button", { name: "Install from source…" }).click(); await page.getByRole("heading", { name: "Install from source" }).waitFor();
    f.state.catalog.catalog_url = "file:///tmp/catalog.json";
  });
});
