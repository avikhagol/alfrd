// Continuous transcript (components/transcript.js): flat keyed order, scroll anchoring,
// the New activity control and the markdown cache, on a small fake DOM (no browser).
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const ROW = 100, PAD = 40;
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.className = ""; this.dataset = {}; this.attrs = {}; this.hidden = false; this.textContent = ""; this._html = ""; this.listeners = {}; }
  get classList() {
    const list = () => this.className.split(/\s+/).filter(Boolean), set = (v) => { this.className = v.join(" "); };
    return { add: (c) => { if (!list().includes(c)) set([...list(), c]); }, remove: (c) => set(list().filter((x) => x !== c)), contains: (c) => list().includes(c),
      toggle: (c, on = !list().includes(c)) => { if (on) this.classList.add(c); else this.classList.remove(c); return on; } };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, f) { (this.listeners[type] ||= []).push(f); }
  append(...nodes) { for (const n of nodes) this.insertBefore(n, null); }
  insertBefore(n, ref) { n.remove(); const i = ref ? this.children.indexOf(ref) : -1; this.children.splice(i < 0 ? this.children.length : i, 0, n); n.parentNode = this; return n; }
  remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; }
  replaceChildren(...nodes) { for (const c of [...this.children]) c.remove(); this.append(...nodes); }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  get isConnected() { let x = this; while (x.parentNode) x = x.parentNode; return Boolean(x.root); }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html; this.children = [];
    const text = /<div class="chat-text">([\s\S]*?)<\/div>/.exec(html);
    if (text) { const p = new El("div"); p.className = "chat-text"; p.textContent = text[1]; this.append(p); }
  }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  querySelectorAll(sel) {
    const one = (s) => s.startsWith(".") ? (e) => e.classList.contains(s.slice(1)) : s.includes(":") ? () => false : (e) => e.tagName === s.toUpperCase();
    const tests = sel.split(",").map((s) => one(s.trim()));
    return this.all().filter((e) => tests.some((t) => t(e)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getBoundingClientRect() {
    const box = scroller, flow = this.parentNode, i = flow ? flow.children.indexOf(this) : 0;
    if (this === box) return { top: 0, bottom: box.clientHeight };
    const top = PAD + i * ROW - box.scrollTop; return { top, bottom: top + ROW };
  }
}
let scroller;
class Box extends El {
  constructor() { super("div"); this.root = true; this.clientHeight = 300; this._top = 0; }
  get flow() { return this.children.find((c) => c.className === "chat-flow"); }
  get scrollHeight() { return PAD * 2 + (this.flow?.children.length || 0) * ROW; }
  get scrollTop() { return this._top; }
  set scrollTop(v) { this._top = Math.max(0, Math.min(v, this.scrollHeight - this.clientHeight)); }
  scroll(v) { this.scrollTop = v; for (const f of this.listeners.scroll || []) f(); }
}
globalThis.document = { hidden: false, activeElement: null, addEventListener() {}, removeEventListener() {}, querySelector: () => null, head: { append() {} }, createElement: (t) => new El(t) };
globalThis.window = { getSelection: () => "", addEventListener() {} };

const T = await import("../../src/alfrd/web/js/components/transcript.js");
const { threadGroups } = await import("../../src/alfrd/web/js/data/chat.js");

const state = () => ({
  requests: [{ id: "r1", text: "First", status: "done" }, { id: "r2", text: "Second", status: "running" }],
  messages: [{ id: "a", request: "r1", role: "user", text: "first", at: "1" }, { id: "b", request: "r2", role: "user", text: "second", at: "2" },
    { id: "c", request: "r1", role: "assistant", text: "**answer**", at: "3" }, { id: "n", role: "system", text: "legacy note", at: "0" }],
  tasks: [], blockers: [],
});
function mount(extra = {}) {
  scroller = new Box();
  const lines = [], renders = [];
  const o = { project: () => "p", sid: () => "s", error: () => "", messageHtml: (m) => `<article class="chat-msg"><div class="chat-text">${m.text}</div></article>`,
    renderMarkdown: (el, text) => { renders.push(text); el.innerHTML = `<p>${text}</p>`; el.classList.add("chat-markdown"); },
    mountLine: (node) => { const line = { node, updates: 0, kicks: 0, stopped: false, update() { line.updates++; }, kick() { line.kicks++; }, stop() { line.stopped = true; } }; node.className = "chat-request-line"; lines.push(line); return line; },
    ...extra };
  return { box: scroller, t: T.mountTranscript(scroller, o), lines, renders };
}
const keys = (box) => box.flow.children.map((n) => n.dataset.key);
const tick = () => new Promise((r) => setTimeout(r, 0));

test("each request is its user message, one activity line, then the answer; nothing is nested", () => {
  const items = T.transcriptItems(threadGroups(state()));
  assert.deepEqual(items.map((i) => i.key), ["m:n", "m:a", "run:r1", "m:c", "m:b", "run:r2"]);
  assert.deepEqual(items.filter((i) => i.first).map((i) => i.key), ["m:n", "m:a", "m:b"]);
  assert.equal(items.filter((i) => i.kind === "activity").length, 2, "exactly one activity line per request");
  const { box, t, lines } = mount();
  t.update(state());
  assert.deepEqual(keys(box), ["m:n", "m:a", "run:r1", "m:c", "m:b", "run:r2"]);
  assert.ok(box.flow.children.every((n) => n.tagName !== "DETAILS" && !n.classList.contains("chat-turn")), "no accordion turn wrapper");
  assert.equal(box.querySelectorAll("details").length, 0);
  assert.deepEqual(box.flow.children.map((n) => n.dataset.request), ["", "r1", "r1", "r1", "r2", "r2"]);
  assert.equal(lines.length, 2);
  const buttons = box.querySelectorAll("button").map((b) => b.textContent);
  assert.deepEqual(buttons, ["New activity"], "no Collapse/Expand controls");
});

test("nodes are keyed: polling keeps existing nodes, replaces changed content in place and drops removed ones", () => {
  const { box, t, lines } = mount();
  const s = state(); t.update(s);
  const before = new Map(box.flow.children.map((n) => [n.dataset.key, n]));
  s.messages.push({ id: "d", request: "r2", role: "assistant", text: "done", at: "4" });
  s.messages[2].text = "**edited answer**";
  t.update(s);
  assert.deepEqual(keys(box), ["m:n", "m:a", "run:r1", "m:c", "m:b", "run:r2", "m:d"]);
  for (const [k, n] of before) assert.equal(box.flow.children.find((x) => x.dataset.key === k), n, `${k} kept its node`);
  assert.match(before.get("m:c").innerHTML, /edited answer/);
  assert.equal(lines.length, 2, "activity lines are mounted once");
  assert.ok(lines.every((l) => l.updates === 2));
  s.messages = s.messages.filter((m) => m.id !== "n");
  t.update(s);
  assert.equal(keys(box)[0], "m:a");
  t.kick(); assert.ok(lines.every((l) => l.kicks === 1));
  t.reset(); assert.ok(lines.every((l) => l.stopped), "a session switch stops every line");
});

test("markdown is cached per message id and text hash", async () => {
  const fresh = () => { const s = state(); for (const m of s.messages) m.text += " (md)"; return s; };
  const first = mount(); first.t.update(fresh()); await tick();
  assert.deepEqual(first.renders, ["legacy note (md)", "**answer** (md)"], "user messages stay plain text");
  const again = mount(); again.t.update(fresh());
  assert.deepEqual(again.renders, [], "a remount (session switch, dock) reuses the sanitized HTML");
  const c = again.box.flow.children.find((n) => n.dataset.key === "m:c");
  assert.equal(c.querySelector(".chat-text").innerHTML, "<p>**answer** (md)</p>");
  assert.ok(c.querySelector(".chat-text").classList.contains("chat-markdown"));
  const s = state(); s.messages[2].text = "**changed**"; again.t.update(s); await tick();
  assert.deepEqual(again.renders, ["**changed**"], "a new text hash renders again");
  assert.notEqual(T.textHash("a"), T.textHash("b"));
  assert.equal(T.textHash("same"), T.textHash("same"));
  const cache = T.markdownCache(2);
  cache.set("x", "1", "one"); cache.set("y", "1", "two"); cache.get("x", "1"); cache.set("z", "1", "three");
  assert.equal(cache.size, 2);
  assert.equal(cache.get("y", "1"), undefined, "least recently used goes first");
  assert.equal(cache.get("x", "1"), "one");
  assert.equal(cache.get("x", "2"), undefined, "same id, other text");
});

test("following readers stay at the end; others keep their place and get New activity", () => {
  const { box, t } = mount();
  const s = state(); t.update(s);
  assert.equal(box.scrollTop, box.scrollHeight - box.clientHeight, "starts at the end");
  s.messages.push({ id: "d", request: "r2", role: "assistant", text: "more", at: "4" }); t.update(s);
  assert.equal(box.scrollTop, box.scrollHeight - box.clientHeight, "follows new messages");
  const newer = box.querySelector(".chat-new-activity");
  assert.equal(newer.hidden, true);
  box.scroll(350);
  s.messages.push({ id: "e", request: "r2", role: "assistant", text: "even more", at: "5" }); t.update(s);
  assert.equal(box.scrollTop, 350, "a reader above the end is not moved");
  assert.equal(newer.hidden, false, "New activity appears");
  s.messages.push({ id: "z", request: "r1", role: "assistant", text: "late answer to the first request", at: "2.5" }); t.update(s);
  assert.equal(box.scrollTop, 450, "content inserted above the anchor shifts the scroll by its height");
  t.update(s);
  assert.equal(box.scrollTop, 450, "an unchanged poll moves nothing");
  newer.onclick();
  assert.equal(box.scrollTop, box.scrollHeight - box.clientHeight);
  assert.equal(newer.hidden, true);
  box.scroll(0); s.messages.push({ id: "f", request: "r2", role: "assistant", text: "f", at: "6" }); t.update(s);
  assert.equal(newer.hidden, false);
  box.scroll(box.scrollHeight); assert.equal(newer.hidden, true, "reaching the end hides it");
});

test("anchor search, empty state and paused updates", () => {
  const nodes = [0, 1, 2, 3].map((i) => ({ getBoundingClientRect: () => ({ bottom: (i + 1) * 10 }) }));
  assert.equal(T.anchorIndex(nodes, 25), 2);
  assert.equal(T.anchorIndex(nodes, 0), 0);
  assert.equal(T.anchorIndex(nodes, 99), -1);
  let error = "";
  const { box, t } = mount({ error: () => error });
  t.update({ requests: [], messages: [] });
  const empty = box.children.find((n) => n.textContent.startsWith("No messages yet"));
  assert.equal(empty.hidden, false);
  error = "offline"; t.update(state());
  assert.equal(empty.hidden, true);
  assert.equal(box.querySelector(".chat-flow-status").textContent, "Updates paused — reconnecting");
});

test("the Chat page and the dock both render through transcript.js; the accordion is gone", async () => {
  const web = new URL("../../src/alfrd/web/", import.meta.url);
  const read = (p) => readFile(new URL(p, web), "utf8");
  for (const f of ["js/components/chat_view.js", "js/components/chat_dock.js"]) {
    const src = await read(f);
    assert.match(src, /import \{ mountTranscript \} from "\.\/transcript\.js"/, f);
    assert.match(src, /mountTranscript\(\$\("#(chat|cd)-log"/, f);
    assert.doesNotMatch(src, /mountThread/, f);
  }
  for (const f of ["js/components/transcript.js", "js/components/chat_thread.js", "js/components/chat_view.js", "js/components/chat_dock.js", "css/chat.css"]) {
    const src = await read(f);
    assert.doesNotMatch(src, /chat-turn|Collapse earlier|Expand earlier|data-collapse|data-expand|ui:chat\.history/, f);
  }
});
