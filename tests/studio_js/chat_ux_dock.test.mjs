import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

import { filterSessions } from "../../src/alfrd/web/js/data/chat_sessions.js";
import { createRequestStop } from "../../src/alfrd/web/js/data/chat.js";

test("local search composes Needs me, preserves order, and never reads message bodies", () => {
  const sessions = [{id:"alpha",title:"Repeated",pending_user:0}, {id:"b",title:"Repeated",pending_user:2,last_message:{text:"Preview"}}, {id:"c",title:"Long title",messages:[{text:"secret"}]}];
  assert.deepEqual(filterSessions(sessions,"REPEATED",true).map(s=>s.id),["b"]);
  assert.deepEqual(filterSessions(sessions,"preview").map(s=>s.id),["b"]);
  assert.deepEqual(filterSessions(sessions,"alpha").map(s=>s.id),["alpha"]);
  assert.deepEqual(filterSessions(sessions,"secret"),[]);
  assert.equal(sessions.length,3);
});

test("Stop freezes confirmed IDs, rechecks eligibility, isolates requests and retries partial failure", async () => {
  const state = {requests:[{id:"old",status:"running"},{id:"r",status:"running"}],tasks:[{row:"old",request:"old",status:"running"},{row:"done",request:"r",status:"done"},{row:"a",request:"r",status:"running"},{row:"b",request:"r",status:"queued"},{row:"c",request:"r",status:"queued"}]};
  const calls=[], reports=[], confirms=[]; let fail=true;
  const stop=createRequestStop({state:()=>state, context:()=>({project:"p",sid:"s"}),refresh:async()=>{},confirm:(s)=>{confirms.push(s); state.tasks.push({row:"new",request:"r",status:"queued"}); state.tasks.find(t=>t.row==="c").status="done"; return true;},cancel:async id=>{calls.push(id);if(id==="b" && fail)throw Error("offline");}, report:s=>reports.push(s)});
  await stop.stop(); assert.deepEqual(calls,["a","b"]); assert.match(confirms[0],/Cancel 3.*request r/); assert.match(reports[0],/Stopped 1 of 3 tasks. 2 couldn't be stopped/);
  assert.equal(stop.state().label,"Retry Stop"); fail=false; await stop.stop(); assert.deepEqual(calls,["a","b","b"]); assert.ok(!calls.includes("new"));
});

test("Stop refuses cancellation and planning, and guards session switches during confirmation", async()=>{
  const state={requests:[{id:"r",status:"planning"}],tasks:[]};let sid="s",calls=0;
  const args={state:()=>state,context:()=>({project:"p",sid}),refresh:async()=>{},cancel:async()=>calls++};
  const planning=createRequestStop({...args,confirm:()=>false});assert.equal(planning.state().disabled,true); assert.match(planning.state().reason,/Planning/);await planning.stop();
  state.tasks.push({row:"a",request:"r",status:"running"});await planning.stop();assert.equal(calls,0);
  const switching=createRequestStop({...args,confirm:()=>{sid="other";return true;}});await switching.stop();assert.equal(calls,0);
});

test("Stop serializes confirmation and requires a successful fresh snapshot", async()=>{
  const state={requests:[{id:"r",status:"running"}],tasks:[{row:"a",request:"r",status:"running"}]};
  let resolveConfirm, confirmations=0, calls=0, fresh=false;
  const stop=createRequestStop({state:()=>state,context:()=>({project:"p",sid:"s"}),
    confirm:()=>{confirmations++;return new Promise(resolve=>{resolveConfirm=resolve;});},
    refresh:async()=>fresh,cancel:async()=>calls++});
  const first=stop.stop();await stop.stop();assert.equal(confirmations,1);
  resolveConfirm(true);await first;assert.equal(calls,0);assert.equal(stop.state().label,"Retry Stop");
  fresh=true;const retry=stop.stop();resolveConfirm(true);await retry;assert.equal(calls,1);
});

test("dock host integration: sessions, permission continuity, Stop and responsive sheets", { skip: !chromium && "Playwright unavailable" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (data, type) => { res.writeHead(200, { "Content-Type": type }); res.end(data); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme")}/theme.css"><link rel="stylesheet" href="/css/chat.css"><body style="display:block;height:100dvh;margin:0"><style>#home{height:100%;min-height:0}</style><main id="home"></main><aside id="dock" hidden></aside></body>`, "text/html");
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
      document.documentElement.dataset.theme = new URL(location.href).searchParams.get("theme");
      const { server } = await import("/js/data/server.js");
      const { DEFAULTS } = await import("/js/data/chat.js");
      const ago = (s) => new Date(Date.now() - s * 1000).toISOString();
      const requests = [{ id: "r1", text: "Earlier request", status: "done", created: ago(400), finished: ago(300), usage: { total_cost_usd: .1 } },
        { id: "r2", text: "Redesign the chat", status: "running", created: ago(90), mailbox: { pending_user: 1 }, usage: {} }];
      const tasks = [{ row: "r1.old", request: "r1", kind: "task", owner: "codex", task: { title: "Old result" }, status: "done", logs: [{ unit: "old", status: "done" }] },
        ...["a", "b", "c"].map((id, i) => ({ row: `r2.${id}`, request: "r2", kind: "task", owner: id === "b" ? "sonnet" : "codex", task: { title: ["Audit conversation layout", "Verify keyboard and mobile", "Integrate reviewed changes"][i], depends_on: id === "c" ? ["a", "b"] : [] }, status: ["done", "running", "queued"][i], logs: id === "c" ? [] : [{ unit: id, log: `${id}.log`, status: i === 0 ? "done" : "running" }] }))];
      const envelope = JSON.stringify({ protocol: "alfrd.orchestration/1", kind: "result", result: { status: "done", summary: "The earlier work is ready.", artifacts: [] } });
      const messages = [{ id: "m1", request: "r1", role: "user", text: "Earlier request", at: ago(400) }, { id: "m2", request: "r1", role: "assistant", agent: "codex", text: envelope, at: ago(300) },
        { id: "m3", request: "r2", role: "user", text: 'Redesign the chat. Preserve this example: {"example": true}', at: ago(90) }];
      const approval = { id: "permission", seq: 1, version: 1, state: "pending", kind: "permission_request", sender: { kind: "agent", agent_id: "codex", task_id: "a" }, recipient: { kind: "user" }, subject: "Publish preview", body: "Publish the reviewed preview.", payload: { action: "publish", description: "Publish preview", target: "preview/chat", action_hash: "sha256:abc", scope: "once" } };
      window.mail = [approval,
        { ...approval, id: "peer", seq: 2, kind: "info", recipient: { kind: "agent", agent_id: "reviewer", task_id: "b" }, subject: "API ready", body: "Use the reply endpoint.", payload: {} },
        { ...approval, id: "orch", seq: 3, kind: "info", recipient: { kind: "orchestrator" }, subject: "Review complete", body: "Ready for summary.", payload: {} }];
      window.fixture = { session: { id: "s", title: "Chat UX review" }, requests, tasks, messages, settings: DEFAULTS, mailbox_version: 1,
        graph: { nodes: tasks.map((x) => ({ id: x.row, title: x.task.title, owner: x.owner, request: x.request, status: x.status, kind: x.kind })), edges: [] },
        blockers: [{ id: "d1", task: "r2.a", request: "r2", needs: "user", mailbox_message_id: "permission", question: "Publish preview?", status: "pending" },
          { id: "d2", task: "r2.b", request: "r2", needs: "user", kind: "permission", repeat: 2, reason: "outside the allowed directories", question: "Grant access?", status: "pending",
            options: ["Grant this task access and re-run: /srv/site", "Reassign this task to @codex", "Cancel this task"] }], decisions: [], plan: { runner_alive: true } };
      messages.splice(2, 0,
        { id: "m-orch", request: "r1", role: "assistant", agent: "claude", text: "I split the review between layout and verification. The changes will preserve the continuous conversation.", at: ago(290) },
        { id: "m-design", request: "r1", role: "assistant", agent: "codex", text: "Layout note: keep the composer anchored and make the speaker easy to identify.\n\n```js\nconst layout = { thread: 'continuous', inspector: 'optional' };\nconsole.log(layout);\n```", at: ago(280) },
        { id: "m-verify", request: "r1", role: "assistant", agent: "sonnet", text: "Keyboard review is complete. Next I will verify the narrow layout and permission flow.", at: ago(260) });
      window.fixture.session.title = "Chat UX review";
      window.fixture.blockers = window.fixture.blockers.slice(0, 1);
      approval.subject = "Allow preview build";
      approval.body = "Run the local preview build for the reviewed changes?";
      approval.payload = { action: "run_command", description: "Build the local preview", target: "preview/chat", action_hash: "sha256:" + "a".repeat(64), scope: "once", choices: ["approve", "deny"], arguments: { folders: ["/tmp/alfrd-preview"], bash_commands: ["npm run build"] } };
      window.activityCalls = []; window.mutations = []; window.tailVersion = 1; window.offline = false;
      Object.assign(server, { session: { mutations_enabled: true }, request: async (url) => {
        if(window.sessionOffline && /\/sessions\/s(?:\?|$)/.test(url)) throw new Error("snapshot unavailable");
        let data;
        if (url.includes("/activity?")) {
          window.activityCalls.push(url); if (window.offline) throw new Error("offline");
          const units = new URL(url, location.origin).searchParams.get("units")?.split(",");
          data = { items: tasks.flatMap((task) => task.logs.filter((l) => units ? units.includes(l.unit) : l.status === "running").map((l) => ({ ...l, row: task.row, request: task.request, owner: task.owner, kind: task.kind, title: task.task.title, seconds: 10,
            display_tail: Array.from({ length: 120 }, (_, i) => `Public output ${window.tailVersion} line ${i}`), tail: ['{"type":"technical"}'] }))) };
        } else if (url.includes("/mailbox/")) data = { messages: window.mail, events: [], next_cursor: "one", has_more: false };
        else if (url.endsWith("/sessions")) data = { sessions: [{ id: "s", title: "Chat UX review", pending_user: 1 }, { id: "other", title: "Repeated long session title for navigation review", pending_user: 0 }, { id: "pending", title: "Repeated long session title for navigation review", pending_user: 2, last_message: {text: "Need approval"} }] };
        else if (url.endsWith("/settings")) data = DEFAULTS;
        else data = window.fixture;
        return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
      }, mutate: async (url, data) => {
        window.mutations.push({ url, data });
        if (url.endsWith("/cancel")) {
          const row = decodeURIComponent(url.split("/tasks/")[1].split("/")[0]);
          if (window.failCancel && row === "r2.c") throw new Error("could not stop");
          tasks.find(t => t.row === row).status = "cancelled";
        }
        if (window.replyError) { const e = new Error(window.replyError); e.status = window.replyStatus; throw e; }
        if (url.includes("/reply")) {
          const reply = { id: "reply", seq: 4, kind: "reply", reply_to: approval.id, sender: { kind: "user" }, recipient: approval.sender, payload: data };
          approval.state = "resolved"; window.mail.push(reply);
          return { message: reply, resolution: { state: "resolved", version: 2 }, delivery: "recorded" };
        }
        return {};
      } });
      const host = document.getElementById(dock ? "dock" : "home"), mod = await import(dock ? "/js/components/chat_dock.js" : "/js/components/chat_view.js");
      const ctx = { state: { mode: "server", chatSession: "s", selectedProject: "p", selectedTarget: "", liveStatus: { state: "live" } }, activeProject: () => "p", toast(text) { window.lastToast = text; }, update() { mod.render(host, ctx); }, modal(html, setup) {
        const root = document.createElement('section'); root.id = 'graph-modal';
        root.style.cssText = 'position:fixed;inset:20px;z-index:100;display:flex;flex-direction:column;background:var(--surface-float)';
        root.innerHTML = html; document.body.append(root);
        root.querySelector('[data-close]').onclick = () => root.remove();
        setup(root, () => root.remove());
      } };
      window.refresh = () => ctx.update(); window.ctx = ctx; window.dockModule = mod;
      await mod.render(host, ctx, dock ? { width: 380, isOpen: () => true, onClose: () => mod.hide() } : undefined);
    }, dock);
    await page.locator('[data-key="run:r2"] .chat-run-row').first().waitFor();
    return page;
  };


  await t.test("search and Needs me leave route, conversation and draft selected", async () => {
    const page=await open("obsidian-orbit",1440); t.after(()=>page.close());
    await page.locator("#chat-input").fill("unsent draft");
    const route=await page.evaluate(()=>location.hash);
    await page.locator("#chat-session-search").fill("Repeated");
    assert.equal(await page.locator(".chat-session").count(),2);
    await page.locator("[data-chat=needs-me]").click();
    assert.equal(await page.locator(".chat-session").count(),1);
    assert.equal(await page.locator("#chat-input").inputValue(),"unsent draft");
    assert.equal(await page.evaluate(()=>location.hash),route);
    await page.locator("[data-chat=clear-search]").click();
    await page.locator(".chat-session-menu summary").click();
    await page.locator('[data-chat=rename][data-sid-act="s"]').click();
    await page.locator("#chat-rename-input").fill("Keep failed edit");
    await page.evaluate(()=>{window.replyError="offline";});
    await page.locator("#chat-rename-input").press("Enter");
    await page.locator("[data-rename-error]").waitFor();
    await page.evaluate(()=>window.refresh());
    assert.equal(await page.locator("#chat-rename-input").inputValue(),"Keep failed edit");
    await page.locator("#chat-rename-input").press("Escape");
    await page.evaluate(()=>{window.fixture.tasks[1].result={summary:"Ready",artifacts:["review.txt@abc123"]};window.dispatchEvent(new CustomEvent("alfrd-events",{detail:{project:"p",events:[{plan:"chat-s"}]}}));});
    await page.getByRole("button",{name:"Audit conversation layout",exact:true}).click();
    await page.locator('#chat-task [data-artifact="review.txt@abc123"]').waitFor();
  });
  await t.test("permission cards retain answer and focus across polls and send exact version/hash", async()=>{
    const page=await open("daylight-orbit",900);t.after(()=>page.close());
    const card=page.locator('[data-mail-id="permission"]');
    const notice=page.locator('.chat-mailbox > p[role="status"]').filter({hasText:"Permission requested by @codex"});
    await notice.waitFor();
    assert.equal(await notice.count(),1);
    assert.equal(await notice.textContent(),"Permission requested by @codex");
    await card.locator("textarea").fill("Reviewed exact scope");
    await notice.evaluate(el=>{window.noticeWrites=0;new MutationObserver(records=>{window.noticeWrites+=records.length;}).observe(el,{childList:true,characterData:true,subtree:true});});
    await page.evaluate(()=>{window.mail.push({...window.mail[0],id:"permission-two",seq:9,payload:{...window.mail[0].payload,action_hash:"different-hash"}});window.dispatchEvent(new Event("alfrd-mail-replied"));});
    await page.locator('[data-mail-id="permission-two"]').waitFor();
    assert.equal(await card.locator("textarea").inputValue(),"Reviewed exact scope");
    assert.equal(await card.locator("textarea").evaluate(e=>e===document.activeElement),true);
    assert.equal(await page.evaluate(()=>window.noticeWrites),0,"polls do not repeat the same permission announcement");
    await card.getByRole("button",{name:/Action details/}).click();
    assert.match(await card.locator("[data-action-scope]").textContent(),/npm run build/);
    await card.getByRole("button",{name:"Approve once"}).click();
    const payload=await page.evaluate(()=>window.mutations.at(-1).data);
    assert.equal(payload.expected_version,1);assert.equal(payload.action_hash,"sha256:"+"a".repeat(64));
    assert.equal(payload.answer,"Reviewed exact scope");
    await page.evaluate(()=>window.dispatchEvent(new Event("alfrd-mail-replied")));
    await page.waitForFunction(()=>document.querySelector('[data-mail-id="permission"] fieldset')?.disabled);
  });
  await t.test("inspector decision navigation releases the modal before focusing the inline card", async()=>{
    const page=await open("daylight-orbit",900);t.after(()=>page.close());
    await page.locator("[data-chat=inspect]").click();
    assert.equal(await page.locator(".chat-main").evaluate(e=>e.inert),true);
    await page.locator('#chat-decisions [data-mail-jump="permission"]').click();
    assert.equal(await page.locator(".chat-main").evaluate(e=>e.inert),false);
    assert.equal(await page.locator("#chat-inspector").getAttribute("aria-modal"),null);
    assert.equal(await page.locator('[data-mail-id="permission"]').evaluate(e=>e.contains(document.activeElement)),true);
    await page.locator('[data-mail-id="permission"] textarea').fill("Answer after navigation");
  });
  await t.test("page and dock Stop controls confirm only their request and keep draft", async()=>{
    for (const dock of [false,true]) {
      const page=await open("obsidian-orbit",1024,dock);t.after(()=>page.close());
      const prefix=dock?"cd":"chat";
      await page.locator(`#${prefix}-input`).fill("Keep this draft");
      await page.evaluate(()=>{window.failCancel=true;});
      const questions=[];page.on("dialog",async d=>{questions.push(d.message());await d.accept();});
      await page.locator(`#${prefix}-stop`).click();
      await page.waitForFunction(()=>window.lastToast?.includes("Stopped 1 of 2"));
      assert.match(questions[0],/request r2/);
      assert.equal(await page.locator(`#${prefix}-input`).inputValue(),"Keep this draft");
      assert.equal(await page.locator(`#${prefix}-stop`).textContent().then(s=>s.trim()),"Retry Stop");
      await page.evaluate(()=>{window.failCancel=false;});
      await page.locator(`#${prefix}-stop`).click();
      await page.waitForFunction(()=>window.lastToast?.includes("Stopped 1 of 1"));
      const calls=await page.evaluate(()=>window.mutations.filter(m=>m.url.endsWith("/cancel")));
      assert.deepEqual(calls.map(c=>c.url.split("/tasks/")[1]),["r2.b/cancel","r2.c/cancel","r2.c/cancel"]);
      assert.equal(calls[0].data.expected_status,"running");
      await page.close();
    }
  });
  await t.test("page and dock never cancel from stale state after refresh failure",async()=>{
    for(const dock of [false,true]) {
      const page=await open("obsidian-orbit",1024,dock);t.after(()=>page.close());
      await page.evaluate(()=>{window.sessionOffline=true;});
      page.on("dialog",d=>d.accept());
      await page.locator(dock?"#cd-stop":"#chat-stop").click();
      await page.waitForFunction(()=>window.lastToast?.includes("Stopped 0 of 2"));
      assert.equal(await page.evaluate(()=>window.mutations.length),0);
      await page.close();
    }
  });
  await t.test("permission conflict requires review; transport retry preserves exact key",async()=>{
    const page=await open("daylight-orbit",1024);t.after(()=>page.close());
    const card=page.locator('[data-mail-id="permission"]');
    await card.locator("textarea").fill("keep explanation");
    await page.evaluate(()=>{window.replyError="offline";});
    await card.getByRole("button",{name:"Approve once"}).click();
    await card.locator("[data-error]").filter({hasText:"offline"}).waitFor();
    const first=await page.evaluate(()=>window.mutations.at(-1).data);
    await card.getByRole("button",{name:"Approve once"}).click();
    assert.equal(await page.evaluate(()=>window.mutations.at(-1).data.idempotency_key),first.idempotency_key);
    await page.evaluate(()=>{window.replyStatus=409;window.replyError="version conflict";});
    await card.getByRole("button",{name:"Approve once"}).click();
    await page.waitForFunction(()=>document.querySelector('[data-mail-id="permission"] fieldset').disabled);
    await page.evaluate(()=>{window.replyError=null;window.mail[0]={...window.mail[0],version:2,payload:{...window.mail[0].payload,action_hash:"sha256:changed"}};window.dispatchEvent(new Event("alfrd-mail-replied"));});
    await page.waitForFunction(()=>!document.querySelector('[data-mail-id="permission"] fieldset').disabled);
    const before=await page.evaluate(()=>window.mutations.length);
    await card.getByRole("button",{name:"Approve once"}).click();
    assert.equal(await page.evaluate(()=>window.mutations.length),before,"changed scope needs renewed review");
    await card.getByRole("button",{name:/Action details/}).click();
    assert.match(await card.locator("[data-action-scope]").textContent(),/sha256:changed/);
    await card.getByRole("button",{name:"Deny",exact:true}).click();
    const last=await page.evaluate(()=>window.mutations.at(-1).data);
    assert.equal(last.action_hash,"sha256:changed");assert.equal(last.expected_version,2);
    assert.equal(await card.locator("textarea").inputValue(),"keep explanation");
  });
  await t.test("empty-session suggestions insert without dispatch",async()=>{
    const page=await open("obsidian-orbit",900);t.after(()=>page.close());
    await page.evaluate(()=>{window.fixture.messages=[];window.fixture.requests=[];window.fixture.tasks=[];window.dispatchEvent(new CustomEvent("alfrd-events",{detail:{project:"p",events:[{plan:"chat-s"}]}}));});
    const prompt=page.getByRole("button",{name:"Explain this project",exact:true});await prompt.waitFor();await prompt.click();
    assert.equal(await page.locator("#chat-input").inputValue(),"Explain this project");assert.equal(await page.evaluate(()=>window.mutations.length),0);
  });
  for (const theme of ["obsidian-orbit","daylight-orbit"]) for (const width of [360,640,900,1024,1440]) {
    await t.test(`${theme} ${width}: page and dock bounds, identities and sheet focus`, async()=>{
      for (const dock of [false,true]) {
        const page=await open(theme,width,dock);t.after(()=>page.close());
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        assert.equal(await page.locator('.chat-msg[data-agent="codex"] time').first().isVisible(),true);
        const stopButton=page.locator(dock?"#cd-stop":"#chat-stop");assert.equal(await stopButton.isVisible(),true);
        if(dock){
          assert.equal(await page.locator("#dock").getAttribute("role"),width<=900?"dialog":"complementary");
          if(width<=900){assert.equal(await page.locator("#home").evaluate(e=>e.inert),true);await page.locator("#cd-input").press("Escape");assert.equal(await page.locator("#home").evaluate(e=>e.inert),false);}
        }else if(width<=900){
          await page.locator("[data-chat=sessions]").click();
          const nav = page.locator(".chat-sessions");
          const last = nav.locator('a[href]:visible, button:visible:not([disabled]), input:visible, [tabindex="0"]:visible').last();
          await last.focus(); await page.keyboard.press("Tab");
          assert.equal(await nav.locator("[data-chat=new]").evaluate(e=>e===document.activeElement),true);
          await page.keyboard.press("Shift+Tab");assert.equal(await last.evaluate(e=>e===document.activeElement),true);
          assert.equal(await page.locator(".chat-main").evaluate(e=>e.inert),true);
          await page.locator("#chat-session-search").press("Escape");assert.equal(await page.locator(".chat-main").evaluate(e=>e.inert),false);
          assert.equal(await page.locator("[data-chat=sessions]").evaluate(e=>e===document.activeElement),true);
          await page.locator("[data-chat=inspect]").click();
          assert.equal(await page.locator("#chat-inspector").getAttribute("aria-modal"),"true");
          await page.locator("#chat-inspector button").first().press("Escape");
          assert.equal(await page.locator("[data-chat=inspect]").evaluate(e=>e===document.activeElement),true);
        }
        await page.close();
      }
    });
  }
});
