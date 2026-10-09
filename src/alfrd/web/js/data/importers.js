// Browser-side importers: turn user-selected files (alfrd.yaml, AVICA
// *_result.csv, dataset tables, avica.inp, avica.meta sidecars, logs) into the
// Studio's in-memory model. Nothing here executes pipeline code.

import { parseYaml } from "../utils/yaml_parser.js";
import { parseCsv, jsonCell, AliasLog, setAliasRules, resolveAlias } from "../utils/csv_parser.js";
import { manifestToWorkflows } from "./model.js";
import { avicaInterest, buildAvicaIndex, detectCodes, parseSummaryFile, parseResultFiles, DEFAULT_PATTERNS } from "./avica.js";
import { studioManifest, classifyLogs, msPathPatterns, specInstances, pathRegex, defaultManifestText } from "./defs.js";

const RESULT_SUFFIX = /_result\.(csv|tsv)$/i;
// File-name-only result CSV patterns (no folders), newest AVICA name first.
const RESULT_NAMES = ["result__{target}__{project_code}__{workdirname}.csv", "result_{target}_{project_code}_{workdirname}.csv"];

function baseName(path) {
  return String(path).split(/[\\/]/).pop();
}

function dirParts(path) {
  const parts = String(path).split(/[\\/]/);
  parts.pop();
  return parts.filter(Boolean);
}

function seconds(a, b) {
  const s = Date.parse(a);
  const e = Date.parse(b);
  if (!Number.isFinite(s) || !Number.isFinite(e)) return null;
  return Math.max(0, (e - s) / 1000);
}

function int(v) {
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) ? n : 0;
}

// Mirrors alfrd.gui.summaries._avica_status so browser and server agree.
function avicaStatus(row) {
  const ok = int(row.success_count);
  const failed = int(row.failed_count);
  if (ok && failed) return "warning";
  if (failed) return "failed";
  return ok ? "completed" : "pending";
}

function noteFor(row) {
  const desc = jsonCell(row.desc, []);
  if (Array.isArray(desc) && desc.length) return desc.map(String).join(" · ").replace(/\s+/g, " ").trim();
  if (desc && typeof desc !== "object") return String(desc).replace(/\s+/g, " ").trim();
  const detail = jsonCell(row.detail);
  if (detail && typeof detail === "object") return Object.entries(detail).map(([k, v]) => `${k}: ${v}`).join(", ");
  return detail ? String(detail) : "";
}

/**
 * Parse one AVICA `<TARGET>_result.csv` (append-only step history).
 * Returns { steps, history, artifacts, error }.
 */
export function parseResultCsv(text, { aliases = new AliasLog(), file = "result.csv" } = {}) {
  const { header, rows } = parseCsv(text);
  if (!header.includes("name") || !header.includes("success_count")) {
    return { steps: {}, history: [], artifacts: [], error: `${file}: not an AVICA result table (needs name, success_count columns)` };
  }
  const steps = {};
  const history = [];
  const artifacts = [];
  const attempts = {};
  rows.forEach((row) => {
    const raw = String(row.name || "").trim();
    if (!raw) return;
    const key = aliases.resolve(raw, file);
    attempts[key] = (attempts[key] || 0) + 1;
    const success = jsonCell(row.success, []);
    const attempt = {
      step: key,
      alias: raw !== key ? raw : null,
      attempt: attempts[key],
      status: avicaStatus(row),
      success_count: int(row.success_count),
      failed_count: int(row.failed_count),
      items: Array.isArray(success) ? success : [],
      started: row.start_stamp || null,
      finished: row.end_stamp || null,
      duration: seconds(row.start_stamp, row.end_stamp),
      note: noteFor(row),
      detail: jsonCell(row.detail),
    };
    history.push(attempt);
    const detail = attempt.detail;
    if (detail && typeof detail === "object") {
      Object.entries(detail).forEach(([band, p]) => {
        if (typeof p === "string" && p && p !== "done") artifacts.push({ step: key, band, path: p.includes(",/") ? p.slice(p.lastIndexOf(",/") + 1) : p });
      });
    }
    const prev = steps[key];
    steps[key] = {
      ...attempt,
      attempts: [...(prev?.attempts || []), attempt],
    };
  });
  // Future-proof: a result CSV may carry the AVICA project code per row.
  const codes = [...new Set(rows.map((r) => r.project_code || r.PROJECT_CODE || r.Project_code).filter(Boolean))];
  return { steps, history, artifacts, codes, error: null };
}

/** Parse AVICA's simple `key = value` config (avica.inp). */
export function parseKeyValue(text) {
  const out = {};
  String(text).split(/\r?\n/).forEach((line) => {
    const t = line.replace(/#.*$/, "").trim();
    const m = /^([^=:]+?)\s*[=:]\s*(.*)$/.exec(t);
    if (!m) return;
    const v = m[2].trim().replace(/^(["'])(.*)\1$/, "$2");
    if (/^none$/i.test(v)) out[m[1]] = null;
    else if (/^(true|false)$/i.test(v)) out[m[1]] = v.toLowerCase() === "true";
    else if (/^[-+]?\d+(\.\d+)?$/.test(v)) out[m[1]] = Number(v);
    else out[m[1]] = v;
  });
  return out;
}

const MAX_TEXT = 25 * 1024 * 1024;

/**
 * Read a FileList/array of File objects (folder picker, file picker or drop).
 * Only files the Studio understands are read (alfrd.yaml, avica.inp, result
 * CSVs, avica.meta/*, rPicard *.inp, crash snapshots); AVICA logs keep a
 * lazy File handle; measurement sets, scratch and notebook folders are skipped.
 * Paths are made relative to the folder holding alfrd.yaml (the ALFRD root).
 */
export async function readFiles(fileList) {
  const files = Array.from(fileList || []).map((file) => ({ file, path: (file.webkitRelativePath || file.relativePath || file.name).replace(/\\/g, "/") }));
  // A single picked/dropped JSON is a scan bundle (`alfrd avica scan --bundle`) or
  // a Studio snapshot: read it whole — the AVICA layout filter below would skip it.
  if (files.length === 1 && /\.json$/i.test(files[0].file.name) && !/^avica[._]/i.test(files[0].file.name)) {
    const { file, path } = files[0];
    const out = [{ path, rel: file.name, name: file.name, size: file.size, mtime: file.lastModified, text: await file.text() }];
    out.rootName = null;
    out.ignored = 0;
    out.msDirs = [];
    return out;
  }
  const manifests = files.filter((f) => /(^|\/)\.?alfrd\.ya?ml$/i.test(f.path)).sort((a, b) => a.path.split("/").length - b.path.split("/").length);
  let prefix = "";
  if (manifests.length) prefix = manifests[0].path.split("/").slice(0, -1).join("/");
  else if (files.length && files.every((f) => f.path.includes("/"))) {
    const first = files[0].path.split("/")[0];
    if (files.every((f) => f.path.startsWith(`${first}/`))) prefix = first;
  }
  const rootName = prefix.split("/").pop() || null;
  // A picked folder without alfrd.yaml uses the default one (a local file replaces it).
  const fallback = !manifests.length && prefix ? defaultManifestText(rootName) : null;
  if (fallback) {
    const blob = new Blob([fallback], { type: "text/yaml" });
    blob.name = "alfrd.yaml";
    blob.lastModified = 0;
    manifests.push({ file: blob, path: `${prefix}/alfrd.yaml`, isDefault: true });
  }
  // meta_dir may be renamed in alfrd.yaml (avica.meta_dir); peek before filtering.
  let metaDir = null;
  if (manifests.length) {
    const m = /^\s*meta_dir:\s*["']?([^"'\s#]+)/m.exec(await manifests[0].file.text());
    if (m) metaDir = m[1];
  }
  const out = [];
  const wanted = [];
  let ignored = 0;
  const msDirs = new Set();
  const logRes = manifests.length ? await logPathHints(manifests[0].file) : [];
  if (fallback) out.push({ path: `${prefix}/alfrd.yaml`, rel: "alfrd.yaml", name: "alfrd.yaml", size: fallback.length, mtime: 0, text: fallback, default: true });
  for (const { file, path } of files) {
    if (prefix && !path.startsWith(`${prefix}/`)) { ignored += 1; continue; }
    const rel = prefix ? path.slice(prefix.length + 1) : path;
    const ms = /^(.*?\.ms)\//.exec(rel);
    if (ms) msDirs.add(ms[1]);
    if (logRes.some((re) => re.test(rel))) { out.push({ path, rel, name: file.name, size: file.size, mtime: file.lastModified, file }); continue; }
    const interest = avicaInterest(rel, file.name, file.size, metaDir);
    if (!interest) { ignored += 1; continue; }
    if (interest === "lazy" || file.size > MAX_TEXT) {
      out.push({ path, rel, name: file.name, size: file.size, file });
      continue;
    }
    wanted.push({ path, rel, file });
  }
  // Read in parallel (small files; the browser already enumerated the folder).
  let next = 0;
  const worker = async () => {
    while (next < wanted.length) {
      const { path, rel, file } = wanted[next++];
      out.push({ path, rel, name: file.name, size: file.size, text: await file.text() });
    }
  };
  await Promise.all(Array.from({ length: Math.min(16, wanted.length) }, worker));
  out.rootName = rootName;
  out.ignored = ignored;
  out.msDirs = [...msDirs];
  return out;
}

/** Loose regexes for the log patterns of an alfrd.yaml (used before work dirs are known). */
async function logPathHints(manifestFile) {
  try {
    const defs = studioManifest(parseYaml(await manifestFile.text()));
    const { logSpecs } = await import("./defs.js");
    return logSpecs(defs).map((spec) => {
      const loose = spec.pattern.replace(/\{(workdir|meta_dir)\}/g, "__WD__").replace(/\{logs\}/g, "__LOGS__").replace(/\{target_dir\}/g, "__TD__").replace(/\{step\}/g, spec.step || "__ANY__");
      const src = pathRegex(loose).source.replace(/__WD__/g, "(?:[^/]+/)*?[^/]+").replace(/__LOGS__|__TD__|__ANY__/g, "[^/]+");
      return new RegExp(src);
    });
  } catch {
    return [];
  }
}

/**
 * Entries from `alfrd avica scan --bundle FILE` (the same files the folder scan
 * reads, collected on the machine that holds the reduction tree).
 */
export function entriesFromScanBundle(data) {
  if (!data || !data.alfrd_avica_scan || !Array.isArray(data.files)) return null;
  const out = data.files.map((f) => ({ path: f.rel, rel: f.rel, name: f.rel.split("/").pop(), size: f.size || 0, mtime: f.mtime ? f.mtime * 1000 : null, text: typeof f.text === "string" ? f.text : undefined, hint: f.hint, marker: f.marker, log: f.log, default: f.default || undefined }));
  out.rootName = data.root_name || null;
  out.msPaths = Array.isArray(data.ms_paths) ? data.ms_paths : null;
  out.ignored = 0;
  out.scan = { root: data.root, generated: data.generated };
  return out;
}

function kindOf(entry) {
  const { rel, name } = entry;
  if (entry.hint === "targets") return "table"; // alfrd.yaml targets.csv (may sit below the root)
  if (entry.hint) return entry.hint;
  if (entry.log) return "log";
  const depth = rel.split("/").length;
  if (/^\.?alfrd\.ya?ml$/i.test(name) && depth <= 2) return "manifest";
  if (/^avica\.summary\.(json|txt)$/i.test(name)) return "summary";
  if (RESULT_SUFFIX.test(name)) return "result";
  if (/\.(csv|tsv)$/i.test(name)) {
    const head = String(entry.text || "").split(/\r?\n/, 1)[0] || "";
    if (/(^|[,\t])name([,\t]|$)/.test(head) && /success_count/.test(head)) return "result";
    return depth === 1 ? "table" : "other";
  }
  if (/\.inp$/i.test(name) && depth === 1) return "config";
  return "avica";
}

/** Match known measurement-set folders against overview.ms_path. */
function matchMsDirs(dirs, defs, ctx) {
  const out = [];
  msPathPatterns(defs).forEach((pattern, order) => {
    specInstances({ pattern, step: null }, ctx).forEach((i) => {
      if (/\{(logs|target_dir|workdir|meta_dir)\}/.test(i.pattern)) return;
      const re = pathRegex(i.pattern);
      dirs.forEach((rel) => {
        const m = re.exec(rel);
        if (m) out.push({ rel, target: m.groups?.target || null, band: m.groups?.band || null, workdir: i.wd?.id || null, order });
      });
    });
  });
  return out;
}

/**
 * Build an import bundle from files returned by readFiles (or synthetic
 * {path, name, size, text} entries used by the demo and tests).
 * @param {Array} files
 * @param {{source?: string, projectHint?: string, rootName?: string}} options
 */
export function buildBundle(files, { source = "import", projectHint = null, rootName = null } = {}) {
  const aliases = new AliasLog();
  const messages = [];
  const bundle = { manifest: null, manifestFile: null, workflowInfo: null, targets: [], configs: [], logs: [], tables: [], messages, aliases: [], files: [], avica: null };
  const entries = files.map((f) => ({ ...f, rel: (f.rel || f.path).replace(/^\/+/, "") }));
  setAliasRules({}); // field aliases come only from the alfrd.yaml being read
  if (files.ignored) messages.push({ level: "info", text: `${files.ignored} file(s) outside the AVICA layout ignored (measurement sets, scratch, notebooks...).` });
  const kinds = entries.map((f) => ({ ...f, kind: kindOf(f) }));
  bundle.files = kinds.filter((f) => !f.marker).map((f) => ({ path: f.rel, kind: f.kind, size: f.size }));

  // 1. Manifest: the shallowest alfrd.yaml wins.
  kinds.filter((f) => f.kind === "manifest").sort((a, b) => a.rel.split("/").length - b.rel.split("/").length).forEach((f) => {
    if (typeof f.text !== "string") return;
    try {
      const parsed = parseYaml(f.text);
      if (bundle.manifest) {
        messages.push({ level: "info", text: `Additional manifest ${f.rel} ignored (using ${bundle.manifestFile}).` });
        return;
      }
      const info = manifestToWorkflows(parsed, f.name);
      bundle.manifest = parsed;
      bundle.manifestFile = f.rel;
      bundle.manifestDefault = Boolean(f.default);
      if (f.default) messages.push({ level: "info", text: "No alfrd.yaml in this folder: using the default one. Save it from Project settings to make a local copy (the local file then replaces the default)." });
      bundle.manifestText = f.text;
      bundle.workflowInfo = info;
      info.errors.forEach((e) => messages.push({ level: "error", text: `${f.name}: ${e}` }));
      info.aliases.forEach((a) => aliases.resolve(a.from, f.name));
    } catch (error) {
      messages.push({ level: "error", text: `${f.rel}: ${error.message}` });
    }
  });
  const manifestAvica = (bundle.manifest && typeof bundle.manifest.avica === "object" && bundle.manifest.avica) || {};
  const defs = studioManifest(bundle.manifest || {});
  bundle.defs = defs;
  // ALFRD project = what alfrd.yaml says (not the AVICA observation code).
  const alfrdProject = projectHint || bundle.workflowInfo?.name || rootName || files.rootName || "imported";
  bundle.alfrdProject = alfrdProject;
  const primaryKey = bundle.manifest?.primary_key || bundle.manifest?.datasets?.primary_key || "TARGET_NAME";

  // 2. AVICA config (avica.inp) and the cached `avica pipe config --summary`.
  const configName = manifestAvica.config || "avica.inp";
  kinds.filter((f) => f.kind === "config").forEach((f) => {
    const canonical = resolveAlias(f.name).name;
    if (f.name !== configName && canonical !== configName && !(defs.template === "avica" && f.name === "avica.inp")) return;
    bundle.configs.push({ path: f.rel, name: f.name, values: parseKeyValue(f.text), text: f.text });
    if (canonical !== f.name) aliases.resolve(f.name, f.rel);
  });
  const config = Object.assign({}, ...bundle.configs.map((c) => c.values));
  let summary = null;
  kinds.filter((f) => f.kind === "summary").forEach((f) => {
    const parsed = parseSummaryFile(f.name, f.text);
    if (parsed && (!summary || /\.json$/i.test(f.name))) summary = parsed;
    if (!parsed) messages.push({ level: "warn", text: `${f.name}: could not read the avica pipe config --summary table.` });
  });
  bundle.summary = summary;

  // 3. AVICA tree index (project codes, avica.meta, rPicard templates, logs).
  const avicaEntries = kinds.filter((f) => f.kind === "avica" || f.kind === "result");
  if (avicaEntries.length || bundle.configs.length || summary) {
    bundle.avica = buildAvicaIndex(entries, { manifestAvica, manifest: bundle.manifest, config, summary });
    bundle.avica.project = alfrdProject;
    bundle.avica.provider = "files";
    const c = Object.keys(bundle.avica.codes).length;
    if (c) messages.push({ level: "info", text: `AVICA: ${c} project code folder(s) under ${bundle.avica.targetDir}/ (${Object.keys(bundle.avica.codes).slice(0, 8).join(", ")}${c > 8 ? ", ..." : ""}).` });
  }

  // 3b. Step logs and log artifacts (alfrd.yaml), MS storage paths (overview.ms_path).
  const ctxPaths = {
    logsDir: bundle.avica?.logsDir || manifestAvica.logs || "avica.logs",
    targetDir: bundle.avica?.targetDir,
    metaDir: bundle.avica?.patterns?.meta_dir?.[0] || "avica.meta",
    workdirs: Object.values(bundle.avica?.codes || {}).map((c) => ({ rel: c.wd, id: c.id, code: c.code })),
  };
  const known = entries.filter((e) => e.log);
  const notesFile = kinds.find((f) => f.kind === "notes");
  if (notesFile) bundle.notesText = notesFile.text || "";
  const listed = classifyLogs(entries.filter((e) => !e.marker && e.hint !== "notes" && (e.file || e.log || kindOf(e) === "avica" || /\.(log|json|out)|\.log_|\.out_/i.test(e.name))), defs, ctxPaths);
  const byRel = new Map(listed.map((l) => [l.rel, l]));
  known.forEach((e) => {
    const cur = byRel.get(e.rel) || { rel: e.rel, name: e.name, size: e.size, mtime: e.mtime, file: e.file, text: e.text, groups: [], steps: [] };
    const info = e.log || {};
    (info.groups || []).forEach((g) => !cur.groups.includes(g) && cur.groups.push(g));
    (info.steps || []).forEach((g) => !cur.steps.includes(g) && cur.steps.push(g));
    ["band", "target", "workdir"].forEach((k) => { if (info[k]) cur[k] = info[k]; });
    byRel.set(e.rel, cur);
  });
  bundle.logFiles = [...byRel.values()].sort((a, b) => (b.mtime || 0) - (a.mtime || 0) || b.rel.localeCompare(a.rel));
  bundle.msPaths = files.msPaths || matchMsDirs(files.msDirs || [], defs, ctxPaths);
  if (bundle.avica) {
    bundle.avica.logFiles = bundle.logFiles;
    bundle.avica.msPaths = bundle.msPaths;
  }

  // 4. Dataset tables provide target identity, project code and paths.
  // The declared targets file (hint "targets") is read last, so its FITS names
  // and codes win over other root tables (the plan CSV included).
  const datasetRows = new Map();
  const targetsRel = String(defs.targets?.csv || "alfrd.targets.csv").replace(/^\.\//, "");
  kinds.forEach((f) => { if (f.kind === "table" && f.rel === targetsRel) f.hint = "targets"; });
  const tables = kinds.filter((f) => f.kind === "table");
  const targetsFile = tables.find((f) => f.hint === "targets");
  if (targetsFile) bundle.targetsFile = { rel: targetsFile.rel, text: targetsFile.text || "" };
  [...tables.filter((f) => f.hint !== "targets"), ...tables.filter((f) => f.hint === "targets")].forEach((f) => {
    const { header, rows } = parseCsv(f.text);
    const key = header.find((h) => h === primaryKey) || header.find((h) => /^(target(_name)?|source|name)$/i.test(h));
    bundle.tables.push({ path: f.rel, header, rows: rows.length, ...(f.hint === "targets" ? { targets: true } : {}) });
    if (!key) return;
    const codeCol = header.find((h) => /^project_code$/i.test(h));
    const fileCodes = new Map();
    rows.forEach((row) => {
      const id = String(row[key] || "").trim();
      if (!id) return;
      // One row per (target, code) in the targets file: keep every code.
      if (codeCol && row[codeCol]) fileCodes.set(id, [...new Set([...(fileCodes.get(id) || []), row[codeCol]])]);
      const merged = { ...(datasetRows.get(id) || {}), ...row, __file: f.rel };
      if (codeCol && fileCodes.has(id)) merged[codeCol] = fileCodes.get(id).join(";");
      datasetRows.set(id, merged);
    });
  });

  const pick = (row, ...names) => {
    if (!row) return null;
    for (const n of names) {
      const hit = Object.keys(row).find((k) => k.toLowerCase() === n.toLowerCase());
      if (hit && row[hit]) return row[hit];
    }
    return null;
  };
  const codesFromRow = (row) => String(pick(row, "PROJECT_CODE", "project_code", "Project") || "").split(/[;,\s]+/).filter(Boolean);
  const cfgTarget = config.target || config.primary_value || null;

  const msMatches = (name, codes) => {
    const ids = new Set(codes.map((c) => c.code));
    const own = (bundle.msPaths || []).filter((p) => p.target === name);
    const shared = (bundle.msPaths || []).filter((p) => !p.target && p.workdir && [...ids].some((id) => p.workdir === id || String(p.workdir).startsWith(`${id}/`)));
    return [...own, ...shared].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || String(a.band || "").localeCompare(String(b.band || "")));
  };
  const msAll = (name, codes) => msMatches(name, codes).map((p) => p.rel);
  const msFor = (name, codes) => msMatches(name, codes)[0]?.rel || null;
  const makeTarget = (name, row, extra) => {
    const detected = detectCodes(bundle.avica, name);
    const declared = codesFromRow(row);
    const codes = [...new Set([...(extra.codes || []), ...declared, ...detected])].map((code) => ({
      code,
      auto: !declared.includes(code) && !(extra.codes || []).includes(code),
    }));
    return {
      id: `${alfrdProject}/${name}`,
      name,
      project: alfrdProject,
      codes,
      msPath: pick(row, "MS_PATH", "ms", "measurement_set", "WORKDIR") || msFor(name, codes) || extra.msPath || null,
      msPaths: msAll(name, codes),
      fitsidi: pick(row, "FILENAMES", "FITSIDI", "INPUT_FILE", "fits"),
      steps: extra.steps || {},
      history: extra.history || [],
      artifacts: extra.artifacts || [],
      columns: row ? Object.fromEntries(Object.entries(row).filter(([k]) => !k.startsWith("__"))) : {},
      source: extra.source,
    };
  };

  // 5. Result CSVs -> targets.
  const seen = new Map();
  kinds.filter((f) => f.kind === "result").forEach((f) => {
    // AVICA <= 0.3 writes <target_dir>/<target>_result.csv (an empty target gives "_result.csv");
    // newer AVICA writes result__<target>__<code>__<workdir>.csv (earlier builds: single "_"),
    // one file per (target, code, workdir).
    const info = bundle.avica?.resultCsvs?.find((r) => r.file === f.rel)
      || parseResultFiles([f.name], { ...DEFAULT_PATTERNS, result_csv: RESULT_NAMES }, ".")[0] || {};
    const name = info.target || f.name.replace(RESULT_SUFFIX, "") || cfgTarget || "(untargeted)";
    const parsed = parseResultCsv(f.text, { aliases, file: f.name });
    if (parsed.error) {
      messages.push({ level: "error", text: parsed.error });
      return;
    }
    const code = info.project_code || null;
    const workdir = info.workdir || null;
    const tag = (a) => ({ ...a, code: a.code || code, workdir: a.workdir || workdir, file: f.rel });
    const part = { file: f.rel, code, workdir, steps: parsed.steps, history: parsed.history.map(tag) };
    const ms = parsed.artifacts.find((a) => /\.ms\/?$/i.test(a.path));
    const id = `${alfrdProject}/${name}`;
    const prev = seen.get(id);
    if (prev && prev.source?.kind === "result_csv") {
      // Same target in another code / work dir: keep one target, history merged in time
      // order, latest attempt per step wins; the per-file results stay in `results`.
      prev.results.push(part);
      prev.history = [...prev.history, ...part.history].sort((a, b) => String(a.started || "").localeCompare(String(b.started || "")));
      Object.entries(parsed.steps).forEach(([k, st]) => {
        const old = prev.steps[k];
        const newer = !old || String(st.finished || st.started || "") >= String(old.finished || old.started || "");
        const attempts = [...(old?.attempts || []), ...(st.attempts || [])];
        prev.steps[k] = newer ? { ...tag(st), attempts } : { ...old, attempts };
      });
      prev.artifacts.push(...parsed.artifacts);
      [...(parsed.codes || []), code].filter(Boolean).forEach((c) => {
        const hit = prev.codes.find((x) => x.code === c);
        if (hit) hit.auto = false; else prev.codes.push({ code: c, auto: false });
      });
      prev.source.files = [...(prev.source.files || [prev.source.file]), f.rel];
      if (!prev.msPath && ms) prev.msPath = ms.path;
      return;
    }
    const steps = Object.fromEntries(Object.entries(parsed.steps).map(([k, st]) => [k, tag(st)]));
    const t = makeTarget(name, datasetRows.get(name), {
      steps, history: part.history, artifacts: parsed.artifacts, codes: [...new Set([...(parsed.codes || []), code].filter(Boolean))],
      msPath: ms ? ms.path : null, source: { kind: "result_csv", file: f.rel, origin: source },
    });
    t.results = [part];
    seen.set(t.id, t);
  });
  // 6. Dataset rows without result history still appear (status unknown).
  datasetRows.forEach((row, name) => {
    const id = `${alfrdProject}/${name}`;
    if (!seen.has(id)) seen.set(id, makeTarget(name, row, { source: { kind: "dataset_table", file: row.__file, origin: source } }));
  });
  bundle.targets = Array.from(seen.values());

  bundle.aliases = aliases.list();
  if (!bundle.targets.length && !bundle.manifest && !bundle.avica && !bundle.configs.length) {
    messages.push({ level: "warn", text: "Nothing importable found. Open the folder that contains alfrd.yaml (with avica.inp, avica.logs/ and the target_dir)." });
  }
  return bundle;
}
