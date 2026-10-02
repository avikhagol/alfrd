// Domain model: canonical AVICA steps, status vocabulary, manifest -> workflow
// normalization and per-target rollups. Pure functions, no DOM access.

import { resolveAlias, AliasLog } from "../utils/csv_parser.js";
import { studioManifest, applyFieldAliases, expandWorkflowSteps } from "./defs.js";

// Step labels, stages, categories, descriptions, icons, metadata and logs are
// read from alfrd.yaml (and its `template:`), see data/defs.js. Nothing about a
// specific pipeline is hardcoded here.

export const STEP_STATUS = {
  completed: { label: "Completed", icon: "checkCircle", tone: "ok" },
  failed: { label: "Failed", icon: "xCircle", tone: "fail" },
  warning: { label: "Partial", icon: "alert", tone: "warn" },
  running: { label: "Running", icon: "sync", tone: "run" },
  queued: { label: "Queued", icon: "hourglass", tone: "muted" },
  pending: { label: "Pending", icon: "clock", tone: "muted" },
  skipped: { label: "Skipped", icon: "minus", tone: "muted" },
};

export const OVERALL_STATUS = {
  completed: { label: "Completed", icon: "checkCircle", tone: "ok" },
  failed: { label: "Failed", icon: "xCircle", tone: "fail" },
  warning: { label: "Attention", icon: "alert", tone: "warn" },
  running: { label: "Running", icon: "sync", tone: "run" },
  unknown: { label: "Unknown", icon: "help", tone: "muted" },
};

/** Map any external status vocabulary onto the Studio's step statuses. */
export function normalizeStatus(value) {
  const v = String(value ?? "").toLowerCase();
  if (["ok", "succeeded", "success", "completed", "done", "true", "passed"].includes(v)) return "completed";
  if (["failed", "fail", "error", "false"].includes(v)) return "failed";
  if (["partial", "warning", "warn", "attention"].includes(v)) return "warning";
  if (["running", "active", "simulating", "in_progress"].includes(v)) return "running";
  if (["queued"].includes(v)) return "queued";
  if (["skipped", "interrupted", "cancelled"].includes(v)) return "skipped";
  return "pending";
}

/** Latest-attempt state for one step of a target. */
export function stepState(target, key) {
  return (target.steps && target.steps[key]) || { status: "pending", attempts: [] };
}

/** Roll a target's step states up into an overall status and counters. */
export function rollup(target, steps) {
  const keys = steps.length ? steps : Object.keys(target.steps || {});
  let done = 0;
  let runtime = 0;
  let hasRuntime = false;
  let failed = null;
  let warning = null;
  let running = null;
  let touched = 0;
  keys.forEach((key, index) => {
    const s = stepState(target, key);
    if (s.status !== "pending") touched += 1;
    if (s.status === "completed") done += 1;
    if (Number.isFinite(s.duration)) {
      runtime += s.duration;
      hasRuntime = true;
    }
    if (s.status === "failed" && !failed) failed = { key, index, state: s };
    if (s.status === "warning" && !warning) warning = { key, index, state: s };
    if (s.status === "running" && !running) running = { key, index, state: s };
  });
  let status = "unknown";
  if (failed) status = "failed";
  else if (running) status = "running";
  else if (warning) status = "warning";
  else if (keys.length && done === keys.length) status = "completed";
  else if (touched > 0) status = "warning";
  const next = keys.findIndex((k) => stepState(target, k).status !== "completed");
  return {
    status, done, total: keys.length, runtime: hasRuntime ? runtime : null,
    failed, warning, running, next: next === -1 ? null : next,
  };
}

/**
 * Normalize any supported ALFRD manifest shape into a Studio workflow list.
 * Supported: `workflows:` as a list or mapping (steps as strings or objects),
 * and legacy `entrypoint:` lists. Returns { name, workflows, errors, warnings, aliases, manifest }.
 */
export function manifestToWorkflows(manifest, fileName = "alfrd.yaml", { aliases: applyAliases = true } = {}) {
  const errors = [];
  const warnings = [];
  const workflows = [];
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    return { name: fileName, workflows, errors: ["Manifest must be a YAML mapping."], warnings, aliases: [], manifest };
  }
  if (applyAliases) applyFieldAliases(manifest);
  const aliases = new AliasLog();
  const defs = studioManifest(manifest);
  if (defs.template && !defs.templateLoaded) warnings.push(`Template "${defs.template}" is not available; using the step definitions in ${fileName} only.`);
  const project = manifest.project && typeof manifest.project === "object" ? manifest.project : {};
  const name = project.id || project.name || manifest.name || fileName.replace(/\.ya?ml$/, "");
  const stageDefs = Array.isArray(defs.stages)
    ? defs.stages.map((s, i) => (typeof s === "string" ? { id: s, title: s } : { id: s.id ?? `stage${i + 1}`, title: s.title ?? s.label ?? s.id }))
    : null;

  const addWorkflow = (wfName, def) => {
    const rawSteps = expandWorkflowSteps(def);
    if (!Array.isArray(rawSteps) || !rawSteps.length) {
      errors.push(`Workflow "${wfName}" declares no steps.`);
      return;
    }
    const seen = new Set();
    const steps = rawSteps.map((raw, index) => {
      const original = stepId(raw);
      if (!original) errors.push(`Workflow "${wfName}" step #${index + 1} has no id.`);
      const key = aliases.resolve(original, fileName);
      if (seen.has(key)) errors.push(`Workflow "${wfName}" repeats step "${key}".`);
      seen.add(key);
      const obj = { ...(defs.tplSteps[key] || {}), ...(defs.steps[key] || {}), ...(raw && typeof raw === "object" ? raw : {}) };
      const dependsRaw = obj.depends_on ?? obj.needs ?? obj.after;
      const depends = dependsRaw === undefined ? null : (Array.isArray(dependsRaw) ? dependsRaw : [dependsRaw]).map((d) => aliases.resolve(d, fileName));
      return {
        key,
        alias: original !== key ? original : null,
        label: obj.label || obj.description_short || key,
        short: obj.short || key,
        description: obj.description || "",
        category: obj.category || "Step",
        stage: obj.stage || null,
        icon: obj.icon || "pipeline",
        params: { ...(obj.params || obj.parameters || {}) },
        inputs: obj.inputs || [],
        outputs: obj.outputs || [],
        metadata: obj.metadata || [],
        logs: obj.logs || [],
        depends,
        command: obj.command || obj.cmd || null,
      };
    });
    // Default dependency chain: each step waits on its predecessor.
    steps.forEach((step, i) => {
      if (step.depends === null) step.depends = i ? [steps[i - 1].key] : [];
      step.depends.forEach((d) => {
        if (!seen.has(d)) errors.push(`Step "${step.key}" depends on unknown step "${d}".`);
      });
    });
    workflows.push({
      name: String(wfName),
      label: (!Array.isArray(def) && def?.label) || String(wfName),
      description: (!Array.isArray(def) && def?.description) || "",
      template: defs.template,
      steps,
      stages: assignStages(steps, stageDefs),
    });
  };

  const templateSteps = Object.keys(defs.tplSteps || {});
  if (Array.isArray(manifest.workflows)) {
    manifest.workflows.forEach((wf, i) => addWorkflow(wf?.name ?? wf?.id ?? `workflow_${i + 1}`, wf));
  } else if (manifest.workflows && typeof manifest.workflows === "object") {
    Object.entries(manifest.workflows).forEach(([k, v]) => addWorkflow(v?.name ?? k, v));
  } else if (Array.isArray(manifest.entrypoint) && defs.template && templateSteps.length) {
    // Entrypoint-only manifest (e.g. AVICA's own alfrd.yaml): the step list comes from the template.
    addWorkflow(defs.template, templateSteps);
    const wf = workflows[workflows.length - 1];
    if (wf) wf.entrypoints = manifest.entrypoint.map((e) => ({ name: e.name, command: e.cmd }));
    warnings.push(`Entrypoint-only manifest: using the step list of template "${defs.template}".`);
  } else if (Array.isArray(manifest.entrypoint)) {
    addWorkflow("default", manifest.entrypoint.map((e) => ({ id: e.name, command: e.cmd })));
  } else if (Array.isArray(manifest.steps)) {
    addWorkflow("default", manifest.steps);
  } else {
    errors.push("No `workflows:` (or legacy `entrypoint:`) section found.");
  }
  const aliasList = aliases.list();
  return { name, workflows, errors, warnings, aliases: aliasList, manifest, defs };
}

function stepId(step) {
  if (typeof step === "string") return step;
  if (step && typeof step === "object") return step.id ?? step.key ?? step.name ?? "";
  return "";
}

/** Group steps into canvas stages (explicit `stage:`, canonical, or chunks of 3). */
function assignStages(steps, stageDefs) {
  const defs = stageDefs || [];
  const byId = new Map(defs.map((d) => [d.id, { ...d, steps: [] }]));
  const allKnown = steps.every((s) => s.stage && byId.has(s.stage));
  if (!allKnown) {
    const custom = [];
    const explicit = new Map();
    steps.forEach((s, i) => {
      const id = s.stage && byId.has(s.stage) ? s.stage : s.stage || `group${Math.floor(i / 3) + 1}`;
      s.stage = id;
      if (!explicit.has(id)) {
        const def = byId.get(id) || { id, title: /^group\d+$/.test(id) ? `Stage ${custom.length + 1}` : id };
        explicit.set(id, { ...def, steps: [] });
        custom.push(explicit.get(id));
      }
      explicit.get(id).steps.push(s.key);
    });
    return custom;
  }
  steps.forEach((s) => byId.get(s.stage).steps.push(s.key));
  return Array.from(byId.values()).filter((d) => d.steps.length);
}

/** Empty workflow shown before any alfrd.yaml is loaded. */
export function defaultWorkflow() {
  return { name: "workflow", label: "No workflow loaded", description: "", template: null, steps: [], stages: [] };
}

/** Resolve an identifier through legacy aliases (re-exported for importers). */
export function canonicalStep(name) {
  return resolveAlias(name).name;
}

/** Search haystack for a target row. */
export function targetText(target) {
  const parts = [target.name, target.project, target.msPath, target.fitsidi, target.meta?.file, target.notes];
  Object.values(target.steps || {}).forEach((s) => parts.push(s.note));
  Object.values(target.columns || {}).forEach((v) => parts.push(v));
  return parts.filter(Boolean).join(" ").toLowerCase();
}
