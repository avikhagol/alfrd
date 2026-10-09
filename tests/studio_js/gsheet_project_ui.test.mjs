// Pure helpers of the Google Sheet project UI; no DOM, no network.
import test from "node:test";
import assert from "node:assert/strict";
import { gidFrom, guessKey, matchCount, statusRules, move, splitField, cleanMapping, sheetURL }
  from "../../examples/plugins/alfrd-gsheet/alfrd_gsheet/web/project_ui.js";

test("gid parsing, key guessing and plan-target matching", () => {
  assert.equal(gidFrom("https://docs.google.com/spreadsheets/d/abc/edit#gid=42"), 42);
  assert.equal(gidFrom("https://docs.google.com/spreadsheets/d/abc/edit?gid=7&x=1"), 7);
  assert.equal(gidFrom("abc"), null);
  const headers = [{ letter: "A", name: "Notes" }, { letter: "B", name: "Source" }, { letter: "C", name: "TARGET_NAME" }];
  assert.equal(guessKey(headers), "TARGET_NAME");
  assert.equal(guessKey(headers, "Source"), "Source");
  assert.equal(guessKey([{ letter: "A", name: "x" }]), "");
  assert.deepEqual(matchCount(["M31", " m33 ", "", "NGC 1"], ["M31", "M33"]), { matched: 2, total: 3 });
});

test("status rules only for same-named headers without a status rule", () => {
  const headers = [{ letter: "A", name: "target" }, { letter: "B", name: "Calibrate" }, { letter: "C", name: "image" }];
  const existing = [{ step: "image", column: "image", field: "status" }];
  assert.deepEqual(statusRules(existing, ["calibrate", "image", "flag"], headers),
    [{ step: "calibrate", column: "Calibrate", field: "status" }]);
  assert.deepEqual(statusRules([...existing, { step: "calibrate", column: "Calibrate", field: "status" }], ["calibrate", "image"], headers), []);
});

test("move keeps bounds; field keys split; draft cleaning drops empty optionals", () => {
  const list = [1, 2, 3];
  assert.equal(move(list, 0, -1), 0); assert.deepEqual(list, [1, 2, 3]);
  assert.equal(move(list, 0, 1), 1); assert.deepEqual(list, [2, 1, 3]);
  assert.deepEqual(splitField("usage.peak_mem"), { base: "usage.*", key: "peak_mem" });
  assert.deepEqual(splitField("results.a.b"), { base: "results.*", key: "a.b" });
  assert.deepEqual(splitField("status"), { base: "status", key: "" });
  const draft = { version: 1, rows: { key_column: "target" }, read_range: "",
    outbound: [{ step: "a", column: "A", field: "status", format: "raw", when: [], template: "x" }],
    inbound: [{ column: "N", to: "plan_cell", step: "a", plan_column: "stale" }] };
  const clean = cleanMapping(draft);
  assert.deepEqual(clean.outbound, [{ step: "a", column: "A", field: "status" }]);
  assert.deepEqual(clean.inbound, [{ column: "N", to: "plan_cell", step: "a" }]);
  assert.equal("read_range" in clean, false);
  assert.equal(draft.outbound[0].template, "x", "the editor draft itself is not mutated");
  assert.equal(sheetURL("a".repeat(20), 3), `https://docs.google.com/spreadsheets/d/${"a".repeat(20)}/edit#gid=3`);
  assert.equal(sheetURL("javascript:alert(1)"), null);
});
