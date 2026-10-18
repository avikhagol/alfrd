// New-project canvas drafts (pure, no DOM): a template's alfrd.yaml data edited with the Workflow
// editor's own edits (data/workflow_edit.js), initial targets, and the payload for
// POST /api/studio/projects/create {draft: {steps, targets}}. The server validates again.
import {
  addStep, connectProblem, connectSteps, dependencyMap, disconnectSteps, editorSteps, entrypointNames, findCycle,
  firstWorkflowOf, removeStep, sequenceOf, setSequence, slugFor, STEP_ID, updateStep,
} from "./workflow_edit.js";
import { isSequence } from "./defs.js";

const clone = (v) => JSON.parse(JSON.stringify(v ?? null));
export const TASK_NAME = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$/;

/** A fresh draft from template data: its workflow (start "template") or no steps (start "empty"). */
export function newDraft(template, tpl, start = "template") {
  const wf = firstWorkflowOf(tpl);
  const kind = isSequence(wf) ? "sequence" : "steps";
  const data = { entrypoint: clone(tpl?.entrypoint || []), steps: clone(tpl?.steps || {}), project_settings: clone(tpl?.project_settings || {}) };
  let skipped = 0;
  if (kind === "sequence") data.workflows = [clone(wf)];
  else {
    const rows = start === "template" ? editorSteps(tpl) : [];
    skipped = rows.filter((r) => r.skip).length;
    const deps = start === "template" ? dependencyMap(tpl) : {};
    // Explicit dependencies, so the canvas shows exactly what is saved; skipped template steps are left out.
    data.workflows = [{ name: wf?.name || "main", steps: rows.filter((r) => !r.skip).map((r) => ({ id: r.id, depends_on: deps[r.id] || [] })) }];
  }
  return { v: 1, template, start, kind, data, roles: {}, targets: [], skipped, path: "", name: "", manifest: ".alfrd/alfrd.yaml", task: "", iterations: 10 };
}

export const agentsOf = (d) => entrypointNames(d.data);
export const personasOf = (d) => Object.entries(d.data?.project_settings?.personas || {}).map(([key, p]) => ({ key, label: p?.label || key }));
export const stepsOf = (d) => editorSteps(d.data).filter((r) => !r.skip);

/** Canvas model: steps (or one pass of agent turns) and their dependencies, with the draft's own labels. */
export function draftModel(d) {
  if (d.kind === "sequence") {
    const seq = sequenceOf(d.data) || [];
    const nodes = seq.map((a, i) => ({ id: `turn-${i + 1}`, title: a, sub: `turn ${i + 1} of each pass`, st: "pending", tone: "muted", label: "Agent", icon: "terminal", hint: i ? `After turn ${i}` : "First turn" }));
    return { nodes, edges: nodes.slice(1).map((n, i) => ({ from: nodes[i].id, to: n.id, tone: "ready", kind: "depends_on" })) };
  }
  const deps = dependencyMap(d.data);
  const nodes = stepsOf(d).map((r) => {
    const agent = r.cmd ? r.cmd.join(" ") : r.entrypoint ? `@${r.entrypoint}` : r.fromTemplate ? "template command" : "no command yet";
    const role = d.roles[r.id] ? ` · ${d.data.project_settings?.personas?.[d.roles[r.id]]?.label || d.roles[r.id]}` : "";
    return { id: r.id, title: r.label || r.id, sub: agent + role, st: "pending", tone: r.cmd || r.entrypoint || r.fromTemplate ? "muted" : "warn",
      label: (deps[r.id] || []).length ? `waits for ${deps[r.id].length}` : "starts first", icon: "clock", hint: (deps[r.id] || []).length ? `Waits for ${deps[r.id].join(", ")}` : "No dependencies" };
  });
  const edges = Object.entries(deps).flatMap(([to, list]) => list.map((from) => ({ from, to, tone: "ready", kind: "depends_on" })));
  return { nodes, edges };
}

// --- edits (each returns a new draft or throws with a message for the user) -------------------

export function addDraftStep(d, label = "New step") {
  if (d.kind === "sequence") {
    const agents = agentsOf(d);
    return { ...d, data: setSequence(d.data, [...(sequenceOf(d.data) || []), agents[0] || "agent"]) };
  }
  const id = slugFor(label, stepsOf(d).map((r) => r.id).concat(editorSteps(d.data).map((r) => r.id)));
  return { ...d, data: addStep(d.data, { id, label, depends_on: [] }), selected: id };
}

export function removeDraftStep(d, id) {
  if (d.kind === "sequence") {
    const seq = [...(sequenceOf(d.data) || [])];
    seq.splice(Number(String(id).replace("turn-", "")) - 1, 1);
    return { ...d, data: setSequence(d.data, seq), selected: null };
  }
  const roles = { ...d.roles }; delete roles[id];
  // Keep every other step's dependencies explicit (removing one must not bring back "after the previous step").
  const deps = dependencyMap(d.data);
  let data = removeStep(d.data, id);
  for (const [key, list] of Object.entries(deps)) if (key !== id) data = updateStep(data, key, { depends_on: list.filter((x) => x !== id) });
  return { ...d, data, roles, selected: null };
}

export function connectDraft(d, from, to) {
  if (d.kind === "sequence") throw new Error("Agent turns run in sequence order; reorder them instead of connecting.");
  return { ...d, data: connectSteps(d.data, from, to) };
}
export const connectDraftProblem = (d, from, to) => (d.kind === "sequence" ? "Agent turns run in sequence order." : connectProblem(d.data, from, to));
export const disconnectDraft = (d, from, to) => ({ ...d, data: disconnectSteps(d.data, from, to) });

/** Step fields from the inspector: {id, label, agent: entrypoint | "" , cmd: argv | null, role}. */
export function updateDraftStep(d, id, f) {
  if (d.kind === "sequence") {
    const seq = [...(sequenceOf(d.data) || [])];
    seq[Number(String(id).replace("turn-", "")) - 1] = f.agent;
    return { ...d, data: setSequence(d.data, seq) };
  }
  const next = String(f.id ?? id).trim();
  const data = updateStep(d.data, id, { id: next, label: f.label ?? "", entrypoint: f.cmd ? "" : f.agent || "", cmd: f.cmd || null });
  const roles = { ...d.roles }; delete roles[id];
  if (f.role) roles[next] = f.role;
  return { ...d, data, roles, selected: next };
}

// --- validation and payload ------------------------------------------------------

export function targetProblem(name, taken = [], task = false) {
  const n = String(name || "").trim();
  if (!n) return "Enter a name.";
  if (task && !TASK_NAME.test(n)) return "A task name uses 1–64 letters, digits, . _ or - and cannot start with a dot.";
  if (n.startsWith("#")) return "A name cannot start with #.";
  if (n.includes("@")) return "A name cannot contain @.";
  if (/[/\\]/.test(n)) return "A name cannot contain / or \\.";
  if (taken.includes(n)) return `“${n}” is already listed.`;
  return "";
}

/** {errors, warnings} before saving (the server repeats these checks). */
export function draftProblems(d) {
  const errors = [], warnings = [];
  if (!String(d.path || "").trim()) errors.push("Choose the project folder.");
  if (String(d.name || "").length > 200) errors.push("The project name is longer than 200 characters.");
  d.targets.forEach((t, i) => { const p = targetProblem(t, d.targets.slice(0, i), d.kind === "sequence"); if (p) errors.push(`Target “${t}”: ${p}`); });
  if (d.kind === "sequence") {
    if (!(sequenceOf(d.data) || []).length) errors.push("Add at least one agent turn.");
    if (!String(d.task || "").trim()) errors.push("An agent loop needs an initial task.");
    if (d.targets.length > 1) errors.push("An agent loop starts with one task; add more later from the Target picker.");
    const n = Number(d.iterations);
    if (!Number.isInteger(n) || n < 1 || n > 200) errors.push("Iterations: a whole number from 1 to 200.");
    const unknown = (sequenceOf(d.data) || []).filter((a) => !agentsOf(d).includes(a));
    if (unknown.length) errors.push(`Unknown agent(s) in the sequence: ${unknown.join(", ")}.`);
    return { errors, warnings };
  }
  const rows = stepsOf(d);
  if (!rows.length) errors.push("Add at least one step.");
  rows.forEach((r) => { if (!STEP_ID.test(r.id)) errors.push(`Step id “${r.id}” is not valid.`); });
  const deps = dependencyMap(d.data);
  for (const [id, list] of Object.entries(deps)) for (const dep of list) if (!(dep in deps)) errors.push(`${id} waits for unknown step ${dep}.`);
  const cycle = findCycle(deps);
  if (cycle) errors.push(`Dependency cycle: ${cycle.join(" → ")}.`);
  const agents = agentsOf(d), personas = personasOf(d).map((p) => p.key);
  rows.forEach((r) => {
    if (r.entrypoint && !agents.includes(r.entrypoint)) errors.push(`${r.id}: agent ${r.entrypoint} is not an entrypoint of this template.`);
    if (d.roles[r.id] && !personas.includes(d.roles[r.id])) errors.push(`${r.id}: persona ${d.roles[r.id]} does not exist.`);
    if (!r.cmd && !r.entrypoint && !r.fromTemplate) warnings.push(`${r.id} has no agent or command yet; it cannot run until you give it one.`);
  });
  if (!d.targets.length) warnings.push("No target yet: the project opens without one selected.");
  return { errors, warnings };
}

/** The POST /studio/projects/create body. */
export function draftPayload(d) {
  const out = { path: String(d.path).trim(), template: d.template, manifest: d.manifest || ".alfrd/alfrd.yaml" };
  if (String(d.name || "").trim()) out.name = String(d.name).trim();
  const targets = d.targets.map((t) => String(t).trim()).filter(Boolean);
  if (d.kind === "sequence") {
    Object.assign(out, { task: d.task, iterations: Number(d.iterations), sequence: sequenceOf(d.data) || [] });
    out.draft = { targets };
    return out;
  }
  const deps = dependencyMap(d.data);
  out.draft = {
    targets,
    steps: stepsOf(d).map((r) => ({
      id: r.id, depends_on: deps[r.id] || [],
      ...(r.label ? { label: r.label } : {}),
      ...(r.cmd ? { cmd: r.cmd } : r.entrypoint ? { entrypoint: r.entrypoint } : {}),
      ...(d.roles[r.id] ? { role: d.roles[r.id] } : {}),
    })),
  };
  return out;
}
