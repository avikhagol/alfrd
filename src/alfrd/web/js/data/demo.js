// Deterministic demo dataset. It is generated as the same *files* a real
// AVICA tree contains (datasets.csv, <TARGET>_result.csv, avica.meta,
// avica.inp) and then parsed by the real importers, so "Demo data" exercises
// exactly the code path used for imported results.

import { toCsv } from "../utils/csv_parser.js";
import { buildBundle } from "./importers.js";

// The demo's alfrd.yaml (template: avica supplies labels, stages, metadata and logs).
export const DEMO_MANIFEST = `# Demo ALFRD project (in-memory; shown with \`alfrd serve --demo\` or ?demo=1)
name: avica-demo
description: AVICA demo reductions
template: avica
project_settings:
  field_aliases:
    vasco_avg: avica_avg
    vascometa_ms: avicameta_ms
workflows:
  - name: avica
    label: AVICA batch VLBI calibration
    steps:
      - preprocess_fitsidi
      - fits_to_ms
      - phaseshift
      - vasco_avg
      - vascometa_ms
      - avica_snr
      - {id: avica_fill_input, depends_on: [avica_snr]}
      - {id: avica_split_ms, depends_on: [avica_snr]}
      - {id: rpicard, depends_on: [avica_fill_input, avica_split_ms]}
`;
const DEMO_STEPS = ["preprocess_fitsidi", "fits_to_ms", "phaseshift", "avica_avg", "avicameta_ms", "avica_snr", "avica_fill_input", "avica_split_ms", "rpicard"];

// Typical seconds per step (mean) used to jitter demo durations.
const TYPICAL = [41, 72, 19, 22, 34, 62, 18, 45, 1500];

function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

// fate: number of completed steps, then what happens at the next one.
const TARGETS = [
  { p: "BW112", n: "J1440+0127", done: 5, next: "fail", retries: 1, legacy: true,
    note: "SNR 4.2σ below threshold (min_snr: 5.0σ). Calibration fringe peak non-convergent on baseline LA-PT.",
    ms: "/scratch/vlbi/BW112/J1440+0127.ms", fits: "BW112_raw_J1440.fitsidi", ra: "14h40m12.4s", dec: "+01°27′31″", freq: "15.37 GHz (U-Band)", base: 45, meta: true },
  { p: "BW112", n: "BW112_01", done: 9, legacy: true, ms: "/scratch/vlbi/BW112/BW112_01.ms", fits: "BW112_raw_01.fitsidi", meta: true },
  { p: "BW112", n: "J11109+1704", done: 8, legacy: true, ms: "/scratch/vlbi/BW112/J11109+1704.ms", fits: "BW112_raw_J1110.fitsidi", meta: true },
  { p: "BW112", n: "BW112_03", done: 9, legacy: true, ms: "/scratch/vlbi/BW112/BW112_03.ms", fits: "BW112_raw_03.fitsidi", meta: true },
  { p: "BW112", n: "BW112_04", done: 9, legacy: true, ms: "/scratch/vlbi/BW112/BW112_04.ms", fits: "BW112_raw_04.fitsidi", meta: false },
  { p: "BW112", n: "J1419+0628", done: 9, legacy: true, ms: "/scratch/vlbi/BW112/J1419+0628.ms", fits: "BW112_raw_J1419.fitsidi", meta: true },
  { p: "BB049", n: "0326+277", done: 5, next: "running", ms: "/data/archive/BB049/0326+277.ms", fits: "/data/archive/BB049/0326+277.fitsidi",
    ra: "03h26m13.9s", dec: "+27°43′51″", freq: "4.988 GHz (C-Band)", base: 45, corr: "VLBA FX Correlator", meta: true },
  { p: "BB049", n: "J0336+3218", done: 9, ms: "/data/archive/BB049/J0336+3218.ms", fits: "BB049_J0336.fitsidi", meta: true },
  { p: "BB049", n: "J0339+2605", done: 9, ms: "/data/archive/BB049/J0339+2605.ms", fits: "BB049_J0339.fitsidi", meta: true },
  { p: "BB049", n: "0316+413", done: 9, ms: "/data/archive/BB049/0316+413.ms", fits: "BB049_0316.fitsidi", meta: true },
  { p: "BB049", n: "J0319+4130", done: 3, next: "partial", note: "S-band averaging failed: no valid channels after flagging (X ok).",
    ms: "/data/archive/BB049/J0319+4130.ms", fits: "BB049_J0319.fitsidi", meta: false },
  { p: "BB049", n: "J0322+2848", done: 9, ms: "/data/archive/BB049/J0322+2848.ms", fits: "BB049_J0322.fitsidi", meta: true },
  { p: "BB049", n: "J0329+2756", done: 9, ms: "/data/archive/BB049/J0329+2756.ms", fits: "BB049_J0329.fitsidi", meta: true },
  { p: "BB049", n: "J0331+2815", done: 9, ms: "/data/archive/BB049/J0331+2815.ms", fits: "BB049_J0331.fitsidi", meta: true },
  { p: "BT085", n: "J0006-0623", done: 1, next: "partial", note: "missing IF4 — 3 of 4 IFs converted",
    ms: "/srv/evn/BT085/J0006-0623.ms", fits: "BT085_J0006.fitsidi", meta: false },
  { p: "BT085", n: "BT085_ref", done: 9, ms: "/srv/evn/BT085/ref_cal.ms", fits: "BT085_ref.fitsidi", meta: true },
  { p: "BT085", n: "BT085_02", done: 8, next: "fail", note: "Unhandled exception: calibration failed (rPicard exit 1, fringe-fit diverged on EF-WB).",
    ms: "/srv/evn/BT085/BT085_02.ms", fits: "BT085_02.fitsidi", meta: true },
  { p: "BT085", n: "BT085_03", done: 0, ms: "/srv/evn/BT085/BT085_03.ms", fits: "BT085_03.fitsidi", meta: false },
];

function resultCsv(t, index) {
  const rand = rng(1000 + index * 77);
  const rows = [["name", "success_count", "failed_count", "start_stamp", "detail", "desc", "success", "end_stamp"]];
  let clock = Date.UTC(2026, 8, 1 + (index % 20), 8 + (index % 6), 0, 0);
  const stamp = () => new Date(clock).toISOString().slice(0, 19);
  const name = (step) => {
    if (!t.legacy) return step;
    if (step === "avica_avg") return "vasco_avg";
    if (step === "avicameta_ms") return "vascometa_ms";
    return step;
  };
  const emit = (step, ok, failed, desc, detail, success, dur) => {
    const start = stamp();
    clock += dur * 1000;
    rows.push([name(step), ok, failed, start, detail ? JSON.stringify(detail) : "", JSON.stringify(desc), JSON.stringify(success), stamp()]);
    clock += 2000;
  };
  DEMO_STEPS.forEach((step, i) => {
    const dur = Math.round(TYPICAL[i] * (0.7 + rand() * 0.8));
    if (i < t.done) {
      const detail = step === "fits_to_ms" ? { 0: t.ms } : step === "rpicard" ? { X: `outputs/X_calibrated.uvf`, S: `outputs/S_calibrated.uvf` } : null;
      emit(step, step === "avica_avg" || step === "rpicard" ? 2 : 1, 0, step === "avica_avg" ? ["X", "S"] : ["done"], detail,
        step === "avica_avg" || step === "rpicard" ? [true, true] : [true], dur);
    } else if (i === t.done && t.next === "fail") {
      for (let r = 0; r <= (t.retries || 0); r += 1) {
        emit(step, 0, 1, [t.note], null, [false], dur + r * 30);
      }
    } else if (i === t.done && t.next === "partial") {
      emit(step, 1, 1, [t.note], null, [true, false], dur);
    }
  });
  return toCsv(rows);
}

// The demo's ALFRD project (alfrd.yaml name). BW112/BB049/BT085 are AVICA project codes.
export const DEMO_ALFRD_PROJECT = "avica-demo";

const FREQ = { BW112: [15.2, 15.3, 15.4, 15.5], BB049: [4.93, 4.96, 4.99, 5.02], BT085: [4.93, 4.96, 4.99, 5.02] };

function metaFiles(t) {
  const code = t.p;
  const base = `reductions/${code}/wd/avica.meta`;
  const json = (name, data) => ({ path: `${base}/${name}`, name, size: JSON.stringify(data).length, text: JSON.stringify(data) });
  const ants = ["BR", "FD", "HN", "KP", "LA", "MK", "NL", "OV", "PT", "SC"];
  return [
    json(`msmeta_sources_X_${t.n}.avica`, {
      c_target: t.ra ? `${t.ra} ${t.dec}` : null,
      bands_dict: { X: { spws: [0, 1, 2, 3], reffreqs: FREQ[code].map((f) => f * 1e9), nobs: 1 } },
      other_sources: ["4C39.25", "0823+033", "OK290"],
      scanlist_seq: [1, 2, 3, 4, 5, 6],
    }),
    json(`refants_X_${t.n}.avica`, { refant: t.n === "J1440+0127" ? ["LA", "PT", "FD"] : ants.slice(0, 5) }),
    json(`sources_snr_X_${t.n}.avica`, { X: { calibrators_instrphase: ["4C39.25", "OK290"], calibrators_bandpass: ["4C39.25"], calibrators_rldly: null, calibrators_dterms: null, calibrators_phaseref: t.n === "J0319+4130" ? null : ["J1438+0114"], science_target: [t.n] } }),
    json(`sources_X_${t.n}.avica`, { FIELD_ID: [3, 7, 11], NAME: ["4C39.25", "OK290", "0823+033"], SNR: [45.9, t.n === "J1440+0127" ? 4.2 : 38.1, 22.6] }),
  ];
}

function templateFiles(t) {
  const wd = `reductions/${t.p}/wd`;
  const inp = (path, lines) => ({ path, name: path.split("/").pop(), size: 0, text: `${lines.join("\n")}\n` });
  return [
    inp(`${wd}/input_template/array.inp`, ["array_type = generic", "refant = LA,FD,KP,PT,OV"]),
    inp(`${wd}/input_template/array_finetune.inp`, ["fringe_solint_optimize_search_cal = 56;60;84;120", "fringe_solint_optimize_search_sci = estimate", "accor_solint = int", "fringe_minSNR_mb_short_cal = 3.5"]),
    inp(`${wd}/wd_X_${t.n}/input_template_X_${t.n}/array.inp`, ["array_type = generic", `refant = ${t.n === "J1440+0127" ? "LA,PT,FD" : "BR,FD,HN"}`]),
    inp(`${wd}/wd_X_${t.n}/input_template_X_${t.n}/array_finetune.inp`, ["fringe_solint_optimize_search_cal = 56;60;84;120", "accor_solint = int", "fringe_minSNR_mb_short_cal = 3.5"]),
  ];
}

const SUMMARY_ROWS = [
  ["preprocess_fitsidi", "removables", "default/step", ["raw/*.tmp"]],
  ["preprocess_fitsidi", "rm_pre", "default/step", false],
  ["fits_to_ms", "mpi_cores", "default/step", 5],
  ["avica_avg", "mpi_cores", "default/step", 5],
  ["avica_avg", "drop_dead_pol", "default/step", "auto"],
  ["avica_snr", "mpi_cores", "default/step", 5],
  ["avica_snr", "snr_threshold_phref", "inpfile/core", 7],
  ["avica_snr", "flux_threshold_phref", "default/core", 0.15],
  ["avica_split_ms", "mpi_cores", "default/step", 10],
  ["rpicard", "mpi_cores", "inpfile/step", 3],
  ["rpicard", "picard_input_template_update", "inpfile/core", "input_temp_update"],
  ["other", "target_dir", "inpfile/core", "reductions/"],
  ["other", "folder_for_fits", "inpfile/core", "/data/fits/"],
  ["other", "size_limit", "default/core", 2000.0],
].map(([step, parameter, source, value]) => ({ step, parameter, source, value }));

/** Build the demo import bundle (an ALFRD project folder in memory). */
export function demoBundle() {
  const table = [["TARGET_NAME", "PROJECT_CODE", "FILENAMES", "MS_PATH", "RA", "DEC", "FREQ", "BASELINES", "CORRELATOR"]];
  const files = [];
  const listobs = new Set();
  TARGETS.forEach((t, i) => {
    table.push([t.n, t.p, t.fits, t.ms, t.ra || "", t.dec || "", t.freq || "", t.base || "", t.corr || ""]);
    if (t.done > 0 || t.next) {
      files.push({ path: `reductions/${t.n}_result.csv`, name: `${t.n}_result.csv`, size: 0, text: resultCsv(t, i) });
    }
    if (t.meta) {
      files.push(...metaFiles(t), ...templateFiles(t));
      if (!listobs.has(t.p)) {
        listobs.add(t.p);
        const text = JSON.stringify({ filepath: [`${t.p}_raw_file1.fitsidi`, `${t.p}_raw_file2.fitsidi`] });
        files.push({ path: `reductions/${t.p}/wd/avica.meta/fitsfiles_used.avica`, name: "fitsfiles_used.avica", size: text.length, text });
      }
    }
  });
  files.unshift({ path: "datasets.csv", name: "datasets.csv", size: 0, text: toCsv(table) });
  files.unshift({ path: "alfrd.yaml", name: "alfrd.yaml", size: DEMO_MANIFEST.length, text: DEMO_MANIFEST });
  const demoLog = (path, lines) => files.push({ path, name: path.split("/").pop(), size: lines.join("\n").length, text: `${lines.join("\n")}\n` });
  demoLog("reductions/BW112/wd/wd_X/avica_avg_casa_log-20260901_081200.log", ["2026-09-01 08:12:00 INFO mstransform::::casa  ##### Begin Task: mstransform", "2026-09-01 08:12:41 INFO mstransform::::casa  Averaging to 2 s, 500 kHz (demo)", "2026-09-01 08:13:02 INFO mstransform::::casa  ##### End Task: mstransform"]);
  demoLog("reductions/BW112/wd/wd_X/avica_snr_casa_log-20260901_081530.log", ["2026-09-01 08:15:30 INFO fringefit::::casa  ##### Begin Task: fringefit", "2026-09-01 08:16:10 WARN fringefit::::casa  Low SNR 4.2 on LA-PT (demo)", "2026-09-01 08:16:12 SEVERE fringefit::::casa  No phase-reference candidate above snr_threshold_phref=7 (demo)"]);
  demoLog("reductions/BB049/wd/wd_X_0326+277/avica_split_ms_casa_log-20260903_101500.log", ["2026-09-03 10:15:00 INFO mstransform::::casa  split selected sources (demo)"]);
  files.push({ path: "avica.inp", name: "avica.inp", size: 0, text: "target_dir = reductions/\nfolder_for_fits = /data/fits/\npicard_input_template_update = input_temp_update\nsnr_threshold_phref = 7\nrpicard.mpi_cores = 3\n" });
  files.push({ path: "avica.summary.json", name: "avica.summary.json", size: 0, text: JSON.stringify({ command: "avica pipe config --summary", rows: SUMMARY_ROWS }) });
  files.push({ path: "input_temp_update/array_finetune.inp", name: "array_finetune.inp", size: 0, text: "accor_solint = 10\nfringe_exhaustive_refant_search_islands = True\n" });
  files.push({ path: "avica.logs/avica__log-20260922_122612.log", name: "avica__log-20260922_122612.log", size: 0, text: "2026-09-22 12:26:12 INFO avica.pipeline [avica_snr] starting (demo)\n" });
  const crash = JSON.stringify({ target: "J1440+0127", first_col: "avica_snr", _exception: "Traceback (most recent call last):\n  File \"avica/pipe/steps.py\", line 1260, in run\nValueError: SNR 4.2 below snr_threshold_phref=7 for all phase-reference candidates (demo)" });
  files.push({ path: "avica.logs/avica_crash_avica_snr.json", name: "avica_crash_avica_snr.json", size: crash.length, text: crash });
  const bundle = buildBundle(files, { source: "demo", projectHint: DEMO_ALFRD_PROJECT });
  bundle.manifestFile = "alfrd.yaml (demo)";
  // Demo logs have no File handle; keep their text readable.
  (bundle.avica?.logs || []).forEach((l) => {
    const f = files.find((x) => x.name === l.name);
    if (f && !l.crash) l.text = f.text;
  });
  // Result CSVs have no notion of "in progress"; mark the demo's live target.
  const live = bundle.targets.find((t) => t.name === "0326+277");
  if (live) {
    live.steps.avica_snr = { step: "avica_snr", status: "running", attempt: 1, attempts: [], started: new Date(Date.now() - 252000).toISOString(), duration: 252, note: "Live telemetry (demo)" };
  }
  bundle.targets.forEach((t) => {
    t.projectTitle = "AVICA demo reductions";
    if (t.name === "J1440+0127") t.role = "Astrometric Calibrator";
  });
  return bundle;
}
