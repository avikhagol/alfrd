// Real browser coverage of plugin activation, DOMPurify, reference Markdown and
// converter interactions. Run with ALFRD_PLAYWRIGHT=<package/index.mjs>; optional
// ALFRD_CHROMIUM_EXECUTABLE selects a locally installed Chromium executable.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname, sep } from "node:path";

let chromium = null;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const repo = fileURLToPath(new URL("../../", import.meta.url));
const web = resolve(repo, "src/alfrd/web");
const markdown = resolve(repo, "examples/plugins/alfrd-markdown/alfrd_markdown/web");
const maliciousMarkdown = '# Safe heading\n\n**Bold text**\n\n<script>window.pluginXss = true</script>\n<img src="/missing-image" onerror="window.pluginXss = true">\n<a href="javascript:window.pluginXss=true">unsafe link</a>';

async function serveFixtures(t) {
  const requests = [];
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    requests.push(url.pathname + url.search);
    const send = (body, type = "text/html", status = 200) => {
      response.writeHead(status, { "Content-Type": type, "X-Content-Type-Options": "nosniff" });
      response.end(body);
    };
    if (url.pathname === "/") return send('<!doctype html><html><head><link rel="stylesheet" href="theme.css"></head><body><div id="modal"></div><div id="panel"></div></body></html>');
    if (url.pathname === "/bad.js") return send('export function activate() { throw new Error("activation exploded"); }', "text/javascript");
    if (url.pathname === "/good.js") return send(`export function activate(api) {
      api.registerCommand({id:"good-command",label:"Good command",run(){window.commandRan=true;}});
      api.registerViewer({id:"good-viewer",match:[".good"],render(file,host){host.textContent=file.text;}});
      window.goodActivated = (window.goodActivated || 0) + 1;
    }`, "text/javascript");
    if (url.pathname.endsWith(".css")) return send("", "text/css");
    if (url.pathname === "/api/studio/projects/project/file") {
      return send(url.searchParams.get("path").endsWith(".md") ? maliciousMarkdown : "%!PS example source", "text/plain");
    }
    let base, relative;
    if (url.pathname.startsWith("/studio/plugins/markdown/")) {
      base = markdown; relative = url.pathname.slice("/studio/plugins/markdown/".length);
    } else if (url.pathname.startsWith("/studio/")) {
      base = web; relative = url.pathname.slice("/studio/".length);
    } else return send("not found", "text/plain", 404);
    const path = resolve(base, relative);
    if (!path.startsWith(base + sep)) return send("not found", "text/plain", 404);
    try {
      const bytes = await readFile(path);
      send(bytes, [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/plain");
    } catch { send("not found", "text/plain", 404); }
  });
  let port;
  for (port = 5170; port < 5200; port++) {
    try {
      await new Promise((accept, reject) => {
        server.once("error", reject);
        server.listen(port, "127.0.0.1", () => { server.removeListener("error", reject); accept(); });
      });
      break;
    } catch (error) { if (error.code !== "EADDRINUSE") throw error; }
  }
  assert.ok(port < 5200, "No available test server port between 5170 and 5199");
  t.after(() => new Promise((accept) => { server.close(accept); server.closeAllConnections(); }));
  return { origin: `http://127.0.0.1:${port}`, requests };
}

async function initialize(page, origin) {
  await page.goto(origin);
  await page.evaluate(async () => {
    window.plugins = await import("/studio/js/components/plugin_api.js");
    window.viewers = await import("/studio/js/components/viewers.js");
    window.panels = await import("/studio/js/components/panels.js");
    window.logs = [];
    window.commands = [];
    window.ctx = {
      state: { selectedProject: "project" },
      palette: { register(provider) { window.commands.push(provider); } },
      log(...args) { window.logs.push(args); }, toast() {}, update() {},
      modal(html, mount) {
        const root = document.querySelector("#modal");
        root.innerHTML = html;
        mount(root);
      },
    };
    window.markdownList = { plugins: [{ id: "markdown", title: "Markdown", active: true, enabled: true, status: "ok",
      web: { js: "/studio/plugins/markdown/index.js", css: "/studio/plugins/markdown/index.css" } }] };
    window.converter = { src: [".eps"], to: "pdf", plugin: "ps2pdf", title: "PostScript to PDF" };
    // Observe Blob lifetime without replacing the browser's actual Blob APIs.
    window.createdBlobUrls = [];
    window.revokedBlobUrls = [];
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (blob) => { const url = create(blob); window.createdBlobUrls.push(url); return url; };
    URL.revokeObjectURL = (url) => { window.revokedBlobUrls.push(url); revoke(url); };
  });
}

test("Studio plugins in a real browser", { skip: chromium ? false : "Playwright not available" }, async (t) => {
  const { origin, requests } = await serveFixtures(t);
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());

  await t.test("DOMPurify strips scripts, event handlers and JavaScript links", async (t) => {
    const page = await browser.newPage(); t.after(() => page.close());
    await initialize(page, origin);
    const sanitized = await page.evaluate(async () => {
      const purify = (await import("/studio/js/vendor/purify.es.mjs")).default;
      const api = window.plugins.api(window.ctx, purify);
      const html = api.sanitize('<p>Keep this</p><script>window.pluginXss=true</script><img src="/missing-image" onerror="window.pluginXss=true"><a href="javascript:window.pluginXss=true">link</a>');
      document.querySelector("#panel").innerHTML = html;
      return { html, paragraph: document.querySelector("#panel p").textContent,
        scripts: document.querySelectorAll("#panel script").length,
        handler: document.querySelector("#panel img").hasAttribute("onerror"),
        href: document.querySelector("#panel a").getAttribute("href") };
    });
    assert.equal(sanitized.paragraph, "Keep this");
    assert.equal(sanitized.scripts, 0);
    assert.equal(sanitized.handler, false);
    assert.equal(sanitized.href, null);
    assert.equal(await page.evaluate(() => window.pluginXss), undefined);
  });

  await t.test("one failing activation is isolated and successful plugins activate once", async (t) => {
    const page = await browser.newPage(); t.after(() => page.close());
    await initialize(page, origin);
    const result = await page.evaluate(async () => {
      const list = { theme: "good", plugins: [
        { id: "bad", title: "Bad", active: true, enabled: true, status: "ok", web: { js: "/bad.js" } },
        { id: "good", title: "Good", active: true, enabled: true, status: "ok", web: { js: "/good.js", css: "/good.css" }, theme_css: "/good-theme.css" },
        { id: "disabled", active: false, enabled: false, status: "disabled", web: { js: "/disabled-never.js" } },
        { id: "broken-python", title: "Broken Python", active: false, enabled: true, status: "error", error: "Python exploded" },
      ] };
      await window.plugins.activateAll(list, window.ctx);
      // Refreshing metadata must not register a successful plugin again.
      await window.plugins.activateAll({ ...list, plugins: list.plugins.filter((p) => p.id !== "bad") }, window.ctx);
      window.commands[0]()[0].run();
      return { errors: window.plugins.pluginErrors, logs: window.logs, activated: window.goodActivated,
        commandRan: window.commandRan, viewer: window.viewers.viewerFor("sample.good").id,
        css: document.querySelector("link[data-plugin-style=good]").getAttribute("href"),
        theme: document.querySelector("link[data-plugin-theme]").getAttribute("href") };
    });
    assert.equal(result.activated, 1);
    assert.equal(result.commandRan, true);
    assert.equal(result.viewer, "good-viewer");
    assert.deepEqual(result.errors, [{ id: "bad", title: "Bad", message: "activation exploded" }]);
    assert.equal(result.logs.filter(([, text]) => text.includes("Python exploded")).length, 1);
    assert.ok(result.logs.some(([level, text, scope]) => level === "error" && text.includes("bad: browser: activation exploded") && scope === "plugins"));
    assert.equal(result.css, "/good.css"); assert.equal(result.theme, "/good-theme.css");
    assert.ok(!requests.some((path) => path.includes("disabled-never")));
  });

  await t.test("reference Markdown renders sanitized content in a full modal and a text panel", async (t) => {
    const page = await browser.newPage(); t.after(() => page.close());
    await initialize(page, origin);
    await page.evaluate(async () => {
      await window.plugins.activateAll(window.markdownList, window.ctx);
      window.viewers.openViewer(window.ctx, "project", "notes.md");
    });
    await page.waitForSelector("#modal .viewer-full h1");
    assert.equal(await page.textContent("#modal h1"), "Safe heading");
    assert.equal(await page.textContent("#modal strong"), "Bold text");
    assert.equal(await page.locator("#modal script,#modal [onerror],#modal a[href^='javascript:']").count(), 0);
    assert.ok(requests.some((path) => path === "/api/studio/projects/project/file?path=notes.md"));
    await page.evaluate(async (text) => {
      const html = await window.panels.renderPanels({ panels: [{ index: 0, panel: "text", title: "Notes", scope: "target",
        instances: [{ files: [{ rel: "panel.md", text }] }] }] }, window.ctx, { project: "project" });
      const root = document.querySelector("#panel"); root.innerHTML = html;
      root.querySelectorAll("details").forEach((node) => { node.open = true; });
      window.panels.mountViewers(root, "project");
    }, maliciousMarkdown);
    await page.waitForSelector("#panel h1");
    assert.equal(await page.textContent("#panel h1"), "Safe heading");
    assert.equal(await page.textContent("#panel strong"), "Bold text");
    assert.equal(await page.locator("#panel script,#panel [onerror],#panel a[href^='javascript:']").count(), 0);
    assert.equal(await page.evaluate(() => window.pluginXss), undefined);
  });

  await t.test("conversion matches only a fallback viewer and keeps text until PDF success", async (t) => {
    const page = await browser.newPage(); t.after(() => page.close());
    await initialize(page, origin);
    const matches = await page.evaluate(async () => {
      await window.plugins.activateAll(window.markdownList, window.ctx);
      window.viewers.setConverters([{ ...window.converter, src: [".eps", ".md", ".pdf"] }]);
      return ["figure.eps", "figure.EPS", "notes.md", "figure.pdf", "unknown.txt"].map((path) => Boolean(window.viewers.conversionButton(path)));
    });
    assert.deepEqual(matches, [true, true, false, false, false]);
    let pending;
    const requested = new Promise((accept) => { pending = accept; });
    await page.route("**/api/studio/projects/project/convert?**", (route) => pending(route));
    await page.evaluate(() => window.viewers.openViewer(window.ctx, "project", "figure.eps", undefined, { text: "%!PS original" }));
    await page.click("#modal [data-convert-rel]");
    const route = await requested;
    assert.equal(new URL(route.request().url()).searchParams.get("path"), "figure.eps");
    assert.equal(new URL(route.request().url()).searchParams.get("to"), "pdf");
    assert.equal(await page.locator("#modal [data-convert-rel]").isDisabled(), true);
    assert.equal(await page.getAttribute("#modal [data-convert-rel]", "aria-busy"), "true");
    assert.match(await page.textContent("#modal [data-convert-rel]"), /Converting/);
    assert.equal(await page.textContent("#modal pre"), "%!PS original");
    await route.fulfill({ status: 200, contentType: "application/pdf", body: "%PDF-1.4\n%%EOF\n" });
    await page.waitForSelector("#modal iframe.viewer-pdf");
    assert.match(await page.getAttribute("#modal iframe", "src"), /^blob:/);
    assert.equal(await page.textContent("#modal [data-convert-status]"), "Converted by PostScript to PDF · cached");
    assert.equal(await page.locator("#modal [data-convert-rel]").isHidden(), true);
    await page.evaluate(() => { document.querySelector("#modal").innerHTML = ""; });
    await page.waitForFunction(() => window.createdBlobUrls.length === 1 && window.revokedBlobUrls.includes(window.createdBlobUrls[0]));
  });

  await t.test("conversion JSON error preserves text and lets the user retry", async (t) => {
    const page = await browser.newPage(); t.after(() => page.close());
    await initialize(page, origin);
    await page.route("**/api/studio/projects/project/convert?**", (route) => route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { code: 503, message: "missing binary: gs", detail_tail: "" } }) }));
    await page.evaluate(() => {
      window.viewers.setConverters([window.converter]);
      window.viewers.openViewer(window.ctx, "project", "figure.eps", undefined, { text: "%!PS keep this" });
    });
    await page.click("#modal [data-convert-rel]");
    await page.waitForFunction(() => document.querySelector("#modal [data-convert-status]").textContent === "missing binary: gs");
    assert.equal(await page.textContent("#modal pre"), "%!PS keep this");
    assert.match(await page.textContent("#modal [data-convert-rel]"), /Try again/);
    assert.equal(await page.locator("#modal [data-convert-rel]").isDisabled(), false);
    assert.equal(await page.getAttribute("#modal [data-convert-rel]", "aria-busy"), null);
    assert.equal(await page.locator("#modal [data-convert-status]").isVisible(), true);
    assert.equal(await page.locator("#modal iframe").count(), 0);
  });

  await t.test("a late original-text response cannot overwrite a successful conversion", async (t) => {
    const page = await browser.newPage(); t.after(() => page.close());
    await initialize(page, origin);
    let acceptFile;
    const requestedFile = new Promise((accept) => { acceptFile = accept; });
    await page.route("**/api/studio/projects/project/file?**", (route) => acceptFile(route));
    await page.route("**/api/studio/projects/project/convert?**", (route) => route.fulfill({ status: 200, contentType: "application/pdf", body: "%PDF-1.4\n%%EOF\n" }));
    await page.evaluate(() => {
      window.viewers.setConverters([window.converter]);
      window.viewers.openViewer(window.ctx, "project", "figure.eps");
    });
    const original = await requestedFile;
    await page.click("#modal [data-convert-rel]");
    await page.waitForSelector("#modal iframe.viewer-pdf");
    // Finish the earlier fetch only after conversion has already rendered.
    await original.fulfill({ status: 200, contentType: "text/plain", body: "%!PS late original" });
    await page.waitForLoadState("networkidle");
    assert.equal(await page.locator("#modal iframe.viewer-pdf").count(), 1);
    assert.equal(await page.locator("#modal pre").count(), 0);
  });

  await t.test("text-panel conversion replaces only its file row", async (t) => {
    const page = await browser.newPage(); t.after(() => page.close());
    await initialize(page, origin);
    await page.route("**/api/studio/projects/project/convert?**", (route) => route.fulfill({ status: 200, contentType: "application/pdf", body: "%PDF-1.4\n%%EOF\n" }));
    await page.evaluate(async () => {
      window.viewers.setConverters([window.converter]);
      const root = document.querySelector("#panel");
      root.innerHTML = await window.panels.renderPanels({ panels: [{ index: 0, panel: "text", title: "Files", scope: "target",
        instances: [{ files: [{ rel: "figure.eps", text: "%!PS panel source" }, { rel: "notes.txt", text: "untouched notes" }] }] }] }, window.ctx, { project: "project" });
      root.querySelectorAll("details").forEach((node) => { node.open = true; });
      window.panels.mountViewers(root, "project");
    });
    assert.equal(await page.locator("#panel [data-convert-rel]").count(), 1);
    await page.click("#panel [data-convert-rel]");
    await page.waitForSelector("#panel iframe.viewer-pdf");
    assert.equal(await page.textContent("#panel pre"), "untouched notes");
    assert.equal(await page.textContent("#panel [data-convert-status]"), "Converted by PostScript to PDF · cached");
  });
});
