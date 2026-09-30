// Step picker state (data/step_select.js).
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const S = await import(path.join(path.resolve(here, "../../src/alfrd/web/js"), "data/step_select.js"));

const order = ["a", "b", "c", "d", "e"];
const stage = { a: "pre", b: "pre", c: "avg", d: "cal", e: "cal" };
const groups = S.groupSteps(order, (id) => stage[id], [{ id: "cal", title: "Calibration" }, { id: "pre", title: "Pre" }, { id: "avg" }]);
const make = (selected) => S.createSelection(order, { groups, selected });

test("step select: groups follow the workflow order, titles from the template's stages", () => {
  assert.deepEqual(groups.map((g) => [g.id, g.title, g.steps]), [["pre", "Pre", ["a", "b"]], ["avg", "avg", ["c"]], ["cal", "Calibration", ["d", "e"]]]);
  assert.deepEqual(S.groupSteps(["x"], () => null).map((g) => g.title), ["Other"]);
});

test("step select: master toggles all / none from each state", () => {
  const sel = make(null);
  assert.equal(S.masterState(sel), "all");
  S.toggleAll(sel);
  assert.equal(S.masterState(sel), "none");
  assert.deepEqual(S.selected(sel), []);
  S.toggleAll(sel);
  assert.deepEqual(S.selected(sel), order);
  const some = make(["b"]);
  assert.equal(S.masterState(some), "mixed");
  S.toggleAll(some);
  assert.equal(S.masterState(some), "all", "mixed → all");
  assert.equal(S.countText(some), "5 of 5 selected");
});

test("step select: stage tri-state and invert", () => {
  const sel = make(["a"]);
  assert.equal(S.stageState(sel, "pre"), "mixed");
  S.toggleStage(sel, "pre");
  assert.equal(S.stageState(sel, "pre"), "all");
  S.toggleStage(sel, "pre");
  assert.equal(S.stageState(sel, "pre"), "none");
  S.toggleStage(sel, "cal");
  S.invert(sel);
  assert.deepEqual(S.selected(sel), ["a", "b", "c"]);
});

test("step select: shift range, keyboard extend and from/to keep workflow order", () => {
  const sel = make([]);
  S.toggle(sel, "d");
  S.toggle(sel, "b", { shift: true });
  assert.deepEqual(S.selected(sel), ["b", "c", "d"]);
  S.toggle(sel, "c", { shift: false });
  assert.deepEqual(S.selected(sel), ["b", "d"]);
  S.extendTo(sel, "e"); // anchor c is now off: c..e cleared
  assert.deepEqual(S.selected(sel), ["b"]);
  S.setRange(sel, "b", "d");
  assert.deepEqual(S.selected(sel), ["b", "c", "d"]);
  S.setRange(sel, "", "b");
  assert.deepEqual(S.selected(sel), ["a", "b"]);
  S.setRange(sel, "d", "b");
  assert.deepEqual(S.selected(sel), ["b", "c", "d"], "reversed ends give the same range");
  const clicked = make([]);
  ["e", "a", "c"].forEach((id) => S.toggle(clicked, id));
  assert.deepEqual(S.selected(clicked), ["a", "c", "e"], "workflow order, not click order");
  assert.deepEqual(S.selected(make(["zz", "b"])), ["b"], "unknown ids are dropped");
});
