import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

test("chat list: rename, delete and fold the sessions sidebar", { skip: !chromium && "Playwright not available" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (data, type) => { res.writeHead(200, { "Content-Type": type }); res.end(data); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/obsidian-orbit/theme.css"><link rel="stylesheet" href="/css/chat.css"><body style="display:block;height:auto;margin:0"><main id="home"></main></body>`, "text/html");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());

  const open = async (width) => {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.setDefaultTimeout(7000);
    const errors = []; page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${service.address().port}/`);
    await page.evaluate(async () => {
      const { server } = await import("/js/data/server.js");
      const { DEFAULTS } = await import("/js/data/chat.js");
      window.sessions = [{ id: "a", title: "Alpha", messages: 0, active_requests: 0 }, { id: "b", title: "Beta", messages: 0, active_requests: 0 },
        { id: "c", title: "Busy", messages: 0, active_requests: 1 }];
      window.mutations = []; window.confirm = () => window.answer !== false;
      const state = (sid) => ({ session: { id: sid }, requests: [], tasks: [], messages: [], settings: DEFAULTS, mailbox_version: 1,
        graph: { nodes: [], edges: [] }, blockers: [], decisions: [], plan: { id: `chat-${sid}`, runner_alive: false } });
      Object.assign(server, { session: { mutations_enabled: true }, request: async (url) => {
        let data;
        if (url.endsWith("/sessions")) data = { sessions: window.sessions };
        else if (url.endsWith("/settings")) data = DEFAULTS;
        else if (url.includes("/mailbox/")) data = { messages: [], events: [], next_cursor: null, has_more: false };
        else if (url.includes("/activity")) data = { items: [] };
        else data = state(url.split("/sessions/")[1]?.split(/[/?]/)[0]);
        return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
      }, mutate: async (url, data, method = "POST") => {
        window.mutations.push({ url, data, method });
        const sid = url.split("/sessions/")[1];
        if (method === "PATCH") { const s = window.sessions.find((x) => x.id === sid); s.title = data.title; return { session: s }; }
        if (method === "DELETE") { window.sessions = window.sessions.filter((x) => x.id !== sid); return { id: sid, deleted: true }; }
        return {};
      } });
      const host = document.getElementById("home"), mod = await import("/js/components/chat_view.js");
      const ctx = { state: { mode: "server", chatSession: "a", selectedProject: "p", selectedTarget: "", liveStatus: { state: "live" } },
        activeProject: () => "p", toast(m) { window.lastToast = m; }, update() { mod.render(host, ctx); } };
      window.ctx = ctx;
      mod.render(host, ctx);
    });
    await page.locator('.chat-session[data-sid="a"].on').waitFor({ state: "attached" });
    return { page, errors };
  };

  await t.test("rename: F2 / button, Enter saves, Esc keeps the old name", async () => {
    const { page, errors } = await open(1440); t.after(() => page.close());
    await page.locator('[data-chat=rename][data-sid-act="b"]').click();
    const input = page.locator("#chat-rename-input");
    assert.equal(await input.inputValue(), "Beta");
    await input.fill("Beta release");
    await input.press("Enter");
    await page.locator('.chat-session[data-sid="b"]', { hasText: "Beta release" }).waitFor();
    assert.deepEqual(await page.evaluate(() => window.mutations.at(-1)), { url: "/studio/projects/p/chat/sessions/b", data: { title: "Beta release" }, method: "PATCH" });
    await page.locator('.chat-session[data-sid="a"]').focus();
    await page.keyboard.press("F2");
    await page.locator("#chat-rename-input").fill("Something else");
    await page.keyboard.press("Escape");
    await page.locator('.chat-session[data-sid="a"]', { hasText: "Alpha" }).waitFor();
    assert.equal(await page.evaluate(() => window.mutations.length), 1);
    assert.deepEqual(errors, []);
  });

  await t.test("delete: confirm, open the next chat; busy chats can't be deleted", async () => {
    const { page, errors } = await open(1440); t.after(() => page.close());
    assert.equal(await page.locator('[data-chat=delete][data-sid-act="c"]').isDisabled(), true);
    await page.evaluate(() => { window.answer = false; });
    await page.locator('[data-chat=delete][data-sid-act="a"]').click();
    assert.equal(await page.evaluate(() => window.mutations.length), 0);
    await page.evaluate(() => { window.answer = true; });
    await page.locator('[data-chat=delete][data-sid-act="a"]').click();
    await page.locator('.chat-session[data-sid="b"].on').waitFor();
    assert.equal(await page.locator('.chat-session[data-sid="a"]').count(), 0);
    assert.equal(await page.evaluate(() => window.mutations[0].method), "DELETE");
    assert.equal(await page.evaluate(() => window.ctx.state.chatSession), "b");
    assert.deepEqual(errors, []);
  });

  for (const width of [1440, 1024]) {
    await t.test(`fold the chat list at ${width}px; remembered`, async () => {
      const { page, errors } = await open(width); t.after(() => page.close());
      const list = page.locator(".chat-sessions");
      const mainWidth = () => page.locator(".chat-main").evaluate((e) => e.getBoundingClientRect().width);
      const before = await mainWidth();
      await page.locator("[data-chat=collapse]").click();
      await list.waitFor({ state: "hidden" });
      assert.ok(await mainWidth() > before + 150, "the conversation takes the freed space");
      const show = page.locator("[data-chat=sessions]");
      assert.equal(await show.isVisible(), true);
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("alfrd-studio:ui:chat.sessionsCollapsed"))), true);
      await show.click();
      await list.waitFor({ state: "visible" });
      assert.equal(await show.isVisible(), false);
      assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("alfrd-studio:ui:chat.sessionsCollapsed"))), false);
      assert.deepEqual(errors, []);
    });
  }

  await t.test("phone width: the Chats button still opens the overlay list", async () => {
    const { page, errors } = await open(390); t.after(() => page.close());
    assert.equal(await page.locator("[data-chat=collapse]").isVisible(), false);
    await page.locator("[data-chat=sessions]").click();
    await page.locator(".chat-sessions").waitFor({ state: "visible" });
    await page.locator('[data-chat=rename][data-sid-act="b"]').waitFor({ state: "visible" });
    assert.deepEqual(errors, []);
  });
});
