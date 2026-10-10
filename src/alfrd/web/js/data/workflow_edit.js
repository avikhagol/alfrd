// Pure edits of alfrd.yaml data for the Workflow editor: each returns a changed
// copy; `workflowText` rewrites only changed top-level sections.

import { parseYaml, dumpYaml } from "../utils/yaml_parser.js";
import { replaceSection } from "./agent_settings.js";
import { isSequence, stepId } from "./defs.js";

export const STEP_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const SECTIONS = ["workflows", "steps", "stages", "execution", "step_defaults"];
const sectionsOf = (data) => [...new Set([...SECTIONS, ...Object.keys(isMap(data) ? data : {})])];
// Keys the step form edits; other keys of a step mapping (metadata, params, …) are kept as they are.
const FORM_KEYS = ["label", "category", "stage", "description", "cmd", "entrypoint", "depends_on", "after", "timeout", "status_from", "logs", "skip"];

const clone = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v)));
const isMap = (v) => v && typeof v === "object" && !Array.isArray(v);

/** The first workflow object (list or mapping form), or null. */
export function firstWorkflowOf(data) {
  const wfs = data?.workflows;
  if (Array.isArray(wfs)) return isMap(wfs[0]) ? wfs[0] : null;
  if (isMap(wfs)) { const v = Object.values(wfs)[0]; return isMap(v) ? v : null; }
  return null;
}

/** "sequence" (agent loop: repeat.sequence), "steps" (a step list), or "none" (nothing declared yet). */
export function workflowKind(data, template = null) {
  const wf = firstWorkflowOf(data) || firstWorkflowOf(template);
  if (isSequence(wf)) return "sequence";
  return wf && (Array.isArray(wf.steps) ? wf.steps.length : isMap(wf.steps) && Object.keys(wf.steps).length) ? "steps" : "none";
}

function stepItems(wf) {
  const raw = wf?.steps;
  if (Array.isArray(raw)) return raw;
  if (isMap(raw)) return Object.entries(raw).map(([id, v]) => (isMap(v) ? { id, ...v } : id));
  return [];
}

/** Rows for the editor: one per declared step, skipped ones included. */
export function editorSteps(data, template = null) {
  const own = isMap(data?.steps) ? data.steps : {};
  const tplSteps = isMap(template?.steps) ? template.steps : {};
  return stepItems(firstWorkflowOf(data) || firstWorkflowOf(template)).map((item, index) => {
    const id = stepId(item);
    const inline = isMap(item) ? item : {};
    const merged = { ...(tplSteps[id] || {}), ...(isMap(own[id]) ? own[id] : {}), ...inline };
    const dep = merged.depends_on ?? merged.needs;
    return {
      id, index,
      skip: merged.skip === true,
      label: merged.label || "",
      category: merged.category || "",
      stage: merged.stage || "",
      description: merged.description || "",
      cmd: Array.isArray(merged.cmd) ? merged.cmd.map(String) : null,
      entrypoint: merged.entrypoint || "",
      depends_on: dep == null ? null : (Array.isArray(dep) ? dep : [dep]).map(String),
      after: merged.after ?? null,
      timeout: merged.timeout ?? null,
      status_from: merged.status_from ?? null,
      logs: Array.isArray(merged.logs) ? merged.logs : [],
      fromTemplate: Boolean(tplSteps[id]),
    };
  });
}

export function stageList(data, template = null) {
  const raw = Array.isArray(data?.stages) ? data.stages : Array.isArray(template?.stages) ? template.stages : [];
  return raw.map((s, i) => (typeof s === "string" ? { id: s, title: s } : { id: String(s.id ?? `stage${i + 1}`), title: String(s.title ?? s.label ?? s.id ?? "") }));
}

export function entrypointNames(data, template = null) {
  const names = [...(Array.isArray(template?.entrypoint) ? template.entrypoint : []), ...(Array.isArray(data?.entrypoint) ? data.entrypoint : [])]
    .map((e) => e?.name).filter(Boolean).map(String);
  return [...new Set(names)];
}

/** The command a step runs when it names none of its own (workflow / execution default). */
export function defaultEntrypoint(data, template = null) {
  return (firstWorkflowOf(data) || firstWorkflowOf(template))?.entrypoint || data?.execution?.step_entrypoint || template?.execution?.step_entrypoint || "";
}

// --- edits -------------------------------------------------------------------

/** A copy whose first workflow exists and keeps its steps as a list (seeded from the template's). */
export function withWorkflow(data, template = null) {
  const out = clone(isMap(data) ? data : {});
  let wf = firstWorkflowOf(out);
  if (!wf) {
    const tplWf = firstWorkflowOf(template);
    wf = tplWf ? clone(tplWf) : { name: String(out.name || "main"), steps: [] };
    if (isMap(out.workflows) && !Object.keys(out.workflows).length) out.workflows = [wf];
    else if (Array.isArray(out.workflows)) out.workflows.unshift(wf);
    else out.workflows = [wf];
  }
  if (!Array.isArray(wf.steps)) wf.steps = stepItems(wf);
  return out;
}

const findIndex = (wf, id) => wf.steps.findIndex((s) => stepId(s) === id);

/** A step mapping with only `id` collapses back to its name. */
function compact(item) {
  if (!isMap(item)) return item;
  const keys = Object.keys(item);
  return keys.length === 1 && keys[0] === "id" ? item.id : item;
}

function asMap(item) {
  return isMap(item) ? { ...item } : { id: String(item) };
}

/** Insert a step at `index` (default: the end). `step` is {id, ...form fields}. */
export function addStep(data, step, index = null, template = null) {
  const id = String(step?.id || "").trim();
  if (!STEP_ID.test(id)) throw new Error("A step name uses letters, digits, '-', '_' or '.', and starts with a letter or digit.");
  const out = withWorkflow(data, template);
  const wf = firstWorkflowOf(out);
  if (findIndex(wf, id) >= 0) throw new Error(`The workflow already has a step "${id}".`);
  const item = compact(applyFields({ id }, step));
  const at = index == null || index < 0 || index > wf.steps.length ? wf.steps.length : index;
  wf.steps.splice(at, 0, item);
  return out;
}

function applyFields(item, fields) {
  for (const key of FORM_KEYS) {
    if (!(key in fields)) continue;
    const v = fields[key];
    // depends_on: [] means "starts first" (no wait); null means "after the previous step".
    const empty = v == null || v === "" || (v === false && key !== "skip") || (Array.isArray(v) && !v.length && key !== "depends_on");
    if (empty) delete item[key];
    else item[key] = clone(v);
  }
  return item;
}

/** Change a step's form fields; renaming it updates the steps that wait on it. */
export function updateStep(data, id, fields, template = null) {
  const out = withWorkflow(data, template);
  const wf = firstWorkflowOf(out);
  const i = findIndex(wf, id);
  if (i < 0) throw new Error(`No step "${id}" in the workflow.`);
  const next = String(fields.id ?? id).trim();
  if (next !== id) {
    if (!STEP_ID.test(next)) throw new Error("A step name uses letters, digits, '-', '_' or '.', and starts with a letter or digit.");
    if (findIndex(wf, next) >= 0) throw new Error(`The workflow already has a step "${next}".`);
  }
  const item = applyFields(asMap(wf.steps[i]), fields);
  item.id = next;
  wf.steps[i] = compact(item);
  if (next !== id) renameRefs(wf, id, next);
  return out;
}

function renameRefs(wf, from, to) {
  wf.steps = wf.steps.map((s) => {
    if (!isMap(s)) return s;
    const m = { ...s };
    for (const key of ["depends_on", "needs"]) {
      if (Array.isArray(m[key])) m[key] = m[key].map((d) => (d === from ? to : d));
      else if (m[key] === from) m[key] = to;
    }
    if (m.after === from) m.after = to;
    return m;
  });
}

/** Skip (or run again) a step: `{id, skip: true}` in the workflow list. */
export function setSkip(data, id, skip, template = null) {
  const out = withWorkflow(data, template);
  const wf = firstWorkflowOf(out);
  const i = findIndex(wf, id);
  if (i < 0) throw new Error(`No step "${id}" in the workflow.`);
  const item = asMap(wf.steps[i]);
  if (skip) item.skip = true;
  else if (template?.steps?.[id]?.skip === true) item.skip = false;
  else delete item.skip;
  wf.steps[i] = compact(item);
  // A skip in the project's own step definitions would still hide it.
  if (!skip && isMap(out.steps?.[id]) && out.steps[id].skip) {
    delete out.steps[id].skip;
    if (!Object.keys(out.steps[id]).length) delete out.steps[id];
  }
  return out;
}

/** Move a step to position `to` (clamped) in the list. */
export function moveStep(data, id, to, template = null) {
  const out = withWorkflow(data, template);
  const wf = firstWorkflowOf(out);
  const i = findIndex(wf, id);
  if (i < 0) throw new Error(`No step "${id}" in the workflow.`);
  const [item] = wf.steps.splice(i, 1);
  wf.steps.splice(Math.max(0, Math.min(wf.steps.length, to)), 0, item);
  return out;
}

/** Delete a step; steps that waited on it no longer do. */
export function removeStep(data, id, template = null) {
  const rows = editorSteps(data, template);
  const out = withWorkflow(data, template);
  const wf = firstWorkflowOf(out);
  const i = findIndex(wf, id);
  if (i < 0) throw new Error(`No step "${id}" in the workflow.`);
  wf.steps.splice(i, 1);
  wf.steps = wf.steps.map((s) => {
    const key = stepId(s);
    const external = { ...(template?.steps?.[key] || {}), ...(out.steps?.[key] || {}) };
    const dep = external.depends_on ?? external.needs;
    const inheritedRef = (Array.isArray(dep) ? dep : [dep]).includes(id) || external.after === id;
    if (!isMap(s) && !inheritedRef) return s;
    const m = asMap(s);
    for (const key of ["depends_on", "needs"]) {
      if (Array.isArray(m[key])) { m[key] = m[key].filter((d) => d !== id); if (!m[key].length) delete m[key]; }
      else if (m[key] === id) delete m[key];
    }
    if (m.after === id) delete m.after;
    // Override references supplied by reusable definitions in this workflow only.
    // Explicit [] prevents deleted inherited dependencies from reappearing.
    if (inheritedRef) {
      const row = rows.find((r) => r.id === key);
      if (row?.depends_on?.includes(id)) m.depends_on = row.depends_on.filter((d) => d !== id);
      if (row?.after === id) m.after = null;
    }
    return compact(m);
  });
  return out;
}

/** Add a stage (kept after the template's stages when the project had none of its own). */
export function addStage(data, stage, template = null) {
  const id = String(stage?.id || "").trim();
  if (!STEP_ID.test(id)) throw new Error("A stage id uses letters, digits, '-', '_' or '.'.");
  const out = clone(data);
  const list = stageList(out, template);
  if (list.some((s) => s.id === id)) return out;
  out.stages = [...list, { id, title: String(stage.title || id) }];
  return out;
}

/** Stage order/titles from [{id, title}]; ids and other stage keys are kept. */
export function setStages(data, list, template = null) {
  const raw = Array.isArray(data?.stages) ? data.stages : Array.isArray(template?.stages) ? template.stages : [];
  const byId = new Map(stageList(data, template).map((s, i) => [s.id, raw[i]]));
  const ids = new Set();
  const out = clone(data);
  out.stages = list.map((s) => {
    const id = String(s.id || "").trim();
    if (!STEP_ID.test(id) || ids.has(id)) throw new Error(`Stage id "${id}": invalid or repeated.`);
    ids.add(id);
    const title = String(s.title || "").trim();
    if (!title) throw new Error("Name this stage.");
    const old = byId.get(id);
    return isMap(old) ? { ...clone(old), id, title } : { id, title };
  });
  return out;
}

// --- workflow settings (execution / step_defaults / workflow entrypoint) ----------

/** Each setting as {own, inherited}; own is undefined without an override. */
export function workflowSettings(data, template = null) {
  const wf = firstWorkflowOf(data), tplWf = firstWorkflowOf(template);
  const ex = isMap(data?.execution) ? data.execution : {}, tplEx = isMap(template?.execution) ? template.execution : {};
  const own = (o, k) => (isMap(o) && k in o ? o[k] : undefined);
  const setting = (k, fallback) => ({ own: own(ex, k), inherited: k in tplEx ? tplEx[k] : fallback });
  return {
    entrypoint: { own: own(wf, "entrypoint"), inherited: (wf ? "" : tplWf?.entrypoint) || ex.step_entrypoint || tplEx.step_entrypoint || "" },
    concurrency: setting("concurrency", 1),
    on_failure: setting("on_failure", "stop_target"),
    timeout: setting("timeout", null),
    logs: { own: own(data?.step_defaults, "logs"), inherited: own(template?.step_defaults, "logs") ?? [] },
  };
}

export const ON_FAILURE = ["stop_target", "continue", "stop_plan"];

/** Keys of `s` set to undefined remove the override; others are written; unchanged keys are left alone. */
export function applySettings(data, s, template = null) {
  let out = clone(isMap(data) ? data : {});
  const cur = workflowSettings(out, template);
  const same = (k) => JSON.stringify(cur[k].own) === JSON.stringify(s[k]);
  if ("entrypoint" in s && !same("entrypoint")) {
    out = withWorkflow(out, template);
    const wf = firstWorkflowOf(out);
    if (s.entrypoint === undefined) delete wf.entrypoint; else wf.entrypoint = String(s.entrypoint);
  }
  for (const k of ["concurrency", "on_failure", "timeout"]) {
    if (!(k in s) || same(k)) continue;
    const v = s[k];
    if (v != null && !(k === "concurrency" ? Number.isInteger(v) && v > 0 : k === "timeout" ? Number.isFinite(v) && v > 0 : ON_FAILURE.includes(v))) throw new Error(`Invalid execution.${k}: ${v}`);
    edit(out, "execution", k, v);
  }
  if ("logs" in s && !same("logs")) edit(out, "step_defaults", "logs", s.logs === undefined ? undefined : s.logs.map(String));
  return out;
}

function edit(out, section, key, value) {
  if (value === undefined) {
    if (!isMap(out[section])) return;
    delete out[section][key];
    if (!Object.keys(out[section]).length) delete out[section];
  } else {
    if (!isMap(out[section])) out[section] = {};
    out[section][key] = clone(value);
  }
}

// --- agent loop (repeat.sequence) ----------------------------------------------

/** One pass of agent names, or null when the sequence lists several passes. */
export function sequenceOf(data, template = null) {
  const seq = (firstWorkflowOf(data) || firstWorkflowOf(template))?.repeat?.sequence;
  if (!Array.isArray(seq)) return [];
  return seq.every((s) => typeof s === "string") ? [...seq] : null;
}

export function setSequence(data, agents, template = null) {
  const list = (agents || []).map(String).filter(Boolean);
  if (!list.length) throw new Error("An agent loop needs at least one turn.");
  const out = withWorkflow(data, template);
  firstWorkflowOf(out).repeat.sequence = list;
  return out;
}

export function setTotalTurns(data, n, template = null) {
  const total = Number(n);
  if (!Number.isInteger(total) || total < 1 || total > 200) throw new Error("Total turns: a whole number from 1 to 200.");
  const out = withWorkflow(data, template);
  const repeat = firstWorkflowOf(out).repeat;
  delete repeat.passes;
  repeat.iterations = total;
  return out;
}

// --- text --------------------------------------------------------------------

/** alfrd.yaml text with only changed top-level sections rewritten or removed. */
export function workflowText(text, data) {
  const before = parseYaml(text || "") || {};
  let out = String(text || "");
  for (const key of changedSections(before, data)) {
    out = replaceSection(out, key, key in data ? dumpYaml({ [key]: data[key] }) : "");
  }
  return out.endsWith("\n") ? out : `${out}\n`;
}

/** Number of changed sections between two parsed manifests (for the "unsaved" badge). */
export function changedSections(a, b) {
  return [...new Set([...sectionsOf(a), ...sectionsOf(b)])].filter((k) => JSON.stringify(a?.[k]) !== JSON.stringify(b?.[k]));
}

// --- commands ----------------------------------------------------------------

/** "python run.py --x 'a b'" → argv list (quotes group words; no shell). */
export function splitCommand(text) {
  const out = [];
  let cur = "", quote = null, has = false;
  for (const ch of String(text || "")) {
    if (quote) { if (ch === quote) quote = null; else cur += ch; continue; }
    if (ch === '"' || ch === "'") { quote = ch; has = true; continue; }
    if (/\s/.test(ch)) { if (has || cur) out.push(cur); cur = ""; has = false; continue; }
    cur += ch;
  }
  if (quote) throw new Error("The command has an unclosed quote.");
  if (has || cur) out.push(cur);
  return out;
}

export function joinCommand(argv) {
  return (argv || []).map((a) => (a === "" || /[\s"']/.test(a) ? (a.includes('"') ? `'${a}'` : `"${a}"`) : a)).join(" ");
}

/** A step name from a label: "Run tests" → "run-tests" (unique against `taken`). */
export function slugFor(label, taken = []) {
  const base = String(label || "step").toLowerCase().replace(/[^a-z0-9_.-]+/g, "-").replace(/^[-_.]+|[-_.]+$/g, "").slice(0, 48) || "step";
  const used = new Set(taken);
  let id = base, n = 2;
  while (used.has(id)) id = `${base}-${n++}`;
  return id;
}
