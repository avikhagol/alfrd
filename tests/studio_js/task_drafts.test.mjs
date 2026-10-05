// task.md drafts (design T4.1): openTask() driven through a minimal fake modal DOM.
import test from "node:test";
import assert from "node:assert/strict";

// ---- Browser globals: localStorage that enumerates its keys, a window with pagehide.
let storageFails = false;
class FakeStorage {
  getItem(k) { return Object.hasOwn(this, k) ? this[k] : null; }
  setItem(k, v) { if (storageFails) throw new Error("QuotaExceededError"); this[k] = String(v); }
  removeItem(k) { delete this[k]; }
}
globalThis.localStorage = new FakeStorage();
globalThis.window ||= globalThis;
const winListeners = {};
globalThis.addEventListener = (t, f) => { (winListeners[t] ||= new Set()).add(f); };
globalThis.removeEventListener = (t, f) => { winListeners[t]?.delete(f); };
globalThis.document ||= {};
Object.assign(globalThis.document, { addEventListener() {}, querySelector: () => ({}), createElement: () => ({ dataset: {} }), head: { appendChild() {} }, hidden: false, activeElement: null });
globalThis.location ||= { origin: "http://localhost", protocol: "http:" };
Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async () => {} } }, configurable: true });

const { server } = await import("../../src/alfrd/web/js/data/server.js");
const dialog = await import("../../src/alfrd/web/js/components/agent_dialog.js");

// ---- Fake modal: elements by id from the dialog HTML, with listeners and form state.
const unescape = (s) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
class El {
  constructor(id, attrs = "", value = "") {
    Object.assign(this, { id, hidden: /\shidden(?=[\s>]|$)/.test(attrs), checked: /\schecked(?=[\s>]|$)/.test(attrs), disabled: /\sdisabled(?=[\s>]|$)/.test(attrs),
      readOnly: false, value, textContent: "", dataset: {}, listeners: {} });
  }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  async fire(t) { for (const f of this.listeners[t] || []) await f({ target: this, preventDefault() {} }); }
  focus() { globalThis.document.activeElement = this; }
}
function openModal(html) {
  const els = new Map();
  for (const m of html.matchAll(/<(\w+)([^>]*\sid="([^"]+)"[^>]*)>/g)) {
    const body = m[1] === "textarea" ? unescape(html.slice(m.index + m[0].length, html.indexOf("</textarea>", m.index))) : "";
    els.set(`#${m[3]}`, new El(m[3], m[2], body));
  }
  els.set("#task-current pre", new El("pre"));
  const runs = [...html.matchAll(/data-task-run="([^"]+)"/g)].map((m) => Object.assign(new El("run"), { dataset: { taskRun: m[1] } }));
  const root = new El("root");
  root.querySelector = (s) => els.get(s) || null;
  root.querySelectorAll = (s) => (s === "[data-task-run]" ? runs : []);
  root.html = html;
  return root;
}

let calls;
let onDisk;
function fakeServer({ save } = {}) {
  server.tasks = async () => ({ tasks: [] });
  calls = { save: [], task: 0 };
  onDisk = { text: "# Goal\nShip it.\n", hash: "h1" };
  server.session = { mutations_enabled: true };
  server.task = async () => { calls.task += 1; return { ...onDisk }; };
  server.taskSave = save || (async (p, payload) => {
    calls.save.push(payload);
    if (payload.base_hash !== onDisk.hash) throw Object.assign(new Error("conflict"), { status: 409, body: { current: onDisk.text } });
    onDisk = { text: payload.text, hash: `h${calls.save.length + 1}` };
    return { ...onDisk };
  });
}

/** Open the Task editor; resolves to a handle over the fake dialog. */
async function open(project = "p1", run = "next", mod = dialog, target = null) {
  let root, close;
  const ctx = {
    toast() {},
    modal(html, setup) {
      root = openModal(html);
      close = async () => { await root.fire("beforeclose"); };
      setup(root, close);
    },
  };
  await mod.openTask(ctx, project, { run, target });
  await new Promise((r) => queueMicrotask(r));
  const $ = (s) => root.querySelector(s);
  return {
    root, $, close,
    type: async (text) => { $("#task-text").value = text; await $("#task-text").fire("input"); },
    text: () => $("#task-text").value,
    banner: () => !$("#task-draft").hidden,
  };
}
const stored = () => Object.keys(localStorage).filter((k) => k.includes("draft:task:"));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test.beforeEach(() => {
  Object.keys(localStorage).forEach((k) => localStorage.removeItem(k));
  dialog.forgetTaskDrafts();
  storageFails = false;
  fakeServer();
});

// app.js modal(): the backdrop, Esc and Close button each call close(), which fires
// `beforeclose`; the three paths are checked separately in the browser.
{
  test("draft survives closing the dialog, and nothing is written before Save", async () => {
    const d = await open();
    assert.equal(d.banner(), false);
    await d.type("# Goal\nShip it carefully.\n");
    assert.equal(d.banner(), true, "banner shows while editing");
    await d.close(); // every closure path calls the modal's close(): beforeclose flushes storage
    assert.equal(stored().length, 1, "flushed synchronously on close, before the 250 ms debounce");
    assert.deepEqual(calls.save, [], "task.md and the handoff are untouched until Save");
    assert.equal(onDisk.text, "# Goal\nShip it.\n");
    const again = await open();
    assert.equal(again.text(), "# Goal\nShip it carefully.\n");
    assert.equal(again.banner(), true);
    assert.match(again.$("#task-draft-status").textContent, /restored for the next run/);
  });
}

test("draft survives a page reload (fresh module, storage only)", async () => {
  const d = await open();
  await d.type("edited before reload");
  await wait(300); // debounced write
  assert.equal(stored().length, 1);
  const reloaded = await import(`../../src/alfrd/web/js/components/agent_dialog.js?reload=${Date.now()}`);
  const again = await open("p1", "next", reloaded);
  assert.equal(again.text(), "edited before reload");
  assert.equal(again.banner(), true);
});

test("pagehide flushes the pending write", async () => {
  const d = await open();
  await d.type("typed just before the tab closed");
  assert.equal(stored().length, 0, "still debounced");
  [...(winListeners.pagehide || [])].forEach((f) => f());
  assert.equal(stored().length, 1);
  await d.close();
});

test("drafts are isolated by project and run", async () => {
  const a = await open("p1", "r1");
  await a.type("p1 r1 draft");
  await a.close();
  const otherRun = await open("p1", "r2");
  assert.equal(otherRun.text(), "# Goal\nShip it.\n", "another run's draft never replaces this one");
  assert.equal(otherRun.banner(), false);
  assert.match(otherRun.root.html, /data-task-run="r1"/, "but it is offered");
  await otherRun.close();
  const otherProject = await open("p2", "r1");
  assert.equal(otherProject.text(), "# Goal\nShip it.\n");
  assert.doesNotMatch(otherProject.root.html, /data-task-run/);
  await otherProject.close();
  assert.equal((await open("p1", "r1")).text(), "p1 r1 draft");
});

test("an intentionally blank task is a draft; reverting to the baseline clears it", async () => {
  const d = await open();
  await d.type("");
  assert.equal(d.banner(), true, "empty text differs from task.md");
  await d.close();
  const blank = await open();
  assert.equal(blank.text(), "");
  assert.equal(blank.banner(), true);
  await blank.type("# Goal\nShip it.\n");
  assert.equal(blank.banner(), false, "exactly the saved text is no draft");
  await blank.close();
  assert.deepEqual(stored(), []);
  assert.equal((await open()).banner(), false);
});

test("successful Save clears the draft from memory and storage", async () => {
  const d = await open();
  await d.type("new goal");
  await d.$("#task-save").fire("click");
  assert.deepEqual(calls.save, [{ text: "new goal", base_hash: "h1", use_for_next_run: true }]);
  assert.equal(d.banner(), false);
  assert.equal(d.$("#task-message").textContent, "Saved. Start a new run for this task.");
  await d.close();
  assert.deepEqual(stored(), []);
  const again = await open();
  assert.equal(again.text(), "new goal", "from the server, not a draft");
  assert.equal(again.banner(), false);
});

test("failed Save keeps the draft and the text", async () => {
  fakeServer({ save: async () => { throw new Error("disk full"); } });
  const d = await open();
  await d.type("keep me");
  await d.$("#task-save").fire("click");
  assert.equal(d.$("#task-error").textContent, "disk full");
  assert.equal(d.text(), "keep me");
  assert.equal(d.banner(), true);
  await d.close();
  assert.equal((await open()).text(), "keep me");
});

test("409: draft kept, current file shown, and the stale baseline is never blessed", async () => {
  const d = await open();
  await d.type("my edit");
  onDisk = { text: "changed elsewhere", hash: "h9" };
  await d.$("#task-save").fire("click");
  assert.match(d.$("#task-error").textContent, /task\.md changed since this draft started\. Your draft is kept/);
  assert.equal(d.$("#task-current").hidden, false);
  assert.equal(d.$("#task-current pre").textContent, "changed elsewhere");
  assert.equal(d.text(), "my edit");
  await d.close();
  const again = await open(); // the server now reports h9; the draft keeps h1
  assert.equal(again.text(), "my edit");
  await again.$("#task-save").fire("click");
  assert.deepEqual(calls.save.map((s) => s.base_hash), ["h1", "h1"]);
  assert.equal(onDisk.text, "changed elsewhere");
});

test("storage failure: tab-only warning and Copy, memory still recovers after closing", async () => {
  storageFails = true;
  const d = await open();
  await d.type("only in memory");
  await d.close();
  assert.match(d.$("#task-draft-note").textContent, /Draft kept for this tab only\. Browser storage is unavailable/);
  assert.equal(d.$("#task-copy").hidden, false);
  assert.deepEqual(stored(), []);
  const again = await open();
  assert.equal(again.text(), "only in memory");
});

test("typing is blocked while Save is in flight; the submitted revision is what is saved", async () => {
  let release;
  fakeServer({ save: (p, payload) => new Promise((r) => { calls.save.push(payload); release = () => r({ text: payload.text, hash: "h2" }); }) });
  const d = await open();
  await d.type("first");
  const pending = d.$("#task-save").fire("click");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(d.$("#task-text").readOnly, true);
  assert.equal(d.$("#task-save").disabled, true);
  assert.equal(d.$("#task-save").textContent, "Saving…");
  release();
  await pending;
  assert.equal(d.$("#task-text").readOnly, false);
  assert.deepEqual(calls.save.map((s) => s.text), ["first"]);
  assert.equal(d.banner(), false);
});

test("Discard reloads the server text first, and keeps the draft if that fails", async () => {
  const d = await open();
  await d.type("draft text");
  const task = server.task;
  server.task = async () => { throw new Error("offline"); };
  await d.$("#task-discard").fire("click");
  await d.$("#task-discard-yes").fire("click");
  assert.equal(d.$("#task-error").textContent, "offline");
  assert.equal(d.text(), "draft text");
  assert.equal(d.banner(), true);
  server.task = task;
  await d.$("#task-discard").fire("click");
  await d.$("#task-discard-yes").fire("click");
  assert.equal(d.text(), "# Goal\nShip it.\n");
  assert.equal(d.banner(), false);
  await d.close();
  assert.deepEqual(stored(), []);
});

test("Clear all saved data (forgetTaskDrafts + storage.clear) leaves no draft in this tab", async () => {
  storageFails = true; // the memory copy is the only one: it must go too
  const d = await open();
  await d.type("to be cleared");
  await d.close();
  dialog.forgetTaskDrafts();
  const again = await open();
  assert.equal(again.text(), "# Goal\nShip it.\n");
  assert.equal(again.banner(), false);
});


test("task picker isolates drafts and sends the selected target when saving", async () => {
  server.tasks = async () => ({ tasks: [{ name: "task" }, { name: "fix-login" }] });
  const saved = [];
  server.taskSave = async (project, payload, target) => { saved.push(target); return { text: payload.text, hash: "h2" }; };
  const a = await open("p1", "next", dialog, "task");
  assert.match(a.root.html, /id="task-picker"/);
  await a.type("first task draft"); await a.close();
  const b = await open("p1", "next", dialog, "fix-login");
  assert.equal(b.text(), "# Goal\nShip it.\n");
  await b.type("fix login"); await b.$("#task-save").fire("click");
  assert.deepEqual(saved, ["fix-login"]);
  await b.close();
  assert.equal((await open("p1", "next", dialog, "task")).text(), "first task draft");
});

test("New task and Rename submit named task API requests", async () => {
  let tasks = [{ name: "task" }], created, renamed;
  server.tasks = async () => ({ tasks });
  server.taskCreate = async (project, body) => { created = body; tasks = [...tasks, { name: body.name }]; };
  server.taskRename = async (project, old, name) => { renamed = { old, name }; tasks = tasks.map((t) => t.name === old ? { name } : t); };
  const d = await open("p1", "next", dialog, "task");
  await d.$("#task-new").fire("click");
  assert.equal(d.$("#task-name").value, "task-2");
  d.$("#task-new-goal").value = "New independent goal";
  await d.$("#task-name-save").fire("click");
  assert.deepEqual(created, { name: "task-2", task: "New independent goal" });
  const e = await open("p1", "next", dialog, "task-2");
  await e.$("#task-rename").fire("click");
  e.$("#task-name").value = "fix-login";
  await e.$("#task-name-save").fire("click");
  assert.deepEqual(renamed, { old: "task-2", name: "fix-login" });
});


test("legacy root projects keep their file path and hide folder actions", async () => {
  server.tasks = async () => ({ tasks: [{ name: "task" }], folder_layout: false });
  server.task = async () => ({ text: "Legacy goal", hash: "h1", file: "task.md" });
  const d = await open("legacy");
  assert.equal(d.$("#task-new").hidden, true);
  assert.equal(d.$("#task-rename").hidden, true);
  assert.match(d.root.html, /Edit task\.md\./);
  assert.doesNotMatch(d.root.html, /Edit task\/task\.md/);
});
