import test from "node:test";
import assert from "node:assert/strict";
import { expandWorkflowSteps, sequenceAgents, workflowTurns } from "../../src/alfrd/web/js/data/defs.js";
import { turnSettingRows, applyTurnSettings, agentRows, turnRoleRows } from "../../src/alfrd/web/js/data/agent_settings.js";
import { summarizeLoop } from "../../src/alfrd/web/js/components/loop_helpers.js";

const manifest = (repeat, extra = {}) => ({ name: "p", entrypoint: [
  { name: "claude", cmd: ["claude", "-p"] }, { name: "codex", cmd: ["codex", "exec", "-"] },
  { name: "gemini", cmd: ["gemini"], human_review: true }], workflows: [{ name: "loop", repeat, ...extra }] });

test("iterations count turns; the sequence repeats and may stop mid-pass (mirrors agent_loop.expand_sequence)", () => {
  const steps = expandWorkflowSteps({ repeat: { iterations: 9, sequence: ["claude", "claude", "claude", "codex", "codex", "claude"] } });
  assert.deepEqual(steps.slice(0, 4).map((s) => s.id), ["t001-claude", "t002-claude", "t003-claude", "t004-codex"]);
  assert.equal(steps.length, 9);
  assert.deepEqual(steps[2].handoff, { input: "{target}/next-step-claude.md", output: "{target}/next-step-codex.md" });
  assert.deepEqual(steps[8].depends_on, ["t008-claude"]);
});

test("passes, custom passes and validation", () => {
  assert.equal(sequenceAgents({ passes: 3, sequence: ["claude", "codex"] }).length - 1, 6);
  assert.deepEqual(sequenceAgents({ iterations: 5, sequence: [["claude", "codex"], ["codex"]] }).slice(0, 5), ["claude", "codex", "codex", "codex", "codex"]);
  assert.throws(() => sequenceAgents({ iterations: 2, passes: 1, sequence: ["claude"] }), /exactly one/);
  assert.equal(workflowTurns({ repeat: { passes: 2, sequence: ["claude", "codex"] } }), 4);
  assert.equal(workflowTurns({ repeat: { iterations: 5 }, steps: ["a", "b"] }), 5);
});

test("per-turn settings: defaults from entrypoints, only changed rows are written", () => {
  const data = manifest({ iterations: 4, sequence: ["claude", "gemini"] }, { turns: { 3: { manual: true } } });
  const rows = turnSettingRows(data);
  assert.deepEqual(rows.map((r) => [r.id, r.manual, r.human_review]), [["t001-claude", false, false], ["t002-gemini", false, true], ["t003-claude", true, false], ["t004-gemini", false, true]]);
  const changed = rows.map((r) => (r.id === "t001-claude" ? { ...r, human_review: true, after: "+1h", changed: true } : r));
  const result = applyTurnSettings(data, changed);
  assert.deepEqual(result.workflows[0].turns, { 3: { manual: true }, "t001-claude": { human_review: true, after: "+1h" } });
  const cleared = applyTurnSettings(result, turnSettingRows(result).map((r) => ({ ...r, manual: false, human_review: r.agent === "gemini", after: "", changed: true })));
  assert.equal(cleared.workflows[0].turns, undefined);
});

test("agents used in a sequence are listed whatever their CLI", () => {
  const rows = agentRows(manifest({ iterations: 2, sequence: ["claude", "gemini"] }));
  assert.deepEqual(rows.map((r) => [r.name, r.adapter]), [["claude", "claude"], ["codex", "codex"], ["gemini", "generic"]]);
  assert.deepEqual(turnRoleRows(manifest({ iterations: 3, sequence: ["claude", "gemini"] })).map((r) => r.agent), ["claude", "gemini", "claude"]);
});

test("quoted strings continued over lines, as PyYAML writes them, parse like PyYAML", async () => {
  const { parseYaml } = await import("../../src/alfrd/web/js/utils/yaml_parser.js");
  const text = "a:\n  b: '- Focus: one,\n    two.\n\n    - Tone: three''s'\n  c: \"x\\\n    y\n    z\"\n  d: 1\n";
  assert.deepEqual(parseYaml(text), { a: { b: "- Focus: one, two.\n- Tone: three's", c: "xy z", d: 1 } });
  assert.throws(() => parseYaml("a: 'open\n  still\n"), /unterminated/);
});
