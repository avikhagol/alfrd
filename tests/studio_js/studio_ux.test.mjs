import test from "node:test";
import assert from "node:assert/strict";
import { loopResults, pollLoopResults } from "../../src/alfrd/web/js/data/loop_results.js";
import { parseYaml } from "../../src/alfrd/web/js/utils/yaml_parser.js";

const text = `# Project comment
name: demo
description: Original
execution:
  mode: step
  timeout: 60
# Preserve handoff settings
workflows:
- name: agent-loop
  repeat: {iterations: 2}
  steps:
  - id: claude-turn
    handoff: {input: a.md, output: b.md}
    human_review: true
custom: keep-me
`;

test("scheduler turns count independently of retry archives and CSVs", () => {
  const status = { plan: { steps: ["a", "b", "c"] }, table: { rows: [{ cells: { a: "done", b: "running", c: "todo" } }] } };
  const result = loopResults(status, [
    { status: "failed", phase: "failed", started: "2026-10-03T10:00:00Z", finished: "2026-10-03T10:00:10Z", response_bytes: 0 },
    { status: "done", phase: "done", started: "2026-10-03T10:00:10Z", finished: "2026-10-03T10:00:30Z", response_bytes: 300 },
    { status: "running", phase: "awaiting_review", started: "2026-10-03T10:00:30Z", response_bytes: 400 },
  ], Date.parse("2026-10-03T10:01:00Z"));
  assert.equal(result.done, 1); assert.equal(result.total, 3); assert.equal(result.failed, 0);
  assert.equal(result.waiting, 1); assert.equal(result.replies, 2); assert.equal(result.seconds, 60);
  assert.equal(result.turns.length, 3);
});

test("missing timestamps do not invent runtimes", () => {
  const result = loopResults({ plan: { steps: ["a"] } }, [{ status: "done", started: "bad" }, { status: "failed", started: "2026-10-03T10:00:00Z" }]);
  assert.equal(result.seconds, 0); assert.equal(result.total, 1);
  assert.ok(result.turns.every((h) => h.seconds === null));
});

test("historical runs use saved counts when the shared CSV has been reset", () => {
  const result = loopResults({ plan: { status: "finished", steps: ["a", "b"], counts: { done: 2 } },
    table: { rows: [{ cells: { a: "todo", b: "todo" } }] } }, []);
  assert.equal(result.done, 2); assert.equal(result.total, 2);
});

for (const phase of ["awaiting_response", "awaiting_review"]) {
  test(`results polls and times ${phase} until it clears`, () => {
    const handoffs = [{ id: "a", status: "running", phase, started: "2026-10-03T10:00:00Z" }];
    assert.equal(pollLoopResults({ plan: { status: "running" } }, handoffs), true);
    assert.equal(pollLoopResults({ plan: { status: phase } }, handoffs), true);
    assert.equal(loopResults({}, [{ ...handoffs[0], status: phase }], Date.parse("2026-10-03T10:01:00Z")).seconds, 60);
    assert.equal(loopResults({}, handoffs, Date.parse("2026-10-03T10:01:00Z")).seconds, 60);
    assert.equal(pollLoopResults({ plan: { status: "finished" } }, handoffs), false);
  });
}

test("waiting counts only the latest attempt of each scheduler cell", () => {
  const result = loopResults({}, [
    { id: "0001-task-a", row: "task", steps: ["a"], phase: "awaiting_review" },
    { id: "0002-task-a", row: "task", steps: ["a"], phase: "done" },
    { id: "0003-task-b", row: "task", steps: ["b"], phase: "awaiting_response" },
    { id: "0004-task-b", row: "task", steps: ["b"], phase: "awaiting_response" },
    { id: "0005-other-b", row: "other", steps: ["b"], phase: "awaiting_review" },
  ]);
  assert.equal(result.waiting, 2);
  assert.equal(result.turns.length, 5);
});
