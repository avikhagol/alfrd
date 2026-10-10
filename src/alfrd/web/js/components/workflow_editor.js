// Workflow view → Edit workflow: a per-project draft (with undo) of alfrd.yaml's
// first workflow; Save rewrites only changed sections via ctx.saveManifest.

import { $, $$, esc, icon, loadCss } from "../utils/dom.js";
import { parseYaml } from "../utils/yaml_parser.js";
import { manifestToWorkflows } from "../data/model.js";
import { loadTemplate, templateName } from "../data/defs.js";
import {
  ON_FAILURE, STEP_ID, addStage, addStep, applySettings, changedSections, defaultEntrypoint, editorSteps, entrypointNames, firstWorkflowOf,
  joinCommand, moveStep, removeStep, sequenceOf, setSequence, setSkip, setStages, setTotalTurns, slugFor, splitCommand,
  stageList, updateStep, withWorkflow, workflowKind, workflowSettings, workflowText,
} from "../data/workflow_edit.js";

loadCss("css/lazy.css");

const drafts = new Map(); // project -> {base, data, history, template, file}
const DELAY = /^\+?(\d+(\.\d+)?|(\d+d)?(\d+h)?(\d+m)?(\d+s)?)$/i;
const isDelay = (v) => typeof v === "number" || (typeof v === "string" && v.trim() !== "" && DELAY.test(v.replace(/\s/g, "")));

export function editing(project) {
  return Boolean(project && drafts.has(project));
}

function manifestText(ctx, project) {
  const tree = ctx.state.trees?.[project];
  return tree?.manifestText ?? null;
}

export function canEdit(ctx, project) {
  return Boolean(project && manifestText(ctx, project) != null);
}

export async function startEdit(ctx, project) {
  const base = manifestText(ctx, project);
  if (base == null) { ctx.toast("This project's alfrd.yaml is not loaded yet", "warn"); return false; }
  let data;
  try { data = parseYaml(base) || {}; } catch (error) { ctx.toast(`alfrd.yaml does not parse: ${error.message}. Fix it in Settings first.`, "fail"); return false; }
  const name = templateName(data);
  const template = name ? await loadTemplate(name, parseYaml) : null;
  drafts.set(project, { base, data, history: [], template, file: ctx.state.trees?.[project]?.manifestFile || "alfrd.yaml" });
  ctx.update();
  return true;
}

function stopEdit(ctx, project, message = "Changes discarded") {
  drafts.delete(project);
  ctx.update();
  focusAction(null, "start");
  announce(message);
}

// #wfe-live (canvas.js) survives rerenders.
function announce(text) {
  const live = $("#wfe-live");
  if (!live) return;
  live.textContent = "";
  requestAnimationFrame(() => { live.textContent = text; });
}

// Renders replace the opener. Restore focus by identity on a visible action.
function focusAction(id, action = "edit") {
  requestAnimationFrame(() => {
    const suffix = id ? `[data-id="${CSS.escape(id)}"]` : "";
    for (const selector of [`[data-wfe="${action}"]${suffix}`, `[data-wfe="edit"]${suffix}`, '[data-wfe="add"]']) {
      const el = $$(selector).find((el) => !el.disabled && el.getClientRects().length);
      if (el) { el.focus(); break; }
    }
  });
}

export function selectStep(project, key) {
  const d = drafts.get(project);
  if (d) d.selected = key;
}

export function selectedStep(project) {
  return drafts.get(project)?.selected;
}

function dirty(d) {
  try { return changedSections(parseYaml(d.base) || {}, d.data).length > 0; } catch { return true; }
}

function change(ctx, project, fn, id = null, action = "edit", message = "Draft updated; Save to keep changes") {
  const d = drafts.get(project);
  if (!d) return;
  try {
    const next = fn(d.data, d.template);
    d.history.push(d.data);
    if (d.history.length > 100) d.history.shift();
    d.data = next;
    if (id) d.selected = id;
    d.message = message;
    ctx.update();
    focusAction(id, action);
    announce(message);
  } catch (error) {
    ctx.toast(error.message, "fail");
  }
}

/** The draft as the graph / list render it: skipped steps included (flagged), in place. */
export function previewWorkflow(project) {
  const d = drafts.get(project);
  if (!d) return null;
  const rows = editorSteps(d.data, d.template);
  const skipped = new Set(rows.filter((r) => r.skip).map((r) => r.id));
  const shown = withWorkflow(d.data, d.template);
  const wf = firstWorkflowOf(shown);
  // Display includes every step. An inline false also overrides inherited skips.
  if (wf && Array.isArray(wf.steps)) wf.steps = wf.steps.map((s) => ({ ...(s && typeof s === "object" ? s : { id: s }), skip: false }));
  let info;
  try { info = manifestToWorkflows(shown, d.file, { aliases: false }); } catch (error) { info = { workflows: [], errors: [error.message] }; }
  const out = info.workflows[0] || { name: "workflow", label: "workflow", steps: [], stages: [], template: d.template ? templateName(d.data) : null };
  out.steps.forEach((s) => { if (skipped.has(s.key)) s.skipped = true; });
  return out;
}

/** Problems that would make the saved file invalid (skips applied). */
function draftErrors(d) {
  try { return manifestToWorkflows(d.data, d.file, { aliases: false }).errors.filter((e) => !/declares no steps/.test(e)); } catch (error) { return [error.message]; }
}

// --- rendering -----------------------------------------------------------------

/** The bar above the canvas while editing: what changed, undo, discard, save. */
export function editBar(ctx, project, graph = false, selected = null) {
  const d = drafts.get(project);
  const changed = dirty(d);
  const errors = draftErrors(d);
  const kind = workflowKind(d.data, d.template);
  const writable = ctx.canWrite(project);
  const wf = firstWorkflowOf(d.data) || firstWorkflowOf(d.template);
  return `<div class="wfe-bar" role="region" aria-label="Workflow editor">
      <span class="info-ic">${icon("edit")}</span>
      <div class="grow"><b>Editing workflow${wf ? ` · ${esc(wf.label || wf.name || "")}` : ""}</b> <span class="badge tone-${changed ? "warn" : "muted"}">${changed ? "Unsaved changes" : "No changes"}</span>
        <div class="muted small">${kind === "sequence" ? "Set the order agents take turns in and how many turns a task gets." : "Add, skip, reorder and edit steps. Nothing is written until you save."}${errors.length ? ` <span class="wfe-err">${esc(errors[0])}${errors.length > 1 ? ` (+${errors.length - 1})` : ""}</span>` : ""}</div></div>
      ${kind !== "sequence" ? `<button class="btn sm" data-wfe="add">${icon("plus")} Add step</button>` : ""}
      <button class="btn sm" data-wfe="settings">${icon("gear")} Workflow settings</button>
      ${kind !== "sequence" ? '<button class="btn sm" data-wfe="stages">Stages</button>' : ""}
      <button class="btn sm" data-wfe="undo" ${d.history.length ? "" : "disabled"} title="Undo the last change">${icon("reset")} Undo</button>
      <button class="btn sm" data-wfe="yaml" title="Show the YAML that Save writes">${icon("braces")} YAML</button>
      <button class="btn sm" data-wfe="discard">Discard</button>
      <button class="btn sm primary" data-wfe="save" ${changed && !errors.length ? "" : "disabled"} title="${writable ? `Write ${esc(d.file)}` : "No writable folder: alfrd.yaml is downloaded"}">${icon("save")} Save</button>
    </div><span class="small">${esc(d.message || "")}</span>${kind === "sequence" ? sequencePanel(ctx, project, d) : graph ? stepActions(project, d.selected || selected) : ""}`;
}

function stepActions(project, key) {
  const d = drafts.get(project);
  const rows = editorSteps(d.data, d.template);
  const r = rows.find((s) => s.id === key);
  if (!r) return '<p class="muted small">Select a step to use Step actions.</p>';
  return `<div class="wfe-step-actions" role="group" aria-label="Step actions for ${esc(key)}"><b class="mono">${esc(key)}</b>
    <button class="btn sm" data-wfe="${r.skip ? "unskip" : "skip"}" data-id="${esc(key)}">${r.skip ? "Run" : "Skip"}</button>
    <button class="btn sm" data-wfe="edit" data-id="${esc(key)}">Edit</button>
    <button class="btn sm" data-wfe="add" data-at="${r.index + 1}">Add after</button>
    <button class="btn sm" data-wfe="up" data-id="${esc(key)}" ${r.index ? "" : "disabled"}>Move earlier</button>
    <button class="btn sm" data-wfe="down" data-id="${esc(key)}" ${r.index < rows.length - 1 ? "" : "disabled"}>Move later</button>
    <button class="btn sm" data-wfe="delete" data-id="${esc(key)}">Delete step</button>
    <span class="muted small">Steps using “after previous” follow the new order. Chosen dependencies stay the same.</span></div>`;
}

function sequencePanel(ctx, project, d) {
  const seq = sequenceOf(d.data, d.template);
  if (seq === null) return `<div class="wfe-seq muted small">This loop lists several passes in <code>repeat.sequence</code>; edit it in Settings · alfrd.yaml.</div>`;
  const agents = entrypointNames(d.data, d.template);
  const repeat = (firstWorkflowOf(d.data) || firstWorkflowOf(d.template)).repeat;
  const total = repeat.iterations ?? "";
  return `<div class="wfe-seq">
      <div class="wfe-seq-h"><b>Turn order</b><span class="muted small">One pass; it repeats until the total is reached.</span></div>
      <ol class="wfe-chips" aria-label="Turn order">${seq.map((a, i) => `<li class="wfe-chip"><span class="tabular muted">${i + 1}</span><b class="mono">${esc(a)}</b>
          <button class="icon-btn sm" data-wfe="turn-left" data-i="${i}" ${i ? "" : "disabled"} aria-label="Move turn ${i + 1} earlier">${icon("unfold")}</button>
          <button class="icon-btn sm" data-wfe="turn-right" data-i="${i}" ${i < seq.length - 1 ? "" : "disabled"} aria-label="Move turn ${i + 1} later">${icon("fold")}</button>
          <button class="icon-btn sm" data-wfe="turn-remove" data-i="${i}" ${seq.length > 1 ? "" : "disabled"} aria-label="Remove turn ${i + 1}">${icon("close")}</button></li>`).join("")}
        <li class="wfe-chip add"><select class="input sm" id="wfe-turn-agent" aria-label="Agent for the new turn">${agents.map((a) => `<option>${esc(a)}</option>`).join("")}</select><button class="btn sm" data-wfe="turn-add" ${agents.length ? "" : "disabled"}>${icon("plus")} Add turn</button></li>
      </ol>
      <label class="wfe-total"><span class="small">Total turns</span><input class="input sm tabular" type="number" min="1" max="200" step="1" id="wfe-total" value="${esc(total)}" ${"passes" in repeat ? `placeholder="${esc(repeat.passes)} passes"` : ""}></label>
      <button class="btn sm" data-plan="agents" title="Agent commands, models, roles per turn and review">Agents, roles &amp; review…</button>
    </div>`;
}

/** List view while editing: every declared step, skipped ones too, with its tools. */
export function editList(ctx, project) {
  const d = drafts.get(project);
  if (workflowKind(d.data, d.template) === "sequence") return "";
  const rows = editorSteps(d.data, d.template);
  const stages = Object.fromEntries(stageList(d.data, d.template).map((s) => [s.id, s.title]));
  const fallback = defaultEntrypoint(d.data, d.template);
  const insert = (i) => `<tr class="wfe-ins"><td colspan="6"><button class="wfe-ins-btn" data-wfe="add" data-at="${i}" aria-label="Insert a step at position ${i + 1}">${icon("plus")}<span>Insert step here</span></button></td></tr>`;
  return `<table class="tbl list-tbl wfe-tbl"><thead><tr><th>#</th><th>Step</th><th>Stage</th><th>Runs</th><th>Runs after</th><th class="wfe-tools-h">Actions</th></tr></thead><tbody>
    ${rows.map((r, i) => `${insert(i)}<tr class="${r.skip ? "wfe-skipped" : ""}" data-wfe-row="${esc(r.id)}">
        <td class="tabular">${i + 1}</td>
        <td><b class="mono">${esc(r.id)}</b>${r.skip ? ' <span class="badge tone-muted">Skipped</span>' : ""}<div class="muted small">${esc(r.label || "")}</div></td>
        <td>${esc(stages[r.stage] || r.stage || "—")}</td>
        <td class="mono small">${r.cmd ? esc(joinCommand(r.cmd)) : r.entrypoint ? `entrypoint ${esc(r.entrypoint)}` : fallback ? `<span class="muted">${esc(fallback)} (default)</span>` : '<span class="wfe-err">no command</span>'}</td>
        <td class="mono small">${r.depends_on ? (r.depends_on.length ? esc(r.depends_on.join(", ")) : '<span class="muted">starts first</span>') : '<span class="muted">previous step</span>'}${isDelay(r.after) ? ` <span class="chip">+${esc(String(r.after).replace(/^\+/, ""))}</span>` : ""}</td>
        <td class="wfe-tools">${tools(r, i, rows.length)}</td></tr>`).join("")}
    ${insert(rows.length)}</tbody></table>
    ${rows.length ? "" : '<p class="muted small wfe-hint">No steps yet. Add the first one.</p>'}`;
}

function tools(r, i, n) {
  return `<label class="switch sm" title="${r.skip ? "Skipped: never runs; the steps around it are chained" : "Runs"}"><input type="checkbox" data-wfe-skip="${esc(r.id)}" ${r.skip ? "" : "checked"} aria-label="Run ${esc(r.id)}"><i></i></label>
    <button class="icon-btn sm" data-wfe="up" data-id="${esc(r.id)}" ${i ? "" : "disabled"} aria-label="Move ${esc(r.id)} earlier" title="Move earlier">${icon("chevron", "flip")}</button>
    <button class="icon-btn sm" data-wfe="down" data-id="${esc(r.id)}" ${i < n - 1 ? "" : "disabled"} aria-label="Move ${esc(r.id)} later" title="Move later">${icon("chevron")}</button>
    <button class="icon-btn sm" data-wfe="edit" data-id="${esc(r.id)}" aria-label="Edit ${esc(r.id)}" title="Edit">${icon("edit")}</button>
    <button class="icon-btn sm" data-wfe="delete" data-id="${esc(r.id)}" aria-label="Delete ${esc(r.id)}" title="Delete">${icon("trash")}</button>`;
}

/** The small action bar on a graph node while editing. */
export function nodeTools(project, key) {
  const d = drafts.get(project);
  if (!d || workflowKind(d.data, d.template) === "sequence") return "";
  const r = editorSteps(d.data, d.template).find((x) => x.id === key);
  if (!r) return "";
  return `<div class="wfe-node-tools">
      <button class="icon-btn xs" data-wfe="${r.skip ? "unskip" : "skip"}" data-id="${esc(key)}" title="${r.skip ? "Run this step again" : "Skip this step"}" aria-label="${r.skip ? "Run" : "Skip"} ${esc(key)}">${icon(r.skip ? "play" : "pause")}</button>
      <button class="icon-btn xs" data-wfe="edit" data-id="${esc(key)}" title="Edit step" aria-label="Edit ${esc(key)}">${icon("edit")}</button>
      <button class="icon-btn xs" data-wfe="add" data-at="${r.index + 1}" title="Add a step after this one" aria-label="Add a step after ${esc(key)}">${icon("plus")}</button>
    </div>`;
}

export function canAddInGraph(project) {
  const d = drafts.get(project);
  return Boolean(d && workflowKind(d.data, d.template) !== "sequence");
}

/** Blank project: how to start, without opening alfrd.yaml. */
export function emptyState(ctx, project) {
  const can = canEdit(ctx, project) ? "" : "disabled";
  const card = (act, ic, title, text, extra = "") => `<button class="wfe-start-card ${act === "first" ? "primary" : ""}" data-wfe="${act}" ${extra} ${can}>${icon(ic)}<b>${title}</b><span>${text}</span></button>`;
  return `<div class="empty setup-card wfe-start"><h2>Create your workflow</h2>
    <p>A workflow is a list of steps. Each step runs one command (a script, a tool, an agent) for every target.</p>
    <div class="wfe-start-grid">${card("first", "plus", "Add your first step", "Name it and give it a command. Add more later, in the graph or the list.")}
      ${card("template", "sync", "Agent loop", "Claude and Codex take turns on a task, handing off between turns.", 'data-template="agent-loop"')}
      ${card("template", "pipeline", "AVICA pipeline", "The AVICA reduction steps; skip the ones you don't need.", 'data-template="avica"')}</div>
    <p class="muted small">${can ? "Open a project folder first (Projects, at the left of the header)." : 'Prefer the file? <a href="#/config">Settings · alfrd.yaml</a>'}</p></div>`;
}

/** Editing: a dashed "Add step" node below the last stage of the graph (`L` grows to fit it). */
export function graphAddNode(project, L, width) {
  if (!canAddInGraph(project)) return "";
  const y = L.height + 8;
  L.height = y + 92;
  return `<button class="node-add" data-wfe="add" style="left:0;top:${y}px;width:${Math.min(width, 340)}px">${icon("plus")}<span>Add step</span></button>`;
}

// --- actions -------------------------------------------------------------------

export async function handle(ctx, project, action, button) {
  const d = drafts.get(project);
  const id = button?.dataset.id;
  switch (action) {
    case "start": await startEdit(ctx, project); return;
    case "first":
      if (!d && !(await startEdit(ctx, project))) return;
      stepDialog(ctx, project, null, 0);
      return;
    case "template": await applyTemplate(ctx, project, button.dataset.template); return;
    case "discard":
      if (d && dirty(d) && !confirm("Discard the unsaved workflow changes?")) return;
      stopEdit(ctx, project);
      return;
    case "undo": if (d?.history.length) { d.data = d.history.pop(); if (!templateName(d.data)) d.template = null; d.message = "Last change undone"; ctx.update(); focusAction(d.selected); announce(d.message); } return;
    case "save": await save(ctx, project); return;
    case "yaml": showYaml(ctx, d); return;
    case "settings": settingsDialog(ctx, project); return;
    case "stages": stagesDialog(ctx, project); return;
    case "add": stepDialog(ctx, project, null, button?.dataset.at != null ? Number(button.dataset.at) : null); return;
    case "edit": stepDialog(ctx, project, id); return;
    case "skip": change(ctx, project, (data, tpl) => setSkip(data, id, true, tpl), id, "unskip", `${id} skipped; Save to keep changes`); return;
    case "unskip": change(ctx, project, (data, tpl) => setSkip(data, id, false, tpl), id, "skip", `${id} will run; Save to keep changes`); return;
    case "up":
    case "down": {
      const rows = editorSteps(d.data, d.template);
      const to = rows.findIndex((r) => r.id === id) + (action === "up" ? -1 : 1);
      change(ctx, project, (data, tpl) => moveStep(data, id, to, tpl), id, action, `${id} moved to position ${to + 1} of ${rows.length}; Save to keep changes`);
      return;
    }
    case "delete":
      deleteDialog(ctx, project, id);
      return;
    case "turn-left":
    case "turn-right":
    case "turn-remove": {
      const i = Number(button.dataset.i);
      const seq = sequenceOf(d.data, d.template);
      if (action === "turn-remove") seq.splice(i, 1);
      else { const j = i + (action === "turn-left" ? -1 : 1); [seq[i], seq[j]] = [seq[j], seq[i]]; }
      const to = action === "turn-remove" ? Math.min(i, seq.length - 1) : i + (action === "turn-left" ? -1 : 1);
      change(ctx, project, (data, tpl) => setSequence(data, seq, tpl), null, "edit", action === "turn-remove" ? `Turn ${i + 1} removed; Save to keep changes` : `${seq[to]} moved to turn ${to + 1} of ${seq.length}; Save to keep changes`);
      requestAnimationFrame(() => {
        const buttons = $$(`[data-i="${to}"]`).filter((el) => !el.disabled);
        (buttons.find((el) => el.dataset.wfe === action) || buttons[0])?.focus();
      });
      return;
    }
    case "turn-add": {
      const agent = $("#wfe-turn-agent")?.value;
      if (agent) {
        change(ctx, project, (data, tpl) => setSequence(data, [...sequenceOf(data, tpl), agent], tpl), null, "turn-add", `${agent} turn added; Save to keep changes`);
      }
      return;
    }
    default:
  }
}

/** Inputs outside data-wfe buttons: the skip switches and the total turns field. */
export function handleInput(ctx, project, el) {
  if (el.dataset.wfeSkip) {
    const id = el.dataset.wfeSkip;
    change(ctx, project, (data, tpl) => setSkip(data, id, !el.checked, tpl), id, "edit", `${id} ${el.checked ? "will run" : "skipped"}; Save to keep changes`);
    requestAnimationFrame(() => $(`[data-wfe-skip="${CSS.escape(id)}"]`)?.focus());
  }
  else if (el.id === "wfe-total" && el.value !== "") {
    change(ctx, project, (data, tpl) => setTotalTurns(data, Number(el.value), tpl));
    requestAnimationFrame(() => $("#wfe-total")?.focus());
  }
}

async function save(ctx, project) {
  const d = drafts.get(project);
  if (!d) return;
  const errors = draftErrors(d);
  if (errors.length) { ctx.toast(errors[0], "fail"); return; }
  const text = workflowText(d.base, d.data);
  try {
    await ctx.saveManifest(project, text);
    drafts.delete(project);
    ctx.toast(`Workflow saved to ${d.file}`, "ok");
    ctx.update();
    focusAction(null, "start");
    announce(`Saved ${d.file}`);
  } catch (error) {
    ctx.toast(`Not saved: ${error.message}`, "fail");
  }
}

function showYaml(ctx, d) {
  if (!d) return;
  const text = workflowText(d.base, d.data);
  const sections = changedSections(parseYaml(d.base) || {}, d.data);
  ctx.modal(`<header class="modal-h"><h2 id="wfe-title">What Save writes</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p class="muted small">${sections.length ? `Changed sections: <code>${sections.join("</code>, <code>")}</code>. The rest of ${esc(d.file)} stays as it is.` : "No changes yet."}</p><pre class="code wfe-yaml">${esc(text)}</pre></div>`, (root) => root.setAttribute("aria-labelledby", "wfe-title"), "wide");
}

function deleteDialog(ctx, project, id) {
  const d = drafts.get(project);
  const rows = editorSteps(d.data, d.template);
  const i = rows.findIndex((r) => r.id === id);
  const dependents = rows.filter((r) => r.depends_on?.includes(id) || r.after === id).map((r) => r.id);
  ctx.modal(`<header class="modal-h"><h2 id="wfe-title">Delete step ${esc(id)}?</h2></header>
    <div class="modal-b"><p>To keep it for later, skip this step instead.</p>${dependents.length ? `<p>These steps wait on it: ${esc(dependents.join(", "))}. Its dependency references will be removed; remaining chosen dependencies are kept.</p><p>${esc(manifestToWorkflows(removeStep(d.data, id, d.template), d.file, { aliases: false }).workflows[0]?.steps.filter((s) => dependents.includes(s.key)).map((s) => `${s.key}: ${s.depends.length ? `waits for ${s.depends.join(", ")}` : "no step dependencies"}`).join("; ") || "These steps are currently skipped.")}</p>` : ""}</div>
    <footer class="modal-f"><button class="btn" data-close>Cancel</button><button class="btn" data-delete>Delete step</button></footer>`, (root, close) => {
    root.setAttribute("aria-labelledby", "wfe-title");
    $("[data-delete]", root).onclick = () => {
      close();
      const next = rows[i + 1]?.id || rows[i - 1]?.id;
      d.selected = next;
      change(ctx, project, (data, tpl) => removeStep(data, id, tpl), next, "edit", `${id} deleted; Undo to restore it`);
    };
  }, "wfe-modal");
}

/** Template cards start a draft; Save writes it, keeping unrelated keys. */
async function applyTemplate(ctx, project, template) {
  if (!drafts.has(project) && !(await startEdit(ctx, project))) return;
  const d = drafts.get(project);
  try {
    const response = await fetch(`assets/templates/${encodeURIComponent(template)}.yaml`);
    if (!response.ok) throw new Error(`template ${template}: HTTP ${response.status}`);
    const { templateDraft } = await import("../data/yaml_form.js");
    const fromTemplate = parseYaml(templateDraft(await response.text(), d.base, template)) || {};
    const tpl = await loadTemplate(template, parseYaml);
    const label = template === "avica" ? "AVICA pipeline" : "agent loop";
    change(ctx, project, (data) => { d.template = tpl; return { ...data, ...fromTemplate }; }, null, "save", `Draft created from the ${label} template; Save to keep it`);
  } catch (error) {
    ctx.toast(`Template not applied: ${error.message}`, "fail");
  }
}

// --- workflow settings / stages dialogs ---------------------------------------------

const FAILURE = {
  stop_target: ["Stop this target", "Leave later steps for that target unrun; other targets can continue."],
  continue: ["Continue with later steps", "Allow later steps after a failure."],
  stop_plan: ["Stop the whole plan", "Stop scheduling new work for every target."],
};
const wired = (name) => `id="wfs-${name}" name="${name}" aria-describedby="wfs-${name}-help wfs-${name}-err"`;
const notes = (name, help) => `<small class="muted" id="wfs-${name}-help">${help}</small><small class="wfe-msg" id="wfs-${name}-err"></small>`;

function settingsDialog(ctx, project) {
  const d = drafts.get(project);
  const s = workflowSettings(d.data, d.template);
  const loop = workflowKind(d.data, d.template) === "sequence";
  const wf = firstWorkflowOf(d.data) || firstWorkflowOf(d.template) || {};
  const entries = entrypointNames(d.data, d.template);
  const ep = s.entrypoint.own;
  const eff = (v) => (v.own === undefined ? v.inherited : v.own);
  const badge = (v) => `<span class="badge tone-muted">${v.own === undefined ? (d.template ? "Template default" : "Project default") : "Set here"}</span>`;
  const tmode = s.timeout.own === undefined ? "inherit" : s.timeout.own === null ? "none" : "set";
  const fail = eff(s.on_failure);
  ctx.modal(`<header class="modal-h"><h2 id="wfe-title">Workflow settings · ${esc(wf.label || wf.name || "workflow")}</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <form class="modal-b wfe-form" novalidate><p class="muted small">Apply changes to the draft, then save the workflow.</p>
      <h3>Default command</h3>
      ${loop ? '<p class="muted small">Agent-loop turns name their agents in the turn order.</p>' : `<div class="field"><label for="wfs-entrypoint">Default command</label>
        <select class="input" ${wired("entrypoint")}><option value="">${s.entrypoint.inherited ? `Use project default — ${esc(s.entrypoint.inherited)}` : "No default command"}</option>
        ${[...new Set([...entries, ...(ep ? [ep] : [])])].map((e) => `<option value="${esc(e)}" ${e === ep ? "selected" : ""}>${esc(e)}${entries.includes(e) ? "" : " (unknown entrypoint)"}</option>`).join("")}</select>
        ${notes("entrypoint", "Used by steps that do not specify their own command or entrypoint. Applies in step mode.")}</div>`}
      <h3>Project-wide defaults</h3>
      <p class="muted small">These defaults apply to every workflow in this project. Existing plans keep their saved settings; future runs use these defaults unless you override them in Run.</p>
      ${loop ? '<p class="small">Targets at once: <b>1 — required for agent loops/repeated workflows</b><br>On failure: <b>Stop the whole plan — required for repeated workflows</b></p>' : `
      <div class="field"><label for="wfs-concurrency">Targets at once ${badge(s.concurrency)}</label><input class="input tabular" type="number" min="1" max="64" step="1" value="${esc(eff(s.concurrency))}" ${wired("concurrency")}>
        ${notes("concurrency", "Maximum targets running at the same time. Shared-file and work-directory locks may reduce this number.")}</div>
      <div class="field"><label for="wfs-on_failure">On failure ${badge(s.on_failure)}</label><select class="input" ${wired("on_failure")}>
        ${ON_FAILURE.map((k) => `<option value="${k}" ${k === fail ? "selected" : ""}>${FAILURE[k][0]}</option>`).join("")}${ON_FAILURE.includes(fail) ? "" : `<option value="${esc(fail)}" selected>${esc(fail)} (not valid)</option>`}</select>
        ${notes("on_failure", FAILURE[fail]?.[1] || "Choose one of the listed actions.")}</div>`}
      <fieldset class="field"><legend>Default command time limit (seconds) ${badge(s.timeout)}</legend>
        <label><input type="radio" name="tmode" value="inherit" ${tmode === "inherit" ? "checked" : ""}> Use inherited default (${s.timeout.inherited == null ? "No limit" : `${esc(s.timeout.inherited)} s`})</label>
        <label><input type="radio" name="tmode" value="set" ${tmode === "set" ? "checked" : ""}> Set a time limit</label>
        <label><input type="radio" name="tmode" value="none" ${tmode === "none" ? "checked" : ""}> No default limit</label>
        <input class="input tabular" type="number" min="0" step="any" aria-label="Time limit in seconds" value="${tmode === "set" ? esc(s.timeout.own) : ""}" ${wired("timeout")}>
        ${notes("timeout", "Applies per command. A step or entrypoint time limit takes precedence.")}</fieldset>
      <h3>Log files</h3>
      <fieldset class="field"><legend>Log patterns for every step ${badge(s.logs)}</legend>
        <label><input type="radio" name="lmode" value="inherit" ${s.logs.own === undefined ? "checked" : ""}> Use inherited patterns${s.logs.inherited.length ? `: <code>${esc(s.logs.inherited.join(", "))}</code>` : " (none)"}</label>
        <label><input type="radio" name="lmode" value="set" ${s.logs.own !== undefined ? "checked" : ""}> Use these patterns (empty shows none)</label>
        <textarea class="input mono" rows="3" aria-label="Log patterns, one per line" placeholder="{workdir}/logs/{step}_*.log" ${wired("logs")}>${esc((s.logs.own ?? s.logs.inherited).join("\n"))}</textarea>
        ${notes("logs", "These files appear under Logs for every step, in addition to that step's own patterns.")}</fieldset>
    </form>
    <footer class="modal-f"><button class="btn" data-close>Cancel</button><button class="btn primary" data-wfe-submit>Apply to draft</button></footer>`, (root, close) => {
    root.setAttribute("aria-labelledby", "wfe-title");
    const form = $("form", root);
    const sync = () => {
      form.timeout.disabled = form.tmode.value !== "set";
      form.logs.disabled = form.lmode.value !== "set";
      if (form.on_failure) $("#wfs-on_failure-help", form).textContent = FAILURE[form.on_failure.value]?.[1] || "Choose one of the listed actions.";
    };
    sync();
    form.addEventListener("change", sync);
    $("[data-wfe-submit]", root).addEventListener("click", () => {
      const next = {}, errors = [];
      $$("[aria-invalid]", form).forEach((el) => el.removeAttribute("aria-invalid"));
      $$("[id$=-err]", form).forEach((el) => { el.textContent = ""; });
      const keep = (k, v) => (v === eff(s[k]) ? s[k].own : v);
      if (form.entrypoint) next.entrypoint = form.entrypoint.value || undefined;
      if (form.concurrency) {
        const n = Number(form.concurrency.value);
        if (n === Number(eff(s.concurrency))) next.concurrency = s.concurrency.own;
        else if (!Number.isInteger(n) || n < 1 || n > 64) errors.push(["concurrency", "A whole number from 1 to 64."]);
        else next.concurrency = n;
      }
      if (form.on_failure) {
        if (ON_FAILURE.includes(form.on_failure.value)) next.on_failure = keep("on_failure", form.on_failure.value);
        else errors.push(["on_failure", "Choose one of the listed actions."]);
      }
      const t = form.tmode.value, limit = Number(form.timeout.value);
      if (t !== "set") next.timeout = t === "none" ? null : undefined;
      else if (form.timeout.value === "" || !Number.isFinite(limit) || limit <= 0) errors.push(["timeout", "A positive number of seconds."]);
      else next.timeout = limit;
      next.logs = form.lmode.value === "inherit" ? undefined : form.logs.value.split("\n").map((l) => l.trim()).filter(Boolean);
      if (errors.length) {
        for (const [name, text] of errors) { form[name].setAttribute("aria-invalid", "true"); $(`#wfs-${name}-err`, form).textContent = text; }
        form[errors[0][0]].focus();
        return;
      }
      let after;
      try { after = applySettings(d.data, next, d.template); } catch (error) { $("#wfs-timeout-err", form).textContent = error.message; return; }
      close();
      if (JSON.stringify(after) === JSON.stringify(d.data)) { focusAction(null, "settings"); return; }
      change(ctx, project, () => after, null, "settings", "Workflow settings updated; Save to keep changes");
    });
    requestAnimationFrame(() => $("select, input", form)?.focus());
  }, "wfe-modal");
}

function stagesDialog(ctx, project) {
  const d = drafts.get(project);
  const list = stageList(d.data, d.template).map((s) => ({ ...s }));
  const original = JSON.stringify(list);
  const row = (s, i, n) => `<li class="wfe-stage"><span class="tabular muted">${i + 1}</span>
      <div class="grow"><input class="input" data-stage-title="${i}" value="${esc(s.title)}" aria-label="Stage ${i + 1} title" aria-describedby="wfs-stage-${i}"><small class="muted mono" id="wfs-stage-${i}">id ${esc(s.id)}</small> <small class="wfe-msg" data-stage-err="${i}"></small></div>
      <button type="button" class="btn sm" data-stage-move="-1" data-i="${i}" ${i ? "" : "disabled"}>Move earlier</button>
      <button type="button" class="btn sm" data-stage-move="1" data-i="${i}" ${i < n - 1 ? "" : "disabled"}>Move later</button></li>`;
  ctx.modal(`<header class="modal-h"><h2 id="wfe-title">Stages</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p class="muted small">Stages group steps in the graph. Moving a stage changes its display order; step order and dependencies stay as configured.</p>
      <ol class="wfe-stages"></ol><span class="sr-only" aria-live="polite" data-stage-live></span></div>
    <footer class="modal-f"><button class="btn" data-stage-add>${icon("plus")} Add stage</button><span class="grow"></span><button class="btn" data-close>Cancel</button><button class="btn primary" data-wfe-submit>Apply to draft</button></footer>`, (root, close) => {
    root.setAttribute("aria-labelledby", "wfe-title");
    const ol = $(".wfe-stages", root);
    const draw = (focus) => {
      ol.innerHTML = list.map((s, i) => row(s, i, list.length)).join("");
      if (focus) $(focus, ol)?.focus();
    };
    draw();
    ol.addEventListener("input", (e) => { const i = e.target.dataset.stageTitle; if (i != null) list[i].title = e.target.value; });
    ol.addEventListener("click", (e) => {
      const b = e.target.closest("[data-stage-move]");
      if (!b) return;
      const i = Number(b.dataset.i), dir = Number(b.dataset.stageMove), j = i + dir;
      [list[i], list[j]] = [list[j], list[i]];
      const same = `[data-i="${j}"][data-stage-move="${dir}"]`;
      draw(j === 0 || j === list.length - 1 ? `[data-i="${j}"][data-stage-move="${-dir}"]` : same);
      $("[data-stage-live]", root).textContent = `${list[j].title || list[j].id} moved to stage ${j + 1} of ${list.length}`;
    });
    $("[data-stage-add]", root).addEventListener("click", () => {
      list.push({ id: slugFor("stage", list.map((s) => s.id)), title: "" });
      draw(`[data-stage-title="${list.length - 1}"]`);
    });
    $("[data-wfe-submit]", root).addEventListener("click", () => {
      const empty = list.findIndex((s) => !s.title.trim());
      if (empty >= 0) {
        const input = $(`[data-stage-title="${empty}"]`, ol);
        input.setAttribute("aria-invalid", "true");
        $(`[data-stage-err="${empty}"]`, ol).textContent = "Name this stage.";
        input.focus();
        return;
      }
      close();
      if (JSON.stringify(list) === original) { focusAction(null, "stages"); return; }
      change(ctx, project, (data, tpl) => setStages(data, list, tpl), null, "stages", "Stages updated; Save to keep changes");
    });
  }, "wfe-modal");
}

// --- step dialog -----------------------------------------------------------------

function stepDialog(ctx, project, id, at = null) {
  const d = drafts.get(project);
  if (!d) return;
  const rows = editorSteps(d.data, d.template);
  const row = id ? rows.find((r) => r.id === id) : null;
  if (id && !row) return;
  const others = rows.filter((r) => r.id !== id);
  const stages = stageList(d.data, d.template);
  const entries = entrypointNames(d.data, d.template);
  const fallback = defaultEntrypoint(d.data, d.template);
  const depends = row ? (row.depends_on ?? (row.after != null && !isDelay(row.after) ? [String(row.after)] : null)) : null;
  const after = depends === null ? "previous" : depends.length ? "pick" : "start";
  const runs = row?.cmd ? "cmd" : row?.entrypoint ? "entrypoint" : fallback ? "default" : "cmd";
  const prevId = at != null ? rows[at - 1]?.id : null;
  ctx.modal(`<header class="modal-h"><h2 id="wfe-title">${row ? `Edit step <span class="mono">${esc(row.id)}</span>` : "Add a step"}</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <form class="modal-b wfe-form" novalidate>
      <label class="field"><span>Label</span><input class="input" name="label" value="${esc(row?.label || "")}" placeholder="e.g. Prepare data" autocomplete="off"></label>
      <label class="field"><span>Step name <em class="muted small">(used in commands, logs and plan columns)</em></span><input class="input mono" name="step_id" value="${esc(row?.id || "")}" placeholder="prepare-data" required pattern="${STEP_ID.source.slice(1, -1)}" autocomplete="off" ${row?.fromTemplate ? 'readonly title="A template step keeps its name"' : ""}><small class="wfe-msg" id="wfe-id-msg" aria-live="polite"></small></label>
      <fieldset class="field"><legend>Runs</legend>
        <div class="seg wfe-runs" role="radiogroup">
          <label><input type="radio" name="runs" value="cmd" ${runs === "cmd" ? "checked" : ""}> Command</label>
          ${entries.length ? `<label><input type="radio" name="runs" value="entrypoint" ${runs === "entrypoint" ? "checked" : ""}> Entrypoint</label>` : ""}
          ${fallback ? `<label><input type="radio" name="runs" value="default" ${runs === "default" ? "checked" : ""}> Workflow default (${esc(fallback)})</label>` : ""}
        </div>
        <div data-runs="cmd"><input class="input mono" name="cmd" value="${esc(row?.cmd ? joinCommand(row.cmd) : "")}" placeholder='python scripts/prepare.py --target {target}' autocomplete="off">
          <small class="muted">No shell: quote words with spaces. Placeholders: <code>{target}</code> <code>{workdir}</code> <code>{project_code}</code> <code>{step}</code> <code>{FILENAMES}</code>.</small></div>
        ${entries.length ? `<div data-runs="entrypoint"><select class="input" name="entrypoint">${entries.map((e) => `<option ${e === row?.entrypoint ? "selected" : ""}>${esc(e)}</option>`).join("")}</select></div>` : ""}
      </fieldset>
      <div class="row gap wrap">
        <label class="field grow"><span>Stage</span><select class="input" name="stage"><option value="">No stage</option>${stages.map((s) => `<option value="${esc(s.id)}" ${s.id === row?.stage ? "selected" : ""}>${esc(s.title)}</option>`).join("")}<option value="__new">New stage…</option></select></label>
        <label class="field grow" data-new-stage hidden><span>New stage title</span><input class="input" name="stage_title" placeholder="e.g. Calibration"></label>
      </div>
      <fieldset class="field"><legend>Starts</legend>
        <label><input type="radio" name="after" value="previous" ${after === "previous" ? "checked" : ""}> After the previous step${prevId && !row ? ` (<span class="mono">${esc(prevId)}</span>)` : ""}</label>
        <label><input type="radio" name="after" value="start" ${after === "start" ? "checked" : ""}> No step dependencies</label>
        <small class="muted">Eligible when the run starts; concurrency and other scheduling limits still apply.</small>
        ${others.length ? `<label><input type="radio" name="after" value="pick" ${after === "pick" ? "checked" : ""}> After these steps:</label>
        <div class="wfe-deps" data-pick>${others.map((o) => `<label class="chip"><input type="checkbox" name="dep" value="${esc(o.id)}" ${depends?.includes(o.id) ? "checked" : ""}> <span class="mono">${esc(o.id)}</span></label>`).join("")}</div>` : ""}
      </fieldset>
      <details class="wfe-adv" ${row && (row.timeout || isDelay(row.after) || row.description || row.logs.length || row.category) ? "open" : ""}><summary>More options</summary>
        <div class="row gap wrap">
          <label class="field grow"><span>Time limit (seconds)</span><input class="input tabular" name="timeout" type="number" min="1" step="1" value="${esc(row?.timeout ?? "")}" placeholder="project default"></label>
          <label class="field grow"><span>Start delay</span><input class="input mono" name="delay" value="${esc(isDelay(row?.after) ? row.after : "")}" placeholder="e.g. +30m, 2h"></label>
          <label class="field grow"><span>Category</span><input class="input" name="category" value="${esc(row?.category || "")}" placeholder="Step"></label>
        </div>
        <label class="field"><span>Description</span><textarea class="input" name="description" rows="2">${esc(row?.description || "")}</textarea></label>
        <label class="field"><span>Log files <em class="muted small">(one pattern per line; shown under Logs)</em></span><textarea class="input mono" name="logs" rows="2" placeholder="{workdir}/logs/{step}_*.log">${esc((row?.logs || []).join("\n"))}</textarea></label>
      </details>
      <p class="wfe-msg" id="wfe-form-msg" role="alert"></p>
    </form>
    <footer class="modal-f"><button class="btn" data-close>Cancel</button><button class="btn primary" data-wfe-submit>${row ? "Apply" : "Add step"}</button></footer>`, (root, close) => {
    root.setAttribute("aria-labelledby", "wfe-title");
    $$("summary", root).forEach((el) => { el.tabIndex = 0; });
    const form = $("form", root);
    let idTouched = Boolean(row);
    const sync = () => {
      const r = form.runs?.value || (form.querySelector("[name=runs]:checked") || {}).value;
      $$("[data-runs]", form).forEach((x) => { x.hidden = x.dataset.runs !== r; });
      const pick = $("[data-pick]", form);
      if (pick) pick.classList.toggle("off", (form.querySelector("[name=after]:checked") || {}).value !== "pick");
      $("[data-new-stage]", form).hidden = form.stage.value !== "__new";
    };
    sync();
    form.addEventListener("change", sync);
    form.label.addEventListener("input", () => {
      if (!idTouched) form.step_id.value = slugFor(form.label.value, others.map((o) => o.id));
    });
    form.step_id.addEventListener("input", () => {
      idTouched = true;
      const v = form.step_id.value.trim();
      $("#wfe-id-msg", form).textContent = !v ? "" : !STEP_ID.test(v) ? "Letters, digits, '-', '_' or '.'; start with a letter or digit." : others.some((o) => o.id === v) ? "Another step has this name." : "";
    });
    $$("[name=dep]", form).forEach((c) => c.addEventListener("change", () => { const p = form.querySelector("[name=after][value=pick]"); if (p) p.checked = true; sync(); }));
    const submit = (e) => {
      e?.preventDefault();
      const msg = $("#wfe-form-msg", form);
      try {
        const runsVal = (form.querySelector("[name=runs]:checked") || {}).value;
        const afterVal = (form.querySelector("[name=after]:checked") || {}).value;
        const stepName = form.step_id.value.trim() || slugFor(form.label.value, others.map((o) => o.id));
        const cmd = runsVal === "cmd" ? splitCommand(form.cmd.value) : null;
        if (runsVal === "cmd" && !cmd.length && !fallback) throw new Error("Give the step a command, e.g. python run.py {target}.");
        let stage = form.stage.value;
        const newStage = stage === "__new" ? form.stage_title.value.trim() : "";
        if (stage === "__new" && !newStage) throw new Error("Name the new stage.");
        const deps = afterVal === "pick" ? $$("[name=dep]:checked", form).map((c) => c.value) : afterVal === "start" ? [] : null;
        if (afterVal === "pick" && !deps.length) throw new Error("Pick at least one step to wait for.");
        const delay = form.delay.value.trim();
        if (delay && !isDelay(delay)) throw new Error("Start delay: a duration like +30m, 2h or 90 (seconds).");
        const timeout = form.timeout.value === "" ? null : Number(form.timeout.value);
        if (timeout != null && !(timeout > 0)) throw new Error("Time limit: a positive number of seconds.");
        const fields = {
          id: stepName,
          label: form.label.value.trim(),
          cmd: cmd && cmd.length ? cmd : null,
          entrypoint: runsVal === "entrypoint" ? form.entrypoint.value : null,
          stage: null,
          depends_on: deps,
          after: delay || null,
          timeout,
          category: form.category.value.trim(),
          description: form.description.value.trim(),
          logs: form.logs.value.split("\n").map((l) => l.trim()).filter(Boolean),
        };
        const stageId = newStage ? slugFor(newStage, stageList(d.data, d.template).map((s) => s.id)) : stage;
        fields.stage = stageId || null;
        // Template steps: only send what differs from the template, so the template keeps owning the rest.
        if (row?.fromTemplate) {
          for (const k of ["label", "category", "description", "stage"]) if ((fields[k] || "") === (row[k] || "")) delete fields[k];
          if (JSON.stringify(fields.logs) === JSON.stringify(row.logs)) delete fields.logs;
        }
        let next = d.data;
        if (newStage) next = addStage(next, { id: stageId, title: newStage }, d.template);
        next = row ? updateStep(next, row.id, fields, d.template) : addStep(next, fields, at, d.template);
        d.history.push(d.data);
        d.data = next;
        d.selected = stepName;
        close();
        ctx.update();
        focusAction(stepName);
        ctx.toast(row ? `${stepName} updated — Save to keep it` : `${stepName} added — Save to keep it`, "ok");
      } catch (error) {
        msg.textContent = error.message;
      }
    };
    form.addEventListener("submit", submit);
    $("[data-wfe-submit]", root).addEventListener("click", submit);
    requestAnimationFrame(() => form.label.focus());
  }, "wfe-modal");
}
