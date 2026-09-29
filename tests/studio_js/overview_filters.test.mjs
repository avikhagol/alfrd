// Overview filters (pure functions in data/filters.js). Run with `node --test`.
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "../../src/alfrd/web/js");
const { filterTargets, activeFilters, FILTER_DEFAULTS, NO_CODE } = await import(path.join(web, "data/filters.js"));

const targets = [
  { id: "p/A", name: "A", project: "p", status: "completed", codes: ["BV019"], text: "a 3c274" },
  { id: "p/B", name: "B", project: "p", status: "failed", codes: ["BV019", "RDV41"], text: "b" },
  { id: "p/C", name: "C", project: "p", status: "warning", codes: [], text: "c j0742" },
  { id: "q/D", name: "D", project: "q", status: "unknown", codes: ["RDV41"], text: "d" },
];
const helpers = { rollup: (t) => ({ status: t.status }), codes: (t) => t.codes, text: (t) => t.text };
const names = (ui) => filterTargets(targets, { ...FILTER_DEFAULTS, ...ui }, helpers).map((t) => t.name);

test("filters: defaults keep everything", () => {
  assert.deepEqual(names({}), ["A", "B", "C", "D"]);
});

test("filters: status, code, project, preset and search combine", () => {
  assert.deepEqual(names({ status: "failed" }), ["B"]);
  assert.deepEqual(names({ code: "RDV41" }), ["B", "D"]);
  assert.deepEqual(names({ code: NO_CODE }), ["C"]);
  assert.deepEqual(names({ project: "q" }), ["D"]);
  assert.deepEqual(names({ preset: "attention" }), ["A", "B", "C", "D"], "an unknown (old, saved) preset filters nothing");
  assert.deepEqual(names({ preset: "nometa" }), ["C"]);
  assert.deepEqual(names({ search: "  J0742 " }), ["C"]);
  assert.deepEqual(names({ code: "BV019", preset: "nometa" }), []);
  assert.deepEqual(names({ code: "BV019", status: "warning" }), []);
});

test("filters: active chips, with labels, and clear-all defaults", () => {
  const ui = { ...FILTER_DEFAULTS, status: "failed", code: NO_CODE, search: "x", preset: "nometa" };
  const chips = activeFilters(ui, { status: { failed: "Failed" }, preset: { nometa: "No work folder" } });
  assert.deepEqual(chips.map((c) => c.key), ["status", "preset", "code", "search"]);
  assert.equal(chips[0].label, "Status: Failed");
  assert.equal(chips[2].label, "Code: none");
  assert.deepEqual(activeFilters(FILTER_DEFAULTS), []);
  assert.deepEqual(activeFilters({ ...FILTER_DEFAULTS, preset: "attention" }), [], "no chip for a preset that no longer exists");
});
