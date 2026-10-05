import test from "node:test";
import assert from "node:assert/strict";
import { manifestToWorkflows } from "../../src/alfrd/web/js/data/model.js";

const base = (steps, extra = {}) => ({ name: "p", workflows: [{ name: "wf", steps }], ...extra });

test("skip: true on a workflow step leaves it out and chains its neighbours", () => {
  const { workflows, errors } = manifestToWorkflows(base(["a", { id: "b", skip: true }, "c"]), "alfrd.yaml", { aliases: false });
  assert.deepEqual(errors, []);
  assert.deepEqual(workflows[0].steps.map((s) => s.key), ["a", "c"]);
  assert.deepEqual(workflows[0].steps[1].depends, ["a"]);
  assert.deepEqual(workflows[0].skipped, ["b"]);
});

test("skip in the top-level steps mapping, and explicit dependencies on a skipped step", () => {
  const manifest = base(["a", "b", { id: "c", depends_on: ["a", "b"] }], { steps: { b: { skip: true } } });
  const { workflows, errors } = manifestToWorkflows(manifest, "alfrd.yaml", { aliases: false });
  assert.deepEqual(errors, []);
  assert.deepEqual(workflows[0].steps.map((s) => s.key), ["a", "c"]);
  assert.deepEqual(workflows[0].steps[1].depends, ["a"]);
});

test("after: a duration is a delay, a step name a dependency", () => {
  const { workflows, errors } = manifestToWorkflows(base(["a", "b", { id: "c", after: "+1h" }, { id: "d", after: "a" }]), "alfrd.yaml", { aliases: false });
  assert.deepEqual(errors, []);
  assert.deepEqual(workflows[0].steps.map((s) => s.depends), [[], ["a"], ["b"], ["a"]]);
});
