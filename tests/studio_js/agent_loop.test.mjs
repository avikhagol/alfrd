import test from "node:test";
import assert from "node:assert/strict";
import { manifestToWorkflows } from "../../src/alfrd/web/js/data/model.js";
import { studioManifest } from "../../src/alfrd/web/js/data/defs.js";

test("five cycles use the same step ids in Studio definitions and graph", () => {
  const manifest = { name: "p", workflows: [{ name: "loop", repeat: { iterations: 5 }, steps: [
    { id: "claude-turn", entrypoint: "claude" }, { id: "codex-turn", entrypoint: "codex" },
  ] }] };
  const defs = studioManifest(manifest);
  const result = manifestToWorkflows(manifest);
  assert.deepEqual(result.errors, []);
  const steps = result.workflows[0].steps;
  assert.equal(steps.length, 10);
  assert.deepEqual(steps.map((s) => s.key), defs.stepOrder);
  assert.equal(steps[0].key, "i001-claude-turn");
  assert.deepEqual(steps[2].depends, ["i001-codex-turn"]);
  assert.equal(steps[9].key, "i005-codex-turn");
});

test("invalid repeat bounds fail before expanding a graph", () => {
  assert.throws(() => studioManifest({ workflows: [{ repeat: { iterations: 0 }, steps: [{ id: "x" }] }] }), /iterations/);
});

import { loopTurnCount, summarizeLoop, hasDirtyHandoff } from "../../src/alfrd/web/js/components/loop_helpers.js";
import { MAX_ITERATIONS } from "../../src/alfrd/web/js/data/defs.js";

test("loop turn preview validates bounds and uses workflow size", () => {
  assert.equal(loopTurnCount(5, [{}, {}]), 10);
  assert.equal(loopTurnCount(MAX_ITERATIONS, 3), MAX_ITERATIONS * 3);
  for (const count of [0, MAX_ITERATIONS + 1, 1.5, NaN]) assert.equal(loopTurnCount(count, 2), 0);
});

test("loop summary covers running, paused, failed and completed", () => {
  const units = [
    { iteration: 1, agent: "claude", status: "done" },
    { iteration: 1, agent: "codex", status: "done" },
    { iteration: 2, agent: "claude", status: "running" },
  ];
  const definition = { iterations: 5, steps: 2 };
  assert.equal(summarizeLoop(units, definition), "Turn 3 of 10 · Iteration 2/5 · claude · running");
  assert.match(summarizeLoop(units, { ...definition, status: "paused" }), /paused$/);
  assert.match(summarizeLoop([...units.slice(0, 2), { ...units[2], status: "failed" }], definition), /failed$/);
  assert.equal(summarizeLoop(units.slice(0, 2), { iterations: 1, steps: 2 }), "Turn 2 of 2 · Iteration 1/1 · codex · done");
});

test("refresh guard detects only unsaved recipient edits", () => {
  assert.equal(hasDirtyHandoff(null, "draft"), false);
  assert.equal(hasDirtyHandoff({ text: "saved" }, "saved"), false);
  assert.equal(hasDirtyHandoff({ text: "saved" }, "draft"), true);
});
