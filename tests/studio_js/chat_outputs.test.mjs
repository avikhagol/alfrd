// Frozen expected outputs in the task inspector and "output moved" thread messages (data/chat.js, no DOM).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const C = await import("../../src/alfrd/web/js/data/chat.js");

test("taskOutputs prefers the frozen record and falls back to the plan's paths", () => {
  const frozen = [{ id: "o1", path: "/abs/new.md", rev: 2, history: [{ rev: 1, path: "/abs/old.md", by: "codex" }] }];
  assert.deepEqual(C.taskOutputs({ outputs: frozen, task: { expected_outputs: ["ignored.md"] } }), frozen);
  assert.deepEqual(C.taskOutputs({ task: { expected_outputs: ["a.md", { path: "b/", description: "dir" }, { description: "no path" }] } }), [
    { id: "o1", path: "a.md", description: "", rev: 1, history: [] },
    { id: "o2", path: "b/", description: "dir", rev: 1, history: [] }]);
  assert.deepEqual(C.taskOutputs({}), []);
  assert.deepEqual(C.taskOutputs({ outputs: [] , task: { expected_outputs: ["x"] } }), []);
});

test("an output_changed system message is named as such", () => {
  const m = C.presentMessage({ role: "system", kind: "output_changed", text: "@codex moved output o1" });
  assert.equal(m.identity, "ALFRD · output moved");
  assert.equal(C.presentMessage({ role: "system", text: "x" }).identity, "ALFRD");
});
