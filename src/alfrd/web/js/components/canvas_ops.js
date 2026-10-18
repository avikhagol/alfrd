// Workflow canvas operations, loaded on the first right-click, port drag or canvas key:
// context menus (with keyboard equivalents), connect / disconnect, add / delete steps,
// the "Configure" step inspector, and run actions for the workflow and for one step.
// Every edit goes through the workflow editor's draft (workflow_editor.js), so Save uses
// ctx.saveManifest: the active alfrd.yaml and its conflict guard.

import { $, $$, esc, keyLabel, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";
import { markDirty } from "../data/workspace.js";
import {
  addStep, connectSteps, defaultEntrypoint, dependencyMap, disconnectSteps, editorSteps, entrypointNames, findCycle,
  isDelay, joinCommand, setDependencies, setSkip, slugFor, splitCommand, stageList, updateStep,
} from "../data/workflow_edit.js";
import { loadPlan, openLog, openRunDialog, planAct, planOf } from "./plans.js";

loadCss("css/lazy.css");

let link = null; // keyboard connect: {from, cand}
const forms = new Map(); // project -> unapplied Configure form {key, v, deps, depsTouched, dirty, error}

export const connecting = () => Boolean(link);

/** Why `to` cannot wait for `from` on the graph as shown (null when it can). */
export function linkProblem(api, from, to) {
  const deps = Object.fromEntries(api.steps().filter((s) => !s.skipped).map((s) => [s.key, [...s.depends]]));
  if (!(from in deps) || !(to in deps)) return "Skipped steps cannot be connected.";
  if (from === to) return "A step cannot wait for itself.";
  if (deps[to].includes(from)) return `${to} already waits for ${from}.`;
  const cycle = findCycle({ ...deps, [to]: [...deps[to], from] });
  return cycle ? `This would create a dependency cycle: ${cycle.join(" → ")}.` : null;
}

/** The workflow draft (started when needed); null when this workflow cannot be edited on the canvas. */
async function draft(ctx, api) {
  const project = api.project();
  if (!api.canEdit()) { ctx.toast("This project's alfrd.yaml is not loaded, so the workflow cannot be edited here.", "warn"); return null; }
  const wfe = await api.editor();
  if (!(await wfe.ensureEditing(ctx, project))) return null;
  if (!wfe.canAddInGraph(project)) { ctx.toast("Agent-loop turns are edited in the turn order above the canvas.", "warn"); return null; }
  return { wfe, project, d: wfe.draftOf(project) };
}

function refuse(ctx, api, text) {
  ctx.toast(`Not connected: ${text}`, "fail");
  api.announce(`Not connected: ${text}`);
  return false;
}

// Drag from a step's port: a line follows the pointer; the step under it is marked can / cannot connect.
export function linkMove(api, drag, e, nodeH) {
  const a = api.pos(drag.key), p = api.worldAt(e.clientX, e.clientY);
  if (a) $("#wf-link", api.el)?.setAttribute("d", `M${a.x + a.w / 2},${a.y + nodeH} L${p.x},${p.y}`);
  const over = document.elementFromPoint(e.clientX, e.clientY)?.closest?.(".node");
  const key = over && over.dataset.key !== drag.key ? over.dataset.key : null;
  if (key === drag.over) return;
  $$(".node.link-cand, .node.link-bad", api.el).forEach((n) => n.classList.remove("link-cand", "link-bad"));
  drag.over = key;
  if (key) over.classList.add(linkProblem(api, drag.key, key) ? "link-bad" : "link-cand");
}

export function linkEnd(ctx, api, drag) {
  $("#wf-link", api.el)?.setAttribute("d", "");
  $$(".node.link-cand, .node.link-bad", api.el).forEach((n) => n.classList.remove("link-cand", "link-bad"));
  if (drag.over) connect(ctx, api, drag.key, drag.over);
}

export async function connect(ctx, api, from, to) {
  const problem = linkProblem(api, from, to);
  if (problem) return refuse(ctx, api, problem);
  const e = await draft(ctx, api);
  if (!e) return false;
  const ok = e.wfe.edit(ctx, e.project, (data, tpl) => connectSteps(data, from, to, tpl), to, `${to} now waits for ${from}; Save to keep it`);
  api.focus();
  return ok;
}

export async function disconnect(ctx, api, from, to) {
  const e = await draft(ctx, api);
  if (!e) return false;
  const ok = e.wfe.edit(ctx, e.project, (data, tpl) => disconnectSteps(data, from, to, tpl), to, `${to} no longer waits for ${from}; Save to keep it`);
  api.focus();
  return ok;
}

/** Add a step at a world point (or under `after`, waiting for it); then configure it in the inspector. */
export async function addStepAt(ctx, api, point = null, after = null) {
  const e = await draft(ctx, api);
  if (!e) return;
  const id = slugFor("step", editorSteps(e.d.data, e.d.template).map((r) => r.id));
  const ok = e.wfe.edit(ctx, e.project, (data, tpl) => addStep(data, { id, label: "New step", depends_on: after ? [after] : [] }, null, tpl), id,
    `${id} added${after ? ` after ${after}` : ""}; configure it, then Save`);
  if (!ok) return;
  const at = point || (after && api.pos(after) && { x: api.pos(after).x, y: api.pos(after).y + 176 });
  if (at) api.place(id, at.x, at.y);
  configure(ctx, api, id);
}

export async function configure(ctx, api, key) {
  if (!key) { ctx.toast("Select a step first (arrow keys)", "warn"); return; }
  if (!(await draft(ctx, api))) return;
  api.ui.tab = "step";
  api.ui.inspector = true;
  api.select(key);
  ctx.update();
  requestAnimationFrame(() => $("#sx-label")?.focus());
}

async function remove(ctx, api, key) {
  const e = key && (await draft(ctx, api));
  if (e) e.wfe.handle(ctx, e.project, "delete", { dataset: { id: key } });
}

async function skip(ctx, api, key, on) {
  const e = await draft(ctx, api);
  if (e) e.wfe.edit(ctx, e.project, (data, tpl) => setSkip(data, key, on, tpl), key, `${key} ${on ? "skipped" : "runs again"}; Save to keep it`);
}

// --- keyboard connect ---------------------------------------------------------

function startLink(ctx, api, from) {
  if (!from) { ctx.toast("Select a step first (arrow keys)", "warn"); return; }
  if (!api.canEdit()) { ctx.toast("This workflow cannot be edited here.", "warn"); return; }
  link = { from, cand: null };
  move(api, 1);
  api.announce(`Connecting from ${from}. Arrow keys pick the step that should wait for it, Enter connects, Escape cancels.${link.cand ? ` ${candText(api)}` : ""}`);
}

function candText(api) {
  const problem = link.cand && linkProblem(api, link.from, link.cand);
  return link.cand ? `${link.cand}${problem ? `: ${problem}` : ""}` : "";
}

function move(api, dir) {
  const order = api.steps().filter((s) => !s.skipped && s.key !== link.from).map((s) => s.key);
  if (!order.length) return;
  const i = order.indexOf(link.cand);
  link.cand = order[i < 0 ? (dir > 0 ? 0 : order.length - 1) : (i + dir + order.length) % order.length];
  decorate(api);
}

/** Clicking a step while connecting picks it. */
export function pick(ctx, api, key) {
  if (!link) return;
  const { from } = link;
  link = null;
  decorate(api);
  if (key !== from) connect(ctx, api, from, key);
}

/** Connect-mode marks; canvas.js calls this after every render. */
export function decorate(api) {
  $$(".node.link-from, .node.link-cand, .node.link-bad", api.el).forEach((n) => n.classList.remove("link-from", "link-cand", "link-bad"));
  api.el.querySelector(".wf")?.classList.toggle("linking", Boolean(link));
  if (!link) return;
  const node = (k) => $(`.node[data-key="${CSS.escape(k)}"]`, api.el);
  node(link.from)?.classList.add("link-from");
  if (link.cand) node(link.cand)?.classList.add(linkProblem(api, link.from, link.cand) ? "link-bad" : "link-cand");
}

/** Canvas keys: N add, C connect, E configure, Delete remove; while connecting: arrows, Enter, Escape. */
export function key(ctx, api, e) {
  const sel = api.ui.selected;
  if (link) {
    if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(e.key)) {
      e.preventDefault();
      move(api, e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1);
      api.announce(candText(api));
      return true;
    }
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (link.cand) pick(ctx, api, link.cand); return true; }
    if (e.key === "Escape") { e.preventDefault(); link = null; decorate(api); api.announce("Connecting cancelled"); return true; }
    return false;
  }
  switch (e.key.toLowerCase()) {
    case "n": addStepAt(ctx, api, null, sel || null); return true;
    case "c": startLink(ctx, api, sel); return true;
    case "e": configure(ctx, api, sel); return true;
    case "delete": case "backspace": remove(ctx, api, sel); return true;
    default: return false;
  }
}

// --- menus -------------------------------------------------------------------

function anchorAt(api, x, y) {
  let a = $("#wf-cm-anchor", api.el);
  if (!a) {
    a = document.createElement("span");
    a.id = "wf-cm-anchor";
    a.className = "cm-anchor";
    a.tabIndex = -1;
    $("#wf-canvas", api.el).append(a);
  }
  a.style.left = `${x}px`;
  a.style.top = `${y}px`;
  return a;
}

const later = (fn) => setTimeout(fn, 0); // a menu opened from a menu item must outlive that item's click

/** Right-click (or Shift+F10 / the Menu key) on a step or on empty canvas space. */
export function openMenu(ctx, api, { key = null, x = null, y = null } = {}) {
  const s = key ? api.steps().find((st) => st.key === key) : null;
  if (x == null) {
    const r = (s ? $(`.node[data-key="${CSS.escape(key)}"]`, api.el) : $("#wf-canvas", api.el))?.getBoundingClientRect();
    x = (r?.left || 0) + 16;
    y = (r?.top || 0) + 28;
  }
  const anchor = anchorAt(api, x, y);
  const items = s ? stepItems(ctx, api, s, anchor) : canvasItems(ctx, api, api.worldAt(x, y));
  ctx.menu(anchor, items.filter((it, i, all) => it !== "-" || (i > 0 && all[i - 1] !== "-" && i < all.length - 1)));
}

function stepItems(ctx, api, s, anchor) {
  const items = [];
  if (api.canEdit()) {
    const outs = api.steps().filter((x) => x.depends.includes(s.key)).map((x) => x.key);
    items.push({ label: `Configure ${s.key}`, icon: "edit", hint: "E", run: () => configure(ctx, api, s.key) });
    if (!s.skipped) items.push({ label: "Connect to a step that waits for it…", icon: "graph", hint: "C", run: () => { api.select(s.key); startLink(ctx, api, s.key); api.focus(); } });
    if (s.depends.length || outs.length) items.push({ label: "Disconnect…", icon: "close", run: () => later(() => ctx.menu(anchor, [
      ...s.depends.map((d) => ({ label: `${s.key} stops waiting for ${d}`, icon: "close", run: () => disconnect(ctx, api, d, s.key) })),
      ...outs.map((o) => ({ label: `${o} stops waiting for ${s.key}`, icon: "close", run: () => disconnect(ctx, api, s.key, o) })),
    ])) });
    items.push({ label: "Add a step after it", icon: "plus", hint: "N", run: () => addStepAt(ctx, api, null, s.key) });
    items.push({ label: s.skipped ? "Run it again (unskip)" : "Skip it in the workflow", icon: s.skipped ? "play" : "pause", run: () => skip(ctx, api, s.key, !s.skipped) });
    items.push({ label: "Delete step…", icon: "trash", hint: "Delete", run: () => remove(ctx, api, s.key) });
  }
  return [...items, ...stepRunItems(ctx, api, s)];
}

function canvasItems(ctx, api, at) {
  const items = [];
  if (api.canEdit()) items.push({ label: "Add step here", icon: "plus", hint: "N", run: () => addStepAt(ctx, api, { x: at.x - 160, y: at.y - 40 }) }, "-");
  items.push({ label: "Fit to view", icon: "fit", hint: "0", run: () => api.fit() });
  if (document.fullscreenEnabled) items.push({ label: document.fullscreenElement ? "Exit fullscreen" : "Fullscreen", icon: "expand", hint: "F", run: () => api.fullscreen() });
  items.push({ label: api.ui && document.body.classList.contains("wf-compact") ? "Exit compact mode" : "Compact mode", icon: "fold", hint: keyLabel("Alt+Shift+C"), run: () => api.compact() });
  return [...items, ...workflowRunItems(ctx, api)];
}

// --- run actions ---------------------------------------------------------------

const canRun = (api) => api.plans() && Boolean(server.session?.mutations_enabled);

function workflowRunItems(ctx, api) {
  if (!canRun(api)) return [];
  const project = api.project();
  const p = planOf(project)?.plan;
  const act = (a) => () => planAct(ctx, project, a, null);
  const items = ["-", { label: "Run workflow…", icon: "play", run: act("new") }, { label: "Schedule a run…", icon: "clock", hint: "start time or delay", run: () => schedule(ctx, project) }];
  if (p && ["running", "paused", "interrupted"].includes(p.status)) {
    if (p.start_at) items.push({ label: `Start run ${p.id} now`, icon: "play", run: act("start-now") });
    else if (p.status === "running") items.push({ label: `Pause run ${p.id}`, icon: "pause", run: act("pause") });
    else items.push({ label: `Resume run ${p.id}`, icon: "play", run: act("resume") });
    items.push({ label: `Cancel run ${p.id}…`, icon: "stop", run: act("cancel") });
  }
  return items;
}

/** Run actions for one step: start a run of it, and for the header target's cell in the shown run: stop, retry, skip, log. */
function stepRunItems(ctx, api, s) {
  if (!canRun(api) || s.skipped) return [];
  const project = api.project();
  const status = planOf(project);
  const items = ["-", { label: `Run ${s.key}…`, icon: "play", run: () => planAct(ctx, project, "new", null, { only: [s.key] }) }];
  const row = api.overlay()?.row;
  const v = row?.cells?.[s.key];
  if (!row || !v || !status?.plan) return items;
  const unit = (status.units || []).slice().reverse().find((u) => (u.rows || [u.row]).includes(row.key) && (u.steps || []).includes(s.key));
  const set = (value) => setCell(ctx, project, status, row.key, s.key, value);
  if (v === "running" && unit?.status === "running") items.push({ label: `Stop ${s.key} for ${row.target}…`, icon: "stop", run: () => stopUnit(ctx, project, status.plan.id, unit) });
  if (["failed", "blocked", "interrupted", "cancelled", "done"].includes(v)) items.push({ label: `Retry ${s.key} for ${row.target}`, icon: "reset", run: () => set("todo") });
  if (v === "todo" && !status.plan.loop) items.push({ label: `Skip ${s.key} for ${row.target} in this run`, icon: "minus", run: () => set("skip") });
  if (unit?.log) items.push({ label: "Open its log", icon: "log", run: () => openLog(ctx, project, unit.log) });
  return items;
}

async function setCell(ctx, project, status, rowKey, step, value) {
  try {
    await server.planCells(project, status.plan.id, [{ row: rowKey, step, value }]);
    if (value === "todo" && status.plan.status !== "running") await server.planAction(project, status.plan.id, "resume");
    ctx.toast(value === "todo" ? `${step}: retry queued` : `${step}: skipped in run ${status.plan.id}`, "ok");
  } catch (error) { ctx.toast(error.message, "fail"); }
  loadPlan(ctx, project);
}

/** Stop one running command (SIGTERM to its process group); its cell becomes cancelled. */
export async function stopUnit(ctx, project, planId, unit) {
  if (!window.confirm(`Stop ${unit.steps?.join(", ") || unit.id} for ${unit.target || "this row"}? The command gets SIGTERM (then SIGKILL); the rest of run ${planId} continues.`)) return;
  try {
    await server.planAction(project, planId, `units/${encodeURIComponent(unit.id)}/stop`);
    ctx.toast(`Stopping ${unit.id}`, "ok");
  } catch (error) { ctx.toast(`Not stopped: ${error.message}`, "fail"); }
  loadPlan(ctx, project);
}

export async function schedule(ctx, project) {
  await openRunDialog(ctx, project, {});
  requestAnimationFrame(() => $("#run-at")?.focus());
}

/** Edge click / right-click: disconnect it, or select either end. */
export function edgeMenu(ctx, api, from, to, x, y) {
  ctx.menu(anchorAt(api, x, y), [
    ...(api.canEdit() ? [{ label: `Disconnect: ${to} stops waiting for ${from}`, icon: "close", run: () => disconnect(ctx, api, from, to) }] : []),
    { label: `Select ${from}`, icon: "target", run: () => api.select(from) },
    { label: `Select ${to}`, icon: "target", run: () => api.select(to) },
  ]);
}

// --- Configure (step inspector) --------------------------------------------------

const lines = (text) => String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);

function fresh(d, key) {
  const r = editorSteps(d.data, d.template).find((x) => x.id === key);
  if (!r) return null;
  const fallback = defaultEntrypoint(d.data, d.template);
  return {
    key, row: r, dirty: false, error: "", depsTouched: false,
    deps: [...(dependencyMap(d.data, d.template)[key] || [])],
    implicit: r.depends_on == null && !(r.after != null && !isDelay(r.after)),
    v: {
      label: r.label, id: r.id, runs: r.cmd ? "cmd" : r.entrypoint ? `ep:${r.entrypoint}` : fallback ? "default" : "cmd",
      cmd: r.cmd ? joinCommand(r.cmd) : "", stage: r.stage || "", inputs: r.inputs.join("\n"), outputs: r.outputs.join("\n"),
      timeout: r.timeout ?? "", delay: isDelay(r.after) ? String(r.after) : "", status_from: r.status_from || "", skip: r.skip,
    },
  };
}

function formOf(project, d, key) {
  let f = forms.get(project);
  if (!f || f.key !== key || !f.dirty) { const next = fresh(d, key); if (f?.error && f.key === key && next) next.error = f.error; f = next; if (f) forms.set(project, f); else forms.delete(project); }
  return f;
}

/** The Configure tab: command, dependencies, inputs/outputs and execution options, applied to the draft. */
export function inspector(ctx, api, key) {
  const project = api.project();
  const wfe = api.wfe();
  if (!wfe?.editing(project)) {
    return `<p class="muted small">Change this step's command, dependencies, inputs, outputs and options here. Changes go to a draft; Save writes alfrd.yaml.</p>
      <button class="btn primary" data-sx="start">Configure ${esc(key)}</button>`;
  }
  if (!wfe.canAddInGraph(project)) return '<p class="muted small">Agent-loop turns are set in the turn order above the canvas.</p>';
  const d = wfe.draftOf(project);
  const f = formOf(project, d, key);
  if (!f) return '<p class="muted small">This step is not in the draft.</p>';
  const v = f.v;
  const all = dependencyMap(d.data, d.template);
  const others = editorSteps(d.data, d.template).filter((r) => r.id !== key && !r.skip).map((r) => r.id);
  const entries = entrypointNames(d.data, d.template);
  const fallback = defaultEntrypoint(d.data, d.template);
  const field = (k, label, html, help = "") => `<div class="field sx-f"><label for="sx-${k}">${label}</label>${html}${help ? `<small class="muted" id="sx-${k}-help">${help}</small>` : ""}</div>`;
  const input = (k, extra = "") => `<input class="input" id="sx-${k}" data-sx="field" data-k="${k}" value="${esc(v[k])}" autocomplete="off" ${extra}>`;
  const area = (k, ph) => `<textarea class="input mono" id="sx-${k}" data-sx="field" data-k="${k}" rows="2" placeholder="${esc(ph)}">${esc(v[k])}</textarea>`;
  const dep = (o) => {
    const cycle = !f.deps.includes(o) && findCycle({ ...all, [key]: [...f.deps, o] });
    return `<label class="chip ${cycle ? "muted" : ""}" ${cycle ? `title="${esc(`Would create a cycle: ${cycle.join(" → ")}`)}"` : ""}><input type="checkbox" data-sx="dep" value="${esc(o)}" ${f.deps.includes(o) ? "checked" : ""} ${cycle ? "disabled" : ""}> <span class="mono">${esc(o)}</span>${cycle ? " (cycle)" : ""}</label>`;
  };
  return `<div class="sx" role="group" aria-label="Configure ${esc(key)}">
    ${field("label", "Label", input("label"))}
    ${field("id", "Step name", input("id", `class="input mono" ${f.row.fromTemplate ? 'readonly title="A template step keeps its name"' : ""}`))}
    ${field("runs", "Runs", `<select class="input" id="sx-runs" data-sx="field" data-k="runs"><option value="cmd" ${v.runs === "cmd" ? "selected" : ""}>Command</option>${entries.map((e) => `<option value="ep:${esc(e)}" ${v.runs === `ep:${e}` ? "selected" : ""}>Entrypoint ${esc(e)}</option>`).join("")}${fallback ? `<option value="default" ${v.runs === "default" ? "selected" : ""}>Workflow default (${esc(fallback)})</option>` : ""}</select>`)}
    ${v.runs === "cmd" ? field("cmd", "Command", input("cmd", 'class="input mono" placeholder="python run.py {target}"'), "No shell; quote words with spaces. {target}, {step}, {project_dir} …") : ""}
    <fieldset class="field sx-f"><legend>Waits for</legend>${others.length ? `<div class="wfe-deps">${others.map(dep).join("")}</div>` : '<span class="muted small">No other steps.</span>'}
      <small class="muted">${f.implicit && !f.depsTouched ? "Now: after the previous step in the list. Ticking steps sets them explicitly." : f.deps.length ? "" : "Starts first (no dependencies)."}</small></fieldset>
    ${field("inputs", "Inputs <em class=\"muted small\">(one per line)</em>", area("inputs", "data/{target}.fits"))}
    ${field("outputs", "Outputs <em class=\"muted small\">(one per line)</em>", area("outputs", "{workdir}/{target}.ms"))}
    ${field("stage", "Stage", `<select class="input" id="sx-stage" data-sx="field" data-k="stage"><option value="">No stage</option>${stageList(d.data, d.template).map((st) => `<option value="${esc(st.id)}" ${st.id === v.stage ? "selected" : ""}>${esc(st.title)}</option>`).join("")}</select>`)}
    <div class="row gap wrap">
      ${field("timeout", "Time limit (s)", input("timeout", 'type="number" min="1" step="1" class="input tabular" placeholder="default"'))}
      ${field("delay", "Start delay", input("delay", 'class="input mono" placeholder="+30m"'))}
    </div>
    ${field("status_from", "Status from", `<select class="input" id="sx-status_from" data-sx="field" data-k="status_from"><option value="">Plan default</option>${[["exit_code", "Exit code"], ["result_csv", "Results CSV"], ["both", "Both"]].map(([k, l]) => `<option value="${k}" ${v.status_from === k ? "selected" : ""}>${l}</option>`).join("")}</select>`)}
    <label class="sx-skip"><input type="checkbox" id="sx-skip" data-sx="field" data-k="skip" ${v.skip ? "checked" : ""}> Skip this step (it never runs; steps around it are chained)</label>
    <p class="wfe-msg" role="alert" id="sx-error">${esc(f.error)}</p>
    <div class="row gap"><button type="button" class="btn primary grow" data-sx="apply" id="sx-apply" ${f.dirty ? "" : "disabled"}>Apply to draft</button><button type="button" class="btn" data-sx="revert" ${f.dirty ? "" : "disabled"}>Revert</button></div>
    <button type="button" class="link-btn small" data-sx="dialog">More options (description, logs, category)…</button>
  </div>`;
}

/** May the selection leave the configured step? Asks when its form has unapplied changes. */
export function mayLeaveStep(ctx, project) {
  const f = forms.get(project);
  if (!f?.dirty) return true;
  if (!window.confirm(`Discard the changes to ${f.key} that are not applied yet?`)) return false;
  forms.delete(project);
  markDirty(project, "inspector", false);
  return true;
}

export async function inspectorEvent(ctx, api, e, t) {
  const project = api.project();
  const act = t.dataset.sx;
  if (e.type === "click" && t.tagName !== "BUTTON") return;
  if (act === "start") { await configure(ctx, api, api.ui.selected); return; }
  const wfe = api.wfe();
  const d = wfe?.draftOf(project);
  const f = forms.get(project);
  if (!d || !f) return;
  if (act === "field" || act === "dep") {
    if (act === "dep") {
      f.deps = $$("[data-sx=dep]:checked", api.el).map((c) => c.value);
      f.depsTouched = true;
    } else f.v[t.dataset.k] = t.type === "checkbox" ? t.checked : t.value;
    f.dirty = true;
    markDirty(project, "inspector", true);
    $$("#sx-apply, [data-sx=revert]", api.el).forEach((b) => { b.disabled = false; });
    if (e.type === "change" && (t.dataset.k === "runs" || act === "dep")) ctx.update(); // show/hide the command, cycle marks
    return;
  }
  if (act === "revert") { forms.delete(project); markDirty(project, "inspector", false); ctx.update(); return; }
  if (act === "dialog") { wfe.openStep(ctx, project, f.key); return; }
  if (act !== "apply") return;
  let next, id;
  try {
    ({ next, id } = applyForm(d, f));
  } catch (error) {
    f.error = error.message;
    ctx.update();
    api.announce(error.message);
    return;
  }
  forms.delete(project);
  markDirty(project, "inspector", false);
  wfe.edit(ctx, project, () => next, id, `${id} updated; Save to keep it`);
}

/** The draft with the Configure form applied → {next, id}; throws a readable error. */
export function applyForm(d, f) {
  const v = f.v, r = f.row;
  const id = String(v.id || "").trim();
  const fields = { id, label: String(v.label || "").trim(), stage: v.stage || null, status_from: v.status_from || null, inputs: lines(v.inputs), outputs: lines(v.outputs) };
  if (v.runs === "cmd") {
    const argv = splitCommand(v.cmd);
    if (!argv.length && !defaultEntrypoint(d.data, d.template)) throw new Error("Give the step a command, e.g. python run.py {target}.");
    Object.assign(fields, { cmd: argv.length ? argv : null, entrypoint: null });
  } else Object.assign(fields, { cmd: null, entrypoint: v.runs.startsWith("ep:") ? v.runs.slice(3) : null });
  const timeout = v.timeout === "" || v.timeout == null ? null : Number(v.timeout);
  if (timeout != null && !(timeout > 0)) throw new Error("Time limit: a positive number of seconds.");
  fields.timeout = timeout;
  const delay = String(v.delay || "").trim();
  if (delay && !isDelay(delay)) throw new Error("Start delay: a duration like +30m, 2h or 90 (seconds).");
  if (delay) fields.after = delay;
  else if (isDelay(r.after)) fields.after = null;
  if (r.fromTemplate) {
    for (const k of ["label", "stage"]) if ((fields[k] || "") === (r[k] || "")) delete fields[k];
    for (const k of ["inputs", "outputs"]) if (JSON.stringify(fields[k]) === JSON.stringify(r[k])) delete fields[k];
  }
  let next = updateStep(d.data, r.id, fields, d.template);
  if (f.depsTouched) next = setDependencies(next, id, f.deps, d.template);
  if (Boolean(v.skip) !== r.skip) next = setSkip(next, id, Boolean(v.skip), d.template);
  return { next, id };
}
