import test from "node:test";
import assert from "node:assert/strict";
import { agentRows, reviewRows, applyAgentSettings, replaceSection, loopRunSelection, nextTaskName } from "../../src/alfrd/web/js/data/agent_settings.js";
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


import { personaRows, turnRoleRows, applyPersonaSettings } from "../../src/alfrd/web/js/data/agent_settings.js";
const personalities = [
  { key: "manager", label: "Manager", instructions: "Plan." },
  { key: "developer", label: "Developer", instructions: "Build." },
  { key: "reviewer", label: "Reviewer", instructions: "Review." },
];
test("personality helpers assign four turns and preserve the input", () => {
  const turns = turnRoleRows(manifest);
  assert.deepEqual(turns.map((r) => r.agent), ["claude", "codex", "claude", "codex"]);
  const keys = ["manager", "developer", "reviewer", "developer"];
  turns.forEach((row, i) => { row.keys = [keys[i]]; });
  const result = applyPersonaSettings(manifest, personalities, turns);
  assert.deepEqual(personaRows(result), personalities);
  assert.deepEqual(result.workflows[0].roles, keys);
  assert.deepEqual(turnRoleRows(result).map((r) => r.keys), keys.map((key) => [key]));
  assert.equal(manifest.project_settings, undefined);
});
test("turn role cycles support combined roles, partial cycles and deletion", () => {
  const turns = turnRoleRows(manifest);
  turns.forEach((row, i) => { row.keys = i % 2 ? [] : ["manager", "reviewer"]; });
  const result = applyPersonaSettings(manifest, personalities, turns);
  assert.deepEqual(result.workflows[0].roles, [["manager", "reviewer"], null]);
  assert.deepEqual(applyPersonaSettings(result, personalities.slice(1), turns).workflows[0].roles, ["reviewer", null]);
  assert.deepEqual(applyPersonaSettings(manifest, personalities, [{ keys: ["manager"] }, { keys: [] }, { keys: ["manager"] }]).workflows[0].roles, ["manager", null]);
});
test("step fallback is materialized and removed when editing turn roles", () => {
  const data = structuredClone(manifest); data.workflows[0].steps[0].role = "manager";
  const turns = turnRoleRows(data);
  assert.deepEqual(turns.map((r) => r.keys), [["manager"], [], ["manager"], []]);
  const result = applyPersonaSettings(data, personalities, turns);
  assert.equal(result.workflows[0].steps[0].role, undefined);
  assert.deepEqual(result.workflows[0].roles, ["manager", null]);
});
test("personality form helpers reject invalid definitions", () => {
  for (const row of [{ ...personalities[0], key: "Bad" }, { ...personalities[0], label: "" }, { ...personalities[0], instructions: "x".repeat(4001) }])
    assert.throws(() => applyPersonaSettings(manifest, [row], []), /Persona/);
  assert.throws(() => applyPersonaSettings(manifest, [personalities[0], personalities[0]], []), /unique/);
});


test("empty personality settings and untouched agent settings preserve the manifest", () => {
  for (const data of [manifest, { ...manifest, project_settings: { custom: "keep" } }]) {
    const result = applyPersonaSettings(data, [], turnRoleRows(data));
    assert.deepEqual(result, data);
    assert.deepEqual(applyAgentSettings(result, agentRows(result), false, reviewRows(result)), data);
  }
});
test("removing all assignments omits roles and removing all personalities omits personas", () => {
  const assigned = applyPersonaSettings(manifest, personalities, [{ keys: ["manager"] }]);
  const cleared = applyPersonaSettings(assigned, [], turnRoleRows(manifest));
  assert.equal(Object.hasOwn(cleared.project_settings, "personas"), false);
  assert.equal(Object.hasOwn(cleared.workflows[0], "roles"), false);
});


test("loop selection isolates the selected task and defaults files relative to its folder", () => {
  const info = { table: { rows: [
    { target: "task", files: "task.md", cells: { a: "running" } },
    { target: "fix-login", files: "", cells: { a: "todo" } },
  ] } };
  assert.deepEqual(loopRunSelection(info, "fix-login").rows, [{ target: "fix-login", files: "task.md", code: "", workdir: "" }]);
  assert.equal(loopRunSelection({}, "another").rows[0].files, "task.md");
  assert.equal(nextTaskName([]), "task");
  assert.equal(nextTaskName([{ name: "task" }, { name: "task-2" }]), "task-3");
});

test("editing personalities preserves concise later-turn summaries", () => {
  const rows = personalities.map((p) => ({ ...p, summary: "Brief reminder." }));
  const result = applyPersonaSettings({ workflows: [{ steps: ["build"] }] }, rows, [{ keys: ["manager"] }]);
  assert.equal(result.project_settings.personas.manager.summary, "Brief reminder.");
});
