import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve } from "node:path";

let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch {}
const web = new URL("../../src/alfrd/web/", import.meta.url);
const mime = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml", ".yaml": "text/plain" };

test("progressive startup, failures, retry and persistent picker in Chromium", { skip: chromium ? false : "Playwright not available" }, async (t) => {
  const http = createServer(async (req, res) => {
    try {
      const path = new URL(req.url, "http://local").pathname;
      const file = new URL(path === "/" ? "index.html" : path.slice(1), web);
      res.writeHead(200, { "Content-Type": mime[extname(file.pathname)] || "application/octet-stream" });
      res.end(await readFile(file));
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => http.listen(0, "127.0.0.1", r));
  const browser = await chromium.launch();
  t.after(async () => { await browser.close(); await new Promise((r) => http.close(r)); });
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce", hasTouch: true, isMobile: true });
  const errors = []; page.on("pageerror", (e) => errors.push(e.message));
  const held = new Map(), counts = new Map();
  const names = ["a", "b", "c", "d", "e", "f", "g", "h", "i"];
  const json = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
  await page.addInitScript(() => {
    window.streams = [];
    window.EventSource = class { constructor(url) { window.streams.push(url); } addEventListener() {} close() {} };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    counts.set(path, (counts.get(path) || 0) + 1);
    if (path === "/api/studio/session") return json(route, { app: "alfrd", version: "test", projects: names, default_project: "a", runtime_enabled: true });
    if (path === "/api/projects") return json(route, { projects: names.map((name) => ({ name, identifier: name, display_name: `Project ${name}`, root_path: `/university/long/shared/project/${name}` })) });
    if (/\/(scan|workflows)$/.test(path)) { held.set(path, route); return; }
    if (path.endsWith("/matrix")) return json(route, { rows: [{ dataset_id: 1, dataset_name: "target", cells: {} }] });
    return json(route, {});
  });
  const scanPath = (p) => `/api/studio/projects/${p}/scan`;
  const runtimePath = (p) => `/api/projects/${p}/workflows`;
  const release = async (path, data, status) => { await page.waitForFunction(() => !!window.alfrdStudio); for (let i = 0; !held.has(path) && i < 100; i++) await new Promise((r) => setTimeout(r, 10)); assert.ok(held.has(path), path); const route = held.get(path); held.delete(path); await json(route, data, status); };
  const scan = (p) => ({ alfrd_avica_scan: 1, root: `/university/long/shared/project/${p}`, root_name: p, files: [{ rel: "alfrd.yaml", text: `name: ${p}\nworkflows:\n  main:\n    steps: [one]\n` }], ms_paths: [], live: { epoch: "test", version: 0 } });
  await page.goto(`http://127.0.0.1:${http.address().port}/#/overview?p=a&t=a%2Ftarget`);
  await page.waitForFunction(() => window.alfrdStudio?.ctx.projects().length === 9).catch(async (e) => { console.log({errors, paths: [...counts.keys()], body: await page.locator("body").innerText()}); throw e; });
  assert.equal(await page.locator("#project-load-status").count(), 1);
  assert.equal(await page.locator("#main").getAttribute("aria-busy"), null);
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.source), "server");
  await page.waitForFunction(() => window.streams.length > 0, null, {timeout: 3000}).catch(async (e) => { console.log({errors, state: await page.evaluate(() => ({live: alfrdStudio.ctx.state.liveStatus, logs: alfrdStudio.ctx.state.consoleLines, hidden: document.hidden}))}); throw e; });
  assert.ok(await page.evaluate(() => streams.some((s) => s.includes("projects="))), "live starts before all scans finish");
  await page.evaluate(() => alfrdStudio.ctx.goTo("workflow"));
  await page.waitForFunction(() => document.querySelector("#view-workflow").getAttribute("aria-busy") === "true");
  await release(scanPath("a"), scan("a"));
  await page.waitForFunction(() => !!alfrdStudio.ctx.state.trees.a);
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.loadInfo("a").badge), "Loading runtime");
  await page.waitForFunction(() => document.querySelector("#view-workflow").getAttribute("aria-busy") === "false");
  await page.evaluate(() => alfrdStudio.ctx.goTo("overview"));
  assert.equal(await page.evaluate(() => !!alfrdStudio.ctx.state.trees.b), false, "selected files usable while background held");
  await page.evaluate(() => { window.statusNode = document.querySelector("#project-load-status"); alfrdStudio.ctx.state.workflowFile.modified = true; alfrdStudio.ctx.state.workflowFile.text = "draft text"; });
  await release(runtimePath("a"), { workflows: [{ name: "main", sequence: ["one"] }] });
  await page.waitForFunction(() => alfrdStudio.ctx.loadInfo("a").badge === "");
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.workflowFile.text), "draft text");
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.selectedTarget), "a/target", "pending target deep link is applied when runtime rows arrive");
  await page.click("#pick-project");
  await page.fill(".picker-filter", "Project");
  await page.press(".picker-filter", "ArrowDown");
  const activeId = await page.locator(".picker-filter").getAttribute("aria-activedescendant");
  await page.evaluate(() => { document.querySelector(".picker-list").scrollTop = 70; window.pickerNode = document.querySelector(".picker-pop"); });
  const scroll = await page.locator(".picker-list").evaluate((e) => e.scrollTop);
  await release(scanPath("b"), { error: { message: "shared disk unavailable" } }, 500);
  await release(runtimePath("b"), { workflows: [] });
  await page.waitForFunction(() => alfrdStudio.ctx.loadInfo("b").badge === "Partly loaded");
  assert.equal(await page.evaluate(() => window.pickerNode === document.querySelector(".picker-pop")), true);
  assert.equal(await page.locator(".picker-filter").inputValue(), "Project");
  assert.equal(await page.locator(".picker-filter").getAttribute("aria-activedescendant"), activeId);
  assert.equal(await page.locator(".picker-list").evaluate((e) => e.scrollTop), scroll);
  assert.equal(await page.evaluate(() => document.activeElement.classList.contains("picker-filter")), true);
  assert.equal(await page.evaluate(() => statusNode === document.querySelector("#project-load-status")), true);
  assert.match(await page.locator("#project-load-status").textContent(), /Project a: loaded/);
  const bounds = await page.locator(".picker-pop").boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
  await page.press(".picker-filter", "Escape");
  await page.evaluate(() => alfrdStudio.ctx.switchProject("b"));
  const retry = page.locator("#project-load-notice button[data-project=b]");
  await retry.waitFor();
  assert.equal(await page.locator("#view-overview").getByText("No targets yet", { exact: true }).count(), 0);
  assert.ok((await retry.boundingBox()).height >= 44, "coarse-pointer Retry target is at least 44px");
  await retry.focus();
  assert.ok(await retry.evaluate((e) => parseFloat(getComputedStyle(e).outlineWidth) >= 2), "keyboard focus is visible");
  await retry.press("Enter");
  await page.waitForFunction(() => document.querySelector("#project-load-notice button[data-project=b]")?.getAttribute("aria-disabled") === "true");
  assert.equal(await page.evaluate(() => document.activeElement.dataset.project), "b", "Retry remains focused while disabled");
  // Free a background job so a retry may start, without exceeding two jobs.
  await release(scanPath("c"), scan("c")); await release(runtimePath("c"), { workflows: [] });
  await release(scanPath("b"), scan("b"));
  await page.waitForFunction(() => alfrdStudio.ctx.loadInfo("b").badge === "");
  assert.equal(counts.get(runtimePath("b")), 1, "retry only failed scan source");
  await page.waitForFunction(() => !document.querySelector("#project-load-notice button[data-project=b]"));
  assert.equal(await page.evaluate(() => ["H2", "MAIN"].includes(document.activeElement.tagName)), true, "retry removal restores useful focus");
  await page.evaluate(() => alfrdStudio.ctx.switchProject("all"));
  await page.waitForFunction(() => document.querySelector("#project-load-notice").textContent.includes("Results are still loading"));
  for (const theme of ["dark", "light"]) {
    await page.addStyleTag({ url: `css/themes/${theme === "light" ? "daylight-orbit" : "obsidian-orbit"}/theme.css` });
    assert.ok(await page.locator("#project-load-notice").evaluate((e) => e.getBoundingClientRect().right <= innerWidth));
    const contrast = await page.locator("#project-load-notice .grow").evaluate((e) => {
      const rgb = (s) => s.match(/[\d.]+/g).map(Number);
      const body = rgb(getComputedStyle(document.body).backgroundColor), bg = rgb(getComputedStyle(e.parentElement).backgroundColor);
      const composite = bg.slice(0, 3).map((v, i) => v * (bg[3] ?? 1) + body[i] * (1 - (bg[3] ?? 1)));
      const lum = (v) => v.map((n) => n / 255).map((n) => n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4).reduce((sum, n, i) => sum + n * [.2126, .7152, .0722][i], 0);
      const values = [lum(rgb(getComputedStyle(e).color)), lum(composite)].sort((a, b) => b - a);
      return (values[0] + .05) / (values[1] + .05);
    });
    assert.ok(contrast >= 4.5, `${theme} notice text contrast: ${contrast.toFixed(2)}:1`);
    t.diagnostic(`${theme} notice contrast ${contrast.toFixed(2)}:1`);
    if (process.env.ALFRD_LOADING_SCREENSHOTS) await page.screenshot({ path: resolve(process.env.ALFRD_LOADING_SCREENSHOTS, `alfrd-loading-${theme}.png`) });
  }
  const oldPaths = [...held.keys()];
  await page.evaluate(() => alfrdStudio.ctx.switchProject("a"));
  await page.evaluate(async () => { window.reloadResult = await alfrdStudio.loadServer(); });
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.loadInfo("a").badge), "Updating");
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.workflowFile.text), "draft text", "reload keeps current draft");
  for (const path of oldPaths) await release(path, path.endsWith("scan") ? { ...scan(path.split("/")[4]), project_name: "STALE" } : { workflows: [] });
  assert.equal(await page.evaluate(() => Object.values(alfrdStudio.ctx.state.projectNames).includes("STALE")), false);
  await release(scanPath("a"), { error: { message: "retry disk failure" } }, 500);
  await release(runtimePath("a"), { workflows: [] });
  await page.waitForFunction(() => alfrdStudio.ctx.loadInfo("a").badge === "Update failed");
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.workflowFile.text), "draft text");
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.source), "server");
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.consoleLines.some((l) => /No project yet/.test(l.text || l.message))), false);
  const again = page.locator("#project-load-notice button[data-project=a]");
  await again.press("Enter");
  await page.focus("#pick-project");
  for (const path of [...held.keys()].filter((p) => !p.includes("/a/"))) await release(path, path.endsWith("scan") ? scan(path.split("/")[4]) : { workflows: [] });
  await release(scanPath("a"), scan("a"));
  await page.waitForFunction(() => !document.querySelector("#project-load-notice button[data-project=a]"));
  assert.equal(await page.evaluate(() => document.activeElement.id), "pick-project", "successful retry never steals focus from another control");
  assert.equal(await page.evaluate(() => alfrdStudio.ctx.state.workflowFile.text), "draft text");
  assert.deepEqual(errors, []);
});
