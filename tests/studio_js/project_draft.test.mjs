// New-project canvas drafts (data/project_draft.js): templates, canvas edits through the Workflow
// editor's edits, validation before saving, and the creation payload (no browser DOM).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const { parseYaml } = await import("../../src/alfrd/web/js/utils/yaml_parser.js");
const D = await import("../../src/alfrd/web/js/data/project_draft.js");
const tpl = (name) => parseYaml(readFileSync(new URL(`../../src/alfrd/web/assets/templates/${name}.yaml`, import.meta.url), "utf8"));

test("an empty basic draft: add, connect, configure, then the payload has explicit dependencies", () => {
  let d = D.newDraft("basic", tpl("basic"), "empty");
  assert.equal(d.kind, "steps");
  assert.deepEqual(D.draftProblems(d).errors, ["Choose the project folder.", "Add at least one step."]);
  d = D.addDraftStep(d, "Fetch data");
  d = D.addDraftStep(d, "Build");
  assert.equal(d.selected, "build");
  d = D.connectDraft(d, "fetch-data", "build");
  d = D.updateDraftStep(d, "build", { label: "Build it", cmd: ["make", "all"], agent: "" });
  d = { ...d, path: "/tmp/p", name: "p", targets: ["alpha"] };
  const report = D.draftProblems(d);
  assert.deepEqual(report.errors, []);
  assert.match(report.warnings.join(" "), /fetch-data has no agent or command/);
  assert.deepEqual(D.draftPayload(d), { path: "/tmp/p", template: "basic", manifest: ".alfrd/alfrd.yaml", name: "p", draft: {
    targets: ["alpha"], steps: [{ id: "fetch-data", depends_on: [], label: "Fetch data" }, { id: "build", depends_on: ["fetch-data"], label: "Build it", cmd: ["make", "all"] }] } });
  const model = D.draftModel(d);
  assert.deepEqual(model.edges.map((e) => [e.from, e.to]), [["fetch-data", "build"]]);
  assert.equal(model.nodes[1].sub, "make all");
});

test("cycles and bad connections are refused before anything is saved", () => {
  let d = D.newDraft("basic", tpl("basic"), "empty");
  d = D.addDraftStep(D.addDraftStep(D.addDraftStep(d, "a"), "b"), "c");
  d = D.connectDraft(D.connectDraft(d, "a", "b"), "b", "c");
  assert.match(D.connectDraftProblem(d, "c", "a"), /cycle: a → b → c → a/);
  assert.throws(() => D.connectDraft(d, "c", "a"), /cycle/);
  assert.match(D.connectDraftProblem(d, "a", "a"), /itself/);
  assert.match(D.connectDraftProblem(d, "a", "b"), /already waits/);
  d = D.disconnectDraft(d, "a", "b");
  assert.equal(D.connectDraftProblem(d, "c", "a"), null);
  d = D.removeDraftStep(d, "b");
  assert.deepEqual(D.draftModel(d).edges, [], "removing a step drops its edges");
});

test("renaming keeps dependencies and personas; unknown agents and personas are errors", () => {
  let d = D.newDraft("basic", tpl("basic"), "empty");
  d = D.addDraftStep(D.addDraftStep(d, "a"), "b");
  d = D.connectDraft(d, "a", "b");
  d = D.updateDraftStep(d, "a", { id: "first", label: "First", agent: "", cmd: ["true"] });
  assert.deepEqual(D.draftPayload({ ...d, path: "/x" }).draft.steps.map((s) => [s.id, s.depends_on]), [["first", []], ["b", ["first"]]]);
  d = { ...d, path: "/x", roles: { b: "ghost" } };
  d = D.updateDraftStep(d, "b", { label: "B", agent: "nope", cmd: null, role: "ghost" });
  const errors = D.draftProblems(d).errors.join(" ");
  assert.match(errors, /agent nope is not an entrypoint/);
  assert.match(errors, /persona ghost does not exist/);
});

test("a template start keeps its runnable steps with explicit dependencies; skipped steps are left out", () => {
  const d = D.newDraft("avica", tpl("avica"), "template");
  const steps = D.stepsOf(d);
  assert.equal(d.skipped, 1);
  assert.ok(!steps.some((s) => s.id === "phaseshift"));
  const payload = D.draftPayload({ ...d, path: "/x" });
  assert.deepEqual(payload.draft.steps[0].depends_on, []);
  assert.deepEqual(payload.draft.steps[1].depends_on, ["preprocess_fitsidi"]);
  assert.ok(D.agentsOf(d).includes("avica-step"));
  assert.equal(D.newDraft("avica", tpl("avica"), "empty").data.workflows[0].steps.length, 0);
});

test("agent-loop drafts edit the sequence and name the first task", () => {
  let d = D.newDraft("agent-loop", tpl("agent-loop"));
  assert.equal(d.kind, "sequence");
  d = D.addDraftStep(d);
  d = D.updateDraftStep(d, "turn-3", { agent: "codex" });
  assert.deepEqual(D.draftModel(d).nodes.map((n) => n.title), ["claude", "codex", "codex"]);
  assert.match(D.connectDraftProblem(d, "turn-1", "turn-3"), /sequence order/);
  d = { ...d, path: "/x", task: "Build it", iterations: 6, targets: ["a", "b"] };
  assert.match(D.draftProblems(d).errors.join(" "), /starts with one task/);
  d = { ...d, targets: [".hidden"] };
  assert.match(D.draftProblems(d).errors.join(" "), /task name/);
  d = { ...d, targets: ["first"] };
  assert.deepEqual(D.draftProblems(d).errors, []);
  assert.deepEqual(D.draftPayload(d), { path: "/x", template: "agent-loop", manifest: ".alfrd/alfrd.yaml", task: "Build it", iterations: 6,
    sequence: ["claude", "codex", "codex"], draft: { targets: ["first"] } });
  d = D.removeDraftStep(d, "turn-1");
  assert.deepEqual(D.draftModel(d).nodes.map((n) => n.title), ["codex", "codex"]);
});

test("target names: the same rules as the header's New target", () => {
  assert.equal(D.targetProblem("", []), "Enter a name.");
  assert.match(D.targetProblem("#x", []), /#/);
  assert.match(D.targetProblem("a@b", []), /@/);
  assert.match(D.targetProblem("a/b", []), /\//);
  assert.match(D.targetProblem("a", ["a"]), /already listed/);
  assert.equal(D.targetProblem("J0001+0001", []), "");
  assert.match(D.targetProblem("J0001+0001", [], true), /task name/);
});
