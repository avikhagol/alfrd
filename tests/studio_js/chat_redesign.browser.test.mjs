import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

test("redesigned chat: both themes, responsive inspector, history, live tails and dock", { skip: !chromium && "Playwright not available" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (data, type) => { res.writeHead(200, { "Content-Type": type }); res.end(data); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme")}/theme.css"><link rel="stylesheet" href="/css/chat.css"><body style="display:block;height:auto;margin:0"><main id="home"></main><aside id="dock" hidden></aside></body>`, "text/html");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const open = async (theme, width, dock = false) => {
    const page = await browser.newPage({ viewport: { width, height: 980 } });
    page.setDefaultTimeout(7000);
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async (dock) => {
      const { server } = await import("/js/data/server.js");
      const { DEFAULTS } = await import("/js/data/chat.js");
      const ago = (s) => new Date(Date.now() - s * 1000).toISOString();
      const requests = [{ id: "r1", text: "Earlier request", status: "done", created: ago(400), finished: ago(300), usage: { total_cost_usd: .1 } },
        { id: "r2", text: "Redesign the chat", status: "running", created: ago(90), mailbox: { pending_user: 1 }, usage: {} }];
      const tasks = [{ row: "r1.old", request: "r1", kind: "task", owner: "codex", task: { title: "Old result" }, status: "done", logs: [{ unit: "old", status: "done" }] },
        ...["a", "b", "plan"].map((id) => ({ row: `r2.${id}`, request: "r2", kind: id === "plan" ? "plan" : "task", owner: "codex", task: { title: id === "a" ? "Implement chat" : "Verify chat" }, status: "running", logs: [{ unit: id, log: `${id}.log`, status: "running" }] }))];
      const envelope = JSON.stringify({ protocol: "alfrd.orchestration/1", kind: "result", result: { status: "done", summary: "The earlier work is ready.", artifacts: [] } });
      const messages = [{ id: "m1", request: "r1", role: "user", text: "Earlier request", at: ago(400) }, { id: "m2", request: "r1", role: "assistant", agent: "codex", text: envelope, at: ago(300) },
        { id: "m3", request: "r2", role: "user", text: 'Redesign the chat. Preserve this example: {"example": true}', at: ago(90) }];
      const approval = { id: "permission", seq: 1, version: 1, state: "pending", kind: "permission_request", sender: { kind: "agent", agent_id: "codex", task_id: "a" }, recipient: { kind: "user" }, subject: "Publish preview", body: "Publish the reviewed preview.", payload: { action: "publish", description: "Publish preview", target: "preview/chat", action_hash: "sha256:abc", scope: "once" } };
      window.mail = [approval,
        { ...approval, id: "peer", seq: 2, kind: "info", recipient: { kind: "agent", agent_id: "reviewer", task_id: "b" }, subject: "API ready", body: "Use the reply endpoint.", payload: {} },
        { ...approval, id: "orch", seq: 3, kind: "info", recipient: { kind: "orchestrator" }, subject: "Review complete", body: "Ready for summary.", payload: {} }];
      window.fixture = { session: { id: "s", title: "Design chat" }, requests, tasks, messages, settings: DEFAULTS, mailbox_version: 1,
        graph: { nodes: tasks.map((x) => ({ id: x.row, title: x.task.title, owner: x.owner, request: x.request, status: x.status, kind: x.kind })), edges: [] },
        blockers: [{ id: "d1", task: "r2.a", request: "r2", needs: "user", mailbox_message_id: "permission", question: "Publish preview?", status: "pending" },
          { id: "d2", task: "r2.b", request: "r2", needs: "user", kind: "permission", repeat: 2, reason: "outside the allowed directories", question: "Grant access?", status: "pending",
            options: ["Grant this task access and re-run: /srv/site", "Reassign this task to @codex", "Cancel this task"] }], decisions: [], plan: { runner_alive: true } };
      window.activityCalls = []; window.mutations = []; window.tailVersion = 1; window.offline = false;
      Object.assign(server, { session: { mutations_enabled: true }, request: async (url) => {
        let data;
        if (url.includes("/activity?")) {
          window.activityCalls.push(url); if (window.offline) throw new Error("offline");
          const units = new URL(url, location.origin).searchParams.get("units")?.split(",");
          data = { items: tasks.flatMap((task) => task.logs.filter((l) => units ? units.includes(l.unit) : l.status === "running").map((l) => ({ ...l, row: task.row, request: task.request, owner: task.owner, kind: task.kind, title: task.task.title, seconds: 10,
            display_tail: Array.from({ length: 120 }, (_, i) => `Public output ${window.tailVersion} line ${i}`), tail: ['{"type":"technical"}'] }))) };
        } else if (url.includes("/mailbox/")) data = { messages: window.mail, events: [], next_cursor: "one", has_more: false };
        else if (url.endsWith("/sessions")) data = { sessions: [{ id: "s", title: "Design chat" }] };
        else if (url.endsWith("/settings")) data = DEFAULTS;
        else data = window.fixture;
        return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
      }, mutate: async (url, data) => {
        window.mutations.push({ url, data });
        if (window.replyError) throw new Error(window.replyError);
        if (url.includes("/reply")) {
          const reply = { id: "reply", seq: 4, kind: "reply", reply_to: approval.id, sender: { kind: "user" }, recipient: approval.sender, payload: data };
          approval.state = "resolved"; window.mail.push(reply);
          return { message: reply, resolution: { state: "resolved", version: 2 }, delivery: "recorded" };
        }
        return {};
      } });
      const host = document.getElementById(dock ? "dock" : "home"), mod = await import(dock ? "/js/components/chat_dock.js" : "/js/components/chat_view.js");
      const ctx = { state: { mode: "server", chatSession: "s", selectedProject: "p", selectedTarget: "", liveStatus: { state: "live" } }, activeProject: () => "p", toast() {}, update() { mod.render(host, ctx); }, modal(html, setup) {
        const root = document.createElement('section'); root.id = 'graph-modal';
        root.style.cssText = 'position:fixed;inset:20px;z-index:100;display:flex;flex-direction:column;background:var(--surface-float)';
        root.innerHTML = html; document.body.append(root);
        root.querySelector('[data-close]').onclick = () => root.remove();
        setup(root, () => root.remove());
      } };
      window.refresh = () => ctx.update();
      await mod.render(host, ctx, dock ? { width: Math.min(innerWidth, 500), isOpen: () => true } : undefined);
    }, dock);
    await page.locator('.chat-turn-current .chat-run-row').first().waitFor();
    return page;
  };
  for (const theme of ["obsidian-orbit", "daylight-orbit"]) for (const width of [1440, 1024, 390]) {
    await t.test(`${theme} ${width}px`, async () => {
      const page = await open(theme, width); t.after(() => page.close());
      const errors = []; page.on("pageerror", (e) => errors.push(e.message));
      const old = page.locator('.chat-turn').filter({ has: page.locator(':scope > summary', { hasText: "Earlier request" }) });
      assert.equal(await old.getAttribute("open"), null);
      await old.locator(':scope > summary').click();
      await old.getByText("The earlier work is ready.", { exact: true }).waitFor();
      assert.equal(await old.locator('.chat-text').last().innerText(), "The earlier work is ready.");
      await old.locator(".chat-message-slot details > summary").click();
      assert.ok((await old.locator('.chat-message-slot pre').last().innerText()).includes("alfrd.orchestration/1"));
      const active = page.locator('.chat-turn-current');
      assert.equal(await active.locator('.chat-run-row').count(), 3);
      const row = active.locator('.chat-run-row').filter({ hasText: "Implement chat" });
      await row.locator('.chat-output > summary').click();
      await page.waitForFunction(() => document.querySelector('.chat-output[open] pre').textContent.includes('Public output'));
      assert.equal(await row.locator('[data-cost]').innerText(), "—");
      const pre = row.locator('.chat-act-tail');
      await pre.evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
      await page.evaluate(() => { window.tailVersion = 2; window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'p', events: [{ plan: 'chat-s' }] } })); });
      await page.waitForFunction(() => document.querySelector('.chat-output[open] pre').textContent.includes('Public output 2'));
      assert.equal(await pre.evaluate((el) => el.scrollTop), 0);
      await row.getByRole('button', { name: 'Jump to latest' }).click();
      assert.ok(await pre.evaluate((el) => el.scrollTop > 0));
      assert.equal(await active.locator('.chat-mail-card').count(), 1);
      await active.locator(':scope > summary').click();
      assert.equal(await active.getAttribute('open'), '', 'pending cards cannot be hidden by collapsing history');
      assert.equal(await page.locator('.chat-mail-card input:checked').count(), 0);
      if (width <= 1280) await page.getByRole('button', { name: 'Inspect', exact: true }).click();
      assert.equal(await page.locator('#chat-progress').isVisible(), true);
      assert.equal(await page.locator('#chat-decisions').isVisible(), true);
      assert.match(await page.locator('[data-decision="d2"]').innerText(), /Blocked 2× with the same access[\s\S]*Grant this task access and re-run: \/srv\/site[\s\S]*Reassign this task to @codex/);
      assert.equal(await page.locator('#chat-graph').isVisible(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (width === 1440) {
        await active.getByRole('button', { name: 'View graph', exact: true }).click();
        await page.locator('#chat-gfull .node').first().waitFor();
        assert.equal(await page.locator('#chat-gfull .node').count(), 3, 'run graph filters to this request');
        await page.locator('#chat-gfull .gv-canvas').press('ArrowRight');
        await page.locator('#chat-gfull .gv-canvas').press('Enter');
        assert.equal(await page.locator('#graph-modal').count(), 0);
        assert.equal(await page.locator('#chat-task').isVisible(), true);
        await page.locator('#chat-input').fill('An unsent follow-up');
        await pre.evaluate((el) => { window.savedPre = el; });
        await page.evaluate(() => window.refresh());
        assert.equal(await pre.evaluate((el) => el === window.savedPre), true);
        assert.equal(await page.locator('#chat-input').inputValue(), 'An unsent follow-up');
        await page.evaluate(() => {
          window.tailVersion = 3;
          window.fixture.requests[1].status = 'done';
          window.fixture.requests[1].finished = new Date().toISOString();
          for (const task of window.fixture.tasks.filter((t) => t.request === 'r2')) { task.status = 'done'; for (const log of task.logs) log.status = 'done'; }
          window.dispatchEvent(new CustomEvent('alfrd-events', { detail: { project: 'p', events: [{ plan: 'chat-s' }] } }));
        });
        await page.waitForFunction(() => document.querySelector('.chat-turn-current .chat-run').open === false);
        await page.waitForFunction(() => document.querySelector('.chat-output[open] pre')?.textContent.includes('Public output 3'));
        assert.equal(await old.getAttribute('open'), '', 'explicit history disclosure survives updates');
      }
      if (width === 1440) await page.screenshot({ path: `/tmp/chat-redesign-${theme}.png` });
      assert.deepEqual(errors, []);
    });
  }
  for (const outcome of ["approve", "deny"]) await t.test(`mailbox ${outcome}: run placement, request and durable outcome`, async () => {
    const page = await open("daylight-orbit", 1024); t.after(() => page.close());
    const run = page.locator('.chat-turn-current .chat-run');
    const team = run.locator('.chat-team');
    await team.waitFor();
    assert.equal(await team.getAttribute('open'), null, 'team mail starts collapsed inside its run');
    assert.equal(await page.locator('.chat-turn:not(.chat-turn-current) .chat-team:not([hidden])').count(), 0);
    await team.locator('summary').click();
    assert.match(await team.innerText(), /API ready/);
    assert.match(await team.innerText(), /codex → orchestrator/);
    const card = page.locator('.chat-mail-card');
    await card.locator('textarea').fill('Reviewed this action');
    await card.getByRole('button', { name: outcome === 'approve' ? 'Approve once' : 'Deny', exact: true }).click();
    await page.waitForFunction((outcome) => document.querySelector('[data-resolution]').textContent.includes(outcome === 'approve' ? 'Approved once' : 'Denied'), outcome);
    const mutation = await page.evaluate(() => window.mutations[0]);
    assert.equal(mutation.url, '/studio/projects/p/chat/sessions/s/requests/r2/mailbox/messages/permission/reply');
    assert.equal(mutation.data.outcome, outcome);
    assert.equal(mutation.data.action_hash, 'sha256:abc');
    assert.equal(mutation.data.expected_version, 1);
    assert.equal(mutation.data.answer, 'Reviewed this action');
    assert.ok(mutation.data.idempotency_key);
    await page.waitForTimeout(220);
    assert.equal(await card.getByRole('button', { name: 'Approve once', exact: true }).isDisabled(), true);
    assert.match(await card.locator('[data-resolution]').innerText(), outcome === 'approve' ? /Approved once/ : /Denied/);
    await run.locator(':scope > summary').click();
    assert.equal(await card.isVisible(), true, 'permission outcome remains visible with run collapsed');
  });
  await t.test("mailbox failure preserves draft and permits a changed retry", async () => {
    const page = await open("daylight-orbit", 1024); t.after(() => page.close());
    const card = page.locator('.chat-mail-card'); await card.waitFor();
    await page.evaluate(() => { window.replyError = 'Request failed'; });
    await card.locator('textarea').fill('Check the target');
    await card.getByRole('button', { name: 'Approve once', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-error]').textContent === 'Request failed');
    assert.equal(await card.getByRole('button', { name: 'Approve once', exact: true }).isDisabled(), false);
    assert.equal(await card.locator('textarea').inputValue(), 'Check the target');
    await card.getByRole('button', { name: 'Approve once', exact: true }).click();
    await page.waitForFunction(() => window.mutations.length === 2);
    assert.equal(await page.evaluate(() => window.mutations[0].data.idempotency_key === window.mutations[1].data.idempotency_key), true);
    await page.evaluate(() => { window.replyError = ''; });
    await card.getByRole('button', { name: 'Deny', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('[data-resolution]').textContent.includes('Denied'));
    assert.equal(await page.evaluate(() => window.mutations[0].data.idempotency_key === window.mutations[2].data.idempotency_key), false);
  });
  await t.test("dock shares history, readable messages and expandable live output", async () => {
    const page = await open("daylight-orbit", 1024, true); t.after(() => page.close());
    assert.equal(await page.locator('#cd-log .chat-turn-current .chat-run-row').count(), 3);
    await page.locator('#cd-log .chat-output > summary').nth(1).click();
    await page.waitForFunction(() => document.querySelector('#cd-log .chat-output[open] pre').textContent.includes('Public output'));
    await page.locator('.chat-mail-card').getByRole('button', { name: 'Deny', exact: true }).click();
    await page.waitForFunction(() => window.mutations.length > 0);
    assert.equal(await page.evaluate(() => window.mutations[0].data.outcome), "deny");
    assert.equal(await page.evaluate(() => window.mutations[0].data.action_hash), "sha256:abc");
    assert.equal(await page.getByRole('link', { name: 'Open full', exact: true }).isVisible(), true);
  });
});
