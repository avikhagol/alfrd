// New project wizard (Projects → New project): pure helpers for its steps, agent-command checkboxes,
// turn order and review summary. The rows come from GET /studio/entrypoint-options; no option is
// hard-coded here. agent_modal.js renders the steps; tests/studio_js/create_wizard.test.mjs covers these.
import { esc } from "../utils/dom.js";

export const STEPS = [
  { id: "folder", title: "Folder" },
  { id: "commands", title: "Agent commands" },
  { id: "agents", title: "Agents" },
  { id: "review", title: "Review & create" },
];
export const CUSTOM_NAME = /^[a-z][a-z0-9_-]{0,39}$/;

/** Steps for a template: only an agent loop has the Agents step (task, iterations, turn order), after the
 *  commands are ticked because the turn order is built from them. */
export function stepsFor(template) {
  return STEPS.filter((s) => s.id !== "agents" || template === "agent-loop");
}

/** Checkbox rows (shown options; a hidden one is listed under "Why isn't X listed?"). `selected`: Set of ids. */
export function optionRows(options, selected) {
  const row = (o) => `<label class="cw-opt"><input type="checkbox" name="agent" value="${esc(o.id)}" aria-describedby="cw-help-${esc(o.id)}" ${selected.has(o.id) ? "checked" : ""}>
    <span class="cw-opt-b"><b>${esc(o.label)}</b> <span class="mono muted small">${esc(o.id)}</span>${o.status ? ` <span class="badge ${o.available ? "tone-ok" : "tone-warn"}">${esc(o.status)}</span>` : ""}
    <span class="muted small" id="cw-help-${esc(o.id)}">${esc(o.help)}</span></span></label>`;
  const shown = options.filter((o) => o.shown);
  const common = shown.filter((o) => o.group === "common"), advanced = shown.filter((o) => o.group === "advanced");
  const custom = shown.find((o) => o.group === "custom");
  const hidden = options.filter((o) => !o.shown);
  return `${common.map(row).join("")}
    ${advanced.length ? `<details class="cw-adv" ${advanced.some((o) => selected.has(o.id)) ? "open" : ""}><summary>Advanced integrations</summary>${advanced.map(row).join("")}</details>` : ""}
    ${custom ? row(custom) : ""}
    ${hidden.length ? `<details class="cw-why"><summary class="muted small">Why isn't ${hidden.map((o) => esc(o.label)).join(", ")} listed?</summary><ul class="small">${hidden.map((o) => `<li><b>${esc(o.label)}</b>: ${esc(o.hidden_reason)}</li>`).join("")}</ul></details>` : ""}`;
}

/** Loop agents in catalogue order (then custom commands); `order` (names) keeps an edited turn order. */
export function loopAgents(options, selected, customNames = []) {
  return [...options.filter((o) => o.loop && o.id !== "custom" && selected.has(o.id)).map((o) => o.id), ...customNames];
}

/** The turn order: `order` filtered to the loop agents (an unticked agent leaves it), plus one turn for
 *  each agent ticked since (not in `before`, the agents `order` was made for); or all of them. */
export function sequenceFrom(agents, order = [], before = null) {
  const kept = order.filter((a) => agents.includes(a));
  if (!kept.length) return [...agents];
  return before ? [...kept, ...agents.filter((a) => !before.includes(a))] : kept;
}

/** Problems that keep Next disabled on a step (one line each, "<field>: <what>. <fix>"). */
export function stepProblems(step, state) {
  const out = [];
  if (step === "folder") {
    if (!state.path?.trim()) out.push("Folder: is empty. Enter or browse to the project folder.");
    if ((state.name || "").length > 200) out.push("Name: is longer than 200 characters. Shorten it.");
  }
  if (step === "agents") {
    if (!state.task?.trim()) out.push("Initial task: is empty. Describe what the agents should do.");
    if (!(state.iterations >= 1 && state.iterations <= state.maxTurns)) out.push(`Iterations: must be 1–${state.maxTurns}. Enter a number in that range.`);
    if (!state.sequence?.length) out.push("Turn order: is empty. Add at least one turn.");
  }
  if (step !== "folder") {
    if (state.template === "agent-loop" && !state.agents.length) out.push("Agent commands: an agent loop needs at least one command that edits files. Tick Claude Code, Codex or a custom command.");
    if (state.custom) {
      const c = state.custom;
      if (!CUSTOM_NAME.test(c.name || "")) out.push("Custom command name: use 1–40 lowercase letters, digits, _ or -, starting with a letter.");
      else if (state.taken?.includes(c.name)) out.push(`Custom command name: ${c.name} is already used. Pick another name.`);
      if (!(c.cmd || "").trim()) out.push("Custom command: is empty. Enter a program and its arguments.");
    }
    const missing = state.template === "agent-loop" ? (state.sequence || []).filter((a) => !state.agents.includes(a)) : [];
    if (step !== "commands" && missing.length) out.push(`Turn order: ${missing.join(", ")} is not a ticked agent command. Tick it or remove it from the order.`);
  }
  return out;
}

/** The POST /studio/projects/create payload from the wizard state. */
export function createPayload(state) {
  const payload = { path: state.path.trim(), template: state.template, manifest: state.manifest, agents: state.selected.filter((id) => id !== "custom") };
  if (state.name?.trim()) payload.name = state.name.trim();
  if (state.custom) payload.custom = [{ name: state.custom.name, cmd: state.custom.cmd.trim(), ...(state.custom.model_option ? { model_option: state.custom.model_option } : {}) }];
  if (state.template === "agent-loop") Object.assign(payload, { task: state.task, iterations: state.iterations, sequence: state.sequence });
  return payload;
}

/** Review step: folder, file, agents/turns and the exact commands that will be written; warnings last. */
export function reviewSummary(payload, options, { entries = [], warnings = [], turns = "" } = {}) {
  const byId = Object.fromEntries(options.map((o) => [o.id, o]));
  const commands = [...payload.agents.map((id) => ({ name: id, label: byId[id]?.label || id, cmd: byId[id]?.cmd, status: byId[id] })), ...entries.map((e) => ({ name: e.name, label: "Custom command", cmd: e.cmd }))];
  const notes = [...warnings, ...payload.agents.filter((id) => byId[id] && !byId[id].available).map((id) => `${byId[id].label}: ${byId[id].status}. Install it before the first run.`)];
  return `<dl class="cw-review">
    <dt>Folder</dt><dd class="mono">${esc(payload.path)}</dd>
    <dt>Name</dt><dd>${esc(payload.name || payload.path.split("/").filter(Boolean).pop() || "")}</dd>
    <dt>Template</dt><dd>${esc(payload.template)}</dd><dt>Configuration file</dt><dd class="mono">${esc(payload.manifest)}</dd>
    ${payload.template === "agent-loop" ? `<dt>Turns</dt><dd>${esc(turns || payload.sequence.join(" → "))}</dd>` : ""}
    <dt>Agent commands</dt><dd>${commands.length ? `<ul class="cw-cmds">${commands.map((c) => `<li><b>${esc(c.label)}</b> <span class="mono small">${esc(c.name)}</span>${c.cmd ? `<pre class="code small">${esc(c.cmd.map((a) => (/\s/.test(a) ? JSON.stringify(a) : a)).join(" "))}</pre>` : '<span class="muted small"> built in (chat only)</span>'}</li>`).join("")}</ul>` : '<span class="muted">None. Add commands later in Settings → Entrypoints.</span>'}</dd>
  </dl>${notes.length ? `<ul class="msgs">${notes.map((w) => `<li class="lvl-warn">${esc(w)}</li>`).join("")}</ul>` : ""}`;
}
