import test from "node:test";
import assert from "node:assert/strict";

// Only the DOM surface used by mountActivity; the real chat-run-head handler is exercised.
class El {
  constructor() { this.dataset = {}; this.children = []; this.selectors = new Map(); this.textContent = ""; this.scrollTop = 0; }
  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new El());
    return this.selectors.get(selector);
  }
  append(el) { this.children.push(el); }
  remove() {}
  addEventListener() {}
  setAttribute() {}
  closest() { return null; }
  contains() { return false; }
  get isConnected() { return true; }
}
globalThis.document = { hidden: false, activeElement: null, createElement: () => new El(), addEventListener() {}, removeEventListener() {} };
globalThis.window = { getSelection: () => "" };
const { mountActivity } = await import("../../src/alfrd/web/js/components/chat_activity.js");

function summary(state, extra) {
  const box = new El(), calls = [];
  const api = mountActivity(box, { project: () => "p", sid: () => "s", request: () => "r", state: () => state, agentName: (n) => n,
    viewGraph: (id) => calls.push(["graph", id]), ...(extra ? extra(calls) : {}) });
  api.kick();
  const head = box.querySelector(".chat-run-head");
  api.stop();
  return { head, text: head.textContent, calls };
}
const task = (row, status, request = "r") => ({ row, request, status, kind: "task" });
const req = (extra) => ({ id: "r", status: "running", created_ts: 1000, finished: new Date(1000 * 1000 + 125000).toISOString(), ...extra });

test("summary shows status, task count, duration and reported cost", () => {
  const { text } = summary({ requests: [req({ usage: { total_cost_usd: 0.1234 } })], tasks: [task("a", "done"), task("b", "running"), task("c", "done", "other")] });
  assert.equal(text, "Working · 1/2 tasks complete · 2m 5s · $0.123");
});

test("a recorded zero cost is shown, an absent cost is omitted", () => {
  assert.match(summary({ requests: [req({ usage: { total_cost_usd: 0 } })], tasks: [] }).text, /\$0\.000$/);
  assert.doesNotMatch(summary({ requests: [req()], tasks: [] }).text, /\$|—/);
  assert.doesNotMatch(summary({ requests: [req({ usage: { total_cost_usd: null } })], tasks: [] }).text, /\$|—/);
});

test("a non-numeric cost is omitted, not shown as a dash", () => {
  assert.doesNotMatch(summary({ requests: [req({ usage: { total_cost_usd: "n/a" } })], tasks: [] }).text, /\$|—/);
});

test("legacy request without timestamps or task list omits those fields", () => {
  assert.equal(summary({ requests: [{ id: "r", status: "done" }] }).text, "Run completed");
});

test("empty state: no request yields an empty line, not placeholders", () => {
  assert.equal(summary({ requests: [], tasks: [] }).text, "");
});

test("an unparsable created time omits duration instead of showing a dash", () => {
  assert.equal(summary({ requests: [{ id: "r", status: "done", created: "garbage" }] }).text, "Run completed");
});

test("activating the summary opens the inspector, falling back to the graph", () => {
  const state = { requests: [req()], tasks: [] };
  const a = summary(state, (calls) => ({ inspect: (id) => calls.push(["inspect", id]) }));
  a.head.onclick();
  assert.deepEqual(a.calls, [["inspect", "r"]]);
  const b = summary(state);
  b.head.onclick();
  assert.deepEqual(b.calls, [["graph", "r"]]);
});
