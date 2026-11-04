import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));
const plugin = fileURLToPath(new URL("../../examples/plugins/alfrd-markdown/alfrd_markdown/web/", import.meta.url));

test("Markdown reading column, fixed close, tabs and safe links", { skip: !chromium && "Playwright not available" }, async t => {
  const requests = [];
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (data, type = "text/plain", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(data); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/plugin/index.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme")}/theme.css"><body><div id="modal-host" hidden></div>`, "text/html");
    if (url.pathname.endsWith("/file")) {
      const path = url.searchParams.get("path"); requests.push(path);
      if (path === "notes/next.md") return send("# Next\n\n[Plain](plain.txt)\n\n" + "paragraph\n\n".repeat(60) + "## Destination\n\nEnd");
      if (path === "notes/plain.txt") return send("long plain text ".repeat(300));
      return send(JSON.stringify({ error: { message: "Path outside project" } }), "application/json", 403);
    }
    const base = url.pathname.startsWith("/plugin/") ? plugin : web;
    const path = resolve(base, url.pathname.replace(/^\/plugin\//, "").replace(/^\//, ""));
    if (!path.startsWith(base)) return send("", "text/plain", 404);
    try { send(await readFile(path), /\.m?js$/.test(extname(path)) ? "text/javascript" : "text/css"); }
    catch { send("", "text/plain", 404); }
  });
  await new Promise(done => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise(done => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  for (const theme of ["obsidian-orbit", "daylight-orbit"]) for (const width of [1440, 390]) for (const entry of ["viewer", "blob", "chat_view", "chat_dock"]) await t.test(`${theme} ${width} ${entry}`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    t.after(() => page.close());
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async entry => {
      const { activate } = await import("/plugin/index.js");
      const { registerViewer, openViewer, viewerFor } = await import("/js/components/viewers.js");
      const { default: purify } = await import("/js/vendor/purify.es.mjs");
      activate({ registerViewer, sanitize: html => purify.sanitize(html) });
      const text = "# Reading\n\nA readable paragraph.\n\n[Next](../notes/next.md#destination) [Here](#bottom) [Web](https://example.com) [Mail](mailto:a@example.com) [Denied](../../../secret.txt)\n\n| Name | Value |\n| --- | --- |\n| A | B |\n\n| Wide | Value |\n| --- | --- |\n| " + "long".repeat(250) + " | B |\n\n" + "paragraph\n\n".repeat(70) + "## Bottom\n\nEnd";
      const ctx = { state: { selectedProject: "p" }, toast() {}, modal(html, setup, size) {
        const host = document.querySelector("#modal-host"); host.hidden = false;
        host.innerHTML = `<div class="modal-back"></div><div class="modal ${size}" role="dialog">${html}</div>`;
        host.querySelector("[data-close]").onclick = () => { host.hidden = true; };
        setup(host.querySelector(".modal"));
      } };
      if (entry === "viewer") openViewer(ctx, "p", "docs/start.md", undefined, { text });
      else if (entry === "blob") await (await import("/js/components/files_tab.js")).openBlob(ctx, "docs/start.md", new Blob([text], { type: "text/markdown" }));
      else {
        const { esc, icon } = await import("/js/utils/dom.js");
        const source = await (await fetch(`/js/components/${entry}.js`)).text();
        const start = source.indexOf("async function viewArtifact(ref)");
        const body = source.slice(start, source.indexOf("\n}\n", start) + 2).replaceAll('import("./viewers.js")', 'import("/js/components/viewers.js")');
        const run = new Function("ctxRef", "S", "D", "get", "base", "esc", "icon", "$", "viewerFor", "openViewer", `${body}; return viewArtifact;`)(ctx, { project: "p", sid: "s" }, { project: "p", sid: "s" },
          async () => ({ path: "docs/start.md", content: text, offset: 0, length: text.length, eof: true, sha256: "verified", size: text.length }), () => "/chat", esc, icon, (sel, root) => root.querySelector(sel), viewerFor, openViewer);
        await run("docs/start.md@verified");
      }
    }, entry);
    await page.waitForSelector(".file-markdown h1");
    const metrics = await page.evaluate(() => {
      const el = document.querySelector(".file-markdown"), p = el.querySelector("p"), tables = el.querySelectorAll(".file-table-wrap");
      const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number);
      const luminance = color => rgb(color).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
      const background = getComputedStyle(document.querySelector(".modal")).backgroundColor;
      const contrast = color => { const a = luminance(color), b = luminance(background); return (Math.max(a, b) + .05) / (Math.min(a, b) + .05); };
      const contentWidth = el.clientWidth - parseFloat(getComputedStyle(el).paddingLeft) - parseFloat(getComputedStyle(el).paddingRight);
      return { contrast: contrast(getComputedStyle(el).color), linkContrast: contrast(getComputedStyle(el.querySelector("a")).color), contentWidth, paragraph: p.getBoundingClientRect().width, table: tables[0].getBoundingClientRect().width,
        wideScroll: tables[1].scrollWidth > tables[1].clientWidth, tableContainsCells: tables[1].querySelector("table").scrollWidth <= tables[1].querySelector("table").clientWidth + 1, overflow: el.scrollWidth > el.clientWidth,
        color: getComputedStyle(el).color, background: getComputedStyle(document.querySelector(".modal")).backgroundColor };
    });
    assert.ok(metrics.contentWidth <= 781 && metrics.contentWidth > 250, JSON.stringify(metrics));
    assert.ok(Math.abs(metrics.paragraph - metrics.table) < 1);
    assert.equal(metrics.tableContainsCells, true); assert.equal(metrics.wideScroll, true); assert.equal(metrics.overflow, false);
    assert.ok(metrics.contrast >= 4.5); assert.ok(metrics.linkContrast >= 4.5);
    assert.equal(await page.getByText("Web", { exact: true }).getAttribute("target"), "_blank");
    assert.equal(await page.getByText("Web", { exact: true }).getAttribute("rel"), "noopener noreferrer");
    assert.equal(await page.getByText("Mail", { exact: true }).getAttribute("target"), null);
    await page.screenshot({ path: `/tmp/alfrd-md-${entry}-${theme}-${width}.png` });
    const close = await page.locator("[data-close]").boundingBox();
    const header = await page.locator(".modal-h").boundingBox();
    assert.ok(close.x + close.width > header.x + header.width - 25);
    await page.getByText("Here", { exact: true }).click();
    assert.ok(await page.locator(".file-markdown").evaluate(el => el.scrollTop) > 500);
    assert.deepEqual(await page.locator("[data-close]").boundingBox(), close);
    await page.locator(".file-markdown").evaluate(el => el.scrollTop = 0);
    await page.getByText("Next", { exact: true }).click();
    await page.waitForSelector(".file-markdown:not([hidden]) #destination");
    assert.equal(await page.getByRole("tab").count(), 2);
    assert.ok(await page.locator(".file-markdown:not([hidden])").evaluate(el => el.scrollTop) > 500);
    await page.locator(".file-markdown:not([hidden])").evaluate(el => el.scrollTop = 0);
    await page.getByText("Plain", { exact: true }).click();
    await page.waitForSelector(".file-text pre");
    assert.equal(await page.getByRole("tab").count(), 3);
    assert.equal(await page.locator(".file-text pre").evaluate(el => getComputedStyle(el).whiteSpace), "pre-wrap");
    assert.equal(await page.locator(".file-text").evaluate(el => el.scrollWidth > el.clientWidth), false);
    await page.getByRole("tab", { name: "start.md", exact: true }).click();
    await page.locator(".file-markdown:not([hidden])").evaluate(el => el.scrollTop = 0);
    await page.getByText("Denied", { exact: true }).click();
    await page.getByText(/Unable to open.*Path outside project/).waitFor();
    assert.ok(page.url().includes("?theme="));
    await page.getByRole("tab", { name: "secret.txt" }).press("Home");
    assert.equal(await page.getByRole("tab", { name: "start.md" }).getAttribute("aria-selected"), "true");
    await page.locator("[data-close]").click();
    assert.equal(await page.locator("#modal-host").isVisible(), false);
  });
  assert.ok(requests.includes("notes/next.md")); assert.ok(requests.includes("notes/plain.txt"));
  assert.ok(requests.includes("../../secret.txt"));
});
