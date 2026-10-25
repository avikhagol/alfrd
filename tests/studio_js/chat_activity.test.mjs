import test from "node:test";
import assert from "node:assert/strict";

// Only the DOM surface used by mountActivity; run the actual installed click handler.
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

function fixture(tasks) {
  let state = { requests: [{ id: "r", status: "done" }], tasks };
  const box = new El(), calls = [];
  const api = mountActivity(box, { project: () => "p", sid: () => "s", request: () => "r", state: () => state,
    agentName: (name) => name, openLog: (...args) => calls.push(args) });
  api.kick();
  return { api, calls, box, setState(next) { state = next; },
    click(index) { box.querySelector("details").querySelector(".chat-run-rows").children[index].querySelector("[data-log]").onclick(); } };
}
const task = (row, units, request = "r") => ({ row, request, status: "done", owner: "codex",
  logs: units.map((unit, i) => ({ unit, log: `${unit}-${i + 1}.log`, status: "done" })) });

test("Open log marks only the newest retry of the same task as latest", () => {
  const f = fixture([task("a", ["a1", "a2"]), task("b", ["b1", "b2", "b3"])]);
  try {
    f.click(0); f.click(1); f.click(2); f.click(4);
    assert.deepEqual(f.calls, [
      ["a1-1.log", { unit: "a1", attempt: 1, latest: false }],
      ["a2-2.log", { unit: "a2", attempt: 2, latest: true }],
      ["b1-1.log", { unit: "b1", attempt: 1, latest: false }],
      ["b3-3.log", { unit: "b3", attempt: 3, latest: true }],
    ]);
  } finally { f.api.stop(); }
});

test("Open log recalculates latest at click time and scopes retries to the request", () => {
  const f = fixture([task("a", ["a1"])]);
  try {
    f.click(0);
    assert.equal(f.calls.at(-1)[1].latest, true);
    // State can refresh after the button was drawn; do not use its captured rows.
    f.setState({ tasks: [task("a", ["a1", "a2"]), task("a", ["old1", "old2", "old3"], "other")] });
    f.click(0);
    assert.deepEqual(f.calls.at(-1), ["a1-1.log", { unit: "a1", attempt: 1, latest: false }]);
    f.api.kick(); f.click(1);
    assert.deepEqual(f.calls.at(-1), ["a2-2.log", { unit: "a2", attempt: 2, latest: true }]);
  } finally { f.api.stop(); }
});

test("a higher attempt on another task does not make a single attempt historical", () => {
  const f = fixture([task("a", ["a1"]), task("b", ["b1", "b2", "b3"])]);
  try {
    f.click(0);
    assert.deepEqual(f.calls, [["a1-1.log", { unit: "a1", attempt: 1, latest: true }]]);
  } finally { f.api.stop(); }
});


test("M7 run line shows M5 steps alongside the inspector button and preserves controls on unchanged polls", () => {
  const f = fixture([]), step = { request_id: "r", kind: "execute", task_row: "a", status: "waiting", attempt: 1, waiting_on: ["m1"] };
  try {
    f.setState({ requests: [{ id: "r", status: "running", steps: [step] }], tasks: [] }); f.api.kick();
    const steps = f.box.querySelector("[data-current-steps]");
    assert.match(steps.innerHTML, /Execute a · waiting/); assert.match(steps.innerHTML, /data-mail-jump="m1"/);
    const html = steps.innerHTML; f.api.kick(); assert.equal(steps.innerHTML, html);
    assert.equal(typeof f.box.querySelector(".chat-run-head").onclick, "function");
    f.setState({ requests: [{ id: "r", status: "done", steps: [step] }], tasks: [] }); f.api.kick();
    assert.equal(steps.innerHTML, "", "completed request does not advertise an old current step");
    f.setState({ requests: [{ id: "r", status: "running" }], tasks: [] }); f.api.kick();
    assert.equal(steps.innerHTML, "", "legacy requests have no invented current step");
  } finally { f.api.stop(); }
});
