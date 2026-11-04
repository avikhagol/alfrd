import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

test("Log and Reply Find layout, focus, navigation and highlights", { skip: !chromium && "Playwright not available" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (data, type) => { res.writeHead(200, { "Content-Type": type }); res.end(data); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme")}/theme.css"><body><div id="modal-host" hidden></div></body>`, "text/html");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web)) { res.writeHead(404); return res.end(); }
    try { send(await readFile(path), extname(path) === ".js" ? "text/javascript" : "text/css"); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise(done => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise(done => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const before = process.env.ALFRD_FIND_BEFORE;
  const shots = process.env.ALFRD_FIND_SCREENSHOTS;
  if (shots) await mkdir(shots, { recursive: true });
  for (const theme of ["obsidian-orbit", "daylight-orbit"]) for (const width of [1440, 1024, 390]) for (const kind of ["log", "reply"]) await t.test(`${theme} ${width}px ${kind}`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    try {
      await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
      await page.evaluate(async (kind) => {
        const { openFileFull } = await import("/js/components/logview.js");
        const text = kind === "log" ? "Task build · attempt 2\nStarting build\nChecking search layout\nBuild completed\n" : "# Reply\n\nBuild completed successfully.\nThe build is ready for review.\n";
        await openFileFull({ state: { trees: {} },
          readLogRange: async () => ({ text, offset: text.length, live: false, id: "t", size: text.length, mtime: 0 }),
          modal(html, setup, size) {
            const host = document.querySelector("#modal-host");
            host.hidden = false;
            host.innerHTML = `<div class="modal-back"></div><div class="modal ${size}" role="dialog">${html}</div>`;
            setup(host.querySelector(".modal"));
          }, toast() {},
        }, "p", kind === "log" ? "plans/r1/build.log" : "handoffs/0016/response.md", { unit: "build", attempt: 2, latest: true });
      }, kind);
      const bounds = () => page.locator("pre").boundingBox();
      const closed = await bounds();
      await page.locator("[data-text-find-open]").click();
      await page.locator("[data-text-query]").fill("build");
      const metrics = await page.evaluate(() => {
        const bar = document.querySelector("[data-text-find]"), input = bar.querySelector("input");
        const boxes = [...bar.children].map(el => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; });
        return { boxes, bar: bar.getBoundingClientRect().toJSON(), overflow: document.documentElement.scrollWidth > innerWidth,
          focus: getComputedStyle(input).outlineStyle, count: bar.querySelector("[data-text-count]").textContent };
      });
      console.log(JSON.stringify({ theme, width, kind, metrics }));
      if (shots) await page.screenshot({ path: resolve(shots, `${before ? "before" : "after"}-${kind}-${width}-${theme}.png`) });
      if (before) return;
      assert.equal(metrics.overflow, false);
      assert.ok(metrics.boxes.every(b => Math.abs(b.y + b.height / 2 - metrics.boxes[0].y - metrics.boxes[0].height / 2) < 1), JSON.stringify(metrics));
      assert.ok(metrics.boxes.every(b => b.x >= metrics.bar.x && b.x + b.width <= metrics.bar.right + 1));
      assert.equal(metrics.boxes[0].height, 28);
      assert.notEqual(metrics.focus, "none");
      assert.equal(metrics.count, kind === "log" ? "1/3" : "1/2");
      assert.deepEqual(await bounds(), closed, "opening Find must not move the text viewport");
      assert.equal(await page.evaluate(() => CSS.highlights.get("text-find").size), 1);
      await page.locator("[data-text-query]").press("Enter");
      assert.equal(await page.locator("[data-text-count]").textContent(), kind === "log" ? "2/3" : "2/2");
      await page.locator("[data-text-query]").press("Shift+Enter");
      assert.equal(await page.locator("[data-text-count]").textContent(), kind === "log" ? "1/3" : "1/2");
      await page.locator("[data-text-next]").click();
      assert.equal(await page.locator("[data-text-count]").textContent(), kind === "log" ? "2/3" : "2/2");
      await page.locator("[data-text-prev]").click();
      await page.locator("[data-text-query]").fill("missing");
      assert.equal(await page.locator("[data-text-count]").textContent(), "0/0");
      assert.equal(await page.locator("[data-text-next]").isDisabled(), true);
      assert.equal(await page.evaluate(() => CSS.highlights.has("text-find")), false);
      await page.locator("[data-text-query]").fill("build");
      await page.locator("[data-text-next]").focus();
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("[data-text-find]").isVisible(), false);
      assert.equal(await page.locator("[data-text-find-open]").getAttribute("aria-expanded"), "false");
      assert.equal(await page.locator("[data-text-find-open]").evaluate(el => el === document.activeElement), true);
      await page.locator("[data-text-find-open]").click();
      await page.locator("[data-text-find-close]").click();
      assert.equal(await page.locator("[data-text-find]").isVisible(), false);
    } finally { await page.close(); }
  });
});
