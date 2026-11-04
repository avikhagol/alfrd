import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));
test("refresh patches only changed markup and preserves native and project dropdowns", { skip: !chromium && "Playwright unavailable" }, async (t) => {
  const service = createServer(async (req, res) => {
    const path = resolve(web, new URL(req.url, "http://localhost").pathname.slice(1));
    if (req.url === "/") { res.end('<!doctype html><link rel="stylesheet" href="/css/studio.css"><div id="panel" style="overflow:auto;height:120px;width:300px"></div><button id="outside">Outside</button><button id="projects"><span class="picker-val"></span></button>'); return; }
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); res.end(); return; }
    try { res.setHeader("Content-Type", [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); res.end(await readFile(path)); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const page = await browser.newPage(); t.after(() => page.close());
  const errors = []; page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${service.address().port}/`);
  assert.deepEqual(await page.evaluate(async () => {
    const { refreshHTML } = await import("/js/utils/refresh.js");
    const panel = document.getElementById("panel");
    const html = (label, options = "") => `<div><svg><path d="M0 0"/></svg><p id="changed">${label}</p><select id="scope"><option value="a">A</option><option value="b">B</option>${options}</select><details><summary>Details</summary><p style="height:600px">Long details</p></details></div>`;
    refreshHTML(panel, html("First")); panel.querySelector("details").open = true; panel.scrollTop = 70;
    const svg = panel.querySelector("svg"), detail = panel.querySelector("details"), content = detail.lastElementChild;
    const records = [], observer = new MutationObserver((items) => records.push(...items)); observer.observe(panel, { childList: true, subtree: true, attributes: true });
    const unchanged = refreshHTML(panel, html("First")); await Promise.resolve();
    const noWrites = !unchanged && records.length === 0;
    refreshHTML(panel, html("Changed"));
    const patched = panel.querySelector("svg") === svg && panel.querySelector("details") === detail && detail.lastElementChild === content && detail.open && panel.scrollTop === 70;
    const select = panel.querySelector("select"), option = select.options[0]; select.value = "b"; select.focus();
    refreshHTML(panel, html("Latest", '<option value="c">C</option>'));
    const held = select.options.length === 2 && select.options[0] === option && select.value === "b" && document.activeElement === select;
    document.getElementById("outside").focus(); await Promise.resolve();
    const flushed = panel.querySelector("select") === select && select.options.length === 3 && select.value === "b" && panel.querySelector("#changed").textContent === "Latest";
    // A deferred change that is superseded by the current source must not replay on blur.
    select.focus(); refreshHTML(panel, html("Obsolete")); refreshHTML(panel, html("Latest", '<option value="c">C</option>'));
    document.getElementById("outside").focus(); await Promise.resolve();
    const latestWins = panel.querySelector("#changed").textContent === "Latest";
    observer.disconnect(); return [noWrites, patched, held, flushed, latestWins];
  }), Array(5).fill(true));
  await page.evaluate(async () => {
    const { mountPicker } = await import("/js/components/picker.js");
    window.items = Array.from({ length: 30 }, (_, i) => ({ value: `p${i}`, label: `Project ${i}` }));
    window.picker = mountPicker(document.getElementById("projects"), { label: "Project", onChange() {} }); picker.set(items, "p0"); picker.open();
  });
  await page.locator(".picker-filter").fill("Project 1");
  await page.locator(".picker-filter").press("ArrowDown");
  await page.evaluate(() => {
    const list = document.querySelector(".picker-list"), filter = document.querySelector(".picker-filter"); list.scrollTop = 30;
    window.kept = { list, option: list.firstElementChild, filter, active: filter.getAttribute("aria-activedescendant"), top: list.scrollTop };
    picker.set(items, "p0"); picker.set([...items, { value: "new", label: "New project" }], "p0");
  });
  assert.equal(await page.evaluate(() => {
    const filter = document.querySelector(".picker-filter");
    return filter === kept.filter && filter.value === "Project 1" && document.activeElement === filter && filter.getAttribute("aria-activedescendant") === kept.active && kept.list.firstElementChild === kept.option && kept.list.scrollTop === kept.top && picker.value === "p0";
  }), true);
  await page.locator(".picker-filter").press("Escape");
  await page.evaluate(() => picker.open());
  assert.match(await page.locator(".picker-list").innerText(), /New project/);
  assert.deepEqual(errors, []);
});
