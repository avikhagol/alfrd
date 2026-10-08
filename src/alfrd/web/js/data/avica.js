// AVICA reduction-tree support (browser side). Mirrors alfrd/avica_layout.py.
//
//   <root>/alfrd.yaml                      ALFRD project (targets belong to it)
//   <root>/avica.inp, avica.summary.json   AVICA config / `avica pipe config --summary`
//   <root>/avica.logs/                     avica__log-*.log, avica_crash_<step>.json
//   <root>/<target_dir>/<TARGET>_result.csv   (AVICA <= 0.3)
//   <root>/<target_dir>/[<CODE>/<wd>/]result__<TARGET>__<CODE>__<wd>.csv   (newer AVICA;
//        earlier builds wrote result_<TARGET>_<CODE>_<wd>.csv)
//   <root>/<target_dir>/<CODE>/wd/         AVICA project code work dir (BV019, RDV41, ...)
//        avica.meta/  input_template/  wd_<band>/  wd_<band>_<TARGET>/input_template_<band>_<TARGET>/

import { parseKeyValue } from "./importers.js";
import { aliasRules } from "../utils/csv_parser.js";

/** meta_dir plus older names mapped to it by alfrd.yaml field_aliases. */
export function metaDirNames(metaDir = "avica.meta") {
  return [metaDir, ...aliasRules().filter((r) => r.to === metaDir).map((r) => r.from)];
}

// Layout patterns: alfrd.yaml `avica:` keys override these (see alfrd/avica_layout.py).
// Placeholders: {target_dir}, {project_code}, {n}, {band}, {target}; lists mean "any of".
export const DEFAULT_PATTERNS = {
  workdir: ["{target_dir}/{project_code}/wd", "{target_dir}/{project_code}/wd_{n}"],
  band_dir: ["wd_{band}", "wd_{band}_{target}"],
  meta_dir: ["avica.meta"],
  input_templates: ["input_template", "input_template_{n}", "wd_{band}_{target}/input_template_{band}_{target}"],
  // Newest AVICA name first (result__<TARGET>__<CODE>__<wd>.csv), then the earlier
  // single-underscore name; "{target}_result.csv" is AVICA <= 0.3.
  result_csv: [
    "{target_dir}/result__{target}__{project_code}__{workdirname}.csv",
    "{target_dir}/{project_code}/{workdirname}/result__{target}__{project_code}__{workdirname}.csv",
    "{target_dir}/result_{target}_{project_code}_{workdirname}.csv",
    "{target_dir}/{project_code}/{workdirname}/result_{target}_{project_code}_{workdirname}.csv",
    "{target_dir}/{target}_result.csv",
  ],
};
// A target / project code never starts or ends with "_": the "_" / "__" separators
// in result CSV names belong to the pattern, not to the name (mirrors avica_layout).
const GROUPS = { project_code: "[^/_](?:[^/]*[^/_])?", n: "\\d+", band: "[A-Z][A-Z0-9]*?", target: "[^/_](?:[^/]*?[^/_])?", workdirname: "wd(?:_\\d+)?" };
const reEsc = (t) => String(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `{workdirname}` regex from the last segment of each `workdir` pattern (mirrors avica_layout.workdirname_regex). */
export function workdirnameRegex(workdirPatterns = DEFAULT_PATTERNS.workdir) {
  const alts = [...new Set((workdirPatterns || []).map((p) => {
    const last = String(p).replace(/\/+$/, "").split("/").pop();
    let out = "";
    let pos = 0;
    for (const m of last.matchAll(/\{(\w+)\}/g)) {
      out += reEsc(last.slice(pos, m.index)) + (m[1] === "n" ? "\\d+" : "[^/_]+");
      pos = m.index + m[0].length;
    }
    return out + reEsc(last.slice(pos));
  }).filter(Boolean))];
  return alts.length ? `(?:${alts.join("|")})` : GROUPS.workdirname;
}

/** Compile a layout pattern (repeated placeholders must match the same text). */
export function patternRegex(pattern, fixed = {}, groups = {}) {
  // A target_dir of "." / "./" / "" is the project root: drop "{target_dir}/" so
  // root-relative paths like "BV019/wd" match (mirrors avica_layout.pattern_regex).
  if (fixed && "target_dir" in fixed && fixed.target_dir != null && /^\/*\.?\/*$/.test(String(fixed.target_dir))) {
    pattern = pattern.split("{target_dir}/").join("");
    fixed = { ...fixed };
    delete fixed.target_dir;
  }
  const seen = new Set();
  let out = "";
  let pos = 0;
  for (const m of pattern.matchAll(/\{(\w+)\}/g)) {
    out += reEsc(pattern.slice(pos, m.index));
    const name = m[1];
    if (name in fixed && fixed[name] != null) out += reEsc(String(fixed[name]).replace(/\/+$/, ""));
    else if (seen.has(name)) out += `\\k<${name}>`;
    else { seen.add(name); out += `(?<${name}>${groups[name] || GROUPS[name] || "[^/]+?"})`; }
    pos = m.index + m[0].length;
  }
  return new RegExp(`^${out}${reEsc(pattern.slice(pos))}$`);
}

const asList = (v) => (v == null ? [] : Array.isArray(v) ? v.map(String) : [String(v)]);

export function layoutPatterns(manifest) {
  const block = (manifest && typeof manifest.avica === "object" && manifest.avica) || {};
  const out = {};
  Object.entries(DEFAULT_PATTERNS).forEach(([k, v]) => { out[k] = k in block ? asList(block[k]) : v; });
  const art = (manifest?.artifacts || []).find((a) => a?.name === "result_csv");
  const declared = resultCsvArtifactPatterns(art);
  if (!("result_csv" in block) && declared.length) out.result_csv = declared;
  return out;
}

/** result_csv artifact: path_pattern + fallback_patterns, then the built-in names it doesn't list (mirrors Python). */
export function resultCsvArtifactPatterns(art) {
  if (!art) return [];
  const declared = [art.path_pattern, ...asList(art.fallback_patterns)].filter((p) => p && String(p).includes("{target}")).map(String);
  return declared.length ? [...new Set([...declared, ...DEFAULT_PATTERNS.result_csv])] : [];
}

/**
 * Parse result CSV paths → [{file, target, project_code?, workdir?}]. Names are
 * parsed with the patterns (never split on "_"); known codes settle a target with "_".
 */
export function parseResultFiles(rels, pats, targetDir, knownCodes = []) {
  const groups = { workdirname: workdirnameRegex(pats.workdir) };
  const res = pats.result_csv.map((p) => patternRegex(p, { target_dir: targetDir }, groups));
  const codes = [...new Set(knownCodes)].sort((a, b) => b.length - a.length);
  return rels.map((rel) => {
    const m = mostSpecificMatch(res, rel);
    if (!m) return null;
    let g = Object.fromEntries(Object.entries(m.groups || {}).filter(([, v]) => v));
    if (codes.length && g.project_code && !codes.includes(g.project_code)) {
      const name = rel.split("/").pop();
      for (const code of codes) {
        if (!name.includes(`_${code}_`)) continue;
        const better = matchAny(pats.result_csv.map((p) => patternRegex(p, { target_dir: targetDir, project_code: code }, groups)), rel);
        if (better) { g = { ...Object.fromEntries(Object.entries(better.groups || {}).filter(([, v]) => v)), project_code: code }; break; }
      }
    }
    const item = { file: rel, target: g.target || "" };
    if (g.project_code) item.project_code = g.project_code;
    if (g.workdirname) item.workdir = g.workdirname;
    return item;
  }).filter(Boolean);
}

const matchAny = (res, text) => { for (const r of res) { const m = r.exec(text); if (m) return m; } return null; };
// Match from the pattern with the most placeholders, back-references included
// (ties keep pattern order): a loose "{project_code}/{workdirname}/result_{target}.csv"
// must not win over the name that repeats the code + work dir (mirrors avica_layout).
const mostSpecificMatch = (res, text) => {
  let best = null, score = -1;
  for (const r of res) {
    const m = r.exec(text);
    if (!m) continue;
    const n = (r.source.match(/\(\?<\w+>|\\k</g) || []).length;
    if (n > score) { best = m; score = n; }
  }
  return best;
};
const META_TARGET = /^([a-z][a-z_]*?)_([A-Z][A-Z0-9]*)_(.+)\.(?:avica|out)$/;
// Folders that never hold anything the Studio reads (measurement sets, scratch, notebooks...).
const SKIP_SEGMENT = /^(\..+|__marimo__|__pycache__|\.ipynb_checkpoints|py3\d+|raw|tmp_.*|calibration_tables|diagnostics_.*|.*\.ms|.*\.ms\..+|.*\.flagversions)$/;

function coerce(v) {
  if (v === null || v === undefined) return v;
  const t = String(v).trim().replace(/^(["'])(.*)\1$/, "$2");
  if (/^(true|false)$/i.test(t)) return t.toLowerCase() === "true";
  if (/^none$/i.test(t)) return null;
  if (/^[-+]?\d+$/.test(t)) return Number.parseInt(t, 10);
  if (/^[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?$/.test(t)) return Number.parseFloat(t);
  return t;
}

/** Parse the table printed by `avica pipe config --summary` (rich box drawing or plain |). */
export function parseConfigSummary(text) {
  const rows = [];
  let step = "";
  String(text || "").split(/\r?\n/).forEach((raw) => {
    const line = raw.trimEnd();
    if (!/^\s*[│┃|]/.test(line)) return;
    const cells = line.split(/[│┃|]/).slice(1, -1).map((c) => c.trim());
    if (cells.length !== 4) return;
    const [s, param, source, value] = cells;
    if (s.toLowerCase() === "step" && param.toLowerCase() === "parameter") return;
    if (!source && rows.length && !(s && param)) {
      const last = rows[rows.length - 1];
      if (param) last.parameter += param;
      if (value) last.value += value;
      if (s) { last.step += s; step = last.step; }
      return;
    }
    if (s) step = s;
    rows.push({ step, parameter: param, source, value });
  });
  rows.forEach((r) => { r.value = coerce(r.value); });
  return rows;
}

export function parseSummaryFile(name, text) {
  if (/\.json$/i.test(name)) {
    try {
      const data = JSON.parse(text);
      if (Array.isArray(data)) return { rows: data, file: name };
      if (Array.isArray(data?.rows)) return { ...data, file: name };
    } catch { /* fall through */ }
    return null;
  }
  const rows = parseConfigSummary(text);
  return rows.length ? { command: "avica pipe config --summary", rows, file: name } : null;
}

/** Is this path worth looking at? Returns "eager" | "lazy" | null. */
export function avicaInterest(rel, name, size, metaDir = null) {
  const parts = rel.split("/");
  if (parts.slice(0, -1).some((p) => SKIP_SEGMENT.test(p))) return null;
  const depth = parts.length;
  const dir = parts.slice(0, -1).join("/");
  if (depth <= 2 && /^\.?alfrd\.ya?ml$/i.test(name)) return "eager";
  if (depth === 1 && (/\.inp$/i.test(name) || /^avica\.summary\.(json|txt)$/i.test(name))) return "eager";
  if (/(^|\/)avica\.logs$/.test(dir)) return /^avica_crash_.*\.json$/i.test(name) ? "eager" : /\.log$/i.test(name) ? "lazy" : null;
  if (/\.(csv|tsv)$/i.test(name) && !/(^|\/)wd(\/|$)/.test(dir) && depth <= 3) return "eager";
  if (metaDirNames(metaDir || "avica.meta").some((m) => dir === m || dir.endsWith(`/${m}`))) return size < 2 * 1024 * 1024 ? "eager" : null;
  // rPicard inputs (input_template*/, picard_input_template_update/): small key=value files.
  if (/\.inp$/i.test(name) && depth <= 7 && size < 512 * 1024) return "eager";
  return null;
}

function parseMeta(name, text, size) {
  const entry = { name, size };
  const m = META_TARGET.exec(name);
  if (m) Object.assign(entry, { kind: m[1], band: m[2], target: m[3] });
  else entry.kind = name.replace(/\.(avica|json|out)$/i, "");
  if (/\.(avica|json)$/i.test(name)) {
    try {
      entry.data = JSON.parse(text);
      entry.format = "json";
      return entry;
    } catch (error) {
      entry.error = `invalid JSON: ${error.message}`;
    }
  }
  entry.format = "text";
  entry.text = text;
  return entry;
}

/** Normalise a relative dir value; "." is the canonical project root. */
function resolveRel(value) {
  if (!value) return null;
  const rel = String(value).replace(/^(?:\.\/+)+/, "").replace(/\/+$/, "");
  return rel === "" || rel === "." ? "." : rel;
}

/**
 * Build an AVICA index from import entries.
 * @param {Array<{rel:string,name:string,size:number,text?:string,file?:File}>} entries relative to the ALFRD root
 * @param {{config?:object, manifestAvica?:object}} opts
 */
export function buildAvicaIndex(entries, { manifestAvica = {}, manifest = null, config = {}, summary = null } = {}) {
  const values = { target_dir: manifestAvica.target_dir || "reductions/", picard_input_template_update: "", ...config };
  // Core parameters: the `other` section plus rows whose source is `<origin>/core`.
  if (summary) summary.rows.filter((r) => !r.step || r.step === "other" || /\/core$/.test(String(r.source || ""))).forEach((r) => { values[r.parameter] = r.value; });
  let targetDir = resolveRel(values.target_dir) || "reductions";
  const pats = layoutPatterns(manifest);
  // True when some entry path prefix (1-6 segments) is a work dir under `dir`.
  const holdsWorkdirs = (dir) => {
    const res = pats.workdir.map((p) => patternRegex(p, { target_dir: dir }));
    return entries.some((e) => {
      const parts = e.rel.split("/");
      for (let k = 1; k <= Math.min(parts.length - 1, 6); k += 1) if (matchAny(res, parts.slice(0, k).join("/"))) return true;
      return false;
    });
  };
  // Fall back to the folder that actually holds project work dirs / result CSVs.
  // An absolute target_dir cannot be mapped onto browser entries, so it always guesses.
  const hasUnder = (dir) => (dir === "." ? holdsWorkdirs(".") : entries.some((e) => e.rel.startsWith(`${dir}/`)));
  if (entries.length && (targetDir.startsWith("/") || !hasUnder(targetDir))) {
    const guess = (holdsWorkdirs(".") && ".")
      || entries.map((e) => (/^([^/]+)\/[^/]+\/wd(_\d+)?\//.exec(e.rel) || [])[1]).find(Boolean)
      || entries.map((e) => (/^([^/]+)\/[^/]+_result\.csv$/.exec(e.rel) || [])[1]).find(Boolean);
    if (guess) targetDir = guess;
  }
  const metaDir = pats.meta_dir[0] || "avica.meta";
  const metaNames = metaDirNames(metaDir);
  const wdRes = pats.workdir.map((p) => patternRegex(p, { target_dir: targetDir }));
  const bandRes = pats.band_dir.map((p) => patternRegex(p));
  const tplRes = pats.input_templates.map((p) => patternRegex(p));
  // Work dirs: the shortest prefix of each entry path that matches a workdir pattern.
  const wds = new Map(); // wd path -> code
  const wdOf = (rel) => {
    const parts = rel.split("/");
    for (let k = 2; k < Math.min(parts.length, 6); k += 1) {
      const prefix = parts.slice(0, k).join("/");
      if (wds.has(prefix)) return prefix;
      const m = matchAny(wdRes, prefix);
      if (m) { wds.set(prefix, m.groups?.project_code || parts[k - 2]); return prefix; }
    }
    return null;
  };
  const found = {};
  entries.forEach((e) => {
    const wd = wdOf(e.rel);
    if (!wd) return;
    const c = (found[wd] ||= { code: wds.get(wd), wd, workdir: wd.split("/").pop(), bands: new Set(), targets: new Set(), meta: [], templates: [] });
    const rest = e.rel.slice(wd.length + 1);
    const segs = rest.split("/");
    for (let k = 1; k < segs.length; k += 1) {
      const m = matchAny(bandRes, segs.slice(0, k).join("/"));
      if (m) {
        if (m.groups?.band) c.bands.add(m.groups.band);
        if (m.groups?.target) c.targets.add(m.groups.target);
      }
    }
    if (metaNames.includes(segs[0]) && segs.length === 2 && typeof e.text === "string") {
      const meta = parseMeta(e.name, e.text, e.size);
      if (segs[0] !== metaDir) meta.legacy = segs[0];
      c.meta.push(meta);
      if (meta.target) c.targets.add(meta.target);
    }
    if (/\.inp$/i.test(e.name) && typeof e.text === "string") {
      const folder = segs.slice(0, -1).join("/");
      if (folder && matchAny(tplRes, folder)) c.templates.push({ folder: `${wd}/${folder}`, rel: folder, file: e.name, values: parseKeyValue(e.text) });
    }
  });
  const perCode = {};
  Object.values(found).forEach((c) => { perCode[c.code] = (perCode[c.code] || 0) + 1; });
  const codes = {};
  Object.values(found).sort((a, b) => a.wd.localeCompare(b.wd)).forEach((c) => {
    c.id = perCode[c.code] === 1 ? c.code : `${c.code}/${c.workdir}`;
    c.bands = [...c.bands].sort();
    c.targets = [...c.targets].sort();
    c.meta.sort((a, b) => a.name.localeCompare(b.name));
    codes[c.id] = c;
  });

  const updateRel = resolveRel(values.picard_input_template_update);
  const updateFiles = {};
  if (updateRel) {
    entries.filter((e) => e.rel.startsWith(`${updateRel}/`) && /\.inp$/i.test(e.name) && typeof e.text === "string")
      .forEach((e) => { updateFiles[e.name] = parseKeyValue(e.text); });
  }
  const detected = !updateRel
    ? [...new Set(entries.map((e) => (/^([^/]*input_temp[^/]*update[^/]*)\/[^/]+\.inp$/i.exec(e.rel) || [])[1]).filter(Boolean))][0] || null
    : null;
  const logsDir = manifestAvica.logs || "avica.logs";
  const logs = entries.filter((e) => e.rel.startsWith(`${logsDir}/`) && e.rel.split("/").length === 2)
    .map((e) => {
      const crash = /^avica_crash_(.+)\.json$/.exec(e.name);
      const log = { name: e.name, size: e.size, kind: crash ? "crash" : "log", step: crash ? crash[1] : null, file: e.file };
      if (!crash && typeof e.text === "string") log.text = e.text; // log tail from `alfrd avica scan --bundle`
      if (crash && typeof e.text === "string") {
        try {
          const d = JSON.parse(e.text);
          log.crash = { target: d.target || d.primary_value || null, exception: d._exception || null, first_col: d.first_col || null };
        } catch { /* ignore */ }
      }
      return log;
    })
    .sort((a, b) => b.name.localeCompare(a.name));
  return {
    targetDir,
    values,
    summary,
    codes,
    update: { folder: updateRel, files: updateFiles, detected },
    logsDir,
    logs,
    patterns: pats,
    resultCsvs: parseResultFiles(entries.filter((e) => /\.csv$/i.test(e.name || e.rel)).map((e) => e.rel), pats, targetDir, Object.values(codes).map((c) => c.code)),
  };
}

/** avica.meta + templates for one code (and optionally one target), same shape as the server. */
export function workdirFromIndex(index, code, target) {
  const c = index?.codes?.[code];
  if (!c) return null;
  const meta = c.meta.filter((m) => !target || !m.target || m.target === target);
  const tplRes = (index.patterns?.input_templates || DEFAULT_PATTERNS.input_templates).map((p) => patternRegex(p, target ? { target } : {}));
  const templates = c.templates
    .filter((t) => !target || matchAny(tplRes, t.rel || t.folder.slice(c.wd.length + 1)))
    .map((t) => ({ ...t, updated: index.update.files[t.file] || {} }));
  return { code: c.code, id: c.id, target, wd: c.wd, bands: c.bands, meta, templates, template_update: index.update };
}

/** Index shape from the server's /api/studio/avica/<project>/layout response. */
export function indexFromServerLayout(layout) {
  const codes = {};
  (layout.project_codes || []).forEach((c) => {
    const id = c.id || c.code;
    codes[id] = { id, code: c.code, wd: c.wd, workdir: c.workdir, bands: c.bands, targets: c.targets, meta: null, metaFiles: c.meta_files, templates: null, remote: true };
  });
  const summary = layout.config?.summary || null;
  return {
    targetDir: layout.target_dir,
    values: layout.config?.values || {},
    sources: layout.config?.sources || {},
    summary,
    codes,
    update: { folder: layout.picard_input_template_update, files: {}, detected: layout.picard_input_template_update_detected },
    logsDir: layout.logs_dir,
    logs: layout.logs || [],
    resultCsvs: layout.result_csvs || [],
    patterns: layout.patterns,
    remote: true,
  };
}

/** Codes whose work dir mentions this target (wd_<band>_<target>/ or *_<band>_<target>.avica). */
export function detectCodes(index, targetName) {
  if (!index) return [];
  return Object.values(index.codes).filter((c) => c.targets.includes(targetName)).map((c) => c.id || c.code);
}

/** Step → {param: {value, source}} from the summary rows (or avica.inp <step>.<param>). */
export function stepParamsFromConfig(index, stepKeys) {
  const out = {};
  if (index?.summary?.rows?.length) {
    index.summary.rows.forEach((r) => {
      if (!stepKeys.includes(r.step)) return;
      // Runtime inputs (fitsfiles, target...) and objects (lf) are not configurable.
      if (/^required\/runtime$/.test(String(r.source)) || /^<.* object at 0x[0-9a-f]+>$/.test(String(r.value))) return;
      (out[r.step] ||= {})[r.parameter] = { value: r.value, source: r.source };
    });
    return out;
  }
  Object.entries(index?.values || {}).forEach(([k, v]) => {
    const m = /^([A-Za-z]\w*)\.([\w.]+)$/.exec(k);
    if (m && stepKeys.includes(m[1])) (out[m[1]] ||= {})[m[2]] = { value: v, source: "avica.inp/step" };
  });
  return out;
}
