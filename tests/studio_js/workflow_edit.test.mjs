import test from "node:test";
import assert from "node:assert/strict";
import { parseYaml } from "../../src/alfrd/web/js/utils/yaml_parser.js";
import { manifestToWorkflows } from "../../src/alfrd/web/js/data/model.js";
import { registerTemplate } from "../../src/alfrd/web/js/data/defs.js";
import {
  addStep, editorSteps, moveStep, removeStep, sequenceOf, setSequence, setSkip, setTotalTurns,
  splitCommand, joinCommand, slugFor, updateStep, workflowKind, workflowText, withWorkflow,
} from "../../src/alfrd/web/js/data/workflow_edit.js";

const BLANK = "# my project\nname: demo\ndescription: A new configurable ALFRD project.\nentrypoint: []\nsteps: {}\nworkflows: []\n";

test("a blank project gets a workflow from its first step; other lines are kept", () => {
  const data = parseYaml(BLANK);
  assert.equal(workflowKind(data), "none");
  const next = addStep(data, { id: "prepare", label: "Prepare data", cmd: ["python", "prep.py", "{target}"] });
  const text = workflowText(BLANK, next);
  assert.match(text, /^# my project/);
  assert.match(text, /description: A new configurable ALFRD project\./);
  const info = manifestToWorkflows(parseYaml(text), "alfrd.yaml", { aliases: false });
  assert.deepEqual(info.errors, []);
  assert.equal(info.workflows[0].name, "demo");
  assert.deepEqual(info.workflows[0].steps.map((s) => [s.key, s.label]), [["prepare", "Prepare data"]]);
  assert.deepEqual(info.workflows[0].steps[0].command, ["python", "prep.py", "{target}"]);
});

test("add, skip, move, rename and remove keep the list valid", () => {
  let data = { name: "p", workflows: [{ name: "wf", steps: ["a", "b", { id: "c", depends_on: ["b"] }] }] };
  data = addStep(data, { id: "x" }, 1);
  assert.deepEqual(data.workflows[0].steps, ["a", "x", "b", { id: "c", depends_on: ["b"] }]);
  data = setSkip(data, "b", true);
  assert.deepEqual(data.workflows[0].steps[2], { id: "b", skip: true });
  let info = manifestToWorkflows(data, "alfrd.yaml", { aliases: false });
  assert.deepEqual(info.workflows[0].steps.map((s) => s.key), ["a", "x", "c"]);
  assert.deepEqual(info.workflows[0].skipped, ["b"]);
  data = setSkip(data, "b", false);
  assert.equal(data.workflows[0].steps[2], "b");
  data = updateStep(data, "b", { id: "bb", label: "Bee" });
  assert.deepEqual(data.workflows[0].steps[3], { id: "c", depends_on: ["bb"] });
  data = moveStep(data, "c", 0);
  assert.equal(data.workflows[0].steps[0].id, "c");
  data = removeStep(data, "bb");
  assert.deepEqual(data.workflows[0].steps, ["c", "a", "x"]);
  assert.throws(() => addStep(data, { id: "a" }), /already has/);
  assert.throws(() => addStep(data, { id: "bad name" }), /letters/);
});

test("a template workflow is copied before a step is skipped", () => {
  const template = { steps: { s1: { label: "One", stage: "st" }, s2: { label: "Two" } }, workflows: [{ name: "avica", steps: ["s1", "s2"] }] };
  const data = setSkip(withWorkflow({ name: "p", template: "x" }, template), "s2", true, template);
  assert.deepEqual(data.workflows, [{ name: "avica", steps: ["s1", { id: "s2", skip: true }] }]);
  const rows = editorSteps(data, template);
  assert.deepEqual(rows.map((r) => [r.id, r.label, r.skip, r.fromTemplate]), [["s1", "One", false, true], ["s2", "Two", true, true]]);
});

test("unskipping clears a skip in the project's own step definitions", () => {
  const data = setSkip({ workflows: [{ name: "w", steps: ["a"] }], steps: { a: { skip: true } } }, "a", false);
  assert.equal(data.steps.a, undefined);
});

test("minimal template rows resolve without changing the manifest; inherited unskip survives Save", () => {
  const template = { steps: { a: {}, b: { skip: true } }, workflows: [{ name: "pipeline", label: "Pipeline", entrypoint: "runner", steps: ["a", "b"] }] };
  registerTemplate("editor-test", template);
  const original = "# keep me\nname: p\ntemplate: editor-test\nentrypoint: []\nproject_settings:\n  unknown: preserved\n";
  const data = parseYaml(original);
  assert.equal(workflowKind(data, template), "steps");
  assert.deepEqual(editorSteps(data, template).map((r) => [r.id, r.skip]), [["a", false], ["b", true]]);
  assert.equal(workflowText(original, data), original);
  const next = setSkip(data, "b", false, template);
  assert.equal(next.workflows[0].label, "Pipeline");
  assert.equal(next.workflows[0].entrypoint, "runner");
  assert.deepEqual(next.workflows[0].steps[1], { id: "b", skip: false });
  const saved = workflowText(original, next);
  assert.ok(saved.startsWith(original));
  const info = manifestToWorkflows(parseYaml(saved), "alfrd.yaml", { aliases: false });
  assert.deepEqual(info.errors, []);
  assert.deepEqual(info.workflows[0].steps.map((s) => s.key), ["a", "b"]);
  assert.equal(editorSteps(setSkip(setSkip(next, "b", true, template), "b", false, template), template)[1].skip, false);
  assert.equal(data.workflows, undefined);
});

test("step status source can override the AVICA default and return to inheritance", () => {
  const template = { execution: { status_from: "both" }, steps: { a: {} }, workflows: [{ name: "avica", steps: ["a"] }] };
  const original = "name: p\ntemplate: avica\n";
  let data = updateStep(parseYaml(original), "a", { status_from: "exit_code" }, template);
  assert.equal(editorSteps(data, template)[0].status_from, "exit_code");
  const text = workflowText(original, data);
  assert.equal(parseYaml(text).workflows[0].steps[0].status_from, "exit_code");
  data = updateStep(data, "a", { status_from: null }, template);
  assert.equal(editorSteps(data, template)[0].status_from, null);
  assert.equal(data.workflows[0].steps[0], "a");
  assert.equal(template.execution.status_from, "both");
});

test("agent-loop sequence and total turns", () => {
  let data = { workflows: [{ name: "agent-loop", repeat: { iterations: 4, sequence: ["claude", "codex"] } }] };
  assert.equal(workflowKind(data), "sequence");
  data = setSequence(data, ["claude", "codex", "codex"]);
  data = setTotalTurns(data, 6);
  assert.deepEqual(data.workflows[0].repeat, { iterations: 6, sequence: ["claude", "codex", "codex"] });
  assert.deepEqual(sequenceOf(data), ["claude", "codex", "codex"]);
  assert.equal(sequenceOf({ workflows: [{ repeat: { sequence: [["a"], ["b"]] } }] }), null);
  assert.throws(() => setSequence(data, []), /at least one/);
});

test("delete removes inherited references only in the edited workflow", () => {
  const template = { steps: { a: {}, b: { needs: ["a"], metadata: ["keep"] }, c: { after: "a" } } };
  registerTemplate("delete-test", template);
  const data = { template: "delete-test", steps: { d: { depends_on: ["a", "b"], unknown: "keep" } }, workflows: [{ name: "first", steps: ["a", "b", "c", "d"] }, { name: "other", steps: ["a", "b"] }] };
  const next = removeStep(data, "a", template);
  const info = manifestToWorkflows(next, "alfrd.yaml", { aliases: false });
  assert.deepEqual(info.errors, []);
  assert.deepEqual(info.workflows[0].steps.map((s) => [s.key, s.depends]), [["b", []], ["c", ["b"]], ["d", ["b"]]]);
  assert.deepEqual(next.workflows[1], data.workflows[1]);
  assert.deepEqual(next.steps, data.steps);
  assert.deepEqual(info.workflows[0].steps[0].metadata, ["keep"]);
});

test("commands split like a shell without running one", () => {
  assert.deepEqual(splitCommand(`python run.py --name "a b" 'c"d' {target}`), ["python", "run.py", "--name", "a b", 'c"d', "{target}"]);
  assert.equal(joinCommand(["python", "a b", 'c"d']), `python "a b" 'c"d'`);
  assert.throws(() => splitCommand(`echo "x`), /unclosed/);
  assert.equal(slugFor("Run tests!", ["run-tests"]), "run-tests-2");
});

test("settings: inherit / override / explicit null and empty, section-only writes", async () => {
  const { applySettings, workflowSettings, changedSections } = await import("../../src/alfrd/web/js/data/workflow_edit.js");
  const tpl = { execution: { concurrency: 1, timeout: 3600 }, step_defaults: { logs: ["{logs}/a.log"] }, workflows: [{ name: "t", entrypoint: "run", steps: ["a"] }] };
  const text = "# keep\nname: p\ntemplate: x\nnotify:\n  routes: []\n";
  const data = parseYaml(text);
  const s = workflowSettings(data, tpl);
  assert.equal(s.timeout.own, undefined);
  assert.equal(s.timeout.inherited, 3600);
  assert.deepEqual(s.logs.inherited, ["{logs}/a.log"]);
  assert.equal(s.entrypoint.inherited, "run");
  // Keeping every inherited value changes nothing.
  assert.deepEqual(applySettings(data, { concurrency: undefined, on_failure: undefined, timeout: undefined, logs: undefined, entrypoint: undefined }, tpl), data);
  const next = applySettings(data, { concurrency: 4, on_failure: "continue", timeout: null, logs: [] }, tpl);
  assert.deepEqual(next.execution, { concurrency: 4, on_failure: "continue", timeout: null });
  assert.deepEqual(next.step_defaults, { logs: [] });
  assert.equal(next.workflows, undefined);
  assert.deepEqual(changedSections(data, next).sort(), ["execution", "step_defaults"]);
  const out = workflowText(text, next);
  assert.match(out, /^# keep\nname: p\ntemplate: x\nnotify:\n {2}routes: \[\]/);
  assert.deepEqual(parseYaml(out).execution, { concurrency: 4, on_failure: "continue", timeout: null });
  // Reset removes the override and the emptied section.
  const back = applySettings(next, { concurrency: undefined, on_failure: undefined, timeout: undefined, logs: undefined }, tpl);
  assert.equal(back.execution, undefined);
  assert.equal(back.step_defaults, undefined);
  const reset = workflowText(out, back);
  assert.deepEqual(parseYaml(reset), data);
  assert.ok(reset.startsWith(text));
  const original = { name: "p", workflows: [{ name: "w", steps: ["a"] }] };
  const snapshot = structuredClone(original);
  const applied = applySettings(original, { entrypoint: "run", concurrency: 3 });
  assert.deepEqual(original, snapshot);
  assert.deepEqual(changedSections(original, applied).sort(), ["execution", "workflows"]);
  assert.throws(() => applySettings(data, { concurrency: 0 }, tpl), /concurrency/);
  assert.throws(() => applySettings(data, { on_failure: "explode" }, tpl), /on_failure/);
  const ep = applySettings({ name: "p", workflows: [{ name: "w", steps: ["a"] }, { name: "other", steps: ["z"] }] }, { entrypoint: "run" });
  assert.deepEqual(ep.workflows, [{ name: "w", steps: ["a"], entrypoint: "run" }, { name: "other", steps: ["z"] }]);
});

test("stages: rename and reorder keep ids, metadata and step dependencies", async () => {
  const { setStages, stageList } = await import("../../src/alfrd/web/js/data/workflow_edit.js");
  const data = { stages: [{ id: "a", title: "A", color: "red" }, "b"], workflows: [{ name: "w", steps: [{ id: "s1", stage: "a" }, { id: "s2", stage: "b", depends_on: ["s1"] }] }] };
  const next = setStages(data, [{ id: "b", title: "Bee" }, { id: "a", title: "Calibration" }]);
  assert.deepEqual(next.stages, [{ id: "b", title: "Bee" }, { id: "a", title: "Calibration", color: "red" }]);
  assert.deepEqual(next.workflows, data.workflows);
  assert.deepEqual(stageList(next).map((x) => x.id), ["b", "a"]);
  assert.throws(() => setStages(data, [{ id: "a", title: "" }]), /Name this stage/);
  assert.throws(() => setStages(data, [{ id: "a", title: "x" }, { id: "a", title: "y" }]), /repeated/);
  // Template stages resolve in order before an edit.
  assert.deepEqual(setStages({}, [{ id: "t1", title: "T" }], { stages: [{ id: "t1", title: "Old", x: 1 }] }).stages, [{ id: "t1", title: "T", x: 1 }]);
});
