import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { taskIslands, taskMetrics, islandProgress, outputUnits, ownerSlot } from "../../src/alfrd/web/js/data/chat_islands.js";

const task = (row, status = "running", logs = []) => ({ row, request: "r", kind: "task", owner: "codex", task: { title: row }, status, logs });
test("task islands retain retries, request identity and authoritative task status", () => {
  const a = task("a", "done", [{ unit: "a1", status: "failed" }, { unit: "a2", status: "done" }]);
  const state = { tasks: [a, task("b", "queued"), { ...task("plan", "done"), kind: "plan" }, { ...task("a"), request: "other" }] };
  const groups = taskIslands(state, [{ row: "a", request: "r", unit: "a2", status: "running" }], "r");
  assert.deepEqual(groups.map((t) => [t.row, t.status, t.attempts.length]), [["a", "done", 2], ["b", "queued", 1]]);
  assert.deepEqual(groups[0].attempts.map((t) => t.status), ["failed", "done"]);
  assert.deepEqual(islandProgress(groups), { total: 2, done: 1, failed: 0, blocked: 0, cancelled: 0 });
  assert.equal(islandProgress(taskIslands({ tasks: [{ ...task("plan"), kind: "summary" }] }, [], "r")).total, 0);
});
test("metrics distinguish reported zero, missing data, retry costs and frozen finish times", () => {
  assert.deepEqual(taskMetrics(task("a")), { seconds: null, cost: null });
  assert.deepEqual(taskMetrics({ seconds: 0, usage: { total_cost_usd: 0 } }), { seconds: 0, cost: 0 });
  const timing = { started: "2026-01-01T00:00:00Z", finished: "2026-01-01T00:01:00Z", status: "done", usage: { total_cost_usd: .2 } };
  assert.deepEqual(taskMetrics(timing, [{ cost_usd: 1 }, { cost_usd: 2 }], Date.parse("2026-01-02")), { seconds: 60, cost: .2 });
  assert.equal(taskMetrics({ started: timing.started, status: "running" }, [], Date.parse(timing.started) + 9000).seconds, 9);
  assert.equal(taskMetrics({ status: "done" }, [{ started: timing.started, finished: timing.finished }]).seconds, 60);
});
test("closed output requests no enlarged tails, even after choosing 400", () => {
  const rows = [{ data: { unit: "a" }, output: { open: true }, lines: 400 }, { data: { unit: "b" }, output: { open: false }, lines: 200 }];
  assert.deepEqual(outputUnits(rows, false), []);
  assert.deepEqual(outputUnits(rows, true), [["a", 400]]);
  rows[0].output.open = false;
  assert.deepEqual(outputUnits(rows, true), []);
});
test("dependencies and missing metric fields remain recorded, without guessed values", () => {
  const groups = taskIslands({ tasks: [{ ...task("c", "queued"), depends_on: ["r.a", "r.b"] }] }, [], "r");
  assert.equal(ownerSlot("codex"), "sky");
  assert.notEqual(ownerSlot("agent-a"), ownerSlot("agent-b"));
  assert.deepEqual(groups[0].dependencies, ["r.a", "r.b"]);
  assert.deepEqual(groups[0].metrics, { seconds: null, cost: null });
});

let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional browser gate */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));
test("island DOM, bounded shared polling, disclosure persistence and narrow layouts", { skip: !chromium && "Playwright unavailable" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (value, type) => { res.writeHead(200, { "Content-Type": type }); res.end(value); };
    if (url.pathname === "/") return send('<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/chat-activity.css"><main class="chat" style="display:block;height:auto;padding:8px"><section class="chat-request-line" id="activity"></section><div hidden><section class="chat-request-line" id="second"></section></div></main>', "text/html");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 980 } });
  t.after(() => page.close());
  const errors = []; page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${service.address().port}/`);
  await page.evaluate(async () => {
    const { mountActivity } = await import("/js/components/chat_activity.js");
    const { server } = await import("/js/data/server.js");
    window.calls = [];
    window.req = { id: "r", status: "running", steps: [{ request_id: "r", kind: "execute", task_row: "b", status: "waiting", attempt: 1, waiting_on: ["question"] }] };
    window.tasks = [
      { row: "a", request: "r", kind: "task", owner: "codex", task: { title: "Audit conversation layout" }, status: "done", logs: [{ unit: "a1", status: "failed" }, { unit: "a2", status: "done" }], usage: { total_cost_usd: 0 } },
      { row: "b", request: "r", kind: "task", owner: "sonnet", role: "Verification", task: { title: "Verify keyboard and mobile" }, status: "running", logs: [{ unit: "b1", status: "running" }] },
      { row: "c", request: "r", kind: "task", owner: "codex", task: { title: "Integrate reviewed changes" }, depends_on: ["a", "b"], status: "queued", logs: [] },
      { row: "plan", request: "r", kind: "plan", owner: "claude", status: "done", logs: [] },
    ];
    server.request = async (url) => {
      calls.push(url);
      const explicit = new URL(url, location.origin).searchParams.get("units");
      const items = explicit ? explicit.split(",").map((unit) => ({ unit, row: unit[0], request: "r", kind: "task", status: window.finished ? "done" : "running", seconds: 5, display_tail: ["public output"], tail: ["raw"] }))
        : window.finished ? [] : [{ unit: "b1", row: "b", request: "r", kind: "task", status: "running", seconds: 5, display_tail: ["public output"] }];
      return new Response(JSON.stringify({ items }), { headers: { "Content-Type": "application/json" } });
    };
    const options = { project: () => "p", sid: () => "s", request: () => "r", state: () => ({ requests: [req], tasks }), busy: () => false, agentName: (x) => x };
    window.api = mountActivity(document.querySelector("#activity"), options); api.kick();
    window.api2 = mountActivity(document.querySelector("#second"), options); api2.kick();
    window.savedIsland = document.querySelector(".chat-task-island");
    window.savedOutput = document.querySelector(".chat-task-island .chat-output");
  });
  await page.waitForTimeout(300);
  assert.equal(await page.locator("#activity .chat-task-island").count(), 3);
  assert.equal(await page.locator("#activity .chat-task-island .chat-run-row").count(), 4);
  assert.equal(await page.locator("#activity .chat-output[open]").count(), 0);
  assert.match(await page.locator("#activity [data-task-progress]").innerText(), /1 of 3 tasks complete/);
  assert.match(await page.locator("#activity [data-dependencies]").last().innerText(), /Depends on: a, b/);
  assert.match(await page.locator("#activity .chat-island-metrics").last().innerText(), /Elapsed — \/ Not reported · Cost — \/ Not reported/);
  assert.match(await page.locator("#activity .chat-island-metrics").first().innerText(), /\$0\.000/);
  assert.equal(await page.locator('#activity [data-mail-jump="question"]').count(), 2);
  assert.equal(await page.evaluate(() => calls.length), 1, "both mounts coalesce to one baseline read");
  await page.evaluate(() => {
    window.savedSpinner = document.querySelector('#activity [data-status="running"] .ic');
    window.savedSpin = savedSpinner.getAnimations()[0];
    if (!savedSpin) throw new Error('Running status must be animated');
    api.kick();
  });
  await page.waitForTimeout(250);
  assert.ok(await page.evaluate(() => savedSpinner === document.querySelector('#activity [data-status="running"] .ic')
    && savedSpin === savedSpinner.getAnimations()[0]), "unchanged status retains its SVG and animation across polling redraws");
  const centres = await page.evaluate(async () => {
    const panel = document.querySelector('#activity');
    panel.style.cssText = 'height:200px;overflow:auto';
    const row = savedSpinner.closest('header');
    const offset = () => {
      const s = savedSpinner.getBoundingClientRect(), r = row.getBoundingClientRect();
      return [s.x + s.width / 2 - r.x, s.y + s.height / 2 - r.y];
    };
    const points = [];
    for (const top of [0, 70, 10, 90, 0]) {
      panel.scrollTop = top;
      await new Promise((resolve) => setTimeout(resolve, 80));
      points.push({ offset: offset(), scroll: panel.scrollTop });
    }
    panel.style.cssText = '';
    return points;
  });
  assert.ok(centres.some((p) => p.scroll > 0), "the details panel actually scrolled");
  assert.ok(centres.every((p) => p.offset.every((v, i) => Math.abs(v - centres[0].offset[i]) < 0.1)),
    `spinner stays centred in its scrolling row: ${JSON.stringify(centres)}`);
  await page.locator("#activity .chat-output > summary").first().click();
  await page.locator("#activity select").first().selectOption("400");
  await page.waitForTimeout(250);
  assert.ok(await page.evaluate(() => calls.some((url) => url.includes("lines=400"))));
  await page.locator("#activity .chat-output > summary").first().click();
  await page.waitForTimeout(250);
  await page.evaluate(() => { calls.length = 0; api.kick(); api2.kick(); });
  await page.waitForTimeout(250);
  assert.ok(await page.evaluate(() => calls.every((url) => !url.includes("lines=400"))));
  await page.evaluate(() => { savedOutput.open = true; tasks[0].status = "failed"; req.status = "done"; api.kick(); });
  assert.ok(await page.evaluate(() => savedIsland === document.querySelector(".chat-task-island") && savedOutput.open && document.querySelector("#activity .chat-details").open));
  assert.match(await page.locator("#activity [data-task-error]").first().innerText(), /Failed/);
  await page.evaluate(() => { document.querySelector("#activity .chat-details").open = false; api.kick(); });
  assert.equal(await page.locator("#activity .chat-details").getAttribute("open"), null);
  await page.evaluate(() => { document.querySelector("#activity .chat-details").open = true; });
  await page.evaluate(() => { document.querySelectorAll(".chat-output").forEach((el) => { el.open = false; }); tasks[0].status = "done"; req.status = "running"; api.kick(); });
  for (const theme of ["obsidian-orbit", "daylight-orbit"]) {
    await page.evaluate(async (theme) => {
      document.documentElement.dataset.theme = theme;
      const link = document.createElement("link"); link.rel = "stylesheet"; link.href = `/css/themes/${theme}/theme.css`; document.head.append(link);
      await new Promise((resolve) => { link.onload = resolve; });
    }, theme);
    for (const width of [1440, 360]) {
      await page.setViewportSize({ width, height: 980 });
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${theme} ${width}: no horizontal overflow`);
      if (process.env.ALFRD_ACTIVITY_SCREENSHOTS) {
        await mkdir(process.env.ALFRD_ACTIVITY_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: `${process.env.ALFRD_ACTIVITY_SCREENSHOTS}/${theme}-${width}.png`, fullPage: true });
      }
      if (width === 1440) assert.ok(await page.locator("#activity .chat-task-island").evaluateAll((els) => new Set(els.map((e) => e.getBoundingClientRect().top)).size === 1), "three tasks compare on one row");
      else assert.ok(await page.locator("#activity .chat-task-island").evaluateAll((els) => els.every((e) => e.getBoundingClientRect().height < 650)), "closed output allows next task within transcript height");
    }
  }
  await page.evaluate(() => { finished = true; calls.length = 0; api.kick(); });
  await page.waitForTimeout(250);
  assert.ok(await page.evaluate(() => calls.some((url) => url.includes("units=b1"))), "disappearing running unit gets final explicit read");
  await page.evaluate(() => { api.stop(); api2.stop(); });
  assert.deepEqual(errors, []);
});
