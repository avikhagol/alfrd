import test from "node:test";
import assert from "node:assert/strict";
import { createTaskMenu } from "../../src/alfrd/web/js/components/task_menu.js";
import { taskMenuApi } from "../../src/alfrd/web/js/components/task_menu_api.js";
import { server } from "../../src/alfrd/web/js/data/server.js";

globalThis.document = { activeElement: null };
function fixture(overrides = {}) {
  const handlers = {}, focused = [], writes = [];
  let target = { row: "a", status: "failed", units: ["u1"] }, rev = 3, refreshes = 0;
  const buttons = [0, 1, 2].map((id) => ({ focus() { document.activeElement = this; } }));
  const el = { hidden: true, innerHTML: "", addEventListener(k, fn) { handlers[k] = fn; },
    querySelector: () => buttons[0], querySelectorAll: () => buttons, contains: (e) => buttons.includes(e) };
  const opts = { target: () => target, revision: () => rev, preview: async () => ({ eligible: true, affected_rows: ["a", "b"], current_status: target.status, current_unit: target.units.at(-1) }),
    refresh: async () => { refreshes++; }, run: async (id, t) => writes.push([id, t]), focus: (row) => focused.push(row), error: () => {}, ...overrides };
  const menu = createTaskMenu(el, opts);
  return { menu, el, opts, focused, writes, buttons, set: (t) => { target = t; }, rev: (n) => { rev = n; }, refreshes: () => refreshes,
    key: (key) => handlers.keydown({ key, stopPropagation() {}, preventDefault() {} }) };
}

test("menu hides unavailable actions, arrow/Home/End navigation and Escape/Tab return focus", async () => {
  const r = fixture(); await r.menu.open("a");
  assert.doesNotMatch(r.el.innerHTML, /data-action="(?:log|prompt|reply|files|cancel)"/);
  r.key("ArrowDown"); assert.equal(document.activeElement, r.buttons[1]);
  r.key("End"); assert.equal(document.activeElement, r.buttons[2]);
  r.key("ArrowDown"); assert.equal(document.activeElement, r.buttons[0]);
  r.key("ArrowUp"); assert.equal(document.activeElement, r.buttons[2]);
  r.key("Home"); assert.equal(document.activeElement, r.buttons[0]);
  r.key("Escape"); assert.equal(r.el.hidden, true); assert.deepEqual(r.focused, ["a"]);
  await r.menu.open("a"); r.key("Tab"); assert.equal(r.el.hidden, true);
});

test("retry confirms every dependent row and captures row, revision, status and unit", async () => {
  const r = fixture(); let confirmation;
  globalThis.confirm = (s) => { confirmation = s; return false; };
  await r.menu.open("a"); await r.menu.activate("retry"); assert.equal(r.writes.length, 0);
  globalThis.confirm = (s) => { confirmation = s; return true; };
  await r.menu.activate("retry");
  assert.match(confirmation, /a, b/);
  const [id, target] = r.writes[0]; assert.equal(id, "retry");
  assert.deepEqual([target.row, target.rev, target.status, target.current_unit], ["a", 3, "failed", "u1"]);
  assert.equal(r.el.hidden, true);
});

test("changed revision, status or unit refreshes without writing", async () => {
  for (const change of [(r) => r.rev(4), (r) => r.set({ row: "a", status: "running", units: ["u1"] }), (r) => r.set({ row: "a", status: "failed", units: ["u2"] })]) {
    const r = fixture(); await r.menu.open("a"); change(r); await r.menu.activate("retry");
    assert.equal(r.writes.length, 0); assert.equal(r.refreshes(), 1);
    assert.match(r.el.innerHTML, /Task changed/); assert.equal(r.el.hidden, false);
  }
});

test("409 reloads the open menu and preview without repeating the mutation", async () => {
  let writes = 0, previews = 0;
  const r = fixture({ run: async () => { writes++; throw Object.assign(new Error("changed"), { status: 409 }); },
    preview: async () => { previews++; return { eligible: true, current_status: "failed", current_unit: "u1" }; } });
  await r.menu.open("a"); await r.menu.activate("retry");
  assert.equal(writes, 1); assert.equal(previews, 2); assert.equal(r.refreshes(), 1);
  assert.equal(r.el.hidden, false); assert.match(r.el.innerHTML, /Task changed/);
});

test("removed targets or session changes close without writing", async () => {
  for (const next of [null, { row: "a", status: "failed", context: "another-session", units: ["u1"] }]) {
    const r = fixture(); await r.menu.open("a"); r.set(next); await r.menu.activate("retry");
    assert.equal(r.el.hidden, true); assert.equal(r.writes.length, 0);
  }
});

test("late preview cannot reopen a dismissed menu and preview errors disable mutations", async () => {
  let resolve;
  const r = fixture({ preview: () => new Promise((fn) => { resolve = fn; }) });
  const opening = r.menu.open("a"); r.menu.close(); resolve({ eligible: true }); await opening;
  assert.equal(r.el.hidden, true);
  const bad = fixture({ preview: async () => { throw new Error("offline"); } });
  await bad.menu.open("a"); await bad.menu.activate("retry");
  assert.match(bad.el.innerHTML, /offline/); assert.equal(bad.writes.length, 0);
});

test("orchestrator menu only exposes supported read actions and copy", async () => {
  const r = fixture(); r.set({ row: "a", kind: "plan", status: "failed", units: ["u1"] });
  await r.menu.open("a");
  assert.doesNotMatch(r.el.innerHTML, /data-action="(?:retry|cancel)"/);
  assert.match(r.el.innerHTML, /data-action="copy_id"/);
  await r.menu.activate("retry"); assert.equal(r.writes.length, 0);
});

test("API posts captured preconditions and escapes row/session/project path segments", async () => {
  const original = server.mutate; const calls = [];
  server.mutate = async (...args) => { calls.push(args); };
  try {
    const api = taskMenuApi({ project: () => "p/x", sid: () => "s/x", state: () => ({ tasks: [{ row: "a/x", status: "running" }] }), refresh: async () => {} });
    const t = { ...api.target("a/x"), current_unit: "u3" };
    await api.run("cancel", t);
    assert.deepEqual(calls, [["/studio/projects/p%2Fx/chat/sessions/s%2Fx/tasks/a%2Fx/cancel", { expected_status: "running", expected_unit: "u3" }]]);
  } finally { server.mutate = original; }
});


test("all seven menu actions dispatch and missing read content is hidden", async () => {
  for (const id of ["log", "prompt", "reply", "files", "retry", "cancel", "copy_id"]) {
    const status = id === "cancel" ? "running" : "failed";
    const r = fixture({ preview: async () => ({ eligible: status === "failed", current_status: status, current_unit: "u1" }) });
    r.set({ row: "a", status, units: ["u1"], log: "x.log", prompt: true, reply: true, files: ["x"] });
    globalThis.confirm = () => true;
    await r.menu.open("a");
    await r.menu.activate(id);
    assert.equal(r.writes[0]?.[0], id);
  }
  const r = fixture({ preview: async () => { throw new Error("offline"); } });
  await r.menu.open("a");
  assert.doesNotMatch(r.el.innerHTML, /data-action="(?:retry|cancel)"/);
  await r.menu.activate("copy_id");
  assert.equal(r.writes[0][0], "copy_id");
});

test("prompt and reply use paged session transcript reads and Markdown, never the log endpoint", async () => {
  const original = server.request, calls = [], opened = [];
  server.request = async (url) => {
    calls.push(url);
    if (url.endsWith("retry-preview")) return { ok: true, json: async () => ({ eligible: false, current_status: "done", current_unit: "u1" }) };
    if (url.endsWith("/transcript")) return { ok: true, json: async () => ({ attempts: [{ unit: "u1", prompt_bytes: 9, response_bytes: 8 }] }) };
    return { ok: true, json: async () => url.endsWith("offset=0") ? { content: "# Hi", offset: 0, length: 4, eof: false } : { content: " there", offset: 4, length: 6, eof: true } };
  };
  try {
    const api = taskMenuApi({ project: () => "p/x", sid: () => "s/x", state: () => ({ tasks: [{ row: "a/x", status: "done", result: {} }] }),
      openLog: () => assert.fail("Prompt/reply must not use logs"), openMarkdown: async (...args) => opened.push(args) });
    assert.equal(api.target("a/x").reply, false, "a result does not guarantee an archived response");
    const preview = await api.preview("a/x"), target = { ...api.target("a/x"), ...preview };
    assert.equal(target.reply, true); assert.equal(target.prompt, true);
    await api.run("prompt", target); await api.run("reply", target);
    assert.deepEqual(opened, [["prompt.md", "# Hi there"], ["response.md", "# Hi there"]]);
    assert.ok(calls.includes("/api/studio/projects/p%2Fx/chat/sessions/s%2Fx/tasks/a%2Fx/transcript/u1/prompt?offset=4"));
    assert.ok(calls.some((url) => url.includes("/u1/response?")));
  } finally { server.request = original; }
});

test("missing archives and unavailable retry preview leave Copy ID available", async () => {
  const original = server.request;
  server.request = async (url) => ({ ok: url.endsWith("/transcript"), status: 503,
    json: async () => url.endsWith("/transcript") ? { attempts: [] } : { error: { message: "offline" } } });
  try {
    const api = taskMenuApi({ project: () => "p", sid: () => "s", state: () => ({ tasks: [{ row: "a", status: "running" }] }) });
    const p = await api.preview("a");
    assert.equal(p.mutation_ready, false); assert.equal(p.prompt, false); assert.equal(p.reply, false);
    assert.match(p.preview_error, /offline/);
  } finally { server.request = original; }
});
