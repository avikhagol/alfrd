import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { autogrow, bindComposer, composerHtml } from "../../src/alfrd/web/js/components/chat_composer.js";
import { mountComposerDraft } from "../../src/alfrd/web/js/components/composer_draft.js";
import { sessionDraft } from "../../src/alfrd/web/js/data/chat_draft.js";
import { server } from "../../src/alfrd/web/js/data/server.js";
import { icon } from "../../src/alfrd/web/js/utils/dom.js";

const store = new Map();
globalThis.sessionStorage = { getItem: (k) => store.get(k), setItem: (k, v) => store.set(k, v) };

class El {
  constructor() { this.nodes = new Map(); this.listeners = {}; this.style = {}; this.value = ""; this.scrollHeight = 40; this.attrs = {}; this.hidden = false; this.disabled = false; this.textContent = ""; this.selectionStart = 0; this.selectionEnd = 0; }
  querySelector(s) { if (!this.nodes.has(s)) this.nodes.set(s, new El()); return this.nodes.get(s); }
  addEventListener(k, f) { (this.listeners[k] ||= []).push(f); }
  removeEventListener(k, f) { this.listeners[k] = (this.listeners[k] || []).filter((x) => x !== f); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  focus() {}
  fire(type, event = {}) { const e = { preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...event }; for (const f of this.listeners[type] || []) f(e); return e; }
}
const agents = () => ({ claude: { label: "Claude", roles: ["orchestration"] }, sonnet: { label: "Sonnet", roles: ["tests"] } });
function mount(extra = {}) {
  const root = new El(), calls = { send: 0, drafts: [], commands: [], errors: [], stop: 0 };
  const c = bindComposer(root, "chat", { agents, orchestrator: () => "claude", send: () => { calls.send++; }, onDraft: (t) => calls.drafts.push(t),
    onCommand: (cmd, args) => calls.commands.push([cmd, args]), onError: (m) => calls.errors.push(m), onStop: () => { calls.stop++; }, ...extra });
  const type = (text, caret = text.length) => { c.input.value = text; c.input.setSelectionRange(caret, caret); c.input.fire("input"); };
  const key = (k, extra = {}) => c.input.fire("keydown", { key: k, ...extra });
  return { root, c, calls, type, key, el: (s) => root.querySelector(s) };
}

test("Enter sends, Shift+Enter keeps the newline, modified Enter does nothing", () => {
  const m = mount(); m.type("hello");
  assert.equal(m.key("Enter").prevented, true); assert.equal(m.calls.send, 1);
  const nl = m.key("Enter", { shiftKey: true }); assert.equal(nl.prevented, undefined); assert.equal(m.calls.send, 1);
  m.key("Enter", { ctrlKey: true }); m.key("Enter", { metaKey: true }); assert.equal(m.calls.send, 1);
});

test("IME composition never sends, picks or dismisses", () => {
  const m = mount(); m.type("hello");
  m.key("Enter", { isComposing: true }); m.key("Enter", { keyCode: 229 }); assert.equal(m.calls.send, 0);
  m.type("@so"); m.key("Enter", { isComposing: true }); m.key("Tab", { isComposing: true }); m.key("Escape", { isComposing: true });
  assert.equal(m.c.input.value, "@so"); assert.equal(m.el("#chat-mentions").hidden, false);
});

test("Shift+Enter with hints visible stays a newline and does not insert", () => {
  const m = mount(); m.type("@so");
  const e = m.key("Enter", { shiftKey: true }); assert.equal(e.prevented, undefined); assert.equal(m.c.input.value, "@so");
});

test("Enter/Tab insert the active hint and keep the caret; Escape dismisses and stops propagation", () => {
  const m = mount(); m.type("@so");
  assert.equal(m.el("#chat-mentions").hidden, false);
  m.key("Enter"); assert.equal(m.c.input.value, "@sonnet "); assert.equal(m.c.input.selectionStart, 8); assert.equal(m.calls.send, 0); assert.equal(m.el("#chat-mentions").hidden, true);
  m.type("@"); m.key("ArrowDown"); m.key("Tab"); assert.match(m.c.input.value, /^@\w+ $/);
  m.type("@"); const esc = m.key("Escape"); assert.equal(esc.stopped, true); assert.equal(m.el("#chat-mentions").hidden, true); assert.equal(m.c.input.value, "@");
  const again = m.key("Escape"); assert.equal(again.stopped, undefined, "with hints closed Escape reaches the enclosing sheet");
});

test("pointer selection only inserts; a slash pick inserts, the next Enter executes", () => {
  const m = mount(); m.type("/he");
  m.key("Enter"); assert.equal(m.c.input.value, "/help "); assert.deepEqual(m.calls.commands, []); assert.equal(m.calls.send, 0);
  m.key("Enter"); assert.deepEqual(m.calls.commands, [["help", ""]]); assert.equal(m.c.input.value, ""); assert.equal(m.calls.send, 0);
  m.type("/he"); m.el("#chat-mentions").fire("click", { target: { closest: () => ({ dataset: { agent: "help" } }) } });
  assert.equal(m.c.input.value, "/help "); assert.equal(m.calls.commands.length, 1);
});

test("unknown commands and mentions cannot dispatch; no /benchmark or /inspect", () => {
  const m = mount();
  m.type("/benchmark"); m.key("Escape"); m.key("Enter"); assert.equal(m.calls.send, 0); assert.match(m.calls.errors.at(-1), /Unknown command \/benchmark/);
  m.type("/inspect"); m.key("Escape"); m.key("Enter"); assert.match(m.calls.errors.at(-1), /Unknown command \/inspect/);
  m.type("@nobody hi"); m.key("Enter"); assert.equal(m.calls.send, 0); assert.match(m.calls.errors.at(-1), /Unknown agent @nobody/);
  assert.match(m.c.input.placeholder, /Unknown agent/);
  m.type(" /literal"); m.key("Enter"); assert.equal(m.calls.send, 1, "leading space sends literal slash text");
});

test("target hint names the recipient", () => {
  const m = mount();
  assert.match(m.c.input.placeholder, /^To orchestrator @claude;/);
  m.type("hi"); assert.match(m.c.input.placeholder, /^To orchestrator @claude;/);
  m.type("@sonnet do it"); assert.match(m.c.input.placeholder, /^To @sonnet directly;/);
  m.type(""); assert.match(m.c.input.placeholder, /^To orchestrator @claude;/);
});

test("keyboard cannot bypass a disabled Send", () => {
  const m = mount(); m.el("#chat-send").disabled = true; m.type("hello"); m.key("Enter"); m.c.input.parentForm = null;
  assert.equal(m.calls.send, 0);
  m.el("#chat-form").fire("submit"); assert.equal(m.calls.send, 0);
  m.el("#chat-send").disabled = false; m.key("Enter"); assert.equal(m.calls.send, 1);
});

test("textarea auto-grows to min(240px, 40dvh) and scrolls beyond", () => {
  const t = new El(); t.value = "content"; globalThis.innerHeight = 900;
  t.scrollHeight = 100; autogrow(t); assert.equal(t.style.height, "100px"); assert.equal(t.style.overflowY, "hidden");
  t.scrollHeight = 600; autogrow(t); assert.equal(t.style.height, "240px"); assert.equal(t.style.overflowY, "auto");
  globalThis.innerHeight = 400; autogrow(t); assert.equal(t.style.height, "160px");
  globalThis.visualViewport = { height: 300 }; autogrow(t); assert.equal(t.style.height, "120px");
  t.value = ""; autogrow(t); assert.equal(t.style.height, ""); assert.equal(t.style.overflowY, "hidden");
  delete globalThis.visualViewport; delete globalThis.innerHeight;
  const m = mount(); m.c.input.scrollHeight = 77; m.type("a\nb\nc"); assert.equal(m.c.input.style.height, "77px");
  m.c.input.scrollHeight = 55; m.c.refresh(); assert.equal(m.c.input.style.height, "55px");
});

test("Stop is separate, optional, and leaves the draft alone", () => {
  const plain = mount(); assert.equal(plain.el("#chat-stop").hidden, true);
  let state = { visible: true, disabled: false, label: "Stop request", reason: "" };
  const m = mount({ stopState: () => state }); m.type("keep this");
  assert.equal(m.el("#chat-stop").hidden, false);
  m.el("#chat-stop").fire("click"); assert.equal(m.calls.stop, 1);
  assert.equal(m.c.input.value, "keep this"); assert.deepEqual(m.calls.drafts, ["keep this"]); assert.equal(m.calls.send, 0);
  state = { visible: true, disabled: true, label: "Stopping…", reason: "Stopping" }; m.c.refresh();
  assert.equal(m.el("#chat-stop").disabled, true); assert.equal(m.el("#chat-stop").title, "Stopping");
  assert.equal(m.el("#chat-stop").querySelector("[data-stop-label]").textContent, "Stopping…");
  m.el("#chat-stop").fire("click"); assert.equal(m.calls.stop, 1);
  state = { visible: false }; m.c.refresh(); assert.equal(m.el("#chat-stop").hidden, true);
});

test("Retry appears for failed sends and reuses the submit path", () => {
  const m = mount(); m.type("hello");
  m.el("#chat-send-error").textContent = "Couldn't send. offline"; m.c.refresh(); assert.equal(m.el("#chat-retry").hidden, false);
  m.el("#chat-retry").fire("click"); assert.equal(m.calls.send, 1);
  m.el("#chat-send-error").textContent = ""; m.c.refresh(); assert.equal(m.el("#chat-retry").hidden, true);
});

test("markup: single line, placeholder helper and icon-only schedule/attach in row order", () => {
  for (const id of ["chat", "cd"]) {
    const html = composerHtml(id, { attachments: true });
    for (const part of [`id="${id}-input"`, `id="${id}-send"`, `id="${id}-stop"`, `id="${id}-mentions"`, `id="${id}-send-error"`, `id="${id}-send-reason"`]) assert.ok(html.includes(part), part);
    assert.match(html, /rows="1" placeholder="To orchestrate/);
    assert.doesNotMatch(html, /chat-composer-info|chat-target|chat-hint|Images are resized/);
    for (const [suffix, label] of [["schedule-toggle", "Schedule messages"], ["attach", "Attach images"]]) {
      const button = html.match(new RegExp(`<button[^>]*id="${id}-${suffix}"[^>]*>([\\s\\S]*?)</button>`));
      assert.ok(button); assert.ok(button[0].includes(`title="${label}"`)); assert.ok(button[0].includes(`aria-label="${label}"`));
      assert.doesNotMatch(button[1], /Schedule messages|Attach images/);
    }
    assert.ok(html.indexOf(`${id}-input`) < html.indexOf(`${id}-schedule-toggle`));
    assert.ok(html.indexOf(`${id}-schedule-toggle`) < html.indexOf(`${id}-attach"`));
    assert.ok(html.indexOf(`${id}-attach"`) < html.indexOf(`${id}-send"`));
    assert.match(html, /class="chat-schedule"[^>]*hidden/);
    assert.ok(html.includes(icon("image")));
  }
  assert.doesNotMatch(composerHtml("chat"), /attach-tray/);
});

// --- send states through the shared draft binding ---------------------------------------------------------
server.session = { mutations_enabled: true, csrf_token: "csrf" };
server.request = async () => ({ ok: true, json: async () => ({}) });
const draftMount = (id = "chat") => { const root = new El(); return { root, d: mountComposerDraft(root, id, { settings: () => ({ orchestrator: "claude" }) }), send: root.querySelector(`#${id}-send`), note: root.querySelector(`#${id}-send-reason`) }; };

test("send states: empty, ready, uploading, failed, sending, no session", async () => {
  const a = draftMount(); a.d.select("cx", ""); assert.equal(a.send.disabled, true); assert.match(a.note.textContent, /Choose a session/);
  a.d.select("cx", "s1"); assert.equal(a.send.disabled, true, "empty"); assert.equal(a.note.textContent, "");
  a.d.edit("   "); assert.equal(a.send.disabled, true, "whitespace");
  a.d.edit("hello"); assert.equal(a.send.disabled, false, "ready");
  const model = sessionDraft("cx", "s1");
  model.chips.set("u", { id: "u", status: "uploading", name: "a.png" }); model.sync();
  assert.equal(a.send.disabled, true); assert.match(a.note.textContent, /Wait for uploads to finish/); assert.equal(await a.d.send(), false);
  model.chips.set("u", { id: "u", status: "error", error: "boom", name: "a.png" }); model.sync();
  assert.equal(a.send.disabled, true); assert.match(a.note.textContent, /Retry or remove/); assert.equal(await a.d.send(), false);
  model.chips.delete("u"); model.sync(); assert.equal(a.send.disabled, false);
  let release; server.request = () => new Promise((r) => { release = r; });
  const pending = a.d.send();
  assert.equal(a.send.disabled, true); assert.equal(a.send.querySelector("[data-send-label]").textContent, "Sending…");
  assert.equal(a.root.querySelector("#chat-input").value, "hello", "no optimistic clearing");
  release({ ok: true, status: 201, json: async () => ({}) }); await pending;
  assert.equal(a.root.querySelector("#chat-input").value, ""); assert.equal(a.send.querySelector("[data-send-label]").textContent, "Send");
  assert.equal(a.send.disabled, true, "empty again after accepted send");
  a.d.destroy();
});

test("accepted image-only draft is ready; read-only explains why", () => {
  const a = draftMount(); a.d.select("cy", "s");
  const model = sessionDraft("cy", "s"); model.chips.set("att_0123456789abcdef", { id: "x", ref: "att_0123456789abcdef", status: "done", name: "i" }); model.sync();
  assert.equal(a.send.disabled, false);
  server.session.mutations_enabled = false; a.d.select("cy", "s");
  assert.equal(a.send.disabled, true); assert.match(a.note.textContent, /read-only/); server.session.mutations_enabled = true;
  a.d.destroy();
});

test("edits made while a send is in flight survive the 201", async () => {
  server.request = async () => ({ ok: true, json: async () => ({}) });
  const a = draftMount(); a.d.select("cz", "s"); a.d.edit("first");
  let release; server.request = () => new Promise((r) => { release = r; });
  const pending = a.d.send(); a.d.edit("first plus more");
  release({ ok: true, status: 201, json: async () => ({}) }); await pending;
  assert.equal(a.root.querySelector("#chat-input").value, "first plus more"); a.d.destroy();
});

test("only the composer files and its stylesheet carry composer rules; Stop never touches the draft API", () => {
  const css = readFileSync(new URL("../../src/alfrd/web/css/chat-composer.css", import.meta.url), "utf8");
  assert.match(css, /min\(240px, 40dvh\)/); assert.match(css, /safe-area-inset-bottom/); assert.doesNotMatch(css, /@import/);
  const source = readFileSync(new URL("../../src/alfrd/web/js/components/chat_composer.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /onDraft\(""\)[^;]*onStop/);
});
