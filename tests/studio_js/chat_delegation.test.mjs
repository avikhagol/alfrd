// chat.delegate_decisions in the Settings round trip and delegated-decision messages (data/chat.js, no DOM).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const C = await import("../../src/alfrd/web/js/data/chat.js");

test("delegate_decisions round-trips through mergeChat and chatBlock (off is not written)", () => {
  assert.equal(C.mergeChat({}).delegate_decisions, "off");
  const s = C.mergeChat({ delegate_decisions: "all" });
  assert.equal(s.delegate_decisions, "all");
  assert.equal(C.chatBlock(s).delegate_decisions, "all");
  assert.equal(C.chatBlock({ ...s, delegate_decisions: "off" }).delegate_decisions, undefined);
  assert.deepEqual(C.chatProblems(s), []);
  assert.match(C.chatProblems({ ...s, delegate_decisions: "sometimes" }).join(" "), /Let the orchestrator decide/);
});

test("a delegated_decision system message names the orchestrator", () => {
  const m = C.presentMessage({ role: "system", kind: "delegated_decision", text: "The orchestrator decided for you on r0001.a: v1 or v2? → v2" });
  assert.equal(m.identity, "Orchestrator · decided for you");
  assert.equal(C.presentMessage({ role: "system", kind: "budget", text: "x" }).identity, "ALFRD");
});
