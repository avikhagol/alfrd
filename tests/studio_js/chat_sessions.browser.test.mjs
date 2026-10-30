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
        { id: "c", title: "Busy", plan_status: "running", messages: 0, active_requests: 1 }];
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
        if (url.endsWith("/sessions")) { const session = { id: `n${window.mutations.length}`, title: "New", messages: 0, active_requests: 0 }; window.sessions = [session, ...window.sessions]; return { session }; }
        return {};
      } });
      const host = document.getElementById("home"), mod = await import("/js/components/chat_view.js");
      const ctx = { state: { mode: "server", chatSession: "a", selectedProject: "p", selectedTarget: "", liveStatus: { state: "live" } },
        activeProject: () => window.project || "p", toast(m) { window.lastToast = m; }, update() { mod.render(host, ctx); } };
      window.ctx = ctx;
      mod.render(host, ctx);
    });
    await page.locator('.chat-session[data-sid="a"].on').waitFor({ state: "attached" });
    return { page, errors };
  };

  await t.test("project-scoped active sessions, empty state, live update and drafts survive A/B/A", async () => {
    const { page, errors } = await open(1440); t.after(() => page.close());
    await page.evaluate(async () => {
      const { server } = await import("/js/data/server.js");
      const { mountChatProjects } = await import("/js/data/chat_projects.js");
      const previous = server.request;
      window.projectRows = {
        p: window.sessions,
        q: [{ id: "a", title: "Same id in Q", active_requests: 0, pending_user: 0 },
          { id: "q-busy", title: "Q active", plan_status: "running", active_requests: 1, pending_user: 0 }],
        empty: [],
      };
      server.request = async (url, options) => {
        const project = decodeURIComponent(url.split("/projects/")[1]?.split("/")[0] || "");
        if (url.endsWith("/sessions")) return new Response(JSON.stringify({ sessions: window.projectRows[project] }));
        return previous(url, options);
      };
      ctx.projects = () => Object.keys(window.projectRows).map((id) => ({ id }));
      ctx.chatOn = () => true;
      ctx.projectName = (p) => (p === 'empty' ? 'Empty project' : p);
      window.controller = mountChatProjects(ctx, { decorate() {}, redecorate() {} });
      const update = ctx.update;
      ctx.update = () => { controller.sync(); update(); };
      window.switchChatProject = (project) => {
        window.project = project; ctx.state.selectedProject = project; ctx.state.chatSession = ""; ctx.update();
      };
      controller.sync();
    });
    await page.locator('#chat-input').fill('Draft in P');
    await page.evaluate(() => switchChatProject('q'));
    await page.locator('.chat-session[data-sid="q-busy"].on').waitFor();
    assert.equal(await page.locator('.chat-session[data-sid="c"]').count(), 0);
    assert.equal(await page.locator('.chat-session[data-sid="q-busy"] .chat-running').getAttribute("aria-label"), "Running");
    assert.equal(await page.locator('#chat-input').inputValue(), '');
    await page.locator('#chat-input').fill('Draft in Q');
    await page.locator('.chat-session[data-sid="q-busy"]').focus();
    await page.evaluate(() => {
      window.focusedChatRow = document.activeElement;
      window.projectRows.q = [...window.projectRows.q, { id: 'live-q', title: 'Live Q question', active_requests: 0, pending_user: 1 }];
      window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'q', events: [{ plan: 'chat-live-q' }] } }));
    });
    await page.locator('.chat-session[data-sid="live-q"]').waitFor();
    assert.equal(await page.locator('.chat-session.on').getAttribute('data-sid'), 'q-busy');
    assert.equal(await page.evaluate(() => document.activeElement === window.focusedChatRow), true);
    await page.evaluate(() => switchChatProject('empty'));
    await page.getByText('No conversations in Empty project.', { exact: false }).waitFor();
    assert.equal(await page.locator('.chat-session').count(), 0);
    await page.evaluate(() => switchChatProject('p'));
    await page.locator('.chat-session[data-sid="a"].on').waitFor();
    assert.equal(await page.locator('#chat-input').inputValue(), 'Draft in P');
    assert.equal(await page.locator('.chat-session[data-sid="live-q"]').count(), 0);
    await page.evaluate(() => switchChatProject('q'));
    await page.locator('.chat-session[data-sid="q-busy"].on').waitFor();
    assert.equal(await page.locator('#chat-input').inputValue(), 'Draft in Q');
    assert.deepEqual(errors, []);
  });

  await t.test("one click = one request after the view is mounted again (project switches)", async () => {
    const { page, errors } = await open(1440); t.after(() => page.close());
    for (const p of ["q", "p", "q", "p"]) {
      await page.evaluate((p) => { window.project = p; window.ctx.state.selectedProject = p; window.ctx.update(); }, p);
      await page.locator('.chat-session[data-sid="a"]').waitFor({ state: "attached" });
    }
    await page.evaluate(() => { window.mutations = []; });
    await page.locator("[data-chat=new]").click();
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.mutations.filter((m) => m.url.endsWith("/sessions")).length), 1);
    await page.evaluate(() => { ctx.state.chatSession = "b"; ctx.update(); });
    if (await page.locator('.chat-session-menu').getAttribute('open') === null) await page.locator('.chat-session-menu summary').click();
    await page.locator('[data-chat=delete][data-sid-act="b"]').click();
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => window.mutations.filter((m) => m.method === "DELETE").length), 1);
    assert.deepEqual(errors, []);
  });

  await t.test("rename: F2 / button, Enter saves, Esc keeps the old name", async () => {
    const { page, errors } = await open(1440); t.after(() => page.close());
    await page.evaluate(() => { ctx.state.chatSession = "b"; ctx.update(); });
    if (await page.locator('.chat-session-menu').getAttribute('open') === null) await page.locator('.chat-session-menu summary').click();
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

  await t.test("header title: focus/click edits, Enter/blur save, Escape/empty revert, failures restore", async () => {
    const { page, errors } = await open(1440); t.after(() => page.close());
    const title = page.getByRole("textbox", { name: "Rename chat", exact: true });
    await title.focus();
    assert.equal(await title.evaluate((el) => el === document.activeElement), true);
    assert.equal(await title.getAttribute("maxlength"), "120");
    assert.equal(await title.evaluate((el) => getComputedStyle(el).outlineStyle), "solid");
    await title.fill("  Release notes  ");
    // A live header status change must retain the draft element and cursor.
    await title.evaluate((el) => { window.titleElement = el; el.setSelectionRange(4, 4); ctx.state.liveStatus.state = "polling"; ctx.update(); });
    assert.equal(await title.evaluate((el) => el === window.titleElement && el.selectionStart === 4), true);
    await title.press("Enter");
    await page.locator('.chat-session[data-sid="a"]', { hasText: "Release notes" }).waitFor();
    assert.equal(await title.inputValue(), "Release notes");
    assert.equal(await page.evaluate(() => mutations.length), 1);
    await title.click(); await title.fill("Discard"); await title.press("Escape");
    assert.equal(await title.inputValue(), "Release notes");
    await title.focus(); await title.fill(" \t "); await title.press("Enter");
    assert.equal(await title.inputValue(), "Release notes");
    assert.equal(await page.evaluate(() => mutations.length), 1);
    await title.click(); await title.fill("Blur saved"); await page.locator("#chat-input").focus();
    await page.locator('.chat-session[data-sid="a"]', { hasText: "Blur saved" }).waitFor();
    assert.equal(await title.inputValue(), "Blur saved");
    assert.equal(await page.evaluate(() => mutations.length), 2);
    // Enter immediately followed by blur must issue only one request while saving.
    await page.evaluate(async () => {
      const { server } = await import("/js/data/server.js");
      const mutate = server.mutate;
      server.mutate = (...args) => new Promise((resolve) => { window.finishRename = () => resolve(mutate(...args)); });
    });
    await title.focus(); await title.fill("Pending"); await title.press("Enter");
    assert.equal(await title.getAttribute("aria-busy"), "true");
    await page.evaluate(() => ctx.update());
    await page.evaluate(() => finishRename());
    await page.locator('.chat-session[data-sid="a"]', { hasText: "Pending" }).waitFor();
    assert.equal(await page.evaluate(() => mutations.length), 3);
    await page.evaluate(async () => {
      const { server } = await import("/js/data/server.js");
      server.mutate = async () => { throw new Error("Offline"); };
    });
    await title.focus(); await title.fill("Failed"); await title.press("Enter");
    await page.waitForFunction(() => window.lastToast === "Rename failed: Offline");
    assert.equal(await title.inputValue(), "Pending");
    assert.deepEqual(errors, []);
  });

  await t.test("delete: confirm, open the next chat; busy chats can't be deleted", async () => {
    const { page, errors } = await open(1440); t.after(() => page.close());
    await page.evaluate(() => { ctx.state.chatSession = "c"; ctx.update(); });
    if (await page.locator('.chat-session-menu').getAttribute('open') === null) await page.locator('.chat-session-menu summary').click();
    assert.equal(await page.locator('[data-chat=delete][data-sid-act="c"]').isDisabled(), true);
    await page.evaluate(() => { window.answer = false; });
    await page.evaluate(() => { ctx.state.chatSession = "a"; ctx.update(); });
    if (await page.locator('.chat-session-menu').getAttribute('open') === null) await page.locator('.chat-session-menu summary').click();
    await page.locator('[data-chat=delete][data-sid-act="a"]').click();
    assert.equal(await page.evaluate(() => window.mutations.length), 0);
    await page.evaluate(() => { window.answer = true; });
    await page.evaluate(() => { ctx.state.chatSession = "a"; ctx.update(); });
    if (await page.locator('.chat-session-menu').getAttribute('open') === null) await page.locator('.chat-session-menu summary').click();
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
    assert.equal(await page.locator(".chat-list [data-chat=rename]").count(), 0);
    assert.deepEqual(errors, []);
  });
});
