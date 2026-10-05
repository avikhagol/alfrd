// VIEW 2 — Workflow Canvas: stage-banded node graph, bezier dependency links,
// pan/zoom, mini-map, list view, step inspector, and scheduled runs.
//
// With `alfrd serve`, Run… starts a plan (commands from alfrd.yaml, run by a
// detached runner on the server; see plans.js) and the graph / list / Schedule
// views show its progress. Without a server the old DAG *simulation* remains:
// it only animates the graph in this browser and never executes anything.

import { $, $$, on, esc, icon, short, hms, elapsed, storage, loadUi, saveUi } from "../utils/dom.js";
import { STEP_STATUS } from "../data/model.js";
import { server } from "../data/server.js";
import { targetCodes } from "./attach.js";
import { logItem, logForTarget, projectLogs, zoomablePre } from "./logview.js";
import { scoped } from "../data/workspace.js";
import { RUN_STATUS, CELL, activePlan, cellMenu, countsLine, loadPlan, logButtons, planAct, planOf, planOverlay, plansAvailable, renderSchedule, usageButton } from "./plans.js";

const W = 1040;
const GAP = 32;
const NODE_H = 128;
const BANNER_H = 46;
const STAGE_GAP = 40;

const UI_FIELDS = ["zoom", "panX", "panY", "mode", "minimap", "selected", "tab", "inspector", "fitted", "layoutKey"];
// Per project: pan/zoom, selected step, inspector and an unsaved parameter draft.
// The step inspector starts folded (it opens when a step is picked); "inspector.v2" applies that once to saved states.
const foldOnce = !storage.get("ui:workflow.inspector.v2");
storage.set("ui:workflow.inspector.v2", true);
const ui = scoped("workflow", () => ({
  ...loadUi("workflow.v2", { zoom: 1, panX: 40, panY: 24, mode: "list", minimap: true, selected: null, tab: "params", inspector: false, fitted: false }),
  ...(foldOnce ? { inspector: false } : {}),
  draft: null,
  layout: {},
  layoutKey: null,
}));
const remember = () => saveUi("workflow.v2", ui, UI_FIELDS);

const planRequested = new Set(); // projects whose plan state was fetched once by the Workflow view

const sim = {
  active: false,
  paused: false,
  statuses: {},
  progress: {},
  notes: {},
  modeled: {},
  realMs: {},
  elapsed: 0,
  timer: null,
  last: 0,
  logs: [],
  targetId: null,
  edited: new Set(),
};

export function resetSimulation() {
  clearInterval(sim.timer);
  Object.assign(sim, { active: false, paused: false, statuses: {}, progress: {}, notes: {}, modeled: {}, realMs: {}, elapsed: 0, timer: null, logs: [], targetId: null });
}

function steps(ctx) {
  return ctx.state.workflow.steps;
}

function stepByKey(ctx, key) {
  return steps(ctx).find((s) => s.key === key);
}

function baseStatus(ctx, key) {
  const t = ctx.target();
  return t?.steps?.[key]?.status || "pending";
}


// The selected project only: a stale target from another project must never decide it.
function wfProject(ctx) {
  return ctx.activeProject ? ctx.activeProject() : ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : null;
}


function overlay(ctx) {
  const project = wfProject(ctx);
  return activePlan(project) ? planOverlay(project, ctx.target()) : null;
}

function statusOf(ctx, key) {
  if (sim.active && sim.statuses[key]) return sim.statuses[key];
  const o = overlay(ctx);
  const cell = o?.row?.cells?.[key];
  const mapped = cell && CELL[cell]?.step;
  if (mapped && !(cell === "done" && baseStatus(ctx, key) === "completed")) return mapped;
  return baseStatus(ctx, key);
}


function displayStatus(ctx, step) {
  const st = statusOf(ctx, step.key);
  if (st !== "pending") return st;
  const ready = step.depends.every((d) => statusOf(ctx, d) === "completed");
  return ready ? "pending" : "queued";
}


function computeLayout(ctx) {
  const wf = ctx.state.workflow;
  const key = `layout:${wf.name}:${wf.steps.map((s) => s.key).join(",")}`;
  if (ui.layoutKey !== key || !ui.layoutLoaded) {
    if (ui.layoutKey !== key) ui.fitted = false; // a different workflow: fit once
    ui.layoutKey = key;
    ui.layout = storage.get(key, {});
    ui.layoutLoaded = true;
  }
  const pos = {};
  const banners = [];
  let y = 0;
  wf.stages.forEach((stage, si) => {
    banners.push({ stage, index: si, x: 0, y });
    y += BANNER_H;
    const n = stage.steps.length;
    const perRow = Math.min(n, 3);
    const w = (W - (perRow - 1) * GAP) / perRow;
    stage.steps.forEach((k, i) => {
      const col = i % perRow;
      const row = Math.floor(i / perRow);
      const auto = { x: col * (w + GAP), y: y + row * (NODE_H + GAP), w };
      const saved = ui.layout[k];
      pos[k] = saved ? { ...auto, x: saved.x, y: saved.y } : auto;
    });
    y += Math.ceil(n / perRow) * (NODE_H + GAP) - GAP + STAGE_GAP;
  });
  const maxX = Math.max(W, ...Object.values(pos).map((p) => p.x + p.w));
  const maxY = Math.max(y, ...Object.values(pos).map((p) => p.y + NODE_H + 20));
  return { pos, banners, width: maxX, height: maxY };
}

function edgePath(a, b) {
  if (Math.abs(a.y - b.y) < NODE_H / 2) {
    const leftToRight = a.x < b.x;
    const x1 = leftToRight ? a.x + a.w : a.x;
    const x2 = leftToRight ? b.x : b.x + b.w;
    const y1 = a.y + NODE_H / 2;
    const y2 = b.y + NODE_H / 2;
    const dx = Math.max(24, Math.abs(x2 - x1) / 2) * (leftToRight ? 1 : -1);
    return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`;
  }
  const x1 = a.x + a.w / 2;
  const y1 = a.y + NODE_H;
  const x2 = b.x + b.w / 2;
  const y2 = b.y;
  const dy = Math.max(30, (y2 - y1) / 2);
  return `M${x1},${y1} C${x1},${y1 + dy} ${x2},${y2 - dy} ${x2},${y2 - 2}`;
}

function edgeClass(ctx, from, to) {
  const a = statusOf(ctx, from);
  const b = statusOf(ctx, to);
  if (a === "failed" || b === "failed") return "e-fail";
  if (b === "running") return "e-run";
  if (a === "completed" && (b === "completed" || b === "warning")) return "e-ok";
  if (a === "completed") return "e-ready";
  return "e-idle";
}


export function mount(el, ctx) {
  el.innerHTML = `<div class="wf">
    <div class="wf-toolbar" id="wf-toolbar"></div>
    <div class="wf-main">
      <div class="wf-stage">
        <div class="wf-info" id="wf-info"></div>
        <div class="canvas" id="wf-canvas" tabindex="0" aria-label="Workflow graph. Drag to pan, Ctrl+wheel to zoom.">
          <div class="world" id="wf-world"></div>
          <div class="minimap" id="minimap-panel"></div>
        </div>
        <div class="wf-list" id="wf-list" hidden></div>
        <div class="wf-sched" id="wf-sched" hidden></div>
      </div>
      <aside class="inspector" id="wf-inspector" aria-label="Step inspector"></aside>
    </div></div>`;

  const canvasEl = $("#wf-canvas", el);
  let drag = null;
  canvasEl.addEventListener("pointerdown", (e) => {
    const node = e.target.closest(".node");
    if (e.target.closest(".minimap, .setup-card") || e.button !== 0) return;
    if (node && !e.target.closest("button")) {
      const key = node.dataset.key;
      const p = ui.currentLayout.pos[key];
      drag = { kind: "node", key, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, moved: false };
    } else if (!node) {
      drag = { kind: "pan", sx: e.clientX, sy: e.clientY, ox: ui.panX, oy: ui.panY };
      canvasEl.classList.add("panning");
    }
    if (drag) canvasEl.setPointerCapture(e.pointerId);
  });
  canvasEl.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.sx;
    const dy = e.clientY - drag.sy;
    if (drag.kind === "pan") {
      ui.panX = drag.ox + dx;
      ui.panY = drag.oy + dy;
      applyTransform(el);
    } else {
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (!drag.moved) return;
      const p = ui.currentLayout.pos[drag.key];
      p.x = Math.round((drag.ox + dx / ui.zoom) / 8) * 8;
      p.y = Math.round((drag.oy + dy / ui.zoom) / 8) * 8;
      const n = $(`.node[data-key="${CSS.escape(drag.key)}"]`, el);
      if (n) { n.style.left = `${p.x}px`; n.style.top = `${p.y}px`; }
      drawEdges(el, ctx);
    }
  });
  const end = () => {
    if (drag?.kind === "node") {
      if (drag.moved) {
        const p = ui.currentLayout.pos[drag.key];
        ui.layout[drag.key] = { x: p.x, y: p.y };
        storage.set(ui.layoutKey, ui.layout);
        drawMinimap(el, ctx);
      } else select(ctx, drag.key);
    }
    drag = null;
    canvasEl.classList.remove("panning");
  };
  canvasEl.addEventListener("pointerup", end);
  canvasEl.addEventListener("pointercancel", end);
  canvasEl.addEventListener("wheel", (e) => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      const r = canvasEl.getBoundingClientRect();
      zoomAt(el, ui.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), e.clientX - r.left, e.clientY - r.top);
    } else {
      ui.panX -= e.deltaX;
      ui.panY -= e.deltaY;
      applyTransform(el);
    }
  }, { passive: false });
  canvasEl.addEventListener("keydown", (e) => {
    const order = steps(ctx).map((s) => s.key);
    const i = order.indexOf(ui.selected);
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { select(ctx, order[Math.min(order.length - 1, i + 1)]); e.preventDefault(); }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") { select(ctx, order[Math.max(0, i - 1)]); e.preventDefault(); }
    if (e.key === "+" || e.key === "=") zoomBy(el, 1.15);
    if (e.key === "-") zoomBy(el, 1 / 1.15);
  });

  on(el, "click", "[data-act]", (e, b) => act(el, ctx, b.dataset.act, b));
  const toSchedule = () => { ui.mode = "schedule"; remember(); ctx.update(); };
  on(el, "click", "[data-plan]", (e, b) => {
    if (b.dataset.plan === "schedule") { toSchedule(); return; }
    const only = b.dataset.only ? [b.dataset.only] : null;
    planAct(ctx, wfProject(ctx), b.dataset.plan, b, { only, onStarted: toSchedule });
  });
  on(el, "click", "td[data-cell]", (e, td) => cellMenu(ctx, wfProject(ctx), td));
  on(el, "change", "[data-plan-pick]", (e, sel) => loadPlan(ctx, wfProject(ctx), { id: sel.value }));
  on(el, "click", "[data-tab]", (e, b) => { ui.tab = b.dataset.tab; remember(); renderInspector(el, ctx); });
  on(el, "click", "[data-fold=inspector]", () => { ui.inspector = !ui.inspector; remember(); ctx.update(); requestAnimationFrame(() => drawViewport(el)); });
  on(el, "click", "#minimap-panel .mm-svg", (e, svg) => {
    const r = svg.getBoundingClientRect();
    const L = ui.currentLayout;
    const s = Math.min(r.width / L.width, r.height / L.height);
    const wx = (e.clientX - r.left) / s;
    const wy = (e.clientY - r.top) / s;
    const c = $("#wf-canvas", el).getBoundingClientRect();
    ui.panX = c.width / 2 - wx * ui.zoom;
    ui.panY = c.height / 2 - wy * ui.zoom;
    applyTransform(el);
  });
  on(el, "click", "tr[data-step]", (e, row) => select(ctx, row.dataset.step));
  on(el, "input", "[data-param]", (e, inp) => {
    ensureDraft(ctx);
    const group = inp.dataset.group;
    const key = inp.dataset.param;
    let v = inp.type === "checkbox" ? inp.checked : inp.value;
    if (inp.dataset.type === "Float" || inp.dataset.type === "Integer") v = v === "" ? "" : Number(v);
    if (inp.dataset.type === "List") { try { v = JSON.parse(v); } catch { /* keep text while typing */ } }
    ui.draft[group][key] = v;
    $("#wf-apply", el)?.removeAttribute("disabled");
  });
}

function ensureDraft(ctx) {
  const s = stepByKey(ctx, ui.selected);
  if (!ui.draft || ui.draft.key !== ui.selected) {
    ui.draft = { key: ui.selected, params: { ...(s?.params || {}) } };
  }
}


export function showStep(ctx, key) {
  select(ctx, key);
  ctx.goTo("workflow");
}

function select(ctx, key) {
  if (!key) return;
  ui.selected = key;
  ui.inspector = true;
  remember();
  ui.draft = null;
  ctx.update();
}

function zoomAt(el, z, cx, cy) {
  const nz = Math.min(2.5, Math.max(0.25, z));
  ui.panX = cx - ((cx - ui.panX) * nz) / ui.zoom;
  ui.panY = cy - ((cy - ui.panY) * nz) / ui.zoom;
  ui.zoom = nz;
  applyTransform(el);
}

function zoomBy(el, f) {
  const c = $("#wf-canvas", el).getBoundingClientRect();
  zoomAt(el, ui.zoom * f, c.width / 2, c.height / 2);
}

function fit(el) {
  const c = $("#wf-canvas", el);
  if (!c || !ui.currentLayout) return;
  const r = c.getBoundingClientRect();
  if (!r.width) return;
  const L = ui.currentLayout;
  const reserve = ui.minimap ? 118 : 40; // keep the mini-map from covering the last stage
  const z = Math.min(1.25, Math.max(0.3, Math.min((r.width - 48) / L.width, (r.height - reserve) / L.height)));
  ui.zoom = z;
  ui.panX = (r.width - L.width * z) / 2;
  ui.panY = 20;
  applyTransform(el);
}

function applyTransform(el) {
  remember();
  const world = $("#wf-world", el);
  if (world) world.style.transform = `translate(${ui.panX}px, ${ui.panY}px) scale(${ui.zoom})`;
  const z = $("#wf-zoom", el);
  if (z) z.textContent = `${Math.round(ui.zoom * 100)}%`;
  const fz = document.querySelector("#foot-zoom");
  if (fz) fz.textContent = `Zoom ${Math.round(ui.zoom * 100)}%`;
  drawViewport(el);
}


function act(el, ctx, name, button) {
  switch (name) {
    case "graph":
    case "list":
    case "schedule":
      ui.mode = name;
      remember();
      ctx.update();
      break;
    case "zoom-in": zoomBy(el, 1.2); break;
    case "zoom-out": zoomBy(el, 1 / 1.2); break;
    case "fit": fit(el); break;
    case "reset-layout":
      ui.layout = {};
      storage.remove(ui.layoutKey);
      ui.fitted = false;
      ctx.update();
      break;
    case "minimap":
      ui.minimap = !ui.minimap;
      remember();
      ctx.update();
      break;
    case "validate": validate(ctx); break;
    case "sim": toggleSim(ctx); break;
    case "sim-stop": stopSim(ctx); break;
    case "sim-step": simulateStep(ctx, ui.selected); break;
    case "apply": applyDraft(ctx); break;
    case "reset":
      ui.draft = null;
      renderInspector(el, ctx);
      break;
    case "more":
      ctx.menu(button, [
        { label: "Export workflow YAML", icon: "download", run: () => document.querySelector("#btn-export").click() },
        { label: "Clear simulation", icon: "reset", run: () => { resetSimulation(); ctx.update(); } },
        ...(plansAvailable(ctx, wfProject(ctx)) ? [{ label: "Simulate in the browser (nothing runs)", icon: "play", run: () => toggleSim(ctx) }] : []),
        { label: "Open log stream", icon: "log", run: () => ctx.renderConsole() },
      ]);
      break;
    case "fetch-logs": fetchRunLogs(ctx); break;
    default:
  }
}

function validate(ctx) {
  const wf = ctx.state.workflow;
  const errors = [];
  const warnings = [];
  const keys = new Set(wf.steps.map((s) => s.key));
  wf.steps.forEach((s) => {
    s.depends.forEach((d) => !keys.has(d) && errors.push(`${s.key}: unknown dependency ${d}`));
    Object.entries(s.params).forEach(([k, v]) => {
      if (typeof v === "number" && !Number.isFinite(v)) errors.push(`${s.key}.${k}: not a number`);
      if (/threshold|min_|max_|cores|bin/.test(k) && typeof v === "number" && v < 0) errors.push(`${s.key}.${k}: must be ≥ 0`);
    });
  });
  const indeg = Object.fromEntries(wf.steps.map((s) => [s.key, s.depends.filter((d) => keys.has(d)).length]));
  const queue = Object.keys(indeg).filter((k) => !indeg[k]);
  let seen = 0;
  while (queue.length) {
    const k = queue.shift();
    seen += 1;
    wf.steps.filter((s) => s.depends.includes(k)).forEach((s) => { indeg[s.key] -= 1; if (!indeg[s.key]) queue.push(s.key); });
  }
  if (seen !== wf.steps.length) errors.push("Dependency cycle detected.");
  ctx.state.workflowFile.validated = !errors.length;
  ctx.state.workflowFile.errors = errors;
  ctx.state.workflowFile.warnings = warnings;
  errors.forEach((e) => ctx.log("error", e, "validate"));
  warnings.forEach((w) => ctx.log("warn", w, "validate"));
  ctx.log("info", `Validation: ${errors.length} error(s), ${warnings.length} warning(s) across ${wf.steps.length} steps.`, "validate");
  ctx.toast(errors.length ? `${errors.length} validation error(s) — see log stream` : "Configuration valid", errors.length ? "fail" : "ok");
  ctx.update();
}

async function applyDraft(ctx) {
  if (!ui.draft) return;
  const s = stepByKey(ctx, ui.draft.key);
  if (!s) return;
  const diff = Object.fromEntries(Object.entries(ui.draft.params).filter(([k, v]) => JSON.stringify(s.params[k]) !== JSON.stringify(v)));
  if (!Object.keys(diff).length) { ui.draft = null; ctx.toast("No changes"); ctx.update(); return; }
  const project = wfProject(ctx);
  const avica = ctx.state.workflow.template === "avica" && project && ctx.state.avica?.[project];
  if (avica) {
    try {
      const where = await ctx.writeAvicaConfig(project, Object.fromEntries(Object.entries(diff).map(([k, v]) => [`${s.key}.${k}`, v])));
      ctx.toast(`${s.key}: ${Object.keys(diff).length} value(s) written to ${where}`, "ok");
    } catch (error) {
      ctx.toast(`avica.inp not written: ${error.message}`, "fail");
      return;
    }
  } else {
    ctx.state.workflowFile.modified = true;
    ctx.toast(`${s.key} updated — export the workflow YAML to keep it`, "ok");
  }
  s.params = { ...s.params, ...diff };
  s.paramSources = { ...(s.paramSources || {}), ...Object.fromEntries(Object.keys(diff).map((k) => [k, avica ? "avica.inp/studio" : "edited"])) };
  sim.edited.add(s.key);
  ctx.log("info", `${s.key}: ${Object.entries(diff).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")}.`, "workflow");
  ctx.persist();
  ui.draft = null;
  ctx.update();
}


function modeledSeconds(ctx, key) {
  const t = ctx.target();
  const s = t?.steps?.[key];
  if (Number.isFinite(s?.duration) && s.duration > 0) return s.duration;
  return 60;
}

function startSim(ctx, only = null) {
  const t = ctx.target();
  sim.targetId = t?.id || null;
  sim.active = true;
  sim.paused = false;
  sim.logs = [];
  sim.elapsed = 0;
  steps(ctx).forEach((s) => {
    const base = baseStatus(ctx, s.key);
    const keep = only ? s.key !== only : base === "completed";
    sim.statuses[s.key] = keep ? (only ? statusOf(ctx, s.key) : "completed") : "pending";
    sim.progress[s.key] = sim.statuses[s.key] === "completed" ? 1 : 0;
    sim.modeled[s.key] = modeledSeconds(ctx, s.key);
    sim.realMs[s.key] = Math.min(6000, Math.max(1500, sim.modeled[s.key] * 20));
  });
  sim.only = only;
  simLog(ctx, only ? `Simulating single step ${only}` : `Simulation started for ${t ? t.name : "workflow"} (resume from first incomplete step)`, only);
  sim.last = performance.now();
  clearInterval(sim.timer);
  sim.timer = setInterval(() => tick(ctx), 100);
  tick(ctx);
}

function toggleSim(ctx) {
  if (!sim.active || isFinished()) { startSim(ctx); return; }
  sim.paused = !sim.paused;
  sim.last = performance.now();
  simLog(ctx, sim.paused ? "Simulation paused" : "Simulation resumed");
  ctx.update();
}

function stopSim(ctx) {
  if (!sim.active) return;
  simLog(ctx, "Simulation stopped; graph restored to imported state");
  resetSimulation();
  ctx.update();
}

function simulateStep(ctx, key) {
  if (sim.active && !isFinished()) {
    ctx.toast("A simulation is already running", "warn");
    return;
  }
  startSim(ctx, key);
}

function isFinished() {
  return !Object.values(sim.statuses).some((s) => s === "running" || s === "pending");
}

function simLog(ctx, text, key = null, level = "info") {
  sim.logs.push({ t: new Date(), text, key, level });
  ctx.log(level, `[sim] ${text}`, "simulation");
}

function tick(ctx) {
  if (!sim.active || sim.paused) return;
  const now = performance.now();
  const dt = now - sim.last;
  sim.last = now;
  const wf = steps(ctx);
  let changed = false;
  wf.forEach((s) => {
    if (sim.statuses[s.key] !== "running") return;
    sim.progress[s.key] = Math.min(1, sim.progress[s.key] + dt / sim.realMs[s.key]);
    sim.elapsed += (dt / sim.realMs[s.key]) * sim.modeled[s.key];
    if (sim.progress[s.key] >= 1) {
      const t = ctx.target(sim.targetId);
      const imported = t?.steps?.[s.key];
      const replayFailure = imported && (imported.status === "failed") && !sim.edited.has(s.key);
      sim.statuses[s.key] = replayFailure ? "failed" : "completed";
      sim.notes[s.key] = replayFailure ? "Imported result failed and parameters are unchanged — simulated as failing again." : "Simulated completion (no CASA executed).";
      simLog(ctx, `${s.key} ${replayFailure ? "failed (replayed imported failure)" : "completed"} after ${short(sim.modeled[s.key])} modeled`, s.key, replayFailure ? "warn" : "info");
      changed = true;
    }
  });
  wf.forEach((s) => {
    if (sim.statuses[s.key] !== "pending") return;
    if (sim.only && s.key !== sim.only) return;
    const deps = s.depends.map((d) => sim.statuses[d] || "completed");
    if (!sim.only && deps.some((d) => d === "failed" || d === "skipped")) {
      sim.statuses[s.key] = "skipped";
      simLog(ctx, `${s.key} skipped — upstream failure`, s.key, "warn");
      changed = true;
    } else if (sim.only || deps.every((d) => d === "completed")) {
      sim.statuses[s.key] = "running";
      const cores = stepByKey(ctx, s.key)?.params?.mpi_cores;
      simLog(ctx, `${s.key} started${cores ? ` (mpi_cores=${cores})` : ""}`, s.key);
      changed = true;
    }
  });
  if (isFinished()) {
    clearInterval(sim.timer);
    sim.timer = null;
    const failed = Object.values(sim.statuses).filter((s) => s === "failed").length;
    simLog(ctx, `Simulation finished: ${failed ? `${failed} failed` : "all steps completed"} · modeled ${hms(sim.elapsed)}`, null, failed ? "warn" : "info");
    changed = true;
  }
  if (changed) ctx.update();
  else updateLive(ctx);
}

function updateLive(ctx) {
  const root = document.querySelector("#view-workflow");
  if (!root || root.hidden) return;
  $$(".node.st-running", root).forEach((n) => {
    const p = Math.round((sim.progress[n.dataset.key] || 0) * 100);
    const bar = $(".node-prog i", n);
    if (bar) bar.style.width = `${p}%`;
    const b = $(".badge span", n);
    if (b) b.textContent = `Simulating (${p}%)`;
  });
  const pct = overallPct(ctx);
  const btn = $("#wf-sim-label", root);
  if (btn) btn.textContent = sim.paused ? `Paused (${pct}%)` : `Simulating (${pct}%)`;
  const el = $("#wf-elapsed", root);
  if (el) el.textContent = elapsed(sim.elapsed);
  const ip = $("#insp-prog", root);
  if (ip && sim.statuses[ui.selected] === "running") {
    ip.textContent = `Simulating step… ${Math.round(sim.progress[ui.selected] * 100)}%`;
    const it = $("#insp-time", root);
    if (it) it.textContent = short(sim.modeled[ui.selected] * sim.progress[ui.selected]);
  }
}

function overallPct(ctx) {
  const wf = steps(ctx);
  const total = wf.reduce((n, s) => n + (sim.modeled[s.key] || 0), 0) || 1;
  const done = wf.reduce((n, s) => n + (sim.modeled[s.key] || 0) * (sim.statuses[s.key] === "skipped" ? 1 : sim.progress[s.key] || 0), 0);
  return Math.round((done / total) * 100);
}

async function fetchRunLogs(ctx) {
  const t = ctx.target();
  if (!t?.runId) return;
  try {
    const data = await server.runLogs(t.runId);
    const lines = (data.logs || data.entries || []).map((l) => (typeof l === "string" ? l : `${l.created_at || ""} ${l.level || ""} ${l.message || JSON.stringify(l)}`));
    t._runLogs = lines;
    ctx.update();
  } catch (error) {
    ctx.toast(`Logs unavailable: ${error.message}`, "fail");
  }
}

// ---------------------------------------------------------------------------
// Rendering

export function leave() {
  /* keep simulation running in the background */
}

export function showRun(ctx, project, id = null) {
  if (ctx.switchProject) ctx.switchProject(project, { restoreView: false });
  else ctx.state.selectedProject = project;
  if (ctx.target()?.project !== project) ctx.state.selectedTarget = null;
  if (id) loadPlan(ctx, project, { id }); // that run, not the newest
  ui.mode = "schedule"; remember(); ctx.navigate("workflow");
}

export function render(el, ctx) {
  if (sim.active && sim.targetId && sim.targetId !== ctx.state.selectedTarget) {
    resetSimulation();
  }
  if (!stepByKey(ctx, ui.selected)) ui.selected = steps(ctx).find((s) => ["failed", "running", "warning"].includes(statusOf(ctx, s.key)))?.key || steps(ctx)[0]?.key;
  const project = wfProject(ctx);
  const plans = plansAvailable(ctx, project);
  if (plans && planOf(project) === null && !planRequested.has(project)) { planRequested.add(project); loadPlan(ctx, project, { quiet: true }); }
  if (ui.mode === "schedule" && !plans) ui.mode = "graph";
  renderToolbar(el, ctx);
  renderInfo(el, ctx);
  const list = $("#wf-list", el);
  const canvasEl = $("#wf-canvas", el);
  const sched = $("#wf-sched", el);
  list.hidden = ui.mode !== "list";
  sched.hidden = ui.mode !== "schedule";
  // Each project remembers its own mode: never keep another project's rows in an inactive pane.
  if (list.hidden) list.innerHTML = "";
  if (sched.hidden) sched.innerHTML = "";
  el.querySelector(".wf")?.classList.toggle("list-mode", ui.mode !== "graph");
  el.querySelector(".wf")?.classList.toggle("sched-mode", ui.mode === "schedule");
  canvasEl.hidden = ui.mode !== "graph";
  if (!steps(ctx).length && ui.mode !== "schedule") {
    const box = ui.mode === "list" ? list : $("#wf-world", el);
    if (ui.mode === "graph") { box.style.transform = "none"; $("#minimap-panel", el).hidden = true; }
    box.innerHTML = `<div class="empty setup-card"><h2>Build your workflow</h2><p>A step is one piece of work, such as running a script or asking an agent.</p><p>Add your first step in <a class="btn primary" href="#/config">Settings · alfrd.yaml</a>, then return here to run it.</p></div>`;
  } else if (ui.mode === "list") renderList(el, ctx);
  else if (ui.mode === "schedule") renderSchedule(sched, ctx, project);
  else renderGraph(el, ctx);
  renderInspector(el, ctx);
  const ap = plans ? activePlan(project) : null;
  const right = sim.active
    ? `<span class="chip ${!isFinished() ? "tone-run" : ""}"><i class="dot"></i>${isFinished() ? "Simulation finished" : sim.paused ? "Simulation paused" : "Simulating"}</span>`
    : ap ? `<span class="chip tone-run"><i class="dot"></i>Run ${esc(ap.plan.start_at ? "scheduled" : RUN_STATUS[ap.plan.status] || ap.plan.status)}</span>` : plans ? '<span class="chip"><i class="dot"></i>Ready to run</span>' : '<span class="chip"><i class="dot"></i>Simulation Ready</span>';
  ctx.setFooterRight(`<span class="chip" id="foot-zoom">Zoom ${Math.round(ui.zoom * 100)}%</span> ${right}`);
}

function runGroup(ctx, project) {
  const ap = activePlan(project);
  const counts = {};
  (ap?.table?.rows || []).forEach((r) => Object.values(r.cells).forEach((v) => { if (v && v !== "skip") counts[v] = (counts[v] || 0) + 1; }));
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const canRun = Boolean(server.session?.mutations_enabled);
  return `<div class="sim-group ${ap ? "on" : ""}">
    <button class="btn primary" data-plan="new" ${canRun ? "" : "disabled title='Only from a browser on the same machine as alfrd serve'"} title="Run steps for targets with the commands in alfrd.yaml">${icon("play")} Run…</button>
    <button class="icon-btn" data-plan="dry" title="Dry run: list the commands a run would start, in order (nothing runs)" aria-label="Dry run">${icon("list")}</button>
    ${ctx.state.workflow.template === "agent-loop" || ctx.state.workflow.steps.some((s) => s.handoff) ? '<button class="btn sm" data-plan="task">Edit task</button><button class="btn sm" data-plan="agents">Agents &amp; review</button>' : ''}
    ${ap ? `<button class="btn sm" data-plan="schedule" title="Run ${esc(ap.plan.id)}">${icon(ap.plan.start_at ? "clock" : ap.plan.status === "running" ? "sync" : "pause", ap.plan.status === "running" && ap.runner?.alive && !ap.plan.start_at ? "spin" : "")} ${esc(ap.plan.start_at ? "Scheduled" : RUN_STATUS[ap.plan.status] || ap.plan.status)} ${counts.done || 0}/${total}</button>` : ""}
  </div>`;
}

function renderToolbar(el, ctx) {
  const project = wfProject(ctx);
  const plans = plansAvailable(ctx, project);
  const f = ctx.state.workflowFile;
  const wf = ctx.state.workflow;
  const running = sim.active && !isFinished();
  const pct = sim.active ? overallPct(ctx) : 0;
  $("#wf-toolbar", el).innerHTML = `
    <div class="cfg-pill" title="${esc([...(f.errors || []), ...(f.warnings || [])].join("\n") || f.name)}">${icon("file")}<span class="mono">${esc(f.name)}</span>
      <span class="badge tone-${f.validated ? "ok" : "fail"}">${f.validated ? "Validated" : `${f.errors?.length || 0} error(s)`}</span>${f.modified ? '<span class="badge tone-warn">edited</span>' : ""}</div>
    <span class="stat"><i class="dot ok"></i>${wf.steps.length} Steps / ${wf.stages.length} Stages</span>
    <div class="seg"><button data-act="graph" class="${ui.mode === "graph" ? "on" : ""}">${icon("graph")} Graph</button><button data-act="list" class="${ui.mode === "list" ? "on" : ""}">${icon("list")} List</button>${plans ? `<button data-act="schedule" class="${ui.mode === "schedule" ? "on" : ""}" title="Runs: targets × steps and run order">${icon("clock")} Runs</button>` : ""}</div>
    <button class="btn" data-act="validate">${icon("validate")} Validate configuration</button>
    ${plans && !sim.active ? runGroup(ctx, project) : `<div class="sim-group ${running ? "on" : ""}">`}${plans && !sim.active ? "" : `
      <button class="btn ${running ? "primary" : ""}" data-act="sim" title="Browser simulation only">${icon(running && !sim.paused ? "sync" : "play", running && !sim.paused ? "spin" : "")}<span id="wf-sim-label">${running ? (sim.paused ? `Paused (${pct}%)` : `Simulating (${pct}%)`) : sim.active ? "Re-run simulation" : "Simulate"}</span></button>
      <button class="icon-btn" data-act="sim" ${running ? "" : "disabled"} aria-label="${sim.paused ? "Resume" : "Pause"}" title="${sim.paused ? "Resume" : "Pause"}">${icon(sim.paused ? "play" : "pause")}</button>
      <button class="icon-btn" data-act="sim-stop" ${sim.active ? "" : "disabled"} aria-label="Stop simulation" title="Stop">${icon("stop")}</button>
    </div>`}
    <span class="grow"></span>
    <div class="zoom-group">
      <button class="icon-btn" data-act="zoom-out" aria-label="Zoom out">${icon("minus")}</button>
      <span id="wf-zoom" class="tabular">${Math.round(ui.zoom * 100)}%</span>
      <button class="icon-btn" data-act="zoom-in" aria-label="Zoom in">${icon("plus")}</button>
      <button class="icon-btn" data-act="fit" aria-label="Fit to view" title="Fit to view">${icon("fit")}</button>
      <button class="icon-btn" data-act="reset-layout" aria-label="Reset layout" title="Reset layout">${icon("reset")}</button>
      <button class="icon-btn ${ui.minimap ? "on" : ""}" data-act="minimap" aria-label="Toggle mini-map" title="Mini-map">${icon("map")}</button>
      <button class="icon-btn" data-act="more" aria-label="More">${icon("more")}</button>
    </div>`;
}

function renderInfo(el, ctx) {
  const t = ctx.target();
  const c = t?.columns || {};
  const paramSource = ctx.state.paramSource || "AVICA defaults";
  const facts = [c.RA && `RA: ${c.RA}`, c.DEC && `Dec: ${c.DEC}`, c.FREQ && `Freq: ${c.FREQ}`, c.BASELINES && `Baselines: ${c.BASELINES}`].filter(Boolean);
  $("#wf-info", el).innerHTML = `
    <span class="info-ic">${icon("target")}</span>
    <div class="grow"><b>${esc(t ? `${ctx.projectName(t.project)} / Target: ${t.name}` : "No target selected")}</b>${c.CORRELATOR ? ` <span class="chip">${esc(c.CORRELATOR)}</span>` : ""}
      <div class="muted small">${facts.length ? esc(facts.join(" | ")) : t ? `Status overlay from ${esc(t.source?.kind === "server" ? "ALFRD runtime" : t.source?.file || "import")}` : "Pick a target in the header to overlay its results."}</div></div>
    ${sim.active || !plansAvailable(ctx, wfProject(ctx)) ? `<div class="info-stat"><span class="muted small">Elapsed Sim Time</span><b id="wf-elapsed" class="tabular">${sim.active ? elapsed(sim.elapsed) : "—"}</b></div>` : ""}
    <div class="info-stat"><span class="muted small">Step parameters from</span><b class="small mono">${esc(paramSource)}</b></div>
    ${sim.active ? `<span class="badge tone-run" title="Nothing is executed in the browser">${icon("info")}Simulated</span>` : ""}`;
}

function nodeHtml(ctx, s, p) {
  const plan = overlay(ctx);
  const st = displayStatus(ctx, s);
  const meta = STEP_STATUS[st] || STEP_STATUS.pending;
  const t = ctx.target();
  const imported = t?.steps?.[s.key];
  const simulating = sim.active && sim.statuses[s.key] === "running";
  const pct = Math.round((sim.progress[s.key] || 0) * 100);
  const label = simulating ? `Simulating (${pct}%)` : st === "running" ? "Active" : meta.label;
  const dur = sim.active && sim.statuses[s.key] === "completed" ? sim.modeled[s.key] : imported?.duration;
  const params = Object.entries(s.params).slice(0, 3);
  const sel = ui.selected === s.key;
  return `<div class="node st-${st} ${sel ? "sel" : ""}" data-key="${esc(s.key)}" style="left:${p.x}px;top:${p.y}px;width:${p.w}px" role="button" tabindex="-1" aria-label="${esc(s.key)} ${esc(label)}">
    ${st === "running" ? `<div class="node-prog"><i style="width:${simulating ? pct : 60}%"></i></div>` : ""}
    <div class="node-h">
      <span class="node-ic">${icon(s.icon)}</span>
      <div class="node-t"><b class="mono">${esc(s.key)}</b>${sel ? '<span class="sel-tag">Active selection</span>' : ""}<span>${esc(s.label)}</span></div>
      <span class="badge tone-${meta.tone}">${icon(simulating ? "sync" : meta.icon, simulating ? "spin" : "")}<span>${esc(label)}</span></span>
    </div>
    <div class="node-b">
      <div class="kvs">${params.map(([k, v]) => `<span><em>${esc(k)}:</em><code>${esc(typeof v === "string" ? `"${v}"` : typeof v === "number" && Number.isInteger(v) && /thresh|snr|ratio/.test(k) ? v.toFixed(1) : v)}</code></span>`).join("")}</div>
      <span class="tabular dur">${Number.isFinite(dur) ? esc(short(dur)) : "--:--"}</span>
    </div>
    ${s.alias ? `<span class="alias-tag" title="Resolved from legacy ${esc(s.alias)}">${esc(s.alias)}→</span>` : ""}
    ${plan && !plan.row && plan.counts[s.key] && Object.keys(plan.counts[s.key]).length ? `<div class="node-plan" title="Run ${esc(plan.plan.id)}: cells over all targets">${countsLine(plan.counts[s.key])}</div>` : ""}
  </div>`;
}

function renderGraph(el, ctx) {
  const L = computeLayout(ctx);
  ui.currentLayout = L;
  const wf = ctx.state.workflow;
  const banners = L.banners.map(({ stage, index, y }) => {
    const sts = stage.steps.map((k) => statusOf(ctx, k));
    const n = (x) => sts.filter((s) => s === x).length;
    let summary = "Awaiting dependencies";
    let tone = "muted";
    if (n("failed")) { summary = `${n("failed")} Failed`; tone = "fail"; }
    else if (n("running")) { summary = `${n("running")} Running`; tone = "run"; }
    else if (n("completed") === sts.length) { summary = "All steps done"; tone = "ok"; }
    else if (n("completed") || n("warning")) { summary = `${n("completed")}/${sts.length} completed`; tone = "warn"; }
    return `<div class="banner" style="top:${y}px;width:${L.width}px"><i class="dot ${tone}"></i><b>Stage ${index + 1}: ${esc(stage.title)}</b><span class="badge tone-${tone}">${esc(summary)}</span>${n("running") && sim.active ? '<span class="grow"></span><span class="muted small">Simulation telemetry</span>' : ""}</div>`;
  }).join("");
  const nodes = wf.steps.map((s) => nodeHtml(ctx, s, L.pos[s.key])).join("");
  const world = $("#wf-world", el);
  world.style.width = `${L.width}px`;
  world.style.height = `${L.height}px`;
  world.innerHTML = `<svg class="edges" id="wf-edges" width="${L.width}" height="${L.height}" viewBox="0 0 ${L.width} ${L.height}" aria-hidden="true">
      <defs>${["ok", "fail", "run", "idle", "ready"].map((k) => `<marker id="ar-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ar ar-${k}"/></marker>`).join("")}</defs>
      <g id="wf-edge-g"></g></svg>${banners}${nodes}`;
  drawEdges(el, ctx);
  if (!ui.fitted) {
    requestAnimationFrame(() => { fit(el); ui.fitted = true; });
  } else applyTransform(el);
  const mm = $("#minimap-panel", el);
  mm.hidden = !ui.minimap;
  drawMinimap(el, ctx);
}

function drawEdges(el, ctx) {
  const g = $("#wf-edge-g", el);
  if (!g) return;
  const L = ui.currentLayout;
  g.innerHTML = ctx.state.workflow.steps.flatMap((s) => s.depends.filter((d) => L.pos[d]).map((d) => {
    const cls = edgeClass(ctx, d, s.key);
    const k = cls.slice(2);
    return `<path d="${edgePath(L.pos[d], L.pos[s.key])}" class="edge ${cls}" marker-end="url(#ar-${k})"><title>${esc(d)} → ${esc(s.key)}</title></path>`;
  })).join("");
}

function drawMinimap(el, ctx) {
  const mm = $("#minimap-panel", el);
  if (!mm || mm.hidden || !ui.currentLayout) return;
  const L = ui.currentLayout;
  const tone = { completed: "ok", failed: "fail", running: "run", warning: "warn" };
  mm.innerHTML = `<header><span>Canvas overview</span><button class="icon-btn xs" data-act="minimap" aria-label="Close mini-map">${icon("close")}</button></header>
    <svg class="mm-svg" viewBox="0 0 ${L.width} ${L.height}" preserveAspectRatio="xMidYMid meet">
      ${ctx.state.workflow.steps.map((s) => { const p = L.pos[s.key]; return `<rect x="${p.x}" y="${p.y}" width="${p.w}" height="${NODE_H}" rx="10" class="mm-n mm-${tone[statusOf(ctx, s.key)] || "idle"}"/>`; }).join("")}
      <rect id="mm-view" class="mm-view" x="0" y="0" width="10" height="10"/>
    </svg>`;
  drawViewport(el);
}

function drawViewport(el) {
  const v = $("#mm-view", el);
  const c = $("#wf-canvas", el);
  if (!v || !c) return;
  const r = c.getBoundingClientRect();
  v.setAttribute("x", String(-ui.panX / ui.zoom));
  v.setAttribute("y", String(-ui.panY / ui.zoom));
  v.setAttribute("width", String(r.width / ui.zoom));
  v.setAttribute("height", String(r.height / ui.zoom));
}

function renderList(el, ctx) {
  const t = ctx.target();
  const plan = overlay(ctx);
  $("#wf-list", el).innerHTML = `<table class="tbl list-tbl"><thead><tr><th>#</th><th>Step</th><th>Stage</th><th>Category</th><th>Depends on</th><th>Parameters</th><th>Status</th><th>Runtime</th></tr></thead><tbody>
    ${ctx.state.workflow.steps.map((s, i) => {
      const st = displayStatus(ctx, s);
      const stage = ctx.state.workflow.stages.find((x) => x.id === s.stage);
      const pc = plan && !plan.row ? plan.counts[s.key] : null;
      return `<tr data-step="${esc(s.key)}" class="st-${st} ${ui.selected === s.key ? "sel" : ""}"><td>${i + 1}</td><td class="mono"><b>${esc(s.key)}</b>${s.alias ? ` <small class="muted">(${esc(s.alias)})</small>` : ""}<div class="muted small">${esc(s.label)}</div></td><td>${esc(stage?.title || s.stage)}</td><td>${esc(s.category)}</td><td class="mono small">${esc(s.depends.join(", ") || "—")}</td><td class="mono small">${esc(Object.entries(s.params).map(([k, v]) => `${k}=${v}`).join(" "))}</td><td><span class="badge tone-${STEP_STATUS[st]?.tone}">${icon(STEP_STATUS[st]?.icon)}${esc(STEP_STATUS[st]?.label)}</span>${pc ? `<div class="node-plan">${countsLine(pc)}</div>` : ""}</td><td class="tabular">${esc(short(sim.active && sim.statuses[s.key] === "completed" ? sim.modeled[s.key] : t?.steps?.[s.key]?.duration) || "—")}</td></tr>`;
    }).join("")}</tbody></table>`;
}

function typeOf(key, v) {
  if (typeof v === "boolean") return "Boolean";
  if (typeof v === "number") return Number.isInteger(v) && !/thresh|ratio|snr|sigma|freq/.test(key) ? "Integer" : "Float";
  if (/runtime|time$|timeout/.test(key)) return "Duration";
  return "String";
}

function field(group, key, value, source) {
  const type = Array.isArray(value) ? "List" : typeOf(key, value);
  const doc = source ? `source: ${source}` : "";
  if (type === "List") value = JSON.stringify(value);
  if (type === "Boolean") {
    return `<div class="pf pf-bool"><div><label class="mono b" for="pf-${group}-${esc(key)}">${esc(key)}</label>${doc ? `<p class="muted small">${esc(doc)}</p>` : ""}</div>
      <label class="switch"><input type="checkbox" id="pf-${group}-${esc(key)}" data-group="${group}" data-param="${esc(key)}" ${value ? "checked" : ""}><i></i></label></div>`;
  }
  return `<div class="pf"><div class="row between"><label class="mono b" for="pf-${group}-${esc(key)}">${esc(key)}</label><span class="muted small">${type}</span></div>
    <input class="input ${type === "String" || type === "Duration" ? "" : "tabular"}" id="pf-${group}-${esc(key)}" data-group="${group}" data-param="${esc(key)}" data-type="${type}" ${type === "Float" || type === "Integer" ? `type="number" step="${type === "Float" ? "0.1" : "1"}"` : 'type="text"'} value="${esc(type === "Float" && Number.isInteger(value) ? value.toFixed(1) : value)}">
    ${doc ? `<p class="muted small">${esc(doc)}</p>` : ""}</div>`;
}

function renderInspector(el, ctx) {
  const box = $("#wf-inspector", el);
  el.querySelector(".wf-main").classList.toggle("insp-folded", !ui.inspector);
  if (!ui.inspector) {
    box.innerHTML = `<button class="fold-bar" data-fold="inspector" title="Show step inspector" aria-label="Show step inspector" aria-expanded="false">${icon("unfold")}<span>Inspector · ${esc(ui.selected || "")}</span></button>`;
    return;
  }
  const s = stepByKey(ctx, ui.selected);
  if (!s) { box.innerHTML = `<div class="empty">Select a step to see details.</div>`; return; }
  const src = ui.draft && ui.draft.key === s.key ? ui.draft : { params: s.params };
  const st = displayStatus(ctx, s);
  const meta = STEP_STATUS[st] || STEP_STATUS.pending;
  const t = ctx.target();
  const imported = t?.steps?.[s.key];
  const simulating = sim.active && sim.statuses[s.key] === "running";
  const progressLine = simulating
    ? `<div class="insp-prog run"><i class="dot run"></i><span id="insp-prog">Simulating step… ${Math.round(sim.progress[s.key] * 100)}%</span><span class="grow"></span><span class="tabular" id="insp-time">${short(sim.modeled[s.key] * sim.progress[s.key])}</span></div>`
    : `<div class="insp-prog tone-${meta.tone}">${icon(meta.icon)}<span>${esc(meta.label)}${imported?.attempts?.length > 1 ? ` · ${imported.attempts.length} attempts` : ""}</span><span class="grow"></span><span class="tabular">${esc(short(imported?.duration) || "")}</span></div>`;
  const stepLogs = [
    ...(imported?.attempts || []).map((a) => `${a.started || ""} attempt ${a.attempt}: ${STEP_STATUS[a.status]?.label || a.status}${a.note ? ` — ${a.note}` : ""}`),
    ...(imported && !imported.attempts?.length && imported.note ? [imported.note] : []),
    ...sim.logs.filter((l) => l.key === s.key).map((l) => `${l.t.toISOString().slice(11, 19)} [sim] ${l.text}`),
    ...(t?._runLogs || []),
  ];
  const artifacts = (t?.artifacts || []).filter((a) => a.step === s.key);
  const project = wfProject(ctx);
  const codes = t ? targetCodes(ctx, t).map((c) => c.code) : [];
  const files = projectLogs(ctx, project).filter((f) => (f.steps || []).includes(s.key) && logForTarget(ctx, f, t, codes));
  const nLogs = stepLogs.length + files.length;
  const avica = ctx.state.workflow.template === "avica";
  const paramFrom = ctx.state.paramSource || "alfrd.yaml";
  const po = overlay(ctx);
  const cell = po?.row?.cells?.[s.key];
  const unit = cell ? (planOf(project)?.units || []).slice().reverse().find((u) => (u.rows || [u.row]).includes(po.row.key) && (u.steps || []).includes(s.key)) : null;
  const planLine = cell && cell !== "skip"
    ? `<div class="insp-plan small"><span class="badge tone-${CELL[cell]?.tone || "muted"}">${icon(CELL[cell]?.icon || "info", cell === "running" ? "spin" : "")}Run: ${esc(CELL[cell]?.label || cell)}</span>${unit?.log ? ` ${logButtons(project, unit.log, { label: "command log" })}` : ""}${unit ? ` ${usageButton(project, unit)}` : ""}${unit?.argv ? `<code class="trunc" title="${esc(unit.argv.join(" "))}">${esc(unit.argv.join(" "))}</code>` : ""}${unit?.error ? `<span class="muted">${esc(unit.error)}</span>` : ""}</div>`
    : "";
  box.innerHTML = `
    <header class="insp-h"><span class="node-ic">${icon(s.icon)}</span><div class="grow"><div class="row gap"><b class="mono">${esc(s.key)}</b><span class="chip caps">${esc(s.category)}</span></div><div>${esc(s.label)}</div></div><button class="icon-btn sm" data-fold="inspector" title="Fold inspector" aria-label="Fold inspector" aria-expanded="true">${icon("fold")}</button></header>
    ${progressLine}${planLine}
    <nav class="tabs" role="tablist">
      <button role="tab" data-tab="params" class="${ui.tab === "params" ? "on" : ""}">Parameters</button>
      <button role="tab" data-tab="bindings" class="${ui.tab === "bindings" ? "on" : ""}">Bindings (${s.inputs.length + s.outputs.length})</button>
      <button role="tab" data-tab="logs" class="${ui.tab === "logs" ? "on" : ""}">Logs${nLogs ? ' <i class="dot run"></i>' : ""}</button>
    </nav>
    <div class="insp-b">
      ${ui.tab === "params" ? `
        ${s.description ? `<p class="muted small">${esc(s.description)}</p>` : ""}
        <p class="small muted">${avica ? `Parameters of <code>${esc(s.key)}</code> from <b class="mono">${esc(paramFrom)}</b> (<code>alfrd avica summary</code>). <b>Apply</b> writes <code>${esc(s.key)}.&lt;param&gt; = value</code> to avica.inp${ctx.canWrite(project) ? "" : " (downloaded: no writable folder)"}.` : "Parameters from the step's <code>params:</code> in alfrd.yaml."}</p>
        ${Object.entries(src.params).map(([k, v]) => field("params", k, v, s.paramSources?.[k])).join("") || `<p class="muted small">${avica ? "No step parameters in the summary. Run <code>alfrd avica summary</code> next to alfrd.yaml, then Re-scan." : "No parameters declared for this step."}</p>`}
        <div class="binding-box"><span class="caps small muted">Input source data</span>${s.inputs.slice(0, 2).map((b) => `<div class="row between mono small"><span>${esc(String(b).split("→")[0].trim())} →</span><span>${esc(String(b).split("→")[1]?.trim() || b)}</span></div>`).join("") || '<div class="muted small">none</div>'}</div>
      ` : ""}
      ${ui.tab === "bindings" ? `
        <h5>Depends on</h5><ul class="bind">${s.depends.map((d) => `<li class="mono">${esc(d)} <span class="badge tone-${STEP_STATUS[statusOf(ctx, d)]?.tone}">${esc(STEP_STATUS[statusOf(ctx, d)]?.label)}</span></li>`).join("") || '<li class="muted">No upstream steps</li>'}</ul>
        <h5>Inputs</h5><ul class="bind">${s.inputs.map((b) => `<li class="mono small">${esc(b)}</li>`).join("") || '<li class="muted">—</li>'}</ul>
        <h5>Outputs</h5><ul class="bind">${s.outputs.map((b) => `<li class="mono small">${esc(b)}</li>`).join("") || '<li class="muted">—</li>'}</ul>
        ${artifacts.length ? `<h5>Imported artifacts</h5><ul class="bind">${artifacts.map((a) => `<li class="mono small">${esc(a.band)}: ${esc(a.path)}</li>`).join("")}</ul>` : ""}
        ${s.command ? `<h5>Command</h5><pre class="code">${esc(Array.isArray(s.command) ? s.command.join(" ") : s.command)}</pre>` : ""}
      ` : ""}
      ${ui.tab === "logs" ? `
        ${t?.runId ? `<button class="btn sm" data-act="fetch-logs">${icon("sync")} Fetch runtime logs</button>` : ""}
        <h5>Log files <span class="muted small">(${files.length}${t ? ` for ${esc(t.name)}` : ""})</span></h5>
        ${files.length ? `<div class="insp-logs">${files.slice(0, 60).map((f, i) => logItem(f, { project, open: i === 0 })).join("")}</div>${files.length > 60 ? `<p class="muted small">+${files.length - 60} more in the Logs view.</p>` : ""}`
          : `<p class="muted small">No files match this step's <code>logs:</code> in alfrd.yaml${s.logs?.length ? ` (<code>${esc(s.logs.join("</code>, <code>"))}</code>)` : ""}.</p>`}
        <h5>Attempts</h5>
        ${zoomablePre(stepLogs.join("\n"), { title: `${s.key} attempts`, empty: '<span class="muted">No attempts recorded for this step.</span>' })}
        <a class="link-btn small" href="#/logs">All logs →</a>
      ` : ""}
    </div>
    <footer class="insp-f">
      <div class="row gap"><button class="btn primary grow" data-act="apply" id="wf-apply" ${ui.draft && ui.draft.key === s.key ? "" : "disabled"}>Apply changes</button><button class="btn" data-act="reset">Reset</button></div>
      ${plansAvailable(ctx, project)
        ? `<button class="btn block" data-plan="new" data-only="${esc(s.key)}" ${server.session?.mutations_enabled ? "" : "disabled"} title="Run ${esc(s.key)} for ${t ? esc(t.name) : "targets"} with the command from alfrd.yaml">${icon("play")} Run step…</button>`
        : `<button class="btn block" data-act="sim-step" title="Browser simulation only; nothing is executed">${icon("play")} Simulate step</button>`}
    </footer>`;
}
