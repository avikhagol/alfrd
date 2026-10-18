// New project on a canvas (#/new-project): start empty or from a template, add steps and connect
// them on the canvas (graph_view.js with the Workflow editor's edits, data/workflow_edit.js), assign
// agents and personas, list the first targets, validate, then create through the existing service
// (POST /api/studio/projects/create, files in .alfrd/ by default) and open the project on Workflow
// with its first target selected. The draft is kept in this browser until created or discarded.
import { $, esc, icon, loadCss, storage } from "../utils/dom.js";
import { parseYaml } from "../utils/yaml_parser.js";
import { server } from "../data/server.js";
import { loadTemplate } from "../data/defs.js";
import { joinCommand, splitCommand, dependencyMap, connectProblem, sequenceOf } from "../data/workflow_edit.js";
import {
  addDraftStep, agentsOf, connectDraft, connectDraftProblem, disconnectDraft, draftModel, draftPayload, draftProblems, newDraft,
  personasOf, removeDraftStep, stepsOf, targetProblem, updateDraftStep,
} from "../data/project_draft.js";
import { mountGraph } from "./graph_view.js";

const KEY = "ui:newproject.draft";
let d = storage.get(KEY, null);
let templates = null, graph = null, host = null, ctxRef = null, report = null, busy = false, shown = {};
const save = () => storage.set(KEY, d);
const changed = (k, v) => { const s = JSON.stringify(v); if (shown[k] === s) return false; shown[k] = s; return true; };

export function render(el, ctx) {
  loadCss("css/chat.css");
  ctxRef = ctx;
  if (ctx.state.mode !== "server") { host = null; delete el.dataset.np; el.innerHTML = `<div class="empty"><h2>New project</h2><p>Creating a project needs <code>alfrd serve</code>.</p></div>`; return; }
  if (el.dataset.np !== "1" || !el.isConnected || host !== el) mount(el, ctx);
  if (!templates || !d) return;
  draw();
}

async function mount(el, ctx) {
  host = el; shown = {};
  el.dataset.np = "1";
  el.innerHTML = `<div class="npc">
    <div class="row gap wrap"><h2 class="grow" tabindex="-1" id="np-title">New project</h2>
      <button type="button" class="btn sm" data-np="quick" title="The short form: folder, name and template only">Quick dialog…</button>
      <button type="button" class="btn sm" data-np="reset">${icon("reset")} Discard draft</button></div>
    <p class="muted small">Plan the workflow before creating the project: add steps (N), connect them (drag a step's bottom handle onto another, or C then arrows and Enter), give them agents. The draft stays in this browser until you create it.</p>
    <form class="card npc-form" id="np-form" autocomplete="off" aria-label="Project">
      <label class="field"><span>Folder</span><div class="row gap"><input class="input grow" name="path" required placeholder="/path/to/project"><button type="button" class="btn" data-np="browse" aria-haspopup="dialog">Browse…</button></div></label>
      <label class="field"><span>Name</span><input class="input" name="name" placeholder="Folder name" maxlength="200"></label>
      <label class="field"><span>Template</span><select class="input" name="template"></select></label>
      <fieldset class="field"><legend>Start</legend><label class="row gap small"><input type="radio" name="start" value="template"> The template's workflow</label><label class="row gap small"><input type="radio" name="start" value="empty"> An empty canvas</label></fieldset>
      <label class="field"><span>Configuration file</span><select class="input" name="manifest"><option value=".alfrd/alfrd.yaml">.alfrd/alfrd.yaml (default)</option><option value="alfrd.yaml">alfrd.yaml in the project root (lower precedence)</option></select></label>
      <label class="field" data-loop><span>Initial task</span><textarea class="input" name="task" rows="3"></textarea></label>
      <label class="field" data-loop><span>Iterations (total turns)</span><input class="input" name="iterations" type="number" min="1" max="200"></label>
    </form>
    <div class="npc-main"><div class="npc-graph" id="np-graph"></div><aside class="card npc-side" id="np-side" aria-label="Selected step"></aside></div>
    <section class="card" aria-labelledby="np-targets-h"><h3 id="np-targets-h">Targets</h3><div id="np-targets"></div></section>
    <section class="card npc-check"><div id="np-report" tabindex="-1"></div>
      <div class="row gap"><span class="grow"></span><button type="button" class="btn" data-np="validate">${icon("validate")} Validate</button><button type="button" class="btn primary" data-np="create">${icon("plus")} Create project</button></div></section>
  </div>`;
  graph = mountGraph($("#np-graph", el), { label: "Draft workflow", editable: true, empty: "No steps yet. Add step (N) puts one on the canvas.",
    onSelect: (id) => { d.selected = id; save(); drawSide(); },
    onOpen: (id) => { d.selected = id; save(); drawSide(); requestAnimationFrame(() => $("#np-side input, #np-side select", host)?.focus()); },
    onAdd: () => edit((x) => addDraftStep(x), "Step added"),
    onDelete: (id) => edit((x) => removeDraftStep(x, id), `${id} removed`),
    onConnect: (from, to) => { const problem = connectDraftProblem(d, from, to); if (problem) { ctx.toast(problem, "warn"); return problem; } edit((x) => connectDraft(x, from, to)); return null; },
    onDisconnect: (from, to) => { if (confirm(`${to} no longer waits for ${from}?`)) edit((x) => disconnectDraft(x, from, to)); },
  });
  bind(el, ctx);
  try { templates = (await server.projectTemplates()).templates; } catch (error) { templates = [{ name: "basic", description: "" }]; ctx.toast(`Templates: ${error.message}`, "warn"); }
  if (!d || d.v !== 1) await reset(d?.template || "basic", "template");
  else ctx.update();
}

async function reset(template, start) {
  const tpl = await loadTemplate(template, parseYaml);
  if (!tpl) { ctxRef.toast(`Template ${template} could not be loaded`, "fail"); return; }
  const keep = d ? { path: d.path, name: d.name, manifest: d.manifest } : {};
  d = { ...newDraft(template, tpl, start), ...keep };
  report = null; shown = {}; save(); ctxRef.update();
}

function edit(fn, say = "") {
  try { d = fn(d); report = null; save(); draw(); if (say) $("#np-live", host) && ($("#np-live", host).textContent = say); }
  catch (error) { ctxRef.toast(error.message, "fail"); }
}

function draw() {
  const form = $("#np-form", host);
  if (changed("form", [templates, d.template, d.start, d.kind])) {
    form.template.innerHTML = templates.map((t) => `<option value="${esc(t.name)}" ${t.name === d.template ? "selected" : ""}>${esc(t.name)}${t.description ? ` — ${esc(t.description)}` : ""}</option>`).join("");
    form.querySelectorAll("[name=start]").forEach((r) => { r.checked = r.value === d.start; r.disabled = d.kind === "sequence"; });
    form.querySelectorAll("[data-loop]").forEach((f) => { f.hidden = d.kind !== "sequence"; });
  }
  for (const name of ["path", "name", "task", "iterations", "manifest"]) if (document.activeElement !== form[name] && form[name].value !== String(d[name] ?? "")) form[name].value = d[name] ?? "";
  graph.update(draftModel(d), d.selected);
  drawSide(); drawTargets(); drawReport();
}

function drawSide() {
  const box = $("#np-side", host);
  if (!changed("side", [d.selected, d.data, d.roles, d.kind]) ) return;
  if (d.kind === "sequence") {
    const seq = sequenceOf(d.data) || [], i = Number(String(d.selected || "").replace("turn-", "")) - 1;
    box.innerHTML = `<h3>Agent turns</h3><p class="muted small">An agent loop runs these turns in order and repeats them. Add step (N) appends a turn.</p>
      ${seq[i] ? `<label class="field"><span>Agent of turn ${i + 1}</span><select class="input" data-f="agent">${agentsOf(d).map((a) => `<option ${a === seq[i] ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></label>
      <button type="button" class="btn sm danger" data-np="delete" ${seq.length < 2 ? "disabled" : ""}>${icon("trash")} Remove turn</button>` : '<p class="muted small">Select a turn to change its agent.</p>'}`;
    return;
  }
  const r = stepsOf(d).find((x) => x.id === d.selected);
  if (!r) { box.innerHTML = `<h3>Step</h3><p class="muted small">Select a step on the canvas (or with the arrow keys) to configure it.</p><span class="sr-only" aria-live="polite" id="np-live"></span>`; return; }
  const deps = dependencyMap(d.data)[r.id] || [];
  const agents = agentsOf(d), personas = personasOf(d);
  const mode = r.cmd ? "cmd" : r.entrypoint || "";
  box.innerHTML = `<h3>Step <span class="mono">${esc(r.id)}</span></h3><span class="sr-only" aria-live="polite" id="np-live"></span>
    <label class="field"><span>Id</span><input class="input mono" data-f="id" value="${esc(r.id)}" pattern="[A-Za-z0-9][A-Za-z0-9_.\\-]{0,63}" required></label>
    <label class="field"><span>Label</span><input class="input" data-f="label" value="${esc(r.label)}" maxlength="120"></label>
    <label class="field"><span>Agent</span><select class="input" data-f="agent"><option value="">${r.fromTemplate ? "Template default" : "None yet"}</option>${agents.map((a) => `<option value="${esc(a)}" ${a === mode ? "selected" : ""}>${esc(a)}</option>`).join("")}<option value="cmd" ${mode === "cmd" ? "selected" : ""}>Own command…</option></select></label>
    ${mode === "cmd" ? `<label class="field"><span>Command (no shell)</span><input class="input mono" data-f="cmd" value="${esc(joinCommand(r.cmd))}" placeholder="python run.py {target}"></label>` : ""}
    ${personas.length ? `<label class="field"><span>Persona</span><select class="input" data-f="role"><option value="">None</option>${personas.map((p) => `<option value="${esc(p.key)}" ${d.roles[r.id] === p.key ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>` : ""}
    <fieldset class="field"><legend>Waits for</legend>${stepsOf(d).filter((o) => o.id !== r.id).map((o) => {
      const on = deps.includes(o.id), why = on ? null : connectProblem(d.data, o.id, r.id);
      return `<label class="row gap small" ${why ? `title="${esc(why)}"` : ""}><input type="checkbox" data-dep="${esc(o.id)}" ${on ? "checked" : ""} ${why ? "disabled" : ""}> <span class="mono">${esc(o.id)}</span></label>`;
    }).join("") || '<p class="muted small">No other steps.</p>'}</fieldset>
    <p class="fail-t small" role="alert" id="np-step-error"></p>
    <button type="button" class="btn sm danger" data-np="delete">${icon("trash")} Delete step</button>`;
}

function drawTargets() {
  const box = $("#np-targets", host);
  if (!changed("targets", [d.targets, d.kind])) return;
  const loop = d.kind === "sequence";
  box.innerHTML = `<p class="muted small">${loop ? "The first task's folder name (default <code>task</code>). More tasks come later from the Target picker." : "The project's first targets (rows of alfrd.targets.csv). The first one is selected when the project opens."}</p>
    <ul class="npc-tlist">${d.targets.map((t, i) => `<li><span class="mono">${esc(t)}</span> <button type="button" class="icon-btn xs" data-trm="${i}" aria-label="Remove target ${esc(t)}">${icon("trash")}</button></li>`).join("")}</ul>
    ${loop && d.targets.length ? "" : `<div class="row gap"><label class="sr-only" for="np-target">New target name</label><input class="input sm" id="np-target" placeholder="${loop ? "task" : "target name"}" aria-describedby="np-target-error"><button type="button" class="btn sm" data-np="target">${icon("plus")} Add</button></div><p class="fail-t small" id="np-target-error" role="alert"></p>`}`;
}

function drawReport() {
  const box = $("#np-report", host);
  if (!changed("report", [report, busy])) return;
  $("[data-np=create]", host).disabled = busy;
  $("[data-np=create]", host).setAttribute("aria-busy", String(busy));
  if (!report) { box.innerHTML = `<p class="muted small">${busy ? "Creating…" : "Validate checks the draft; Create validates again on the server."}</p>`; return; }
  box.innerHTML = report.errors.length ? `<div class="callout fail small" role="alert"><b>${report.errors.length} problem${report.errors.length === 1 ? "" : "s"} to fix</b><ul>${report.errors.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>`
    : `<p class="badge tone-ok" role="status">${icon("checkCircle")}<span>Ready to create</span></p>`;
  if (report.warnings.length) box.insertAdjacentHTML("beforeend", `<ul class="small warn-t">${report.warnings.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`);
}

async function create(ctx) {
  report = draftProblems(d);
  if (report.errors.length) { draw(); $("#np-report", host).focus(); return; }
  busy = true; draw();
  try {
    const payload = draftPayload(d);
    const project = await server.createProject(payload);
    const first = d.targets[0] || (d.kind === "sequence" ? "task" : null);
    storage.set(KEY, null); d = null; templates = null; delete host.dataset.np;
    ctx.toast(`Project ${project.name} created`, "ok");
    await ctx.openCreated(project, first);
  } catch (error) {
    report = { errors: [error.message], warnings: [] };
  } finally { busy = false; if (d) draw(); }
}

function bind(el, ctx) {
  const form = $("#np-form", el);
  form.addEventListener("input", (e) => {
    const t = e.target;
    if (["path", "name", "task", "manifest"].includes(t.name)) d[t.name] = t.value;
    if (t.name === "iterations") d.iterations = Number(t.value);
    report = null; save();
  });
  form.addEventListener("change", async (e) => {
    const t = e.target;
    if (t.name === "manifest") { d.manifest = t.value; save(); }
    if (t.name !== "template" && t.name !== "start") return;
    const template = t.name === "template" ? t.value : d.template, start = t.name === "start" ? t.value : d.start;
    if ((stepsOf(d).length || d.targets.length) && !confirm("Replace the canvas with this choice? Steps, connections and targets of the draft are discarded.")) { shown.form = null; draw(); return; }
    await reset(template, start);
  });
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const a = b.dataset.np;
    if (a === "quick") ctx.newProject(d?.path || "");
    else if (a === "reset" && confirm("Discard this draft?")) reset(d.template, "template");
    else if (a === "browse") browseInModal(ctx, form.path);
    else if (a === "validate") { report = draftProblems(d); draw(); $("#np-report", el).focus(); }
    else if (a === "create") create(ctx);
    else if (a === "delete" && d.selected) edit((x) => removeDraftStep(x, x.selected), "Removed");
    else if (a === "target") addTarget();
    else if (b.dataset.trm != null) { d = { ...d, targets: d.targets.filter((_, i) => i !== Number(b.dataset.trm)) }; save(); draw(); $("#np-target", el)?.focus(); }
  });
  el.addEventListener("keydown", (e) => { if (e.target.id === "np-target" && e.key === "Enter") { e.preventDefault(); addTarget(); } });
  el.addEventListener("change", (e) => {
    const t = e.target, side = t.closest("#np-side");
    if (!side || !d.selected) return;
    const err = $("#np-step-error", el);
    try {
      if (t.dataset.dep) {
        const deps = dependencyMap(d.data)[d.selected] || [];
        d = t.checked ? connectDraft(d, t.dataset.dep, d.selected) : disconnectDraft(d, t.dataset.dep, d.selected);
      } else if (d.kind === "sequence") d = updateDraftStep(d, d.selected, { agent: t.value });
      else {
        const r = stepsOf(d).find((x) => x.id === d.selected);
        const f = { id: r.id, label: r.label, agent: r.entrypoint || "", cmd: r.cmd, role: d.roles[r.id] || "" };
        if (t.dataset.f === "id") { if (!t.checkValidity()) throw new Error("A step id uses letters, digits, '.', '_' or '-' and starts with a letter or digit."); f.id = t.value.trim(); }
        if (t.dataset.f === "label") f.label = t.value;
        if (t.dataset.f === "agent") { f.agent = t.value === "cmd" ? "" : t.value; f.cmd = t.value === "cmd" ? (r.cmd || ["true"]) : null; }
        if (t.dataset.f === "cmd") { f.cmd = splitCommand(t.value); if (!f.cmd.length) throw new Error("Enter a command, or choose an agent."); }
        if (t.dataset.f === "role") f.role = t.value;
        d = updateDraftStep(d, d.selected, f);
      }
      report = null; save(); draw();
      const again = t.dataset.f ? $(`#np-side [data-f="${t.dataset.f}"]`, el) : $(`#np-side [data-dep="${CSS.escape(t.dataset.dep || "")}"]`, el);
      again?.focus();
    } catch (error) { if (err) err.textContent = error.message; }
  });
}

function addTarget() {
  const input = $("#np-target", host), err = $("#np-target-error", host);
  const name = input.value.trim(), problem = targetProblem(name, d.targets, d.kind === "sequence");
  if (problem) { err.textContent = problem; input.focus(); return; }
  d = { ...d, targets: [...d.targets, name] }; report = null; save(); draw();
  $("#np-target", host)?.focus();
}


/** Browse… for the project folder: the server folder browser in a dialog (Use this folder fills the field). */
function browseInModal(ctx, input) {
  ctx.modal(`<header class="modal-h"><h2>${icon("folder")} Choose the project folder</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p class="muted small">Folders on the machine running <code>alfrd serve</code>. Open a folder, then <b>Use this folder</b>; <b>New folder</b> creates one there.</p><div id="np-folders"></div></div>`, (root, close) => {
    const pick = (path) => {
      close();
      input.value = path;
      ["input", "change"].forEach((t) => input.dispatchEvent(new Event(t, { bubbles: true })));
      input.focus();
    };
    const b = ctx.browseFolder(root.querySelector("#np-folders"), input, { use: pick, close: () => close() });
    root.addEventListener("beforeclose", () => b.destroy());
  }, "wide");
}
