// chat.watch (scheduled checks) in the Settings round trip (data/chat.js, no DOM).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const C = await import("../../src/alfrd/web/js/data/chat.js");

test("watch merges per key and only changed keys are written", () => {
  const d = C.mergeChat({});
  assert.deepEqual(d.watch, { interval_seconds: 300, stall_seconds: 1800, wake_orchestrator: false, max_checks: 3 });
  assert.equal(C.chatBlock(d).watch, undefined);
  const s = C.mergeChat({ watch: { interval_seconds: 0 } });
  assert.equal(s.watch.stall_seconds, 1800);
  assert.deepEqual(C.chatBlock(s).watch, { interval_seconds: 0 });
  assert.deepEqual(C.chatBlock({ ...s, watch: { ...s.watch, stall_seconds: "900" } }).watch, { interval_seconds: 0, stall_seconds: 900 });
  assert.deepEqual(C.chatProblems(s), []);
});

test("watch problems mirror chat.merge_settings", () => {
  const s = C.mergeChat({});
  assert.match(C.chatProblems({ ...s, watch: { ...s.watch, interval_seconds: 10 } }).join(" "), /Check interval/);
  assert.match(C.chatProblems({ ...s, watch: { ...s.watch, stall_seconds: 5 } }).join(" "), /Stall after/);
  assert.match(C.chatProblems({ ...s, watch: { ...s.watch, max_checks: -1 } }).join(" "), /checks per request/);
});

test("check status covers elapsed time, due checks and disabled scheduling", () => {
  const now = Date.parse("2026-10-26T10:02:00Z");
  assert.equal(C.checkStatus(null, now), "Not checked yet");
  assert.equal(C.checkStatus({ at: "bad" }, now), "Not checked yet");
  const report = { at: "2026-10-26T10:00:00+00:00", next_at: "2026-10-26T10:05:00+00:00" };
  assert.equal(C.checkStatus(report, now), "Checked 2 min ago · next in 3 min");
  assert.equal(C.checkStatus(report, now + 180000), "Checked 5 min ago · next check due");
  assert.equal(C.checkStatus({ at: new Date(now).toISOString(), next_at: null }, now), "Checked just now · scheduled checks off");
});


test("orchestrator wake and check limit round trip with defaults omitted", () => {
  const s = C.mergeChat({ watch: { wake_orchestrator: true, max_checks: 2 } });
  assert.deepEqual(C.chatProblems(s), []);
  assert.deepEqual(C.chatBlock(s).watch, { wake_orchestrator: true, max_checks: 2 });
  assert.deepEqual(C.mergeChat(C.chatBlock(s)).watch, s.watch);
  s.watch.wake_orchestrator = false;
  s.watch.max_checks = "3";
  assert.equal(C.chatBlock(s).watch, undefined);
  s.watch.max_checks = true;
  assert.match(C.chatProblems(s).join(" "), /checks per request/);
  s.watch.wake_orchestrator = "yes";
  assert.match(C.chatProblems(s).join(" "), /Wake the orchestrator/);
});
