import { expandWorkflowSteps, firstWorkflow, isSequence, ownSteps, sequencePasses } from "./defs.js";
// Pure helpers for agent and review settings.
export function replaceSection(text, key, block) {
  const lines = String(text).split("\n"), start = lines.findIndex((l) => new RegExp(`^${key}\\s*:`).test(l));
  if (start < 0) return text.trimEnd() + "\n\n" + block.trimEnd() + "\n";
  let end = start + 1;
  while (end < lines.length && !/^[^\s#-][^:]*:/.test(lines[end])) end++;
  while (end > start + 1 && (/^\s*$/.test(lines[end - 1]) || /^#/.test(lines[end - 1]))) end--;
  lines.splice(start, end - start, ...block.trimEnd().split("\n"));
  return lines.join("\n");
}

export function nextTaskName(tasks) {
  const names = new Set(tasks.map((t) => typeof t === "string" ? t : t.name || t.target));
  let name = "task", number = 2;
  while (names.has(name)) name = `task-${number++}`;
  return name;
}

export function loopRunSelection(info, target = null) {
  const rows = info.table?.rows || [];
  const selected = target || rows[0]?.target || info.loop?.task_row || "task";
  const row = rows.find((r) => r.target === selected);
  return { rows: row ? [{ target: row.target, files: row.files || "task.md", code: row.code || "", workdir: row.workdir || "" }]
    : [{ target: selected, files: "task.md", code: "", workdir: "" }], preferNew: !row || Object.values(row.cells || {}).every((v) => v === "done") };
}

// Agent, review, persona and per-turn settings (only the Agents dialog uses these).

export function personaRows(data) {
  return Object.entries(data.project_settings?.personas || {}).map(([key, value]) => ({ key, ...value }));
}

export function turnRoleRows(data) {
  const wf = firstWorkflow(data);
  if (isSequence(wf)) {
    return expandWorkflowSteps(wf).map((step) => ({ turn: step.turn, iteration: step.turn, iterations: step.iterations, agent: step.entrypoint, keys: [...step.roles] }));
  }
  const raw = wf?.steps || [];
  const steps = Array.isArray(raw) ? raw : Object.entries(raw).map(([id, value]) => ({ id, ...value }));
  const count = wf?.repeat?.iterations || 1, schedule = wf?.roles || [], rows = [];
  for (let iteration = 1; iteration <= count; iteration++) for (const step of steps) {
    const own = typeof step === "object" ? step : { id: step };
    const value = schedule.length ? schedule[rows.length % schedule.length] : own.role;
    rows.push({ turn: rows.length + 1, iteration, iterations: count, agent: own.entrypoint || wf?.entrypoint || own.id,
      keys: typeof value === "string" ? [value] : [...(value || [])] });
  }
  return rows;
}

/** Per-turn executor and review: turn override → step → entrypoint → project default. */

export function turnSettingRows(data) {
  const wf = firstWorkflow(data);
  if (!wf?.repeat) return [];
  const entries = Object.fromEntries((data.entrypoint || []).map((e) => [e.name, e]));
  const fallback = data.project_settings?.human_review ?? false;
  return expandWorkflowSteps(wf).filter((s) => s.handoff).map((s) => {
    const agent = s.entrypoint || wf.entrypoint || s.base_step, entry = entries[agent] || {};
    return { id: s.id, turn: s.turn, label: s.label, agent,
      manual: Boolean(s.manual ?? entry.manual ?? false), human_review: Boolean(s.human_review ?? entry.human_review ?? fallback),
      after: s.after ?? "" };
  });
}

/** Write the rows a person changed as `workflow.turns` overrides; other turns keep theirs. */

export function applyTurnSettings(data, rows) {
  const result = structuredClone(data), wf = firstWorkflow(result);
  if (!wf) return result;
  const turns = { ...(wf.turns || {}) };
  const base = structuredClone(wf); delete base.turns;
  const defaults = Object.fromEntries(turnSettingRows({ ...result, workflows: [base] }).map((r) => [r.id, r]));
  for (const row of rows.filter((r) => r.changed)) {
    const d = defaults[row.id];
    if (!d) continue;
    for (const key of [row.id, row.turn, String(row.turn)]) delete turns[key];
    const change = {};
    if (Boolean(row.manual) !== d.manual) change.manual = Boolean(row.manual);
    if (Boolean(row.human_review) !== d.human_review) change.human_review = Boolean(row.human_review);
    const after = String(row.after ?? "").trim();
    if (after && after !== String(d.after ?? "")) change.after = after;
    if (Object.keys(change).length) turns[row.id] = change;
  }
  if (Object.keys(turns).length) wf.turns = turns; else delete wf.turns;
  return result;
}

export function applyPersonaSettings(data, personas, turnRoles) {
  const result = structuredClone(data), values = {}, keys = new Set();
  for (const row of personas) {
    if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(row.key) || keys.has(row.key)) throw new Error("Persona keys must be unique: 1–40 lowercase letters, digits, underscores or hyphens.");
    if (typeof row.label !== "string" || !row.label.trim() || row.label.length > 60 || /[\r\n]/.test(row.label)) throw new Error("Persona labels must be 1–60 characters on one line.");
    if (typeof row.instructions !== "string" || row.instructions.length > 4000) throw new Error("Persona instructions must be at most 4000 characters.");
    keys.add(row.key); values[row.key] = { label: row.label, instructions: row.instructions, ...(row.summary !== undefined ? { summary: row.summary } : {}) };
  }
  if (personas.length) result.project_settings = { ...(result.project_settings || {}), personas: values };
  else if (result.project_settings) delete result.project_settings.personas;
  const wf = firstWorkflow(result);
  if (!wf) return result;
  const roles = turnRoles.map((row) => {
    const selected = [...new Set(row.keys)].filter((key) => keys.has(key)).sort();
    return selected.length > 1 ? selected : selected[0] || null;
  });
  delete wf.roles;
  // A finite run may end partway through a repeating cycle.
  for (let size = 1; roles.some((role) => role !== null) && size <= roles.length; size++) {
    if (roles.every((role, i) => JSON.stringify(role) === JSON.stringify(roles[i % size]))) {
      wf.roles = roles.slice(0, size); break;
    }
  }
  const steps = Array.isArray(wf.steps) ? wf.steps : Object.values(wf.steps || {});
  for (const step of steps) if (step && typeof step === "object") delete step.role;
  return result;
}

/** The adapter ALFRD uses for an entrypoint. Mirrors agent_io.adapter_for. */
export function entryAdapter(entry) {
  if (entry?.adapter) return entry.adapter;
  const program = String((Array.isArray(entry?.cmd) ? entry.cmd[0] : "") || "").split("/").pop();
  return ["claude", "codex"].includes(program) ? program : "generic";
}

export function agentRows(data) {
  const used = new Set();
  const wf = firstWorkflow(data);
  if (isSequence(wf)) try { sequencePasses(wf.repeat).flat().forEach((item) => used.add(ownSteps(wf)[item]?.entrypoint || item)); } catch { /* invalid sequence */ }
  return (data.entrypoint || []).filter((e) => entryAdapter(e) !== "generic" || e.adapter || used.has(e.name)).map((e) => {
    const args = e.cmd || [], index = args.findIndex((a) => a === "--model" || a === "-m");
    return { name: e.name, adapter: entryAdapter(e), model: e.model || (index >= 0 ? args[index + 1] : args.find((a) => a.startsWith("--model="))?.slice(8)) || "",
      manual: Boolean(e.manual), fallback_models: [...(e.fallback_models || [])] };
  });
}

export function reviewRows(data) {
  const wf = firstWorkflow(data);
  if (isSequence(wf)) {
    const own = ownSteps(wf), entries = Object.fromEntries((data.entrypoint || []).map((e) => [e.name, e]));
    let items = [];
    try { items = [...new Set(sequencePasses(wf.repeat).flat())]; } catch { /* invalid sequence */ }
    return items.map((id) => ({ id, enabled: own[id]?.human_review ?? entries[own[id]?.entrypoint || id]?.human_review ?? data.project_settings?.human_review ?? false }));
  }
  const steps = Array.isArray(wf?.steps) ? wf.steps : Object.entries(wf?.steps || {}).map(([id, value]) => ({ id, ...(typeof value === "object" ? value : {}) }));
  return steps.filter((s) => typeof s === "object" && s.handoff).map((s) => ({ id: s.id || s.key, enabled: s.human_review ?? data.project_settings?.human_review ?? false }));
}

export function applyAgentSettings(data, agents, reviewEnabled, reviews) {
  const result = structuredClone(data);
  for (const row of agents) {
    const previous = agentRows(data).find((a) => a.name === row.name);
    const fallbacks = (row.fallback_models ?? previous.fallback_models ?? []).map((m) => String(m).trim()).filter(Boolean);
    if (row.model.trim() === previous.model && Boolean(row.manual) === previous.manual && JSON.stringify(fallbacks) === JSON.stringify(previous.fallback_models)) continue;
    const entry = result.entrypoint.find((e) => e.name === row.name);
    let skip = false;
    entry.cmd = entry.cmd.filter((arg) => {
      if (skip) { skip = false; return false; }
      if (arg === "--model" || arg === "-m") { skip = true; return false; }
      return !arg.startsWith("--model=");
    });
    if (row.model.trim()) entry.model = row.model.trim();
    else delete entry.model;
    entry.manual = row.manual;
    if (fallbacks.length) entry.fallback_models = fallbacks; else delete entry.fallback_models;
  }
  const reviewChanged = reviewEnabled !== (Boolean(data.project_settings?.human_review) || reviewRows(data).some((r) => r.enabled));
  if (reviewChanged)
    result.project_settings = { ...(result.project_settings || {}), human_review: reviewEnabled };
  const wf = firstWorkflow(result);
  for (const row of reviews) {
    let step = Array.isArray(wf.steps) ? wf.steps.find((s) => (s.id || s.key) === row.id) : wf.steps?.[row.id];
    if (!step && isSequence(wf) && (reviewChanged || (reviewEnabled && row.enabled) !== Boolean(reviewRows(result).find((r) => r.id === row.id)?.enabled))) {
      wf.steps = Array.isArray(wf.steps) ? wf.steps : [];
      step = { id: row.id }; wf.steps.push(step);
    }
    if (step && typeof step === "object" && (reviewChanged || (reviewEnabled && row.enabled) !== Boolean(reviewRows(result).find((r) => r.id === row.id)?.enabled)))
      step.human_review = reviewEnabled && row.enabled;
  }
  return result;
}

export const AGENT_ADAPTERS = ["claude", "codex", "generic"];
