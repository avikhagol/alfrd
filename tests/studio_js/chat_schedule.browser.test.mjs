import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional browser dependency */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

test("shared Chat and narrow dock composer: schedule, add, edit, remove, run and cancel", { skip: !chromium && "Playwright unavailable" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") { res.setHeader("Content-Type", "text/html"); res.end('<link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/chat.css"><main id="chat" class="chat" style="display:block"></main><aside id="dock" class="chat-dock" style="display:block;position:static;width:330px"></aside>'); return; }
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { res.setHeader("Content-Type", extname(path) === ".js" ? "text/javascript" : "text/css"); res.end(await readFile(path)); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.closeAllConnections(); service.close(done); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}), args: ["--no-sandbox"] });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1000, height: 1000 } });
  const errors = []; page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${service.address().port}/`);
  await page.evaluate(async () => {
    const { server } = await import("/js/data/server.js");
    const { composerHtml, bindComposer } = await import("/js/components/chat_composer.js");
    const { mountComposerDraft } = await import("/js/components/composer_draft.js");
    window.state = { schedule: null, schedule_revision: 0, server_now: new Date().toISOString(), schedule_service: { available: true } };
    window.drafts = {};
    window.requests = []; window.mutations = []; window.confirm = () => true;
    server.session = { mutations_enabled: true, csrf_token: "token" };
    const response = (body, status = 200) => new Response(JSON.stringify(body), { status });
    server.request = async (url, init) => {
      if (url.endsWith("/probe")) return response({ agents: {} });
      if (!init?.method) return response(state);
      const body = JSON.parse(init.body); mutations.push({ url, body });
      if (url.endsWith("/messages")) {
        const item = { id: `qi_${state.schedule.items.length + 1}`, text: body.text, attachments: [], created_at: new Date().toISOString(), version: 1 };
        state.schedule.items.push(item); state.schedule_revision++;
        return response({ ...state, queued: true, item, request: null }, 201);
      }
      if (url.endsWith("/schedule")) state.schedule = { id: "sch_one", status: "scheduled", run_at: body.run_at, timezone: body.timezone, items: [] };
      else if (url.includes("/items/")) {
        const item = state.schedule.items.find((i) => i.id === url.split("/").at(-1));
        if (init.method === "DELETE") item.removed_at = new Date().toISOString();
        else { item.text = body.text; item.version++; item.updated_at = new Date().toISOString(); }
      } else if (url.endsWith("/run")) {
        requests.push(state.schedule.items.filter((i) => !i.removed_at).map((i) => i.text));
        state.last_schedule = { ...state.schedule, status: "submitted", request_id: "r0001" }; state.schedule = null;
      } else if (url.endsWith("/cancel")) { state.last_schedule = { ...state.schedule, status: "cancelled" }; state.schedule = null; }
      else Object.assign(state.schedule, { run_at: body.run_at, timezone: body.timezone });
      state.schedule_revision++; return response(state);
    };
    for (const [hostId, id] of [["chat", "chat"], ["dock", "cd"]]) {
      const host = document.getElementById(hostId); host.innerHTML = composerHtml(id, { attachments: true });
      const draft = mountComposerDraft(host, id, { settings: () => ({ orchestrator: "claude" }) });
      bindComposer(host, id, { agents: () => ({ codex: {} }), orchestrator: () => "claude", send: () => draft.send(), onDraft: (text) => draft.edit(text) });
      drafts[id] = draft;
      draft.select("p", "s");
    }
  });
  const create = async (prefix) => {
    await page.locator(`#${prefix}-schedule-toggle`).click();
    await page.locator(`#${prefix}-schedule [name=time]`).fill("2099-10-28T17:00");
    await page.locator(`#${prefix}-schedule [name=zone]`).fill("UTC");
    await page.locator(`#${prefix}-schedule [name=offset]`).focus();
    assert.equal(await page.evaluate(async (prefix) => {
      const select = document.querySelector(`#${prefix}-schedule [name=offset]`), option = select.options[0], value = select.value;
      const { scheduleController } = await import("/js/data/chat_schedule.js"); await scheduleController("p", "s").refresh();
      return select.isConnected && select.options[0] === option && select.value === value && document.activeElement === select && select.closest("dialog").open;
    }, prefix), true, "schedule polling retains the focused offset control and open picker");

    await page.locator(`#${prefix}-schedule [data-save-time]`).click();
    await page.locator(`#${prefix}-schedule [data-run]`).waitFor();
  };
  const heights = {};
  for (const id of ["chat", "cd"]) {
    const input = page.locator(`#${id}-input`);
    await page.waitForFunction((id) => !document.getElementById(`${id}-schedule-toggle`).disabled, id);
    assert.equal(await page.locator(`#${id}-schedule`).isVisible(), false);
    assert.equal(await input.getAttribute("rows"), "1");
    assert.match(await input.getAttribute("placeholder"), /^To orchestrator/);
    const initial = (await input.boundingBox()).height; heights[id] = initial;
    await input.focus();
    assert.equal((await input.boundingBox()).height, initial, "focus alone keeps one line");
    await input.fill(Array.from({length: 30}, (_, i) => `line ${i}`).join("\n"));
    const grown = await input.evaluate((el) => ({ height: el.clientHeight, overflow: getComputedStyle(el).overflowY, scroll: el.scrollHeight }));
    assert.ok(grown.height > initial); assert.ok(grown.height <= 240); assert.ok(grown.scroll > grown.height); assert.equal(grown.overflow, "auto");
    await input.fill("short"); assert.equal((await input.boundingBox()).height, initial);
    await input.fill("");
    const row = await page.locator(`#${id}-form .chat-composer-row`).evaluate((el) => [...el.children].filter(e => !e.hidden).map(e => ({ id: e.id, x: e.getBoundingClientRect().x, bottom: e.getBoundingClientRect().bottom })));
    assert.deepEqual(row.map(e => e.id), ["", `${id}-schedule-toggle`, `${id}-attach`, `${id}-send`]);
    assert.ok(row.every((e, i) => !i || e.x > row[i - 1].x));
    assert.ok(row.every(e => Math.abs(e.bottom - row[0].bottom) < 2));
    const chooser = page.waitForEvent("filechooser"); await page.locator(`#${id}-attach`).click(); await chooser;
  }
  await create("chat");
  assert.equal(await page.locator("#chat-schedule-toggle").getAttribute("aria-pressed"), "true");
  await page.locator("#chat-schedule-toggle").click();
  assert.equal(await page.locator("#chat-schedule").isVisible(), false);
  assert.equal(await page.locator("#chat-schedule-toggle").getAttribute("aria-pressed"), "true", "hiding details preserves the schedule");
  await page.locator("#chat-schedule-toggle").click();
  await page.locator("#cd-schedule-toggle").click();
  await page.locator("#chat-input").fill(Array(5).fill("build a usage report").join("\n")); await page.locator("#chat-send").click();
  await page.waitForFunction(() => document.getElementById("chat-input").value === "");
  assert.equal(await page.locator("#chat-input").evaluate(e => getComputedStyle(e).overflowY), "hidden");
  assert.equal((await page.locator("#chat-input").boundingBox()).height, heights.chat, "sending resets to one line");
  await page.locator("#cd-input").fill("add CSV export"); await page.locator("#cd-send").click();
  await page.locator("#chat-schedule li").nth(1).waitFor();
  await page.locator("#cd-schedule [data-edit]").nth(1).click();
  await page.locator("#cd-schedule [data-edit-text]").fill("add weekly CSV export");
  assert.equal(await page.locator("#cd-schedule [data-run]").isDisabled(), true);
  await page.evaluate(async () => {
    const input = document.querySelector("#cd-schedule [data-edit-text]"); input.focus(); input.setSelectionRange(4, 9); input.style.height = "141px"; window.queuedInput = input;
    const { scheduleController } = await import("/js/data/chat_schedule.js");
    // The fixture shares the controller key shown in its initial setup.
    for (const [project, sid] of [["p", "s"]]) await scheduleController(project, sid).refresh();
  });
  assert.equal(await page.locator("#cd-schedule [data-edit-text]").evaluate(el => el === queuedInput && el.selectionStart === 4 && el.selectionEnd === 9 && el.style.height === "141px" && document.activeElement === el), true);

  await page.locator("#cd-schedule [data-save]").click();
  await page.locator("#chat-schedule li").filter({ hasText: "weekly CSV" }).waitFor();
  await page.locator("#cd-schedule [data-remove]").first().click();
  await page.waitForFunction(() => state.schedule.items.filter((i) => !i.removed_at).length === 1);
  await page.locator("#cd-schedule [data-run]").click();
  await page.locator("#chat-schedule .chat-schedule-status").filter({ hasText: "submitted as r0001" }).waitFor();
  assert.deepEqual(await page.evaluate(() => requests), [["add weekly CSV export"]]);
  await page.locator("#cd-schedule-toggle").click();
  await create("cd");
  await page.locator("#cd-schedule [data-cancel]").click();
  await page.locator("#chat-schedule .chat-schedule-status").filter({ hasText: "cancelled" }).waitFor();
  assert.deepEqual(errors, []);
});
