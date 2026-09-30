// VIEW 1 — Project Overview: grouped spreadsheet grid + target detail drawer.

import { $, on, esc, icon, hms, short, when, copyText, loadUi, saveUi } from "../utils/dom.js";
import { notesAt, noteMark } from "../data/notes.js";
import { avicaIndex, codeChips, detachCode, openAttachPicker, targetCodes } from "./attach.js";
import { STEP_STATUS, OVERALL_STATUS, targetText } from "../data/model.js";
import { server } from "../data/server.js";
import { openRunDialog, plansAvailable } from "./plans.js";
import { PRESETS, NO_CODE, FILTER_DEFAULTS, filterTargets, activeFilters } from "../data/filters.js";

// Remembered in this browser until Settings → "Reset view state".
const UI_FIELDS = ["search", "project", "status", "code", "preset", "mode", "collapsed", "hidden", "drawer", "groupBy"];
const saved = loadUi("overview", {
  search: "", project: "all", status: "all", code: "all", preset: null, mode: "details", collapsed: [], hidden: [], drawer: true, groupBy: "project",
});
const ui = {
  ...saved,
  collapsed: new Set(saved.collapsed),
  hidden: new Set(saved.hidden),
  page: 0,
  checked: new Set(),
};
const remember = () => saveUi("overview", ui, UI_FIELDS);

const LEADING = [
  { id: "project", label: "ALFRD project" },
  { id: "codes", label: "Project code" },
  { id: "ms", label: "MS Storage Path" },
  { id: "status", label: "Status" },
  { id: "stages", label: "Stages" },
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

/** Overview filtered to one project code (command palette). */
export function showCode(ctx, code) {
  ui.code = code;
  ui.page = 0;
  remember();
  ctx.goTo("overview");
  ctx.update();
}

/** Targets after header project scope + local filters (used by CSV export too). */
export function visibleTargets(ctx) {
  return filterTargets(ctx.scopedTargets(), ui, helpers(ctx));
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

// Short, human tag for a failed/partial cell ("missing IF4", "SNR 4.2σ").
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
  el.innerHTML = `<div class="ov"><div class="card ov-head" id="ov-head"></div><div class="ov-body"><div class="card grid-card" id="ov-grid"></div><aside class="card drawer" id="ov-drawer" aria-label="Target details"></aside></div></div>`;

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
    { label: "Overview CSV (visible rows)", icon: "download", run: () => document.querySelector("#btn-export").click() },
  ]));
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
  on(el, "click", "#dr-logs", () => ctx.renderConsole());
  on(el, "click", "#dr-bulk-run", () => {
    const picked = checkedTargets(ctx);
    openRunDialog(ctx, picked[0].project, { targets: picked.map((t) => t.name) });
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
  if (plansAvailable(ctx, project)) out.push(`<button class="link-btn" id="dr-bulk-run" title="New plan from the checked targets">${icon("play")} Run…</button>`);
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

/** Declared targets file of a project, and whether any checked target is in it. */
function targetsFileOf(ctx, project) {
  return ctx.state.trees?.[project]?.targetsFile || null;
}

/** Retry a step of a runtime-managed run (server). Other runs are started from a plan (Run…). */
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
  renderHead(el, ctx);
  renderGrid(el, ctx);
  renderDrawer(el, ctx);
  el.querySelector(".ov-body").classList.toggle("drawer-folded", !ui.drawer);
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
}

function renderTitle(el, ctx) {
  const projects = ctx.projects();
  const scoped = ctx.scopedTargets();
  const src = ctx.state.source;
  const srcLabel = src === "demo" ? "Demo Data" : src === "server" ? "Runtime" : src === "imported" ? "Imported" : "No data";
  $("#ov-title", el).innerHTML = `
      <h2>Project Overview</h2>
      <span class="chip">${esc(srcLabel)} · ${projects.length} project${projects.length === 1 ? "" : "s"} · ${scoped.length} target${scoped.length === 1 ? "" : "s"}</span>
      <span class="grow"></span>
      <button class="btn" id="ov-targets" aria-haspopup="menu">${icon("upload")} Targets ${icon("caret")}</button>
      <button class="btn" id="ov-export" aria-haspopup="menu">${icon("download")} Export ${icon("caret")}</button>`;
}

function renderStatusBar(el, ctx) {
  const scoped = ctx.scopedTargets();
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
  ctx.scopedTargets().forEach((t) => {
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
    <span class="muted small tabular">showing ${shown} of ${ctx.scopedTargets().length}</span>
    <button class="link-btn small" data-unfilter="*">Clear all</button>`;
}

function renderGrid(el, ctx) {
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
    const everyone = ctx.scopedTargets().filter((t) => groupKey(t) === project);
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
    ? `<tr><td colspan="${totalCols}" class="empty">${ctx.state.targets.length ? "No targets match the current filters." : `No project loaded. ${ctx.state.mode === "server" ? "Start <code>alfrd serve</code> in the folder that holds alfrd.yaml, or " : ""}<button class="link-btn" id="ov-import">open the project folder</button>.`}</td></tr>`
    : "";

  $("#ov-grid", el).innerHTML = `
    <div class="grid-scroll" role="region" aria-label="Targets grid" tabindex="0">
    <table class="grid ${ui.mode}">
      <thead>
        <tr class="h1"><th class="fz fz0" colspan="2"></th><th colspan="${cols.length}" class="h1l">Target core metadata &amp; execution telemetry</th>${showStages ? `<th colspan="${steps.length}" class="h1s">Pipeline stages · ${esc(ctx.state.workflow.name)}</th>` : ""}</tr>
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
  ctx.setFooterRight(`Showing ${all.length} of ${ctx.state.targets.length} targets across ${ctx.projects().length} projects | ${ctx.state.source === "demo" ? "Demo Data" : ctx.state.source === "server" ? "Runtime" : "Imported"}`);
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
  const sub = [t.role, r.status === "failed" ? `Failed at Stage ${r.failed.index + 1}` : r.status === "running" ? `Running stage ${r.running.index + 1}` : OVERALL_STATUS[r.status].label].filter(Boolean).join(" — ");
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
    ${issue ? `<div class="callout ${r.failed ? "fail" : "warn"}">${icon(r.failed ? "xCircle" : "alert")}<div><b>Stage ${issue.index + 1} ${r.failed ? "Failure" : "Warning"}: ${esc(issue.key)}</b><p>${esc(issueNote || "No diagnostic message recorded.")}</p>
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
    <div class="row between"><h4>Execution pipeline (${r.done}/${r.total})</h4><span class="muted small tabular">Runtime: ${hms(r.runtime)}</span></div>
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
      <button class="icon-btn" id="dr-logs" title="Open log stream" aria-label="Open log stream">${icon("terminal")}</button>
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
