import test from "node:test";
import assert from "node:assert/strict";
import { agentRows, reviewRows, applyAgentSettings, replaceSection, loopRunSelection } from "../../src/alfrd/web/js/data/agent_settings.js";
import { parseYaml, dumpYaml } from "../../src/alfrd/web/js/utils/yaml_parser.js";

const manifest = { name: "p", entrypoint: [
  { name: "claude", cmd: ["claude", "-p", "--model", "old"] },
  { name: "codex", cmd: ["codex", "exec", "--model=old", "-"] },
], workflows: [{ name: "loop", repeat: { iterations: 2 }, steps: [
  { id: "claude-turn", entrypoint: "claude", handoff: { input: "a.md", output: "b.md" } },
  { id: "codex-turn", entrypoint: "codex", handoff: { input: "b.md", output: "a.md" } },
] }] };

test("models can be changed or reset without duplicate CLI flags", () => {
  const rows = agentRows(manifest);
  assert.equal(rows[0].model, "old");
  assert.equal(rows[1].model, "old");
  const result = applyAgentSettings(manifest, [{ ...rows[0], model: "sonnet" }, { ...rows[1], model: "" }], true,
    [{ id: "claude-turn", enabled: true }, { id: "codex-turn", enabled: false }]);
  assert.equal(result.entrypoint[0].model, "sonnet");
  assert.equal(result.entrypoint[1].model, undefined);
  assert.deepEqual(result.entrypoint[1].cmd, ["codex", "exec", "-"]);
  assert.deepEqual(reviewRows(result).map((r) => r.enabled), [true, false]);
  assert.equal(manifest.entrypoint[0].cmd[2], "--model");
});

test("disabling human adjustments clears all per-step checkpoints", () => {
  const result = applyAgentSettings(manifest, agentRows(manifest), false,
    [{ id: "claude-turn", enabled: true }, { id: "codex-turn", enabled: true }]);
  assert.deepEqual(reviewRows(result).map((r) => r.enabled), [false, false]);
  assert.equal(result.workflows[0].repeat.iterations, 2);
});

test("section replacement handles indentless YAML lists and preserves unrelated comments", () => {
  const text = "name: p\nentrypoint:\n- name: claude\n  cmd: [claude, -p]\n# Keep this comment\nworkflows:\n- name: loop\n  steps: []\n";
  const result = replaceSection(text, "entrypoint", dumpYaml({ entrypoint: manifest.entrypoint }));
  assert.equal(parseYaml(result).entrypoint.length, 2);
  assert.match(result, /# Keep this comment/);
  assert.equal(parseYaml(result).workflows[0].name, "loop");
});

test("mapping workflows retain handoffs and review overrides", () => {
  const data = { ...manifest, workflows: { loop: { steps: { x: { handoff: { input: "a.md", output: "b.md" } } } } } };
  const result = applyAgentSettings(data, agentRows(data), true, [{ id: "x", enabled: true }]);
  assert.equal(result.workflows.loop.steps.x.human_review, true);
  assert.deepEqual(result.workflows.loop.steps.x.handoff, data.workflows.loop.steps.x.handoff);
});

test("completed loop starts a fresh task plan even without imported targets", () => {
  assert.deepEqual(loopRunSelection({}).rows.map((r) => r.target), ["task"]);
  const info = { table: { rows: [{ target: "task", cells: { a: "done", b: "done" } }] } };
  assert.equal(loopRunSelection(info).preferNew, true);
  info.table.rows[0].cells.b = "todo";
  assert.equal(loopRunSelection(info).preferNew, false);
});


test("loop Run selection uses the configured task row", () => {
  assert.equal(loopRunSelection({ loop: { task_row: "custom-task" } }).rows[0].target, "custom-task");
});
