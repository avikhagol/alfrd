// Unit tests for the Studio's browser-side parsers (run with `node --test`).
// Invoked from tests/test_studio.py when Node.js is available.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const web = path.join(root, "src/alfrd/web/js");

const { parseYaml, dumpYaml } = await import(path.join(web, "utils/yaml_parser.js"));
const { parseCsv, resolveAlias, toCsv, setAliasRules } = await import(path.join(web, "utils/csv_parser.js"));
const { manifestToWorkflows, rollup } = await import(path.join(web, "data/model.js"));
const defsMod = await import(path.join(web, "data/defs.js"));
const avicaTemplate = parseYaml(readFileSync(path.join(root, "src/alfrd/web/assets/templates/avica.yaml"), "utf8"));
defsMod.registerTemplate("avica", avicaTemplate);
const AVICA_STEPS = Object.keys(avicaTemplate.steps);
const { parseResultCsv, buildBundle } = await import(path.join(web, "data/importers.js"));
const { demoBundle } = await import(path.join(web, "data/demo.js"));

test("yaml: example manifests parse and round-trip", () => {
  for (const f of ["examples/avica_0.3/alfrd.yaml", "examples/proposed_0_2_1/alfrd.yaml"]) {
    const value = parseYaml(readFileSync(path.join(root, f), "utf8"));
    assert.ok(value.workflows, f);
    assert.deepEqual(parseYaml(dumpYaml(value)), value, f);
  }
});

test("yaml: flow collections, block scalars, legacy entrypoint", () => {
  const v = parseYaml("a: |\n  x\n  y\nb: [1, 'q: r', {c: d}]\nentrypoint:\n  - {name: one, cmd: [never-run]}\n");
  assert.equal(v.a, "x\ny\n");
  assert.deepEqual(v.b, [1, "q: r", { c: "d" }]);
  assert.deepEqual(v.entrypoint, [{ name: "one", cmd: ["never-run"] }]);
  assert.throws(() => parseYaml("a: 1\na: 2\n"), /duplicate key/);
});

test("aliases: only the field_aliases of alfrd.yaml apply", () => {
  setAliasRules({});
  assert.equal(resolveAlias("vasco_avg").alias, null);
  setAliasRules({ vasco_avg: "avica_avg", vascometa_ms: "avicameta_ms", "vasco*": "avica*" });
  assert.deepEqual(resolveAlias("vasco_avg"), { name: "avica_avg", alias: "vasco_avg" });
  assert.deepEqual(resolveAlias("vascometa_ms"), { name: "avicameta_ms", alias: "vascometa_ms" });
  assert.equal(resolveAlias("vasco_new_step").name, "avica_new_step");
  assert.equal(resolveAlias("rpicard").alias, null);
  setAliasRules({});
});

test("template: avica defaults, alfrd.yaml overrides step fields", () => {
  const info = manifestToWorkflows({ name: "p", template: "avica", stages: [{ id: "all", title: "Everything" }], workflows: [{ name: "w", steps: [{ id: "preprocess_fitsidi", stage: "all", label: "Ingest" }, { id: "fits_to_ms", stage: "all" }] }] });
  const [a, b] = info.workflows[0].steps;
  assert.equal(a.label, "Ingest");
  assert.equal(a.category, "Preprocessing"); // from the template
  assert.equal(b.label, "Convert FITS to CASA Measurement Set");
  assert.equal(info.workflows[0].stages.length, 1);
  const plain = manifestToWorkflows({ name: "q", workflows: [{ name: "w", steps: ["preprocess_fitsidi"] }] });
  assert.equal(plain.workflows[0].steps[0].label, "preprocess_fitsidi"); // no template: nothing built in
});

test("manifest: avica_0.3 example becomes the 9-step AVICA workflow", () => {
  const info = manifestToWorkflows(parseYaml(readFileSync(path.join(root, "examples/avica_0.3/alfrd.yaml"), "utf8")));
  assert.deepEqual(info.errors, []);
  assert.deepEqual(info.workflows[0].steps.map((s) => s.key), AVICA_STEPS);
  assert.equal(info.workflows[0].stages.length, 4);
});

test("manifest: unknown dependency is an error", () => {
  const info = manifestToWorkflows({ workflows: [{ name: "w", steps: ["a", { id: "b", depends_on: ["zz"] }] }] });
  assert.match(info.errors.join(), /unknown step "zz"/);
});

test("csv: quoted fields with commas, quotes and newlines", () => {
  const { header, rows } = parseCsv('a,b\n"x, y","he said ""hi""\nthere"\n');
  assert.deepEqual(header, ["a", "b"]);
  assert.equal(rows[0].a, "x, y");
  assert.equal(rows[0].b, 'he said "hi"\nthere');
  assert.equal(parseCsv(toCsv([["k"], ["a,b"]])).rows[0].k, "a,b");
});

test("result csv: latest attempt wins, retries kept, statuses match the server", () => {
  const text = readFileSync(path.join(root, "tests/fixtures/avica_run/reductions/TARGET_A_result.csv"), "utf8");
  const r = parseResultCsv(text, { file: "TARGET_A_result.csv" });
  assert.equal(r.error, null);
  assert.equal(r.steps.avica_avg.status, "completed");
  assert.equal(r.steps.avica_avg.attempts.length, 2);
  assert.equal(r.steps.rpicard.status, "failed");
  assert.match(r.steps.rpicard.note, /calibration failed/);
  assert.equal(r.steps.fits_to_ms.duration, 1);
  const roll = rollup({ steps: r.steps }, AVICA_STEPS);
  assert.equal(roll.status, "failed");
  assert.equal(roll.failed.key, "rpicard");
});

test("bundle: legacy names in result csv are reported", () => {
  const csv = "name,success_count,failed_count,start_stamp,detail,desc,success,end_stamp\nvasco_avg,1,0,2026-01-01T00:00:00,,,[true],2026-01-01T00:00:05\n";
  const b0 = buildBundle([{ path: "reductions/T1_result.csv", name: "T1_result.csv", size: csv.length, text: csv }], { rootName: "P1" });
  assert.equal(b0.targets.length, 1);
  assert.equal(b0.targets[0].name, "T1");
  assert.equal(b0.targets[0].project, "P1"); // ALFRD project = root folder when there is no alfrd.yaml
  assert.ok(b0.targets[0].steps.vasco_avg); // no alfrd.yaml → no aliases
  const yaml = "name: P2\nproject_settings:\n  field_aliases:\n    vasco_avg: avica_avg\nworkflows:\n  - name: w\n    steps: [avica_avg]\n";
  const b = buildBundle([{ path: "alfrd.yaml", name: "alfrd.yaml", size: 1, text: yaml }, { path: "T1_result.csv", name: "T1_result.csv", size: csv.length, text: csv }]);
  assert.ok(b.targets[0].steps.avica_avg);
  assert.deepEqual(b.aliases.map((a) => a.from), ["vasco_avg"]);
});

test("demo: one ALFRD project, 18 targets under 3 AVICA project codes", () => {
  const b = demoBundle();
  assert.equal(b.targets.length, 18);
  assert.deepEqual([...new Set(b.targets.map((t) => t.project))], ["avica-demo"]);
  assert.deepEqual([...new Set(b.targets.flatMap((t) => t.codes.map((c) => c.code)))].sort(), ["BB049", "BT085", "BW112"]);
  assert.equal(b.aliases.length, 2);
  assert.deepEqual(Object.keys(b.avica.codes).sort(), ["BB049", "BT085", "BW112"]);
  assert.equal(b.avica.summary.rows.find((r) => r.parameter === "target_dir").value, "reductions/");
});

test("avica layout: entrypoint manifest, _result.csv, avica.inp params, .avica sidecars", () => {
  const base = path.join(root, "tests/fixtures/avica_layout");
  const read = (rel) => ({ path: rel, name: path.basename(rel), size: 0, text: readFileSync(path.join(base, rel), "utf8") });
  const b = buildBundle([
    read("alfrd.yaml"),
    read("avica.inp"),
    read("reduction/_result.csv"),
    read("wd/avica.meta/msmeta_sources_X_J0319+4130.avica"),
  ]);
  const wf = b.workflowInfo.workflows[0];
  assert.deepEqual(wf.steps.map((s) => s.key), AVICA_STEPS);
  assert.equal(wf.entrypoints[0].command.join(" "), "avica pipe run");
  assert.equal(b.targets.length, 1);
  assert.equal(b.targets[0].name, "J0319+4130"); // untargeted _result.csv falls back to avica.inp target
  assert.equal(b.targets[0].steps.preprocess_fitsidi.status, "failed");
  assert.equal(b.avica.values["rpicard.mpi_cores"], 10);
  assert.equal(b.configs[0].values.target_dir, "reduction");
  assert.equal(b.configs[0].values.sheet_url, null);
  // wd/avica.meta sits outside a <target_dir>/<CODE>/wd layout here, so no code folder is detected.
  assert.equal(Object.keys(b.avica.codes).length, 0);
});

const { parseConfigSummary, avicaInterest, workdirFromIndex } = await import(path.join(web, "data/avica.js"));
import { readdirSync, statSync } from "node:fs";

function walk(dir, base = dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) walk(full, base, out);
    else out.push(full);
  }
  return out;
}

test("summary: rich table with wrapped cells", () => {
  const rows = parseConfigSummary(readFileSync(path.join(root, "tests/fixtures/avica_tree/avica.summary.txt"), "utf8"));
  const get = (step, p) => rows.find((r) => r.step === step && r.parameter === p);
  assert.equal(get("fits_to_ms", "mpi_cores").value, 5);
  assert.equal(get("rpicard", "picard_input_template_update").value, "input_temp_update");
  assert.equal(get("other", "size_limit").value, 2000);
});

test("avica tree: target_dir, project codes, avica.meta, templates and superseded keys", () => {
  const base = path.join(root, "tests/fixtures/avica_tree");
  const files = walk(base).map((full) => {
    const rel = path.relative(base, full).split(path.sep).join("/");
    const name = path.basename(full);
    return { rel, path: rel, name, size: statSync(full).size, interest: avicaInterest(rel, name, statSync(full).size), text: readFileSync(full, "utf8") };
  }).filter((f) => f.interest);
  const b = buildBundle(files, { rootName: "vasco_0.3" });
  assert.equal(b.alfrdProject, "avica-t-0.3");
  assert.equal(b.avica.targetDir, "reductions");
  assert.deepEqual(Object.keys(b.avica.codes).sort(), ["BV019/wd", "BV019/wd_1", "RDV41"]); // wd_{n} from alfrd.yaml
  const t = b.targets.find((x) => x.name === "0742+103");
  assert.deepEqual(t.codes.map((c) => c.code), ["RDV41"]);
  assert.ok(t.codes[0].auto);
  const wd = workdirFromIndex(b.avica, "RDV41", "0742+103");
  assert.ok(wd.meta.some((m) => m.name === "refants_X_0742+103.avica" && m.band === "X"));
  const finetune = wd.templates.find((x) => x.file === "array_finetune.inp");
  assert.equal(finetune.values.accor_solint, "int");
  assert.equal(finetune.updated.accor_solint, 10);
  assert.equal(b.avica.update.folder, "input_temp_update");
  assert.ok(b.avica.logs.some((l) => l.kind === "crash" && l.step === "rpicard" && l.crash.target === "0742+103"));
  assert.equal(b.avica.summary.file, "avica.summary.txt");
});

test("avica interest: skips measurement sets and notebooks, keeps logs lazily", () => {
  assert.equal(avicaInterest("reductions/RDV41/wd/VLBI.ms/table.dat", "table.dat", 10), null);
  assert.equal(avicaInterest("__marimo__/session/name.py.json", "name.py.json", 10), null);
  assert.equal(avicaInterest("avica.logs/avica__log-1.log", "avica__log-1.log", 10), "lazy");
  assert.equal(avicaInterest("reductions/RDV41/wd/avica.meta/listobs.json", "listobs.json", 10), "eager");
  assert.equal(avicaInterest("reductions/RDV41/wd/wd_X_0742+103/input_template_X_0742+103/array.inp", "array.inp", 10), "eager");
});

// Minimal File System Access API mock over a real folder (records every listing).
function fsHandle(full, listed) {
  const { readdirSync, statSync: st, readFileSync: rd } = require_fs;
  const name = path.basename(full);
  if (st(full).isDirectory()) {
    return {
      kind: "directory", name,
      async *entries() {
        listed.push(full);
        for (const n of readdirSync(full)) yield [n, fsHandle(path.join(full, n), listed)];
      },
      async getDirectoryHandle(n) { const p = path.join(full, n); if (!existsSync(p) || !st(p).isDirectory()) throw new Error("NotFound"); return fsHandle(p, listed); },
      async getFileHandle(n) { const p = path.join(full, n); if (!existsSync(p) || !st(p).isFile()) throw new Error("NotFound"); return fsHandle(p, listed); },
    };
  }
  return {
    kind: "file", name,
    async getFile() {
      const buf = rd(full);
      const file = { name, size: buf.length, text: async () => buf.toString("utf8"), slice: (a) => ({ text: async () => buf.subarray(a).toString("utf8") }) };
      return file;
    },
  };
}
import * as require_fs from "node:fs";
import { existsSync, mkdtempSync, cpSync, mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";

test("folder scan: reads only what alfrd.yaml points to, same bundle as a full read", async () => {
  const { scanProjectFolder, expandPatterns } = await import(path.join(web, "data/folder_scan.js"));
  const { entriesFromScanBundle } = await import(path.join(web, "data/importers.js"));
  const tmp = mkdtempSync(path.join(os.tmpdir(), "alfrd-scan-"));
  const base = path.join(tmp, "vasco_0.3");
  cpSync(path.join(root, "tests/fixtures/avica_tree"), base, { recursive: true });
  mkdirSync(path.join(base, "reductions/RDV41/wd/wd_S"), { recursive: true });
  mkdirSync(path.join(base, "reductions/RDV41/wd/VLBI.ms/ANTENNA"), { recursive: true });
  writeFileSync(path.join(base, "reductions/RDV41/wd/VLBI.ms/ANTENNA/table.dat"), "x");
  mkdirSync(path.join(base, "raw/fits"), { recursive: true });

  const listed = [];
  const entries = await scanProjectFolder(fsHandle(base, listed));
  assert.ok(!listed.some((p) => /\.ms|raw/.test(p)), listed.join("\n"));
  const b = buildBundle(entries, { rootName: entries.rootName });
  assert.equal(b.alfrdProject, "avica-t-0.3");
  assert.equal(b.avica.targetDir, "reductions");
  assert.deepEqual(Object.keys(b.avica.codes).sort(), ["BV019/wd", "BV019/wd_1", "RDV41"]);
  assert.deepEqual(b.avica.codes.RDV41.bands, ["S", "X"]);
  assert.ok(b.targets.some((t) => t.name === "0742+103"));
  assert.equal(b.avica.update.folder, "input_temp_update");
  assert.ok(b.avica.logs.some((l) => l.kind === "log" && l.file));
  assert.equal(b.avica.summary.file, "avica.summary.txt");
  assert.ok(!b.files.some((f) => f.path.endsWith("/.dir")));

  // Parent folder picked: alfrd.yaml is found one level down.
  const up = await scanProjectFolder(fsHandle(tmp, []));
  assert.equal(up.rootName, "vasco_0.3");

  const tpl = await expandPatterns(fsHandle(path.join(base, "reductions/RDV41/wd"), []), ["wd_{band}_{target}/input_template_{band}_{target}"]);
  assert.deepEqual(tpl.map((t) => t.rel), ["wd_X_0742+103/input_template_X_0742+103"]);

  // `alfrd avica scan --bundle` JSON → the same entries.
  const scan = entriesFromScanBundle({ alfrd_avica_scan: 1, root_name: "vasco_0.3", files: entries.filter((e) => e.text !== undefined || e.marker).map((e) => ({ rel: e.rel, size: e.size, text: e.text, hint: e.hint, marker: e.marker })) });
  const b2 = buildBundle(scan, { rootName: scan.rootName });
  assert.deepEqual(Object.keys(b2.avica.codes).sort(), Object.keys(b.avica.codes).sort());
  assert.equal(b2.targets.length, b.targets.length);
});

test("alfrd.yaml logs, metadata health and MS paths (folder scan)", async () => {
  const { scanProjectFolder } = await import(path.join(web, "data/folder_scan.js"));
  const { metadataHealth } = defsMod;
  const tmp = mkdtempSync(path.join(os.tmpdir(), "alfrd-logs-"));
  const base = path.join(tmp, "proj");
  cpSync(path.join(root, "tests/fixtures/avica_tree"), base, { recursive: true });
  mkdirSync(path.join(base, "reductions/RDV41/wd/wd_X_0742+103/VLBI_X.ms/ANTENNA"), { recursive: true });
  writeFileSync(path.join(base, "reductions/RDV41/wd/wd_X_0742+103/VLBI_X.ms/ANTENNA/table.dat"), "x");
  mkdirSync(path.join(base, "reductions/RDV41/wd/wd_X_0742+103/diagnostics_1"), { recursive: true });
  writeFileSync(path.join(base, "reductions/RDV41/wd/wd_X_0742+103/diagnostics_1/casa.log_1"), "rpicard log\n");
  const listed = [];
  const entries = await scanProjectFolder(fsHandle(base, listed));
  assert.ok(!listed.some((p) => /\.ms($|\/)/.test(p)), listed.join("\n"));
  const b = buildBundle(entries, { rootName: entries.rootName });
  const byRel = Object.fromEntries(b.logFiles.map((f) => [f.rel, f]));
  const avg = byRel["reductions/RDV41/wd/wd_S/avica_avg_casa_log-20260908_180141.log"];
  assert.deepEqual(avg.steps, ["avica_avg"]);
  assert.equal(avg.band, "S");
  assert.equal(avg.workdir, "RDV41");
  assert.ok(avg.file && !avg.text); // listed only, read on open
  const rp = byRel["reductions/RDV41/wd/wd_X_0742+103/diagnostics_1/casa.log_1"];
  assert.equal(rp.target, "0742+103");
  assert.ok(byRel["avica.logs/avica_crash_rpicard.json"].steps.includes("rpicard"));
  const t = b.targets.find((x) => x.name === "0742+103");
  assert.equal(t.msPath, "reductions/RDV41/wd/wd_X_0742+103/VLBI_X.ms"); // overview.ms_path
  const wd = workdirFromIndex(b.avica, "RDV41", "0742+103");
  const health = metadataHealth(b.defs, wd, t.name, t.steps, b.workflowInfo.workflows[0].steps.map((s) => s.key));
  const pre = health.find((h) => h.step === "preprocess_fitsidi");
  assert.equal(pre.entries.find((e) => e.pattern === "fitsfiles_used.avica").status, "passed");
  const snr = health.find((h) => h.step === "avica_snr");
  assert.equal(snr.entries.find((e) => e.pattern.startsWith("refants_")).files[0].name, "refants_X_0742+103.avica");
  assert.deepEqual(snr.entries.find((e) => e.pattern.startsWith("refants_")).missingBands, ["S"]);
});

test("project settings: field_aliases block rewritten in place", async () => {
  const { setProjectSettings } = await import(path.join(web, "components/alfrd_config.js"));
  const text = readFileSync(path.join(root, "tests/fixtures/avica_tree/alfrd.yaml"), "utf8");
  const out = setProjectSettings(text, { field_aliases: { old: "new" } });
  const v = parseYaml(out);
  assert.deepEqual(v.project_settings, { field_aliases: { old: "new" } });
  assert.equal(v.avica.target_dir, "reductions/");
  assert.match(out, /# Workflow stages/); // comments elsewhere survive
});

test("scan bundle: a picked scan.json is read whole, not dropped by the layout filter", async () => {
  const { readFiles, entriesFromScanBundle } = await import(path.join(web, "data/importers.js"));
  const bundleJson = JSON.stringify({
    alfrd_avica_scan: 1, root: "/data/vasco_0.3", root_name: "vasco_0.3", target_dir: "reductions",
    files: [{ rel: "alfrd.yaml", size: 20, mtime: 1, text: readFileSync(path.join(root, "tests/fixtures/avica_tree/alfrd.yaml"), "utf8") }],
    ms_paths: [],
  });
  const read = await readFiles([new File([bundleJson], "scan.json")]);
  assert.equal(read.length, 1);
  assert.equal(read.ignored, 0);
  assert.match(read[0].text, /"alfrd_avica_scan"/);
  const entries = entriesFromScanBundle(JSON.parse(read[0].text));
  assert.equal(entries.rootName, "vasco_0.3");
  assert.equal(buildBundle(entries, { rootName: entries.rootName }).manifestFile, "alfrd.yaml");
  // avica.summary.json keeps going through the AVICA filter as before.
  const summary = await readFiles([new File(["{\"rows\":[]}"], "avica.summary.json")]);
  assert.equal(summary.length, 1);
});
