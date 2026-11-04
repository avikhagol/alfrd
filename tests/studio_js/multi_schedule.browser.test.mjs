import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional browser dependency */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

test("multiple scheduled blocks: append, new time, edit retention, change time, run and cancel", { skip: !chromium && "Playwright unavailable" }, async (t) => {
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
    window.state = { schedule: null, schedules: [], schedule_revision: 0, server_now: new Date().toISOString(), schedule_service: { available: true } };
    window.drafts = {};
    window.requests = []; window.mutations = []; window.confirm = () => true;
    server.session = { mutations_enabled: true, csrf_token: "token" };
    const response = (body, status = 200) => new Response(JSON.stringify(body), { status });
    server.request = async (url, init) => {
      if (url.endsWith("/probe")) return response({ agents: {} });
      if (!init?.method) return response(state);
      const body = JSON.parse(init.body); mutations.push({ url, body });
      if (url.endsWith("/messages")) {
        const batch = state.schedules.find((b) => b.id === body.schedule_id);
        if (!batch) return response({ error: { message: "Schedule no longer open" } }, 409);
        const item = { id: `qi_${mutations.length}`, text: body.text, attachments: [], created_at: new Date().toISOString(), version: 1 };
        batch.items.push(item); state.schedule_revision++;
        return response({ ...state, queued: true, item, request: null }, 201);
      }
      let selected;
      if (url.endsWith("/schedule")) {
        let batch = state.schedules.find((b) => b.run_at === body.run_at);
        if (!batch) { batch = { id: `sch_${mutations.length}`, status: "scheduled", run_at: body.run_at, timezone: body.timezone, items: [] }; state.schedules.push(batch); }
        state.schedule = batch; selected = batch.id;
      } else {
        const bid = url.split("/schedule/")[1].split("/")[0];
        const batch = state.schedules.find((b) => b.id === bid);
        if (url.includes("/items/")) {
          const item = batch.items.find((i) => i.id === url.split("/").at(-1));
          if (init.method === "DELETE") item.removed_at = new Date().toISOString();
          else { item.text = body.text; item.version++; }
        } else if (url.endsWith("/run") || url.endsWith("/cancel")) {
          if (url.endsWith("/run")) requests.push(batch.items.map((i) => i.text));
          state.last_schedule = { ...batch, status: url.endsWith("/run") ? "submitted" : "cancelled" };
          state.schedules = state.schedules.filter((b) => b !== batch);
          state.schedule = state.schedules[0] || null;
        } else Object.assign(batch, { run_at: body.run_at, timezone: body.timezone });
      }
      state.schedule_revision++; return response({ ...state, selected_schedule_id: selected });
    };
    for (const [hostId, id] of [["chat", "chat"], ["dock", "cd"]]) {
      const host = document.getElementById(hostId); host.innerHTML = composerHtml(id, { attachments: true });
      const draft = mountComposerDraft(host, id, { settings: () => ({ orchestrator: "claude" }) });
      bindComposer(host, id, { agents: () => ({ codex: {} }), orchestrator: () => "claude", send: () => draft.send(), onDraft: (text) => draft.edit(text) });
      drafts[id] = draft;
      draft.select("p", "s");
    }
  });
  const panel = page.locator("#chat-schedule");
  const setTime = async (value) => {
    await panel.locator("[name=time]").fill(value);
    await panel.locator("[name=zone]").fill("UTC");
    await panel.locator("[data-save-time]").click();
  };
  await page.waitForFunction(() => !document.getElementById("chat-schedule-toggle").disabled);
  const structure = () => page.locator("#chat-form .chat-composer-row").evaluate((row) => [...row.children].map((e) => [e.tagName, e.id, e.className]));
  const markup = await structure();
  await page.locator("#chat-schedule-toggle").click();
  await setTime("2099-10-28T17:00");
  const send = async (text) => {
    await page.locator("#chat-input").fill(text);
    assert.equal(await page.locator("#chat-send").isDisabled(), false, await page.locator("#chat-form").innerText());
    await page.locator("#chat-send").click();
    await page.waitForFunction(() => document.getElementById("chat-input").value === "");
  };
  await send("- a\n- b");
  assert.equal(await panel.locator("[data-batch]").count(), 1);
  assert.equal(await panel.locator("[data-enable]").innerText(), "Schedule another message");
  await panel.locator("[data-enable]").click(); await setTime("2099-10-29T17:00");
  await send("second block");
  assert.equal(await panel.locator("[data-batch]").count(), 2);
  const first = panel.locator("[data-batch]").filter({ hasText: "- a" });
  const second = panel.locator("[data-batch]").filter({ hasText: "second block" });
  assert.equal(await first.locator("[data-edit]").count(), 1); // bullets remain one queued item
  assert.equal(await page.evaluate(() => state.schedules[0].items[0].text), "- a\n- b");
  assert.notEqual(await first.getAttribute("data-batch"), await second.getAttribute("data-batch"));
  for (const block of [first, second]) {
    for (const selector of ["[data-edit]", "[data-time]", "[data-append]"]) {
      assert.ok(await block.locator(selector).getAttribute("aria-label"));
      assert.ok(await block.locator(selector).getAttribute("title"));
    }
    assert.equal(await block.locator("[data-run]").count(), 1);
    assert.equal(await block.locator("[data-cancel]").count(), 1);
  }
  await first.locator("[data-append]").click(); await send("append first");
  assert.match(await first.innerText(), /append first/);
  assert.doesNotMatch(await second.innerText(), /append first/);
  await second.locator("[data-append]").click(); await send("append second");
  assert.doesNotMatch(await first.innerText(), /append second/);
  await second.locator("[data-edit]").first().click();
  await panel.locator("[data-edit-text]").fill("second block edited");
  await panel.locator("[data-save]").click();
  await second.filter({ hasText: "second block edited" }).waitFor();
  await first.locator("[data-time]").click(); await setTime("2099-10-28T18:00");
  await second.locator("[data-time]").click(); await setTime("2099-10-30T17:00");
  assert.match(await second.innerText(), /2099/);
  await first.locator("[data-edit]").first().click();
  await panel.locator("[data-edit-text]").fill("unsaved first edit");
  assert.equal(await page.evaluate(async () => {
    const editor = document.querySelector("#chat-schedule [data-edit-text]"); editor.focus(); editor.setSelectionRange(3, 7);
    const { scheduleController } = await import("/js/data/chat_schedule.js");
    state.schedule_revision++; state.schedules[1].items.push({ id: "external", text: "external refresh", version: 1 });
    await scheduleController("p", "s").refresh();
    return editor === document.querySelector("#chat-schedule [data-edit-text]") && editor.value === "unsaved first edit" && editor.selectionStart === 3 && editor.selectionEnd === 7;
  }), true);
  await panel.locator("[data-save]").click();
  await panel.locator("[data-batch]").filter({ hasText: "unsaved first edit" }).waitFor();
  // A retained chat resumes with authoritative schedules after changes while away.
  await page.evaluate(() => { drafts.chat.suspend(); drafts.cd.suspend(); state.schedule_revision++; state.schedules[1].items.push({ id: "while-away", text: "while away", version: 1 }); drafts.chat.resume(); });
  await panel.locator("[data-batch]").filter({ hasText: "while away" }).waitFor();
  // Choosing exactly the first time selects that block for append, without creating a third.
  await panel.locator("[data-enable]").click(); await setTime("2099-10-28T18:00"); await send("same time append");
  assert.equal(await panel.locator("[data-batch]").count(), 2);
  const updated = panel.locator("[data-batch]").filter({ hasText: "unsaved first edit" });
  await updated.locator("[data-run]").click();
  await page.waitForFunction(() => requests.length === 1);
  assert.deepEqual(await page.evaluate(() => requests[0]), ["unsaved first edit", "append first", "same time append"]);
  assert.equal(await panel.locator("[data-batch]").count(), 1);
  assert.match(await second.innerText(), /second block edited/);
  assert.match(await second.innerText(), /append second/);
  await panel.locator("[data-batch] [data-cancel]").click();
  await page.waitForFunction(() => state.schedules.length === 0);
  assert.equal(await page.locator("#chat-input").getAttribute("rows"), "1");
  // Composer structure stays the same; only the existing send label changes during scheduling.
  assert.deepEqual(await structure(), markup);
  assert.deepEqual(errors, []);
});
