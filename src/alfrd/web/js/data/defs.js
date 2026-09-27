// Studio definitions from alfrd.yaml (optionally on top of a template).
//
// Nothing about a particular pipeline is built into the Studio: step labels,
// categories, stages, descriptions, the metadata files a step writes, the log
// files it leaves behind, the Overview "MS Storage Path" column and field
// aliases all come from alfrd.yaml. `template: avica` starts from
// assets/templates/avica.yaml; alfrd.yaml wins for every key it sets.
// Mirrors alfrd/studio_defs.py.

import { setAliasRules, resolveAlias } from "../utils/csv_parser.js";

const TEMPLATES = {};

/** Make a parsed template available synchronously (the app fetches them at boot). */
export function registerTemplate(name, data) {
  if (name && data && typeof data === "object") TEMPLATES[name] = data;
}

export function templateNames() {
  return Object.keys(TEMPLATES);
}

/** Fetch and register a template shipped in assets/templates/ (browser). */
export async function loadTemplate(name, parse) {
  if (TEMPLATES[name] || !/^[a-z][a-z0-9_-]*$/.test(name || "")) return TEMPLATES[name] || null;
  try {
    const response = await fetch(`assets/templates/${name}.yaml`);
    if (!response.ok) return null;
    registerTemplate(name, parse(await response.text()));
  } catch { /* offline or file:// */ }
  return TEMPLATES[name] || null;
}

// Default alfrd.yaml (assets/defaults/alfrd.yaml, same file the server uses):
// read for a folder without its own alfrd.yaml; a local file replaces it.
let DEFAULT_MANIFEST = null;

/** Make the default alfrd.yaml text available synchronously. */
export function registerDefaultManifest(text) {
  DEFAULT_MANIFEST = typeof text === "string" && text.trim() ? text : null;
}

/** Fetch assets/defaults/alfrd.yaml (browser); null when offline or file://. */
export async function loadDefaultManifest() {
  if (DEFAULT_MANIFEST) return DEFAULT_MANIFEST;
  try {
    const response = await fetch("assets/defaults/alfrd.yaml");
    if (response.ok) registerDefaultManifest(await response.text());
  } catch { /* offline or file:// */ }
  return DEFAULT_MANIFEST;
}

/** Default alfrd.yaml text with `name:` set to the folder name (null when none is loaded). */
export function defaultManifestText(folderName = null) {
  if (!DEFAULT_MANIFEST) return null;
  if (!folderName) return DEFAULT_MANIFEST;
  const line = `name: ${JSON.stringify(String(folderName))}`;
  return /^name:.*$/m.test(DEFAULT_MANIFEST) ? DEFAULT_MANIFEST.replace(/^name:.*$/m, line) : `${line}\n${DEFAULT_MANIFEST}`;
}

/** `template:` from alfrd.yaml; an `avica:` block or AVICA entrypoints imply "avica". */
export function templateName(manifest) {
  if (!manifest || typeof manifest !== "object") return null;
  if (typeof manifest.template === "string" && manifest.template) return manifest.template;
  if (manifest.avica && typeof manifest.avica === "object") return "avica";
  const cmds = (manifest.entrypoint || []).map((e) => (Array.isArray(e?.cmd) ? e.cmd[0] : String(e?.cmd || "").split(/\s+/)[0]));
  if (cmds.includes("avica") || /avica\.pipe\.steps/.test(JSON.stringify(manifest.schema || ""))) return "avica";
  return null;
}

function stepList(manifest) {
  const wfs = manifest?.workflows;
  const first = Array.isArray(wfs) ? wfs[0] : wfs && typeof wfs === "object" ? Object.values(wfs)[0] : null;
  const steps = Array.isArray(first) ? first : first?.steps;
  return Array.isArray(steps) ? steps : [];
}

export function stepId(step) {
  if (typeof step === "string") return step;
  if (step && typeof step === "object") return String(step.id ?? step.key ?? step.name ?? "");
  return "";
}

/**
 * alfrd.yaml merged with its template:
 * { template, stages, steps: {id: def}, stepOrder, stepDefaults, overview, results, settings, artifacts }
 */
export function studioManifest(manifest) {
  const m = manifest && typeof manifest === "object" && !Array.isArray(manifest) ? manifest : {};
  const name = templateName(m);
  const tpl = (name && TEMPLATES[name]) || {};
  const steps = {};
  const tplSteps = tpl.steps || {};
  const list = stepList(m);
  list.forEach((raw) => {
    const id = resolveAlias(stepId(raw)).name;
    if (!id) return;
    const own = raw && typeof raw === "object" ? Object.fromEntries(Object.entries(raw).filter(([k]) => !["id", "key", "name"].includes(k))) : {};
    steps[id] = { ...(tplSteps[id] || {}), ...own };
  });
  const byName = new Map();
  [...(tpl.artifacts || []), ...(Array.isArray(m.artifacts) ? m.artifacts : [])].forEach((a) => { if (a && a.name) byName.set(a.name, a); });
  return {
    template: name,
    templateLoaded: Boolean(name && TEMPLATES[name]),
    stages: Array.isArray(m.stages) ? m.stages : tpl.stages || null,
    steps,
    tplSteps,
    stepOrder: list.map((x) => resolveAlias(stepId(x)).name).filter(Boolean),
    stepDefaults: { ...(tpl.step_defaults || {}), ...(m.step_defaults || {}) },
    overview: { ...(tpl.overview || {}), ...(m.overview || {}) },
    results: { ...(tpl.results || {}), ...(m.results || {}) },
    settings: { ...(tpl.project_settings || {}), ...(m.project_settings || {}) },
    artifacts: [...byName.values()],
  };
}

/** Install the field aliases of a manifest (none when it declares none). */
export function applyFieldAliases(manifest) {
  return setAliasRules(studioManifest(manifest).settings.field_aliases || {});
}

// ---------------------------------------------------------------------------
// Patterns: {placeholder} = one folder level, * and ? = shell wildcards.

const GROUPS = { project_code: "[^/]+", n: "\\d+", band: "[A-Z][A-Z0-9]*?", target: "[^/]+?", workdirname: "wd(?:_\\d+)?" };
const reEsc = (t) => String(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const TOKEN = /(\{\w+\}|\*|\?)/;

/** Legacy `{target_dir}/{project_code}/{workdir}` → `{workdir}`. */
export function normalizePattern(p) {
  return String(p || "").trim().replace("{target_dir}/{project_code}/{workdir}", "{workdir}");
}

export function fillPattern(pattern, fixed = {}) {
  const text = String(pattern).replace(/\{(\w+)\}/g, (all, k) => (fixed[k] != null && fixed[k] !== "" ? String(fixed[k]).replace(/^\/+|\/+$/g, "") : all));
  // A root target_dir (".") leaves "./" segments behind; drop them (mirrors studio_defs.fill).
  return text.replace(/(^|\/)\.(\/|$)/g, "$1").replace(/\/$/, "");
}

function tokensToRegex(text, known = {}) {
  const seen = new Set();
  return text.split(TOKEN).filter(Boolean).map((tok) => {
    if (tok === "*") return "[^/]*";
    if (tok === "?") return "[^/]";
    const m = /^\{(\w+)\}$/.exec(tok);
    if (!m) return reEsc(tok);
    const name = m[1];
    if (known[name] != null) return reEsc(known[name]);
    if (seen.has(name)) return `\\k<${name}>`;
    seen.add(name);
    return `(?<${name}>${GROUPS[name] || "[^/]+?"})`;
  }).join("");
}

/** Regex for one folder-name segment (placeholders already captured are literal). */
export function segmentRegex(seg, known = {}) {
  return new RegExp(`^${tokensToRegex(seg, known)}$`);
}

/** Whole-path regex. */
export function pathRegex(pattern) {
  return new RegExp(`^${tokensToRegex(String(pattern).replace(/^\/+|\/+$/g, ""))}$`);
}

export const hasWildcard = (seg) => TOKEN.test(seg);

// ---------------------------------------------------------------------------
// Logs

/** Every log pattern: [{pattern, group, step}] (step logs + log artifacts). */
export function logSpecs(defs) {
  const out = [];
  const defaults = (defs.stepDefaults?.logs || []).map(String);
  Object.entries(defs.steps || {}).forEach(([id, s]) => {
    [...(Array.isArray(s.logs) ? s.logs : s.logs ? [s.logs] : []), ...defaults].forEach((p) => out.push({ pattern: normalizePattern(p), group: `step:${id}`, step: id }));
  });
  (defs.artifacts || []).forEach((a) => {
    if (!a?.path_pattern) return;
    if (a.kind === "log" || a.viewer === "log" || a.show_in_logs) out.push({ pattern: normalizePattern(a.path_pattern), group: `artifact:${a.name}`, step: null, label: a.description || a.name });
  });
  return out;
}

/** Fill a spec for one context; returns a list of {pattern, wd} (one per work dir when needed). */
export function specInstances(spec, ctx) {
  const base = { logs: ctx.logsDir, target_dir: ctx.targetDir, ...(spec.step ? { step: spec.step } : {}) };
  if (/\{(workdir|meta_dir)\}/.test(spec.pattern)) {
    return (ctx.workdirs || []).map((wd) => ({ pattern: fillPattern(spec.pattern, { ...base, workdir: wd.rel, meta_dir: `${wd.rel}/${ctx.metaDir || "avica.meta"}` }), wd }));
  }
  if (/\{step\}/.test(spec.pattern) && !spec.step) return [];
  return [{ pattern: fillPattern(spec.pattern, base), wd: null }];
}

/** Match listed file paths against the log specs → [{rel, name, size, groups, steps, band, target, workdir}]. */
export function classifyLogs(entries, defs, ctx) {
  const inst = logSpecs(defs).flatMap((spec) => specInstances(spec, ctx).map((i) => ({ ...i, spec, re: /\{(logs|target_dir|workdir|meta_dir)\}/.test(i.pattern) ? null : pathRegex(i.pattern) }))).filter((i) => i.re);
  const out = new Map();
  entries.forEach((e) => {
    if (e.marker) return;
    inst.forEach((i) => {
      const m = i.re.exec(e.rel);
      if (!m) return;
      const item = out.get(e.rel) || { rel: e.rel, name: e.name || e.rel.split("/").pop(), size: e.size || 0, mtime: e.mtime || e.file?.lastModified || null, groups: [], steps: [], file: e.file, text: e.text };
      if (!item.groups.includes(i.spec.group)) item.groups.push(i.spec.group);
      if (i.spec.step && !item.steps.includes(i.spec.step)) item.steps.push(i.spec.step);
      if (m.groups?.band) item.band = m.groups.band;
      if (m.groups?.target) item.target = m.groups.target;
      if (i.wd) item.workdir = i.wd.id;
      out.set(e.rel, item);
    });
  });
  return [...out.values()].sort((a, b) => (b.mtime || 0) - (a.mtime || 0) || b.rel.localeCompare(a.rel));
}

/** Human label for a log group id. */
export function groupLabel(group, defs) {
  const [kind, id] = String(group).split(/:(.+)/);
  if (kind === "step") return defs?.steps?.[id]?.label ? `${id} — ${defs.steps[id].label}` : id;
  if (kind === "plan") return `Scheduled run ${id}`;
  const a = (defs?.artifacts || []).find((x) => x.name === id);
  return a?.description || id;
}

// ---------------------------------------------------------------------------
// Metadata health (alfrd.yaml step `metadata:` entries)

function dig(obj, path) {
  return String(path).split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), obj);
}
function emptyValue(v) {
  return v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length) || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length);
}

/** Per-step metadata specs in workflow order: [{step, entries:[{file, label, require}]}]. */
export function metadataSpecs(defs, order = null) {
  const keys = order || defs.stepOrder || Object.keys(defs.steps || {});
  return keys.map((step) => ({ step, entries: (defs.steps?.[step]?.metadata || []).map((m) => (typeof m === "string" ? { file: m } : m)).filter((m) => m && (m.file || m.path)) }))
    .filter((s) => s.entries.length);
}

/**
 * Evaluate metadata health for one work dir + target.
 * @returns [{step, stepStatus, status, entries:[{label, pattern, status, files:[{name, band, status, note}]}]}]
 *   status: passed | warning | failed | missing | notrun
 */
export function metadataHealth(defs, wd, target, stepStates = {}, order = null) {
  const meta = wd?.meta || [];
  const bands = wd?.bands || [];
  return metadataSpecs(defs, order).map(({ step, entries }) => {
    const stepStatus = stepStates?.[step]?.status || "pending";
    const ran = stepStatus !== "pending" && stepStatus !== "queued";
    const results = entries.map((spec) => {
      const pattern = spec.file || String(spec.path).replace(/^\{meta_dir\}\//, "");
      const re = segmentRegex(pattern, target ? { target } : {});
      const hits = meta.filter((m) => re.test(m.name));
      const files = hits.map((m) => {
        let status = "passed";
        let note = "";
        if (m.error) { status = "failed"; note = m.error; }
        else if (m.format === "json") {
          const missing = (spec.require || []).filter((k) => emptyValue(dig(m.data, k)));
          if (missing.length) { status = "warning"; note = `empty or missing: ${missing.join(", ")}`; }
          else if (emptyValue(m.data)) { status = "warning"; note = "file is empty"; }
        } else if (!String(m.text ?? "").trim() && !m.size) { status = "warning"; note = "file is empty"; }
        return { name: m.name, band: m.band || (re.exec(m.name)?.groups?.band ?? null), status, note, size: m.size };
      });
      // Bands expected when the pattern is per band.
      const perBand = /\{band\}/.test(pattern);
      const missingBands = perBand && bands.length ? bands.filter((b) => !files.some((f) => f.band === b)) : [];
      let status;
      if (!files.length) status = ran && stepStatus !== "failed" ? "missing" : ran ? "failed" : "notrun";
      else if (files.some((f) => f.status === "failed")) status = "failed";
      else if (files.some((f) => f.status === "warning") || missingBands.length) status = "warning";
      else status = "passed";
      return { label: spec.label || pattern, pattern, require: spec.require || [], status, files, missingBands };
    });
    const rank = { failed: 4, missing: 3, warning: 2, passed: 1, notrun: 0 };
    const worst = results.reduce((a, r) => (rank[r.status] > rank[a] ? r.status : a), results.length ? "passed" : "notrun");
    const status = results.every((r) => r.status === "notrun") ? "notrun" : worst;
    return { step, stepStatus, status, entries: results };
  });
}

/** Which step wrote a metadata file (for grouping the avica.meta listing). */
export function metaOwner(defs, name, target) {
  for (const { step, entries } of metadataSpecs(defs)) {
    for (const spec of entries) {
      const pattern = spec.file || String(spec.path).replace(/^\{meta_dir\}\//, "");
      if (segmentRegex(pattern, target ? { target } : {}).test(name)) return { step, label: spec.label || pattern };
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Overview columns

export function msPathPatterns(defs) {
  const v = defs.overview?.ms_path;
  return (Array.isArray(v) ? v : v ? [v] : []).map(normalizePattern);
}

/** Directory markers / listed paths → {target: [{rel, band, order}]}. */
export function msPathsByTarget(paths) {
  const out = {};
  (paths || []).forEach((p) => {
    if (!p?.target) return;
    (out[p.target] ||= []).push(p);
  });
  Object.values(out).forEach((l) => l.sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || String(a.band).localeCompare(String(b.band))));
  return out;
}
