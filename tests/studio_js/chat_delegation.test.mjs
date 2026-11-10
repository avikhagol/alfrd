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


test("delegation button allows ASK, hides PRM, repeats and handed-back decisions", async () => {
  globalThis.window = { addEventListener() {} };
  const { canDelegate } = await import("../../src/alfrd/web/js/components/chat_view.js");
  const ask = { mailbox_message_id: "msg_ask", kind: "question" };
  assert.equal(canDelegate(ask), true);
  assert.equal(canDelegate({ mailbox_message_id: "msg_prm", kind: "permission_request" }), false);
  assert.equal(canDelegate({ ...ask, repeat: 2 }), false);
  assert.equal(canDelegate({ ...ask, delegated: "user" }), false);
  assert.equal(canDelegate({ kind: "permission" }), true);
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../../src/alfrd/web/js/components/chat_view.js", import.meta.url), "utf8");
  assert.match(source, /Answer in conversation<\/button>.*canDelegate\(d\).*Let orchestrator decide/);
});
