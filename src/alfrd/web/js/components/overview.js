// VIEW 1 — Project Overview: grouped spreadsheet grid + target detail drawer.

import { $, on, esc, icon, hms, short, when, copyText, loadUi, saveUi, keepScroll, download } from "../utils/dom.js";
import { parseYaml } from "../utils/yaml_parser.js";
import { notesAt, noteMark } from "../data/notes.js";
import { avicaIndex, codeChips, detachCode, openAttachPicker, targetCodes } from "./attach.js";
import { STEP_STATUS, OVERALL_STATUS, targetText } from "../data/model.js";
import { server } from "../data/server.js";
import { openRunDialog, plansAvailable, planOf, loadPlan, RUN_STATUS } from "./plans.js";
import { PRESETS, NO_CODE, FILTER_DEFAULTS, filterTargets, activeFilters } from "../data/filters.js";
import { scoped } from "../data/workspace.js";

// Remembered in this browser until Settings → "Reset view state".
const UI_FIELDS = ["search", "project", "status", "code", "preset", "mode", "collapsed", "hidden", "drawer", "groupBy"];
const saved = loadUi("overview", {
  search: "", project: "all", status: "all", code: "all", preset: null, mode: "details", collapsed: [], hidden: [], drawer: true, groupBy: "project",
});
// Per project workspace (All projects has its own): filters, page, checked rows, drawer.
const ui = scoped("overview", () => ({
  ...saved,
  collapsed: new Set(saved.collapsed),
  hidden: new Set(saved.hidden),
  page: 0,
  checked: new Set(),
}));
const remember = () => saveUi("overview", ui, UI_FIELDS);

const LEADING = [
  { id: "project", label: "ALFRD project" },
  { id: "codes", label: "Project code" },
  { id: "ms", label: "MS Storage Path" },
  { id: "status", label: "Status" },
  { id: "stages", label: "Progress" },
  { id: "runtime", label: "Runtime" },
];

const STATUSES = ["completed", "failed", "warning", "running", "unknown"];
const PRESET_LABELS = { nometa: "No work folder" };

function helpers(ctx) {
  return {
    rollup: (t) => ctx.rollup(t),
    codes: (t) => targetCodes(ctx, t).map((c) => c.code),
    text: (t) => targetText({ ...t, notes: ctx.state.notes[t.id] }),
  };
}


export function showCode(ctx, code) {
  ui.code = code;
  ui.page = 0;
  remember();
  ctx.goTo("overview");
  ctx.update();
}


// Agent-loop projects, from explicit metadata (as in Results): the agent-loop template,
// or a repeat workflow with handoff steps. Their Overview is run history, not targets.
const loopSeen = new Map(); // project -> [manifest text, is loop]
export function isLoopProject(ctx, project) {
  const tree = ctx.state.trees?.[project];
  if (!tree) return false;
  if (tree.defs?.template === "agent-loop") return true;
  const text = tree.manifestText || "";
  if (loopSeen.get(project)?.[0] !== text) {
    let loop = false;
    try { loop = Object.values(parseYaml(text)?.workflows || []).some((w) => w?.repeat && (w.repeat.sequence || Object.values(w.steps || {}).some((s) => s?.handoff))); } catch { /* not YAML */ }
    loopSeen.set(project, [text, loop]);
  }
  return loopSeen.get(project)[1];
}
const scopeIds = (ctx) => (ctx.state.selectedProject && ctx.state.selectedProject !== "all" ? [ctx.state.selectedProject] : ctx.projects().map((p) => p.id));
/** Every project in scope is an agent loop: no target fields, filters, drawer or Latest run card. */
export function loopOnly(ctx) {
  const ids = scopeIds(ctx);
  return ids.length > 0 && ids.every((p) => isLoopProject(ctx, p));
}
/** Scoped targets without agent loops: a loop's plan CSV task row is a run input, not a target. */
const targetsIn = (ctx) => ctx.scopedTargets().filter((t) => !isLoopProject(ctx, t.project));
const runsOf = (project) => (planOf(project) || runLoads.get(project)?.status)?.plans || [];

// Run history export: every known loop run in scope, read when clicked (a frozen snapshot).
let exporting = false;
export function historyItems(ctx) {
  const projects = scopeIds(ctx).filter((p) => isLoopProject(ctx, p));
  if (!projects.length) return [];
  const none = !projects.some((p) => runsOf(p).length);
  return ["csv", "json"].map((ext) => ({
    label: `Export run history · ${ext.toUpperCase()}`, icon: "download", disabled: none || exporting,
    hint: none ? "No run history to export." : "Loop runs among the newest 20 plans per project. Timestamps without a recorded timezone are omitted.", run:() => exportHistory(ctx, projects, ext),
  }));
}
async function exportHistory(ctx, projects, ext) {
  if (exporting) return;
  exporting = true;
  ctx.update();
  try {
    const h = await import("../data/run_history.js");
    const runs = await h.collectHistory(server, projects.map((id) => ({ id, name: ctx.projectName(id) })));
    if (!runs.length) throw new Error("No run history to export.");
    const scope = ctx.state.selectedProject && ctx.state.selectedProject !== "all" ? ctx.projectName(projects[0]) : "all";
    download(h.historyFilename(scope, ext), ext === "csv" ? h.historyCsv(runs) : h.historyJson(runs, projects), ext === "csv" ? "text/csv" : "application/json");
    ctx.toast(`Exported ${runs.length} run${runs.length === 1 ? "" : "s"}.`, "ok");
  } catch (error) {
    ctx.toast(`Couldn’t export run history. ${error.message}`, "fail");
  } finally {
    exporting = false;
    ctx.update();
  }
}

export function visibleTargets(ctx) {
  return filterTargets(targetsIn(ctx), ui, helpers(ctx));
}

function badge(status, extra = "") {
  const s = OVERALL_STATUS[status] || OVERALL_STATUS.unknown;
  return `<span class="badge tone-${s.tone}">${icon(s.icon)}${esc(extra || s.label)}</span>`;
}

function stepCell(t, step, mode) {
  const key = step.key;
  const s = t.steps?.[key];
  const status = s?.status || "pending";
  const meta = STEP_STATUS[status] || STEP_STATUS.pending;
  const tries = s?.attempts?.length || 0;
  const tip = [step.label || key, meta.label, s?.duration != null ? hms(s.duration) : null, tries > 1 ? `${tries} attempts` : null, s?.note].filter(Boolean).join(" · ");
  let body = "";
  if (mode === "summary") {
    body = status === "pending" ? `<span class="dot-empty"></span>` : icon(meta.icon);
  } else if (status === "completed") {
    body = `${icon("check")}${esc(short(s.duration))}`;
  } else if (status === "failed") {
    body = `${icon("xCircle")}<span class="tag">${esc(tagFor(s) || "failed")}</span>`;
  } else if (status === "warning") {
    body = `<span class="tag">${esc(tagFor(s) || "partial")}</span>`;
  } else if (status === "running") {
    body = `${icon("sync", "spin")}${esc(short(s.duration) || "running")}`;
  } else if (status === "queued") {
    body = `${icon("hourglass")}queued`;
  } else {
    body = `<span class="dash">—</span>`;
  }
  if (mode !== "summary" && tries > 1) body += `<sup title="${tries} attempts">×${tries}</sup>`;
  return `<td class="stage-cell st-${status}" title="${esc(tip)}">${body}</td>`;
}

function tagFor(s) {
  const n = String(s?.note || "");
  if (!n) return "";
  const first = n.split(/[.;·(]/)[0].trim();
  return first.length > 18 ? `${first.slice(0, 16)}…` : first;
}

function progress(r, status) {
  const pct = r.total ? Math.round((r.done / r.total) * 100) : 0;
  return `<div class="prog" title="${r.done} of ${r.total} steps completed"><b>${r.done}/${r.total}</b><span class="bar tone-${OVERALL_STATUS[status]?.tone || "muted"}"><i style="width:${pct}%"></i></span></div>`;
}

function counters(ctx, targets) {
  const c = { completed: 0, failed: 0, warning: 0, running: 0, unknown: 0 };
  targets.forEach((t) => { c[ctx.rollup(t).status] += 1; });
  return c;
}

export function mount(el, ctx) {
  el.innerHTML = `<div class="ov"><div class="card ov-head" id="ov-head"></div><div id="ov-home"></div><div class="ov-body"><div class="card grid-card" id="ov-grid"></div><aside class="card drawer" id="ov-drawer" aria-label="Target details"></aside></div></div>`;

  // Typing only refreshes what depends on the filter; the head keeps its input (and focus).
  on(el, "input", "#ov-search", (e) => { ui.search = e.target.value; ui.page = 0; remember(); renderGrid(el, ctx); renderActive(el, ctx); });
  on(el, "change", "#ov-project", (e) => { ui.project = e.target.value; ui.page = 0; remember(); ctx.update(); });
  on(el, "change", "#ov-code", (e) => { ui.code = e.target.value; ui.page = 0; remember(); ctx.update(); });
  on(el, "click", "[data-group-by]", (e, b) => { ui.groupBy = b.dataset.groupBy; ui.page = 0; remember(); ctx.update(); });
  on(el, "click", "[data-preset]", (e, b) => { ui.preset = ui.preset === b.dataset.preset ? null : b.dataset.preset; ui.page = 0; remember(); ctx.update(); });
  on(el, "click", "[data-mode]", (e, b) => { ui.mode = b.dataset.mode; remember(); ctx.update(); });
  on(el, "click", "[data-counter]", (e, b) => { const s = b.dataset.counter; ui.status = ui.status === s ? "all" : s; ui.page = 0; remember(); ctx.update(); });
  on(el, "click", "[data-unfilter]", (e, b) => {
    const keys = b.dataset.unfilter === "*" ? Object.keys(FILTER_DEFAULTS) : [b.dataset.unfilter];
    keys.forEach((k) => { ui[k] = FILTER_DEFAULTS[k]; });
    ui.page = 0; remember(); ctx.update();
  });
  on(el, "click", "[data-attach]", (e) => { e.stopPropagation(); const t = ctx.target(); if (t) openAttachPicker(ctx, t); });
  on(el, "click", "[data-detach]", (e, b) => { e.stopPropagation(); const t = ctx.target(); if (t) detachCode(ctx, t, b.dataset.detach); });
  on(el, "click", "[data-fold=drawer]", () => { ui.drawer = !ui.drawer; remember(); render(el, ctx); });
  on(el, "click", "#dr-metadata", () => ctx.navigate("metadata"));
  on(el, "click", "[data-crash]", async (e, b) => { e.preventDefault(); ctx.openLog(ctx.target()?.project, b.dataset.crash); });
  on(el, "click", "#ov-import", () => ctx.openImport());
  on(el, "click", "#ov-targets", (e, b) => ctx.menu(b, [
    { label: "Import targets CSV…", icon: "upload", hint: "source, FITS files", run: () => ctx.openTargets("import") },
    { label: "Download targets CSV", icon: "download", run: () => ctx.openTargets("download") },
    "-",
    { label: "Import results…", icon: "upload", run: () => ctx.openImport() },
  ]));
  on(el, "click", "#ov-export", (e, b) => ctx.menu(b, [
    ...(loopOnly(ctx) ? [] : [{ label: "Overview CSV (visible rows)", icon: "download", run: () => document.querySelector("#btn-export").click() }]),
    ...historyItems(ctx),
  ]));
  // Run history rows act on that exact run, never the newest by default.
  on(el, "click", "[data-rh-open]", (e, b) => { e.stopPropagation(); ctx.openRun(b.dataset.rhOpen, b.dataset.rhRun); });
  on(el, "click", "[data-rh-results]", async (e, b) => {
    e.stopPropagation();
    try { (await import("./loop_results.js")).selectRun(ctx, b.dataset.rhResults, b.dataset.rhRun); } catch (error) { ctx.toast(error.message, "fail"); return; }
    ctx.switchProject?.(b.dataset.rhResults, { restoreView: false });
    ctx.navigate("results");
  });
  on(el, "click", "[data-rh-task]", async (e, b) => {
    try { await (await import("./agent_settings_dialog.js")).openTask(ctx, b.dataset.rhTask); } catch (error) { ctx.toast(error.message, "fail"); }
  });
  on(el, "click", "[data-rh-workflow]", (e, b) => { ctx.switchProject?.(b.dataset.rhWorkflow, { restoreView: false }); ctx.navigate("workflow"); });
  on(el, "click", "[data-rh-page]", (e, b) => { histPage.set(b.dataset.rhPage, (histPage.get(b.dataset.rhPage) || 0) + Number(b.dataset.step)); renderGrid(el, ctx); });
  on(el, "click", "#ov-columns", (e, b) => {
    const steps = ctx.state.workflow.steps;
    ctx.menu(b, [
      ...LEADING.map((c) => ({ label: `${ui.hidden.has(c.id) ? "Show" : "Hide"} ${c.label}`, icon: ui.hidden.has(c.id) ? "plus" : "minus", run: () => { toggle(ui.hidden, c.id); remember(); ctx.update(); } })),
      "-",
      { label: ui.hidden.has("stages-all") ? "Show pipeline stage columns" : "Hide pipeline stage columns", icon: "columns", hint: `${steps.length} steps`, run: () => { toggle(ui.hidden, "stages-all"); remember(); ctx.update(); } },
      { label: "Reset columns", icon: "reset", run: () => { ui.hidden.clear(); remember(); ctx.update(); } },
    ]);
  });
  on(el, "click", "[data-group]", (e, b) => { toggle(ui.collapsed, b.dataset.group); remember(); renderGrid(el, ctx); });
  on(el, "click", "tr[data-target]", (e, row) => {
    if (e.target.closest("input,button,a")) return;
    ctx.update((s) => { s.selectedTarget = row.dataset.target; });
  });
  on(el, "change", "input[data-check]", (e, cb) => { cb.checked ? ui.checked.add(cb.dataset.check) : ui.checked.delete(cb.dataset.check); renderGrid(el, ctx); });
  on(el, "change", "#ov-check-all", (e) => {
    const page = pageTargets(ctx).rows;
    page.forEach((t) => (e.target.checked ? ui.checked.add(t.id) : ui.checked.delete(t.id)));
    renderGrid(el, ctx);
  });
  on(el, "click", "[data-page]", (e, b) => { ui.page += Number(b.dataset.page); renderGrid(el, ctx); });
  on(el, "click", "[data-copy]", async (e, b) => {
    e.stopPropagation();
    ctx.toast((await copyText(b.dataset.copy)) ? "Path copied" : "Copy failed", "ok");
  });
  on(el, "click", "#dr-open-workflow", () => ctx.navigate("workflow", { target: ctx.state.selectedTarget }));
  on(el, "input", "#dr-notes", (e) => ctx.setNote(ctx.state.selectedTarget, e.target.value));
  on(el, "click", "#dr-retry", () => retryStep(ctx));
  on(el, "click", "[data-home-targets]", () => ctx.openTargets("import"));
  on(el, "click", "[data-home-run]", (e, b) => openRunDialog(ctx, b.dataset.homeRun, { onStarted: () => ctx.openRun(b.dataset.homeRun) }));
  on(el, "click", "[data-home-open]", (e, b) => ctx.openRun(b.dataset.homeOpen));
  on(el, "click", "#dr-logs", () => ctx.openLogs({ target: ctx.state.selectedTarget }));
  on(el, "click", "#dr-bulk-run", () => {
    const picked = checkedTargets(ctx);
    openRunDialog(ctx, picked[0].project, { targets: picked.map((t) => t.name), onStarted: () => ctx.openRun(picked[0].project) });
  });
  on(el, "click", "#dr-bulk-remove", async () => {
    const picked = checkedTargets(ctx);
    const result = await ctx.openTargets("remove", picked[0].project, picked.map((t) => t.name));
    if (!result?.removed?.length) return;
    ui.checked.clear();
    ctx.toast(`${result.removed.length} row(s) removed from the targets file`, "ok");
  });
  on(el, "click", "#ov-clear-check", () => { ui.checked.clear(); renderGrid(el, ctx); });
}

function toggle(set, v) {
  set.has(v) ? set.delete(v) : set.add(v);
}


/** Actions for the checked targets: run them (plan, server) and remove them from the targets file. */
function selectionActions(ctx) {
  const picked = checkedTargets(ctx);
  if (!picked.length) return "";
  if (!oneProject(picked)) return '<span class="muted small">(pick targets of one project to run or remove them)</span>';
  const project = picked[0].project;
  const out = [];
  if (plansAvailable(ctx, project)) out.push(`<button class="link-btn" id="dr-bulk-run" title="New run from the checked targets">${icon("play")} Run…</button>`);
  if (targetsFileOf(ctx, project) || ctx.canWrite(project)) {
    out.push(`<button class="link-btn danger" id="dr-bulk-remove" title="Delete their rows from the targets file (result CSVs and work dirs stay)">${icon("trash")} Remove from targets file</button>`);
  }
  return out.join(" · ");
}

/** Checked targets, all from one project (Run / Remove act on one project's files). */
function checkedTargets(ctx) {
  return [...ui.checked].map((id) => ctx.target(id)).filter(Boolean);
}

function oneProject(targets) {
  return new Set(targets.map((t) => t.project)).size === 1;
}


function targetsFileOf(ctx, project) {
  return ctx.state.trees?.[project]?.targetsFile || null;
}


async function retryStep(ctx, id = ctx.state.selectedTarget) {
  const t = ctx.target(id);
  if (!t?.runId) return;
  const key = document.querySelector("#dr-step")?.value;
  if (!key) return;
  try {
    await server.retryStep(t.runId, key);
    ctx.log("info", `Runtime retry requested for ${t.name} → ${key} (run ${t.runId}).`, "runtime");
    ctx.toast(`Retry of ${key} submitted to the ALFRD runtime`, "ok");
    t.steps[key] = { ...(t.steps[key] || {}), status: "queued" };
    ctx.update();
  } catch (error) {
    ctx.log("error", `Runtime retry failed: ${error.message}`, "runtime");
    ctx.toast(error.message, "fail");
  }
}

function pageTargets(ctx) {
  const all = visibleTargets(ctx);
  const size = ctx.state.prefs.pageSize || 25;
  const pages = Math.max(1, Math.ceil(all.length / size));
  ui.page = Math.min(Math.max(0, ui.page), pages - 1);
  return { all, rows: all.slice(ui.page * size, ui.page * size + size), pages, size };
}

export function render(el, ctx) {
  const loop = loopOnly(ctx);
  renderHome(el, ctx);
  renderHead(el, ctx);
  renderGrid(el, ctx);
  renderDrawer(el, ctx);
  // Agent loops have no target details: run history takes the whole width.
  el.querySelector(".ov-body").classList.toggle("drawer-folded", !ui.drawer && !loop);
  el.querySelector(".ov-body").classList.toggle("no-drawer", loop);
}

const homeAsked = new Set();
export function renderHome(el, ctx) {
  // Setup/run summary only for the selected project; All projects never borrows one.
  const project = ctx.activeProject ? ctx.activeProject() : ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : null;
  const box = $("#ov-home", el);
  if (!project) { box.innerHTML = ""; return; }
  if (plansAvailable(ctx, project) && !homeAsked.has(project)) { homeAsked.add(project); loadPlan(ctx, project, { quiet: true }); }
  if (isLoopProject(ctx, project)) { box.innerHTML = ""; return; } // its run history shows setup, latest and active runs
  const hasSteps = ctx.state.workflow.steps.length > 0, hasTargets = ctx.state.targets.some((t) => t.project === project);
  const run = planOf(project)?.plan;
  box.innerHTML = `${!hasSteps || !hasTargets || !run ? `<section class="card setup-card"><h2>Get started</h2><p>Set up ${esc(ctx.projectName(project))}, then run your workflow.</p><ol class="setup-list">
    <li>${hasSteps ? "✓" : "①"} <a href="#/workflow">Add steps in Workflow</a> · <a href="#/config">Edit alfrd.yaml in Settings</a></li>
    <li>${hasTargets ? "✓" : "②"} <button class="link-btn" data-home-targets>Add targets</button></li>
    <li>${run ? "✓" : "③"} <button class="link-btn" data-home-run="${esc(project)}" ${hasSteps && hasTargets ? "" : "disabled"}>Start a run</button></li></ol></section>` : ""}
    ${run ? `<section class="card setup-card"><div class="row gap wrap"><h2>Latest run</h2><span class="mono">Run ${esc(run.id)}</span><span>${esc(RUN_STATUS[run.status] || run.status)}</span><span class="grow"></span><button class="btn" data-home-open="${esc(project)}">Open run</button><a class="btn primary" href="#/results">View results</a></div></section>` : ""}`;
}

function renderHead(el, ctx) {
  const head = $("#ov-head", el);
  // The search box is built once so live refreshes never take its focus or caret.
  if (!$("#ov-search", head)) {
    head.innerHTML = `
      <div class="row gap wrap ov-title" id="ov-title"></div>
      <div class="ov-toolbar">
        <div class="ov-group" role="group" aria-labelledby="ov-l-status"><span class="ov-label" id="ov-l-status">Status</span><div class="chipbar" id="ov-status-bar"></div></div>
        <div class="ov-row">
          <div class="ov-group grow" role="group" aria-labelledby="ov-l-find"><span class="ov-label" id="ov-l-find">Find</span>
            <label class="search grow">${icon("search")}<input id="ov-search" type="search" placeholder="Search targets, files, codes, notes…" aria-label="Search"></label>
            <span class="row gap" id="ov-find-rest"></span></div>
          <div class="ov-group" role="group" aria-labelledby="ov-l-view" id="ov-view"></div>
        </div>
        <div class="ov-active" id="ov-active" hidden></div>
      </div>`;
    $("#ov-search", head).value = ui.search;
  } else if (document.activeElement !== $("#ov-search", head)) {
    $("#ov-search", head).value = ui.search;
  }
  renderTitle(el, ctx);
  renderStatusBar(el, ctx);
  renderFindView(el, ctx);
  renderActive(el, ctx);
  head.querySelector(".ov-toolbar").hidden = !targetsIn(ctx).length;
}

function renderTitle(el, ctx) {
  const projects = ctx.projects();
  const scoped = targetsIn(ctx);
  const src = ctx.state.source;
  const srcLabel = src === "demo" ? "Demo Data" : src === "server" ? "Runtime" : src === "imported" ? "Imported" : "No data";
  const loop = loopOnly(ctx);
  const runs = loop ? scopeIds(ctx).reduce((n, p) => n + runsOf(p).length, 0) : 0;
  $("#ov-title", el).innerHTML = `
      <h2>Project Overview</h2>
      <span class="chip">${esc(srcLabel)} · ${loop ? `${runs} run${runs === 1 ? "" : "s"}` : `${projects.length} project${projects.length === 1 ? "" : "s"} · ${scoped.length} target${scoped.length === 1 ? "" : "s"}`}</span>
      <span class="grow"></span>
      ${loop ? "" : `<button class="btn" id="ov-targets" aria-haspopup="menu">${icon("upload")} Targets ${icon("caret")}</button>`}
      <button class="btn" id="ov-export" aria-haspopup="menu" ${exporting ? "disabled" : ""}>${icon("download")} ${exporting ? "Preparing history…" : `Export ${icon("caret")}`}</button>`;
}

function renderStatusBar(el, ctx) {
  const scoped = targetsIn(ctx);
  const c = counters(ctx, scoped);
  const h = helpers(ctx);
  const nNoMeta = scoped.filter((t) => !h.codes(t).length).length;
  const chip = (s, n, tone) => `<button class="count ${tone ? `tone-${tone}` : ""} ${ui.status === s ? "on" : ""}" data-counter="${s}" aria-pressed="${ui.status === s}">${tone ? "<i></i>" : ""}${n} ${esc(s === "all" ? "All" : OVERALL_STATUS[s].label)}</button>`;
  $("#ov-status-bar", el).innerHTML = `
    ${chip("all", scoped.length, "")}
    ${chip("completed", c.completed, "ok")}${chip("failed", c.failed, "fail")}${chip("warning", c.warning, "warn")}
    ${c.running || ui.status === "running" ? chip("running", c.running, "run") : ""}${chip("unknown", c.unknown, "")}
    <span class="vsep"></span>
    <button class="pill ${ui.preset === "nometa" ? "on" : ""}" data-preset="nometa" aria-pressed="${ui.preset === "nometa"}">${PRESET_LABELS.nometa} ${nNoMeta}</button>`;
}

function allCodes(ctx) {
  const h = helpers(ctx);
  const seen = new Map();
  let none = 0;
  targetsIn(ctx).forEach((t) => {
    const list = h.codes(t);
    if (!list.length) none += 1;
    list.forEach((c) => seen.set(c, (seen.get(c) || 0) + 1));
  });
  return { codes: [...seen.entries()].sort((a, b) => a[0].localeCompare(b[0])), none };
}

function renderFindView(el, ctx) {
  const projects = ctx.projects();
  if (ui.project !== "all" && !projects.some((p) => p.id === ui.project)) ui.project = "all";
  const { codes, none } = allCodes(ctx);
  if (ui.code !== "all" && ui.code !== NO_CODE && !codes.some(([c]) => c === ui.code)) ui.code = "all";
  const visibleCols = LEADING.filter((c) => !ui.hidden.has(c.id)).length + 1 + (ui.hidden.has("stages-all") ? 0 : ctx.steps().length);
  const totalCols = LEADING.length + 1 + ctx.steps().length;
  $("#ov-find-rest", el).innerHTML = `
    <label class="select-wrap" title="AVICA project code">${icon("folder")}<select id="ov-code" aria-label="Project code filter">
      <option value="all">All codes (${codes.length})</option>
      ${codes.map(([c, n]) => `<option value="${esc(c)}" ${ui.code === c ? "selected" : ""}>${esc(c)} (${n})</option>`).join("")}
      ${none ? `<option value="${NO_CODE}" ${ui.code === NO_CODE ? "selected" : ""}>No code (${none})</option>` : ""}
    </select></label>
    ${projects.length > 1 ? `<label class="select-wrap" title="ALFRD project">${icon("database")}<select id="ov-project" aria-label="ALFRD project filter"><option value="all">All projects (${projects.length})</option>${projects.map((p) => `<option value="${esc(p.id)}" ${ui.project === p.id ? "selected" : ""}>${esc(p.title || p.name)}</option>`).join("")}</select></label>` : ""}`;
  $("#ov-view", el).innerHTML = `<span class="ov-label" id="ov-l-view">View</span>
    <div class="seg" role="group" aria-label="Group rows by"><button data-group-by="project" class="${ui.groupBy === "project" ? "on" : ""}" title="Group rows by ALFRD project">Project</button><button data-group-by="code" class="${ui.groupBy === "code" ? "on" : ""}" title="Group rows by AVICA project code">Code</button></div>
    <div class="seg" role="group" aria-label="Cell detail"><button data-mode="summary" class="${ui.mode === "summary" ? "on" : ""}">Summary</button><button data-mode="details" class="${ui.mode === "details" ? "on" : ""}">Details</button></div>
    <button class="btn" id="ov-columns">${icon("columns")} Columns ${visibleCols}/${totalCols}</button>`;
}

function renderActive(el, ctx) {
  const box = $("#ov-active", el);
  const labels = {
    status: Object.fromEntries(STATUSES.map((s) => [s, OVERALL_STATUS[s].label])),
    preset: PRESET_LABELS,
    project: Object.fromEntries(ctx.projects().map((p) => [p.id, p.title || p.name])),
  };
  const list = activeFilters(ui, labels);
  box.hidden = !list.length;
  if (!list.length) { box.innerHTML = ""; return; }
  const shown = visibleTargets(ctx).length;
  box.innerHTML = `${icon("filter")}
    ${list.map((f) => `<span class="chip">${esc(f.label)}<button class="icon-btn xs" data-unfilter="${f.key}" aria-label="Remove filter ${esc(f.label)}">${icon("close")}</button></span>`).join("")}
    <span class="muted small tabular">showing ${shown} of ${targetsIn(ctx).length}</span>
    <button class="link-btn small" data-unfilter="*">Clear all</button>`;
}

// Polls redraw the grid: keep each viewport's sideways/vertical offset and the focused action.
function renderGrid(el, ctx) {
  keepScroll($("#ov-grid", el), () => drawGrid(el, ctx));
}

function drawGrid(el, ctx) {
  const steps = ctx.state.workflow.steps;
  const showStages = !ui.hidden.has("stages-all");
  const cols = LEADING.filter((c) => !ui.hidden.has(c.id));
  const { all, rows, pages } = pageTargets(ctx);
  const groups = new Map();
  const groupKey = (t) => (ui.groupBy === "code" ? targetCodes(ctx, t)[0]?.code || "(no project code)" : t.project);
  rows.forEach((t) => {
    const k = groupKey(t);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(t);
  });
  const totalCols = 2 + cols.length + (showStages ? steps.length : 0);
  const allChecked = rows.length && rows.every((t) => ui.checked.has(t.id));
  const projectTitle = (p) => ctx.projects().find((x) => x.id === p)?.title || "";

  const body = Array.from(groups.entries()).map(([project, list]) => {
    const everyone = targetsIn(ctx).filter((t) => groupKey(t) === project);
    const c = counters(ctx, everyone);
    const parts = [c.completed && `${c.completed} Completed`, c.failed && `${c.failed} Failed`, c.warning && `${c.warning} Warning`, c.running && `${c.running} Running`, c.unknown && `${c.unknown} Unknown`].filter(Boolean).join(", ");
    const doneAll = everyone.reduce((n, t) => n + ctx.rollup(t).done, 0);
    const pct = everyone.length ? Math.round((doneAll / (everyone.length * Math.max(1, steps.length))) * 100) : 0;
    const collapsed = ui.collapsed.has(project);
    const head = `<tr class="grp"><td colspan="${totalCols}"><div class="grp-in">
        <button class="icon-btn sm ${collapsed ? "" : "open"}" data-group="${esc(project)}" aria-expanded="${!collapsed}" aria-label="Toggle ${esc(ui.groupBy === "code" ? project : ctx.projectName(project))}">${icon("caret")}</button>
        ${ui.groupBy === "code" ? `<span class="proj-chip" style="--h:${hue(project)}">${esc(project)}</span>` : `<span class="alfrd-chip">${esc(projectTitle(project) || ctx.projectName(project))}</span>`}
        <b>${everyone.length} Target${everyone.length === 1 ? "" : "s"}</b>
        <span class="muted">(${esc(parts)})</span>
        <span class="mini-bar" title="${pct}% of steps complete"><i style="width:${pct}%"></i></span><span class="muted small tabular">${pct}%</span>
      </div></td></tr>`;
    if (collapsed) return head;
    return head + list.map((t) => {
      const r = ctx.rollup(t);
      const sel = t.id === ctx.state.selectedTarget;
      const statusLabel = r.status === "running" ? `Stage ${(r.running?.index ?? 0) + 1}` : null;
      return `<tr data-target="${esc(t.id)}" class="${sel ? "sel" : ""}" tabindex="0" aria-selected="${sel}">
        <td class="fz fz0"><input type="checkbox" data-check="${esc(t.id)}" ${ui.checked.has(t.id) ? "checked" : ""} aria-label="Select ${esc(t.name)}"></td>
        <td class="fz fz1 tname">${sel ? '<i class="sel-dot"></i>' : ""}${esc(t.name)}${noteMark(t.project, { target: t.name }, notesAt(ctx, t.project, { target: t.name }).length)}</td>
        ${cols.map((c) => {
          if (c.id === "project") return `<td><span class="alfrd-chip">${esc(ctx.projectName(t.project))}</span></td>`;
          if (c.id === "codes") return `<td class="codes">${codeChips(ctx, t)}</td>`;
          if (c.id === "ms") return `<td class="mono path">${t.msPath ? `<span class="trunc" title="${esc((t.msPaths?.length ? t.msPaths : [t.msPath]).join("\n"))}">${esc(t.msPath)}</span>${t.msPaths?.length > 1 ? `<span class="muted small">+${t.msPaths.length - 1}</span>` : ""}<button class="icon-btn xs" data-copy="${esc(t.msPath)}" aria-label="Copy path">${icon("copy")}</button>` : '<span class="muted">—</span>'}</td>`;
          if (c.id === "status") return `<td>${badge(r.status, statusLabel)}</td>`;
          if (c.id === "stages") return `<td>${progress(r, r.status)}</td>`;
          if (c.id === "runtime") return `<td class="tabular">${hms(r.runtime)}</td>`;
          return "";
        }).join("")}
        ${showStages ? steps.map((s) => stepCell(t, s, ui.mode)).join("") : ""}
      </tr>`;
    }).join("");
  }).join("");

  const empty = !all.length
    ? `<tr><td colspan="${totalCols}" class="empty">${ctx.projects().length ? "No targets yet. Add targets to this project, or clear the filters." : `No project loaded. ${ctx.state.mode === "server" ? "Start <code>alfrd serve</code> in the folder that holds alfrd.yaml, or " : ""}<button class="link-btn" id="ov-import">open the project folder</button>.`}</td></tr>`
    : "";

  $("#ov-grid", el).innerHTML = `
    <div class="grid-scroll" role="region" aria-label="Targets by workflow steps" tabindex="0" data-scroll-key="ov-targets">
    <table class="grid ${ui.mode}">
      <thead>
        <tr class="h1"><th class="fz fz0" colspan="2"></th><th colspan="${cols.length}" class="h1l">Target details and progress</th>${showStages ? `<th colspan="${steps.length}" class="h1s">Workflow steps · ${esc(ctx.state.workflow.name)}</th>` : ""}</tr>
        <tr class="h2">
          <th class="fz fz0"><input type="checkbox" id="ov-check-all" ${allChecked ? "checked" : ""} aria-label="Select page"></th>
          <th class="fz fz1">Target Name</th>
          ${cols.map((c) => `<th>${c.label}</th>`).join("")}
          ${showStages ? steps.map((s, i) => `<th class="sh" title="${esc(s.label)}${s.alias ? ` (field alias of ${esc(s.alias)})` : ""}">${i + 1}. ${esc(s.short || s.key)}</th>`).join("") : ""}
        </tr>
      </thead>
      <tbody>${body}${empty}</tbody>
    </table></div>
    <div class="grid-foot">
      <span>${ui.checked.size ? `Selected: <b>${ui.checked.size}</b> target${ui.checked.size === 1 ? "" : "s"} ${selectionActions(ctx)} · <button class="link-btn" id="ov-clear-check">clear</button>` : `Selected: <b>${esc(ctx.target()?.name || "none")}</b>`}</span>
      <span class="vsep"></span>
      <span class="muted">Showing ${rows.length} of ${all.length} targets across ${groups.size} active group${groups.size === 1 ? "" : "s"}</span>
      <span class="grow"></span>
      <button class="btn sm" data-page="-1" ${ui.page === 0 ? "disabled" : ""}>Previous</button>
      <span class="tabular small">Page ${ui.page + 1} of ${pages}</span>
      <button class="btn sm" data-page="1" ${ui.page >= pages - 1 ? "disabled" : ""}>Next</button>
    </div>`;
  ctx.setFooterRight(loopOnly(ctx) ? `Run history · ${ctx.projects().length} projects | Runtime`
    : `Showing ${all.length} of ${ctx.state.targets.length} targets across ${ctx.projects().length} projects | ${ctx.state.source === "demo" ? "Demo Data" : ctx.state.source === "server" ? "Runtime" : "Imported"}`);
  renderRunGrids(el, ctx);
}


// ---- Run × step grids for projects without targets (agent loops, unknown types). ----
// The grid module (js/data/run_grid.js) is loaded on first use; AVICA target grids never need it.
let runGridMod = null;
let historyMod = null; // data/run_history.js: run summaries for agent-loop history rows
let runGridLoading = null;
const histPage = new Map(); // project -> history page
const summaries = new Map(); // `${project}|${run}` -> {status, run, loading?}: re-read when the run's status changes
let summaryQueue = Promise.resolve(); // one run read at a time
let runGridFocus = null; // {project, pos}: the run search box keeps focus across re-renders
const runFilters = new Map(); // project -> {search, status, run}
const runLoads = new Map(); // project -> {state: "loading"|"ok"|"error", status?, error?}
const runLabel = (s) => RUN_STATUS[s] || s;

export function forgetProject(project) {
  runLoads.delete(project); runFilters.delete(project); homeAsked.delete(project); histPage.delete(project); taskLoads.delete(project);
  [...summaries.keys()].filter((k) => k.startsWith(`${project}|`)).forEach((k) => summaries.delete(k));
  if (runGridFocus?.project === project) runGridFocus = null;
}

/** Scoped agent loops and projects with no targets: these get run grids instead of "No targets yet". */
function targetlessProjects(ctx) {
  const sel = ctx.state.selectedProject;
  const ids = sel && sel !== "all" ? [sel] : ctx.projects().map((p) => p.id);
  return ids.filter((p) => isLoopProject(ctx, p) || !(ctx.state.targets || []).some((t) => t.project === p));
}

function renderRunGrids(el, ctx) {
  const projects = targetlessProjects(ctx);
  if (!projects.length) return;
  bindRunGrids(el, ctx);
  const box = $("#ov-grid", el);
  const only = !targetsIn(ctx).length;
  if (!runGridMod) {
    runGridLoading ||= Promise.all([import("../data/run_grid.js"), import("../data/run_history.js"), import("../utils/dom.js")]).then(([m, h, dom]) => {
      dom.loadCss?.("css/lazy.css");
      historyMod = h;
      runGridMod = m;
      renderGrid(el, ctx);
    }).catch((error) => { runGridLoading = null; ctx.log?.("warn", `Run grid: ${error.message}`, "overview"); });
    if (only) box.innerHTML = `<div class="empty">Loading runs…</div>`;
    return;
  }
  const html = `<div class="rg-wrap" data-scroll-key="rg-wrap">${projects.map((project, index) => runSection(ctx, project, index)).join("")}</div>`;
  if (only) box.innerHTML = html;
  else box.insertAdjacentHTML("beforeend", html);
  if (runGridFocus && (!document.activeElement || document.activeElement === document.body)) {
    const input = [...box.querySelectorAll("[data-rg-search]")].find((x) => x.dataset.rgSearch === runGridFocus.project);
    if (input) { input.focus(); input.setSelectionRange?.(runGridFocus.pos, runGridFocus.pos); }
  }
}

function runStatusOf(ctx, project) {
  if (!plansAvailable(ctx, project)) return { reason: "offline" };
  const load = runLoads.get(project);
  const selected = runFilters.get(project)?.run;
  const cached = selected && selected !== "all" ? load?.status : planOf(project) || load?.status;
  if (cached) {
    const revision = JSON.stringify([cached.plan?.id, cached.loop, cached.units]);
    if (!load || (load.state === "ok" && load.revision !== revision)) fetchRuns(ctx, project, cached);
    if (load?.state === "error") return { reason: "error", error: load.error };
    return { status: cached, handoffs: load?.status?.plan?.id === cached.plan?.id ? load?.handoffs : [] };
  }
  if (load?.state === "error") return { reason: "error", error: load.error };
  if (!load) fetchRuns(ctx, project);
  return { reason: "loading" };
}

async function fetchRuns(ctx, project, status = null) {
  const load = { state: "loading", status };
  runLoads.set(project, load);
  const selected = runFilters.get(project)?.run;
  try {
    Object.assign(load, await runGridMod.loadRunStatus(server, project, selected === "all" ? null : selected, status));
    load.revision = JSON.stringify([load.status.plan?.id, load.status.loop, load.status.units]);
    load.state = "ok";
  } catch (error) { load.state = "error"; load.error = error.message; }
  if (runLoads.get(project) === load) ctx.update();
}

function stepLabel(ctx, project, base, id) {
  const defs = ctx.state.trees?.[project]?.defs?.steps || {};
  const wf = ctx.state.workflowProject === project && (ctx.state.workflow?.steps || []).find((s) => s.key === id || s.key === base);
  return defs[id]?.label || defs[base]?.label || wf?.label || base;
}

function runGridOf(ctx, project) {
  const got = runStatusOf(ctx, project);
  const grid = got.status
    ? runGridMod.buildRunGrid(got.status, { handoffs: got.handoffs, label: (base, id) => stepLabel(ctx, project, base, id) })
    : { kind: "generic", columns: [], groups: [], empty: got.reason };
  return { grid, error: got.error, got };
}

/** A history row's summary: the run in the step grid from its live data, others read once per status. */
function summaryOf(ctx, project, got, ref) {
  const load = runLoads.get(project);
  if (got.status?.plan?.id === ref.id && load?.state === "ok" && load.status?.plan?.id === ref.id) return historyMod.runSummary(project, ctx.projectName(project), got.status, load.handoffs || []);
  const key = `${project}|${ref.id}`;
  const known = summaries.get(key);
  if (known?.status === ref.status) return known.run;
  if (!known?.loading) {
    summaries.set(key, { status: ref.status, run: known?.run || null, loading: true });
    summaryQueue = summaryQueue.then(() => historyMod.loadRunSummary(server, project, ctx.projectName(project), ref.id))
      .then((run) => { summaries.set(key, { status: ref.status, run }); ctx.update(); }, () => summaries.set(key, { status: ref.status, run: null }));
  }
  return known?.run || null;
}

// Tasks of an agent-loop project (own turn count and worktree); a click shows that task's runs.
const taskLoads = new Map(); // project -> {at, tasks, max_iterations, iteration_unit}
function taskStrip(ctx, project) {
  const got = taskLoads.get(project);
  if (!got || (!got.loading && Date.now() - got.at > 30000)) {
    taskLoads.set(project, { ...(got || {}), loading: true, at: Date.now() });
    server.tasks(project).then((r) => { taskLoads.set(project, { ...r, at: Date.now() }); ctx.update(); },
      () => taskLoads.set(project, { tasks: [], at: Date.now() }));
  }
  return runGridMod.renderTaskStrip(project, ctx.projectName(project), got, (runFilters.get(project)?.search || "").trim(), runLabel);
}

function runSection(ctx, project, index) {
  const { grid, error, got } = runGridOf(ctx, project);
  const f = runFilters.get(project) || runGridMod.FILTERS_DEFAULT;
  if (isLoopProject(ctx, project)) {
    const runs = runsOf(project);
    return taskStrip(ctx, project) + runGridMod.renderRunHistory(project, ctx.projectName(project), grid, grid, runs, (id) => summaryOf(ctx, project, got, runs.find((r) => r.id === id)), f,
      { runLabel, error, index, page: histPage.get(project), size: ctx.state.prefs.pageSize || 25, showProject: ctx.state.selectedProject === "all" });
  }
  const shown = runGridMod.filterRunGrid(grid, f, runLabel);
  return runGridMod.renderRunGrid(project, ctx.projectName(project), grid, shown, f, { runLabel, error, index });
}

function bindRunGrids(el, ctx) {
  if (el.dataset.rgBound) return;
  el.dataset.rgBound = "1";
  const set = (project, patch) => {
    runFilters.set(project, { ...(runFilters.get(project) || runGridMod.FILTERS_DEFAULT), ...patch });
    renderGrid(el, ctx);
  };
  on(el, "input", "[data-rg-search]", (e, input) => {
    runGridFocus = { project: input.dataset.rgSearch, pos: input.selectionStart };
    set(input.dataset.rgSearch, { search: input.value });
  });
  on(el, "focusout", "[data-rg-search]", (e) => { if (e.relatedTarget) runGridFocus = null; });
  on(el, "change", "[data-rg-status]", (e, s) => set(s.dataset.rgStatus, { status: s.value }));
  on(el, "change", "[data-rg-run]", (e, s) => {
    runFilters.set(s.dataset.rgRun, { ...(runFilters.get(s.dataset.rgRun) || runGridMod.FILTERS_DEFAULT), run: s.value });
    fetchRuns(ctx, s.dataset.rgRun); renderGrid(el, ctx);
  });
  on(el, "click", "[data-rg-reset]", (e, b) => { runFilters.delete(b.dataset.rgReset); runLoads.delete(b.dataset.rgReset); runGridFocus = null; renderGrid(el, ctx); });
  on(el, "click", "[data-rg-retry]", (e, b) => { runLoads.delete(b.dataset.rgRetry); fetchRuns(ctx, b.dataset.rgRetry); });
  on(el, "click", "[data-rg-cell]", (e, b) => openRunCell(ctx, b.dataset));
  on(el, "click", "[data-rg-task]", (e, b) => {
    const project = b.dataset.rgTask, current = (runFilters.get(project)?.search || "").trim();
    set(project, { search: current === b.dataset.task ? "" : b.dataset.task, run: "all" });
  });
}

async function openRunCell(ctx, d) {
  const project = d.rgCell;
  const found = runGridMod.findCell(runGridOf(ctx, project).grid, d.rgRunId, d.rgRow, d.rgStep);
  if (!found) return;
  const { logButtons } = await import("./plans.js");
  const log = found.cell.log ? logButtons(project, found.cell.log, { label: "View log" }) : "";
  ctx.modal(`<header class="modal-h"><h2>${esc(found.column.label)} · ${esc(found.row.label)}</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b">${runGridMod.cellDetail(ctx.projectName(project), found, log)}</div>`);
}

function hue(text) {
  let h = 0;
  for (const c of String(text)) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

function renderDrawer(el, ctx) {
  const d = $("#ov-drawer", el);
  const t = ctx.target();
  if (!ui.drawer) {
    d.innerHTML = `<button class="fold-bar" data-fold="drawer" title="Show target details" aria-label="Show target details" aria-expanded="false">${icon("unfold")}<span>Details${t ? ` · ${esc(t.name)}` : ""}</span></button>`;
    return;
  }
  if (!t) {
    d.innerHTML = `<div class="empty">Select a target row to see its files, pipeline steps and triage notes.</div>`;
    return;
  }
  const r = ctx.rollup(t);
  const steps = ctx.state.workflow.steps;
  const issue = r.failed || r.warning;
  const issueNote = issue?.state?.note;
  const sub = [t.role, r.status === "failed" ? `Failed at step ${r.failed.index + 1}` : r.status === "running" ? `Running step ${r.running.index + 1}` : OVERALL_STATUS[r.status].label].filter(Boolean).join(" — ");
  const aliasCount = (ctx.state.aliases || []).filter((a) => a.sources.some((s) => String(t.source?.file || "").endsWith(s))).length;
  const codes = targetCodes(ctx, t);
  const index = avicaIndex(ctx, t.project);
  const crash = (index?.logs || []).find((l) => l.kind === "crash" && (!l.crash?.target || l.crash.target === t.name) && (r.failed ? l.step === r.failed.key : false));
  if (ctx.state.mode === "server" && t.datasetId && !t._summaryLoaded) loadSummary(ctx, t);

  d.innerHTML = `
    <header class="dr-h">
      <span class="alfrd-chip" title="ALFRD project">${esc(ctx.projectName(t.project))}</span>
      <h3>${esc(t.name)}</h3>
      <span class="grow"></span>
      ${badge(r.status)}
      <button class="icon-btn sm" data-fold="drawer" aria-label="Fold details" title="Fold details" aria-expanded="true">${icon("fold")}</button>
    </header>
    <p class="muted dr-sub">${esc(sub)}</p>
    <button class="btn primary block" id="dr-open-workflow">Open workflow for this target ${icon("external")}</button>
    ${issue ? `<div class="callout ${r.failed ? "fail" : "warn"}">${icon(r.failed ? "xCircle" : "alert")}<div><b>Step ${issue.index + 1} ${r.failed ? "Failure" : "Warning"}: ${esc(issue.key)}</b><p>${esc(issueNote || "No diagnostic message recorded.")}</p>
      ${crash?.crash?.exception ? `<details class="crash"><summary>Crash snapshot <span class="mono">${esc(crash.name)}</span></summary><pre class="code small">${esc(String(crash.crash.exception).slice(-4000))}</pre></details>` : crash ? `<button class="link-btn small" data-crash="${esc(crash.name)}">Open ${esc(crash.name)}</button>` : ""}</div></div>` : ""}
    <h4>AVICA work folder</h4>
    <div class="codes dr-codes">${codeChips(ctx, t, { editable: true })}</div>
    ${codes.length ? `<p class="small muted">${codes.map((c) => `<span class="mono">${esc(index?.targetDir || "<target_dir>")}/${esc(c.code)}/wd</span>${c.auto ? " (auto-detected)" : ""}`).join("<br>")} · <button class="link-btn" id="dr-metadata">avica.meta &amp; inputs →</button></p>` : `<p class="small muted">No project code attached. ${index ? `Attach one of ${Object.keys(index.codes || {}).length} folder(s) under <code>${esc(index.targetDir)}/</code>.` : "Open the folder containing alfrd.yaml to list work folders."}</p>`}
    <h4>File &amp; storage hierarchy</h4>
    <dl class="files">
      <dt>Raw FITS-IDI</dt><dd class="mono">${esc(t.fitsidi || "—")}</dd>
      <dt>Measurement set</dt><dd class="mono">${(t.msPaths?.length ? t.msPaths : t.msPath ? [t.msPath] : []).map(esc).join("<br>") || "—"}</dd>
      ${t.artifacts?.length ? `<dt>Artifacts</dt><dd class="mono small">${t.artifacts.slice(0, 5).map((a) => `${esc(a.step)} · ${esc(a.band)}: ${esc(a.path)}`).join("<br>")}${t.artifacts.length > 5 ? `<br>+${t.artifacts.length - 5} more` : ""}</dd>` : ""}
    </dl>
    <div class="row between"><h4>Workflow progress (${r.done}/${r.total})</h4><span class="muted small tabular">Runtime: ${hms(r.runtime)}</span></div>
    <ol class="ladder">
      ${steps.map((s, i) => {
        const st = t.steps?.[s.key] || { status: "pending" };
        const m = STEP_STATUS[st.status] || STEP_STATUS.pending;
        const tries = st.attempts?.length || 0;
        return `<li class="st-${st.status}" title="${esc(st.note || "")}">${icon(m.icon)}<span>${i + 1}. ${esc(s.key)}${tries > 1 ? ` <small class="muted">×${tries}</small>` : ""}</span><span class="tabular">${st.status === "pending" || st.status === "queued" ? m.label.toLowerCase() : esc(short(st.duration) || m.label)}</span></li>`;
      }).join("")}
    </ol>
    <div class="prov">
      <b>Import provenance:</b>
      <span class="mono small">${esc(t.source?.file || "—")}</span>
      ${aliasCount ? `<a class="link-btn small" href="#/config">${aliasCount} field alias${aliasCount === 1 ? "" : "es"} applied (Project settings)</a>` : ""}
      ${t.runId ? `<span class="small">Runtime run <code>${esc(t.runId)}</code> · ${esc(t.runStatus || "")}</span>` : ""}
    </div>
    <h4><label for="dr-notes">Triage &amp; analysis notes</label></h4>
    <textarea id="dr-notes" class="input" rows="3" placeholder="Notes are kept in this browser (and in Studio snapshots).">${esc(ctx.state.notes[t.id] || "")}</textarea>
    <div class="row gap dr-actions">
      ${ctx.state.mode === "server" && t.runId ? `<select id="dr-step" class="input" aria-label="Step to retry">${steps.map((s, i) => `<option value="${esc(s.key)}" ${(issue?.key || steps[r.next ?? 0]?.key) === s.key ? "selected" : ""}>${i + 1}. ${esc(s.key)}</option>`).join("")}</select>
      <button class="btn grow" id="dr-retry" title="Submit a retry to the ALFRD runtime">Retry step</button>` : ""}
      <button class="icon-btn" id="dr-logs" title="View target logs" aria-label="View target logs">${icon("log")}</button>
    </div>
    ${t.history?.length ? `<details class="hist"><summary>Attempt history (${t.history.length})</summary><table class="tbl small"><thead><tr><th>Step</th><th>#</th><th>Status</th><th>Start</th><th>Runtime</th></tr></thead><tbody>${t.history.map((h) => `<tr class="st-${h.status}"><td class="mono">${esc(h.step)}</td><td>${h.attempt}</td><td>${esc(STEP_STATUS[h.status]?.label || h.status)}</td><td class="mono">${esc(when(h.started))}</td><td class="tabular">${esc(short(h.duration))}</td></tr>`).join("")}</tbody></table></details>` : ""}
  `;
}

async function loadSummary(ctx, t) {
  t._summaryLoaded = true;
  try {
    const summary = await server.datasetSummary(t.project, t.workflow, t.datasetId);
    t.history = (summary.history || []).map((h) => ({
      step: h.step, attempt: h.attempt, status: normalize(h.status), started: h.started_at, finished: h.finished_at, duration: h.duration_seconds, note: h.note,
    }));
    t.history.forEach((h) => {
      const s = t.steps[h.step];
      if (s) s.attempts = [...(s.attempts || []), h];
    });
    ctx.update();
  } catch (error) {
    ctx.log("warn", `History for ${t.name} unavailable: ${error.message}`, "server");
  }
}

function normalize(s) {
  return { ok: "completed", partial: "warning" }[s] || s;
}
