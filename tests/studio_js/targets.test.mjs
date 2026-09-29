// Targets CSV in the browser (data/targets.js mirrors alfrd/targets_csv.py). Run with `node --test`.
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const T = await import(join(here, "../../src/alfrd/web/js/data/targets.js"));

const spec = T.targetsSpec({}, {});

test("spec: written names from execution, aliases from targets.columns", () => {
  assert.equal(spec.csv, "alfrd.targets.csv");
  assert.deepEqual(spec.written, { key: "TARGET_NAME", files: "FILENAMES", code: "PROJECT_CODE" });
  const own = T.targetsSpec({ targets: { csv: "./lists/s.csv", columns: { key: ["src"] } } }, { files_column: "FITS" });
  assert.equal(own.csv, "lists/s.csv");
  assert.deepEqual(own.aliases.key, ["TARGET_NAME", "src"]);
  assert.equal(own.aliases.files[0], "FITS");
});

test("parse: aliases, file separators, line numbers, problems", () => {
  const text = "\ufeffSource,fitsfilenames,project_code,notes\n3C274,\"a.idifits; b.idifits\",BW106,x\n\nJ0742+103,c d,,\n3C274,e,BW106,\n#x,f,,\nA@B,g,,\n";
  const out = T.parseTargets(text, spec);
  assert.deepEqual(out.columns, { key: "Source", files: "fitsfilenames", code: "project_code" });
  assert.deepEqual(out.rows.map((r) => [r.target, r.files, r.code]), [["3C274", "a.idifits,b.idifits", "BW106"], ["J0742+103", "c,d", ""]]);
  assert.deepEqual(out.rows[0].extra, { notes: "x" });
  assert.deepEqual(out.problems.map((p) => p.line), [5, 6, 7]);
  assert.match(out.problems[0].message, /repeats line 2/);
});

test("parse: TSV, explicit mapping, no key column", () => {
  assert.equal(T.parseTargets("target\tfits\nT1\ta.fits\n", spec).rows[0].files, "a.fits");
  const mapped = T.parseTargets("obj,idi\nT1,a\n", spec, { key: "obj", files: "idi" });
  assert.deepEqual(mapped.rows[0], { target: "T1", files: "a", code: "", extra: {} });
  assert.match(T.parseTargets("x,y\n1,2\n", spec).problems[0].message, /no target column/);
});

test("merge keeps rows and known files; replace reports removed", () => {
  const old = [{ target: "A", files: "a1", code: "BV019", extra: {} }, { target: "B", files: "b1", code: "", extra: {} }];
  const m = T.mergeTargets(old, [{ target: "B", files: "", code: "", extra: {} }, { target: "C", files: "c", code: "", extra: {} }]);
  assert.deepEqual([m.added, m.updated, m.unchanged], [["C"], [], ["B"]]);
  assert.equal(m.rows[1].files, "b1");
  const r = T.mergeTargets(old, [{ target: "B", files: "b2", code: "", extra: {} }], "replace");
  assert.deepEqual([r.updated, r.removed, r.rows.length], [["B"], ["A@BV019"], 1]);
});

test("dump uses the plan CSV's column names and matches the Python writer", () => {
  const text = T.dumpTargets(spec, [{ target: "A", files: "a1,a2", code: "BV019", extra: { notes: "x" } }, { target: "B", files: "", code: "", extra: {} }]);
  assert.equal(text, 'TARGET_NAME,FILENAMES,PROJECT_CODE,notes\nA,"a1,a2",BV019,x\nB,,,\n');
  // Round trip.
  assert.deepEqual(T.parseTargets(text, spec).rows.map((r) => r.files), ["a1,a2", ""]);
});

test("import: the targets file is a dataset table, and its FITS names / codes win", async () => {
  const { buildBundle } = await import(join(here, "../../src/alfrd/web/js/data/importers.js"));
  const plan = "TARGET_NAME,FILENAMES,PROJECT_CODE,fits_to_ms\nT1,old.idifits,,done\n";
  const targets = "TARGET_NAME,FILENAMES,PROJECT_CODE\nT1,new.idifits,BV019\nT1,other.idifits,RDV41\nT2,t2.idifits,\n";
  const entries = [
    { path: "alfrd.targets.csv", rel: "alfrd.targets.csv", name: "alfrd.targets.csv", size: 1, text: targets },
    { path: "alfrd.plan.csv", rel: "alfrd.plan.csv", name: "alfrd.plan.csv", size: 1, text: plan },
  ];
  const b = buildBundle(entries, { rootName: "P" });
  assert.deepEqual(b.tables.map((t) => [t.path, Boolean(t.targets)]), [["alfrd.plan.csv", false], ["alfrd.targets.csv", true]]);
  const t1 = b.targets.find((t) => t.name === "T1");
  assert.equal(t1.fitsidi, "other.idifits");
  assert.deepEqual(t1.codes.map((c) => c.code).sort(), ["BV019", "RDV41"]);
  assert.ok(b.targets.some((t) => t.name === "T2"));
  assert.equal(b.targetsFile.rel, "alfrd.targets.csv");
});

test("drop: a bare name removes every code of it, target@code just that row", () => {
  const rows = [{ target: "A", code: "C1" }, { target: "A", code: "C2" }, { target: "B", code: "" }];
  const one = T.dropTargets(rows, ["A@C2", "Z"]);
  assert.deepEqual(one.removed, ["A@C2"]);
  assert.deepEqual(one.missing, ["Z"]);
  const all = T.dropTargets(rows, ["A", "B"]);
  assert.deepEqual(all.rows, []);
  assert.deepEqual(all.removed, ["A@C1", "A@C2", "B"]);
});
