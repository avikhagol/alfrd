// Run × step grids for projects without targets (agent loops, unknown project types).
// Loaded lazily by the Overview. No DOM access, so node tests can import it.

import { icon } from "../utils/dom.js";

export const GRID_STATUS = {
  completed: { label: "Completed", tone: "ok", mark: "✓" },
  running: { label: "Running", tone: "run", mark: "▶" },
  failed: { label: "Failed", tone: "fail", mark: "✗" },
  blocked: { label: "Blocked", tone: "warn", mark: "⊘" },
  interrupted: { label: "Interrupted", tone: "warn", mark: "!" },
  queued: { label: "Queued", tone: "muted", mark: "…" },
  pending: { label: "Pending", tone: "muted", mark: "○" },
  skipped: { label: "Skipped", tone: "muted", mark: "–" },
  cancelled: { label: "Cancelled", tone: "muted", mark: "⦸" },
  awaiting_response: { label: "Awaiting response", tone: "warn", mark: "✎" },
  awaiting_review: { label: "Awaiting review", tone: "warn", mark: "?" },
  na: { label: "Not applicable", tone: "muted", mark: "—" },
};

const CELL_TO = { done: "completed", failed: "failed", running: "running", blocked: "blocked", interrupted: "interrupted", cancelled: "cancelled" };
const WAITING = ["awaiting_response", "awaiting_review"];
export const FILTERS_DEFAULT = { search: "", status: "all", run: "all" };

/** Fetch the requested run and its handoffs without changing the Workflow selection. */
export async function loadRunStatus(api, project, id = null, status = null) {
  status ||= await api.planStatus(project, id);
  const handoffs = status.plan?.loop ? (await api.handoffs(project, status.plan.id)).handoffs : [];
  return { status, handoffs };
}

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/**
 * Build the grid from a cached plan status (server.planStatus result).
 * opts.label(baseStep, stepId) → visible column label; opts.handoffs → /handoffs items (optional).
 * → {kind: "loop"|"generic", columns:[{id,label}], groups:[{run, status, selected, rows, note}], empty?}
 */
export function buildRunGrid(status, opts = {}) {
  const labelOf = opts.label || ((base) => base);
  if (!status) return { kind: "generic", columns: [], groups: [], empty: "unavailable" };
  const plan = status.plan || null;
  const runs = [...(status.plans || [])];
  if (plan && !runs.some((r) => r.id === plan.id)) runs.unshift(plan);
  if (!runs.length) return { kind: "generic", columns: [], groups: [], empty: "none" };
  const units = status.units || [];
  const loop = Boolean(plan?.loop || status.loop || units.some((u) => u.iteration));
  const table = status.table || {};
  const steps = table.steps?.length ? table.steps : plan?.steps || [];

  const unitIter = new Map();
  units.forEach((u) => (u.steps || []).forEach((st) => { if (u.iteration) unitIter.set(st, Number(u.iteration)); }));
  // Sequence loops (t001-claude, …): every turn is its own column on one row.
  const turnsOnly = loop && steps.length > 0 && steps.every((id) => /^t\d{3}-/.test(id));
  const split = (id) => {
    const m = /^i(\d+)[-_](.+)$/.exec(id);
    if (!loop) return { iteration: 0, base: id };
    if (turnsOnly) return { iteration: 1, base: id };
    return { iteration: unitIter.get(id) ?? (m ? Number(m[1]) : 0), base: m ? m[2] : id };
  };
  const columns = [];
  const seen = new Set();
  steps.forEach((id) => {
    const { base } = split(id);
    if (seen.has(base)) return;
    seen.add(base);
    columns.push({ id: base, label: labelOf(base, id) || base });
  });

  const attempts = new Map();
  units.forEach((u) => (u.rows || [u.row]).forEach((k) => (u.steps || []).forEach((st) => {
    const key = `${k}\u0000${st}`;
    if (!attempts.has(key)) attempts.set(key, []);
    attempts.get(key).push(u);
  })));
  const queued = new Set((status.queue || []).map((q) => `${q.row}\u0000${q.step}`));
  const hand = new Map((opts.handoffs || []).map((h) => [h.id, h]));
  const waitingUnit = WAITING.includes(status.loop?.phase) ? status.loop.unit : null;

  const cellOf = (rowKey, stepId, value) => {
    const key = `${rowKey}\u0000${stepId}`;
    const tries = attempts.get(key) || [];
    const u = tries.at(-1) || null;
    const h = u ? hand.get(u.id) : null;
    let st = CELL_TO[value];
    if (value === "todo") st = queued.has(key) || tries.length ? "queued" : "pending";
    else if (value === "skip") st = tries.length ? "skipped" : "na";
    else if (!st) st = GRID_STATUS[value] ? value : "pending";
    const phase = h?.phase || (u && u.id === waitingUnit ? status.loop.phase : null);
    if (st === "running" && WAITING.includes(phase)) st = phase;
    return {
      status: st, step: stepId, attempts: tries.length, unit: u?.id || null,
      agent: u?.agent || h?.agent || null, model: u?.model || h?.model || null,
      error: u?.error || h?.error || null, log: u?.log || h?.log || null,
      started: u?.started || h?.started || null, finished: u?.finished || h?.finished || null,
    };
  };

  const rows = [];
  const tableRows = table.rows || [];
  const multi = tableRows.length > 1;
  tableRows.forEach((r) => {
    const name = r.target || r.key;
    if (loop) {
      const byIter = new Map();
      steps.forEach((id) => {
        const { iteration, base } = split(id);
        if (!byIter.has(iteration)) byIter.set(iteration, {});
        const v = r.cells?.[id];
        if (v != null && v !== "") {
          const c = cellOf(r.key, id, v);
          if (c.status !== "na") byIter.get(iteration)[base] = c;
        }
      });
      [...byIter.entries()].sort((a, b) => a[0] - b[0]).forEach(([it, cells]) => rows.push({
        key: `${r.key}:${it}`, row: r.key, target: name, iteration: it || null,
        label: `${turnsOnly ? "Turns" : it ? `Iteration ${it}` : "Iteration —"}${multi ? ` · ${name}` : ""}`, cells,
      }));
    } else {
      const cells = {};
      steps.forEach((id) => {
        const v = r.cells?.[id];
        if (v == null || v === "") return;
        const c = cellOf(r.key, id, v);
        if (c.status !== "na") cells[id] = c;
      });
      rows.push({ key: r.key, row: r.key, target: name, iteration: null, label: name, cells });
    }
  });

  const groups = runs.map((r) => {
    const selected = Boolean(plan) && r.id === plan.id;
    const mine = selected ? rows : [];
    return { run: r.id, status: selected ? plan.status : r.status, selected, rows: mine, note: mine.length ? null : "Step details unavailable." };
  });
  return { kind: loop ? "loop" : "generic", columns, groups };
}

const rowText = (g, row, columns) => [g.run, row.label, ...columns.flatMap((c) => {
  const cell = row.cells[c.id];
  return cell ? [c.label, GRID_STATUS[cell.status]?.label, cell.agent, cell.model, cell.error] : [];
})].filter(Boolean).join(" ").toLowerCase();

/** Apply Search / Status / Run filters. Returns a new grid; empty: "nomatch" when nothing is left. */
export function filterRunGrid(grid, filters = FILTERS_DEFAULT, runLabel = (s) => s) {
  if (grid.empty) return grid;
  const f = { ...FILTERS_DEFAULT, ...filters };
  const q = f.search.trim().toLowerCase();
  const groups = grid.groups.filter((g) => f.run === "all" || g.run === f.run).map((g) => {
    if (!g.rows.length) {
      const text = `${g.run} ${runLabel(g.status)} ${g.status}`.toLowerCase();
      return f.status === "all" && (!q || text.includes(q)) ? g : null;
    }
    const rows = g.rows.filter((row) => (f.status === "all" || Object.values(row.cells).some((c) => c.status === f.status))
      && (!q || rowText(g, row, grid.columns).includes(q)));
    return rows.length ? { ...g, rows } : null;
  }).filter(Boolean);
  return groups.length ? { ...grid, groups } : { ...grid, groups, empty: "nomatch" };
}

export const filtersActive = (f = {}) => Object.keys(FILTERS_DEFAULT).some((k) => (f[k] ?? FILTERS_DEFAULT[k]) !== FILTERS_DEFAULT[k]);

/** Find one cell (for the detail dialog). Missing cells are "Not applicable". */
export function findCell(grid, run, rowKey, step) {
  const g = grid.groups.find((x) => x.run === run);
  const row = g?.rows.find((r) => r.key === rowKey);
  const col = grid.columns.find((c) => c.id === step);
  if (!g || !row || !col) return null;
  return { group: g, row, column: col, cell: row.cells[step] || { status: "na", attempts: 0 } };
}

export function elapsed(cell, now = Date.now()) {
  const start = Date.parse(cell?.started || "");
  if (!Number.isFinite(start)) return "—";
  const end = cell.finished ? Date.parse(cell.finished) : now;
  const s = Math.max(0, Math.floor((end - start) / 1000));
  return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function cellName(projectName, run, row, column, cell) {
  return [`Project ${projectName}`, `run ${run}`, row.iteration ? `iteration ${row.iteration}` : row.label ? `row ${row.label}` : "",
    `step ${column.label}`, `status ${GRID_STATUS[cell.status]?.label || cell.status}`, cell.attempts > 1 ? `${cell.attempts} attempts` : ""].filter(Boolean).join(", ");
}

/** Empty-state HTML. reason: none | offline | error | loading | nomatch | unavailable. */
export function emptyState(reason, { project = "", name = "", error = "" } = {}) {
  const p = esc(project);
  if (reason === "none") return `<div class="rg-empty"><p>No runs yet. Start a run from Workflow.</p><a class="btn primary" href="#/workflow">Open workflow</a></div>`;
  if (reason === "offline") return `<div class="rg-empty"><p>Connect with <code>alfrd serve</code> to load run details.</p></div>`;
  if (reason === "error") return `<div class="callout fail rg-empty" role="alert"><span>Could not load runs for ${esc(name || project)}${error ? `: ${esc(error)}` : "."}</span><button class="btn sm" data-rg-retry="${p}">Retry</button></div>`;
  if (reason === "nomatch") return `<div class="rg-empty"><p>No runs match these filters.</p><button class="btn sm" data-rg-reset="${p}">Reset filters</button></div>`;
  if (reason === "unavailable") return `<div class="rg-empty"><p>Step details unavailable.</p></div>`;
  return `<div class="rg-empty"><p class="muted">Loading runs…</p></div>`;
}

/**
 * HTML for one project's section: heading, toolbar, run groups (or an empty state).
 * full: the unfiltered grid (for filter choices); grid: filtered grid. opts: {runLabel, error, index}.
 */
export function renderRunGrid(project, name, full, grid, filters = FILTERS_DEFAULT, opts = {}) {
  const runLabel = opts.runLabel || ((s) => s);
  const f = { ...FILTERS_DEFAULT, ...filters };
  const p = esc(project);
  const id = `rg-${opts.index ?? 0}`;
  const kind = full.kind === "loop" ? "Agent loop runs" : "Runs";
  const hasRuns = !full.empty;
  const toolbar = hasRuns ? `<div class="rg-toolbar" role="group" aria-label="Filter runs of ${esc(name)}">
      <label class="search"><input type="search" data-rg-search="${p}" value="${esc(f.search)}" placeholder="Search runs, steps, agents…" aria-label="Search runs of ${esc(name)}"></label>
      <label class="select-wrap"><select data-rg-status="${p}" aria-label="Status filter for ${esc(name)}"><option value="all">All statuses</option>${Object.entries(GRID_STATUS).filter(([k]) => k !== "na").map(([k, m]) => `<option value="${k}" ${f.status === k ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>
      <label class="select-wrap"><select data-rg-run="${p}" aria-label="Run filter for ${esc(name)}"><option value="all">All runs (${full.groups.length})</option>${full.groups.map((g) => `<option value="${esc(g.run)}" ${f.run === g.run ? "selected" : ""}>Run ${esc(g.run)}</option>`).join("")}</select></label>
      <button class="btn sm" data-rg-reset="${p}" ${filtersActive(f) ? "" : "disabled"}>Reset filters</button>
    </div>` : "";
  const body = grid.empty ? emptyState(grid.empty, { project, name, error: opts.error })
    : grid.groups.map((g) => runGroup(project, name, grid, g, runLabel)).join("");
  return `<section class="rg" data-rg-section="${p}" aria-labelledby="${id}">
    <h3 class="rg-title" id="${id}">${esc(name)} <span class="muted small">· ${kind}</span></h3>
    ${toolbar}${body}</section>`;
}

function runGroup(project, name, grid, g, runLabel) {
  const head = `<h4 class="rg-run-h">Run <span class="mono">${esc(g.run)}</span> <span>${esc(runLabel(g.status) || g.status || "")}</span></h4>`;
  if (!g.rows.length) return `<div class="rg-run">${head}<p class="muted small">Step details unavailable.</p></div>`;
  const corner = grid.kind === "loop" ? "Iteration" : "Run row";
  return `<div class="rg-run">${head}
    <div class="grid-scroll rg-scroll" role="region" aria-label="Run ${esc(g.run)} by workflow steps" tabindex="0" data-scroll-key="${esc(`rg|${project}|${g.run}`)}">
    <table class="rg-table"><caption class="sr-only">${esc(name)} · run ${esc(g.run)} · ${corner.toLowerCase()} by step</caption>
      <thead><tr><th scope="col" class="rg-corner">${corner}</th>${grid.columns.map((c) => `<th scope="col" title="${esc(c.id)}">${esc(c.label)}</th>`).join("")}</tr></thead>
      <tbody>${g.rows.map((row) => `<tr><th scope="row" class="rg-rowh">${esc(row.label)}</th>${grid.columns.map((c) => {
        const cell = row.cells[c.id] || { status: "na", attempts: 0 };
        const m = GRID_STATUS[cell.status] || GRID_STATUS.pending;
        const badge = cell.attempts > 1 ? `<span class="rg-tries" aria-hidden="true">×${cell.attempts}</span>` : "";
        return `<td><button type="button" class="rg-cell st-${esc(cell.status)} tone-${m.tone}" data-rg-cell="${esc(project)}" data-rg-run-id="${esc(g.run)}" data-rg-row="${esc(row.key)}" data-rg-step="${esc(c.id)}" data-focus-key="${esc(`rg|${project}|${g.run}|${row.key}|${c.id}`)}" aria-label="${esc(cellName(name, g.run, row, c, cell))}"><span aria-hidden="true">${m.mark}</span> ${esc(m.label)}${badge}</button></td>`;
      }).join("")}</tr>`).join("")}</tbody>
    </table></div></div>`;
}

// ---- Agent-loop run history: one summary row per plan above the chosen run's step grid. ----

// Plan statuses mapped onto the grid's status filter.
const PLAN_GRID = { finished: "completed", failed: "failed", running: "running", cancelled: "cancelled", paused: "interrupted", interrupted: "interrupted" };

/** runs: [{id, status}] (plan list, newest first) → the rows the Search / Status / Run filters keep (and only target's runs when given). */
export function filterHistory(runs, filters = FILTERS_DEFAULT, runLabel = (s) => s, target = null) {
  const f = { ...FILTERS_DEFAULT, ...filters };
  const q = f.search.trim().toLowerCase();
  return runs.filter((r) => (!target || (r.targets || [r.target]).includes(target)) && (f.run === "all" || r.id === f.run) && (f.status === "all" || PLAN_GRID[r.status] === f.status)
    && (!q || `run ${r.id} ${runLabel(r.status)} ${r.status} ${(r.targets || [r.target]).filter(Boolean).join(" ")}`.toLowerCase().includes(q)));
}

const dur = (s) => (s == null ? "—" : s >= 3600 ? `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`);

/** Empty states of a loop project's history. reason: none | offline | error | loading | nomatch. */
export function historyEmpty(reason, project, error = "", setup = false) {
  const p = esc(project);
  if (reason === "none") return `<div class="rg-empty"><p><b>No runs yet.</b> ${setup ? "Set up your agents, edit task.md" : "Edit task.md, review your agents"}, then start a run in Workflow.</p><div class="row gap wrap">${setup ? `<button class="btn sm" data-home-setup="${p}">${icon("gear")} Setup wizard</button>` : ""}<button class="btn sm" data-rh-task="${p}">${icon("edit")} Edit task</button><button class="btn sm primary" data-rh-workflow="${p}">${icon("workflow")} Open Workflow</button></div></div>`;
  if (reason === "offline") return `<div class="rg-empty"><p>Connect to the ALFRD server to open this run.</p></div>`;
  if (reason === "error") return `<div class="callout fail rg-empty" role="alert"><span>Couldn’t load run history.${error ? ` ${esc(error)}` : ""}</span><button class="btn sm" data-rg-retry="${p}">Retry</button></div>`;
  if (reason === "nomatch") return `<div class="rg-empty"><p>No runs match these filters.</p><button class="btn sm" data-rg-reset="${p}">Clear filters</button></div>`;
  return `<div class="rg-empty"><p class="muted" role="status">Loading run history…</p></div>`;
}

/**
 * HTML for one agent-loop project: toolbar, run history (Run, [ALFRD project], Status,
 * Progress, Runtime, Open run, View results) and the step grid of the run chosen in the
 * Run filter (the latest by default; each run keeps its own step columns).
 * runs: [{id, status}] newest first; summary(id) → runSummary | null (not read yet).
 * opts: {runLabel, error, index, page, size, showProject, target (header target name), targetOnly}.
 */
export function renderRunHistory(project, name, full, grid, runs, summary, filters = FILTERS_DEFAULT, opts = {}) {
  const runLabel = opts.runLabel || ((s) => s);
  const f = { ...FILTERS_DEFAULT, ...filters };
  const p = esc(project);
  const id = `rg-${opts.index ?? 0}`;
  const head = `<h3 class="rg-title" id="${id}">${esc(name)} <span class="muted small">· ${runs.length} run${runs.length === 1 ? "" : "s"}</span></h3>`;
  const wrap = (body) => `<section class="rg" data-rg-section="${p}" aria-labelledby="${id}">${head}${body}</section>`;
  if (full.empty && full.empty !== "nomatch" && !runs.length) return wrap(historyEmpty(full.empty === "unavailable" ? "none" : full.empty, project, opts.error, opts.setup));
  const t = opts.target, only = Boolean(opts.targetOnly && t);
  const pool = only ? filterHistory(runs, {}, runLabel, t) : runs; // the Run filter's choices
  const shown = filterHistory(runs, f, runLabel, only ? t : null);
  const size = opts.size || 25;
  const pages = Math.max(1, Math.ceil(shown.length / size));
  const page = Math.min(Math.max(0, opts.page || 0), pages - 1);
  const rows = shown.slice(page * size, page * size + size);
  const toolbar = `<div class="rg-toolbar" role="group" aria-label="Filter runs of ${esc(name)}">
      <div class="seg" role="group" aria-label="Runs shown"><button data-rg-scope="${p}" data-scope="all" class="${only ? "" : "on"}" aria-pressed="${!only}" title="Runs of every target">All targets</button><button data-rg-scope="${p}" data-scope="target" class="${only ? "on" : ""}" aria-pressed="${only}" ${t ? "" : "disabled"} title="${t ? `Only runs of ${esc(t)} (selected in the header)` : "Select a target in the header"}">Target only</button></div>
      <label class="search"><input type="search" data-rg-search="${p}" value="${esc(f.search)}" placeholder="Search runs…" aria-label="Search runs of ${esc(name)}"></label>
      <label class="select-wrap"><select data-rg-status="${p}" aria-label="Status filter for ${esc(name)}"><option value="all">All statuses</option>${[...new Set(Object.values(PLAN_GRID))].map((k) => `<option value="${k}" ${f.status === k ? "selected" : ""}>${esc(GRID_STATUS[k].label)}</option>`).join("")}</select></label>
      <label class="select-wrap"><select data-rg-run="${p}" aria-label="Run filter for ${esc(name)}"><option value="all">All runs (${pool.length})</option>${pool.map((r) => `<option value="${esc(r.id)}" ${f.run === r.id ? "selected" : ""}>Run ${esc(r.id)}</option>`).join("")}</select></label>
      <button class="btn sm" data-rg-reset="${p}" ${filtersActive(f) ? "" : "disabled"}>Clear filters</button>
    </div>`;
  const row = (r) => {
    const s = summary(r.id);
    const m = GRID_STATUS[PLAN_GRID[r.status]] || GRID_STATUS.pending;
    const pct = s?.total_turns ? Math.round(((s.completed_turns || 0) / s.total_turns) * 100) : 0;
    const noResults = s && !s.ran; // nothing has run: no turn records to show
    const help = `rh-nr-${esc(project)}-${esc(r.id)}`;
    return `<tr data-rh-row="${esc(`${project}|${r.id}`)}">
      <th scope="row" class="rg-rowh rh-run" tabindex="0" title="Run ${esc(r.id)}"><span class="rh-label">Run <span class="mono">${esc(r.id)}</span></span></th>
      ${opts.showProject ? `<td><span class="alfrd-chip">${esc(name)}</span></td>` : ""}
      <td><span class="badge tone-${m.tone}">${esc(runLabel(r.status) || r.status || "—")}</span></td>
      <td class="tabular">${s?.total_turns ? `<span class="prog" title="${s.completed_turns ?? "—"} of ${s.total_turns} turns completed"><b>${s.completed_turns ?? "—"}/${s.total_turns}</b><span class="bar tone-${m.tone}"><i style="width:${pct}%"></i></span></span><span class="sr-only"> turns</span>` : "—"}</td>
      <td class="tabular">${dur(s?.runtime_seconds)}</td>
      <td><button type="button" class="btn sm" data-rh-open="${p}" data-rh-run="${esc(r.id)}" data-focus-key="${esc(`rh-open|${project}|${r.id}`)}" aria-label="Open run ${esc(r.id)}">${icon("play")} Open run</button></td>
      <td><button type="button" class="btn sm" data-rh-results="${p}" data-rh-run="${esc(r.id)}" data-focus-key="${esc(`rh-res|${project}|${r.id}`)}" aria-label="View results for run ${esc(r.id)}"${noResults ? ` disabled aria-describedby="${help}"` : ""}>${icon("results")} View results</button>${noResults ? ` <span class="muted small" id="${help}">No results yet</span>` : ""}</td></tr>`;
  };
  const table = rows.length ? `<div class="grid-scroll rg-scroll" role="region" aria-label="Runs of ${esc(name)}" tabindex="0" data-scroll-key="${esc(`rh|${project}`)}">
    <table class="rg-table rh-table"><caption class="sr-only">${esc(name)} · run history, newest first</caption>
      <thead><tr><th scope="col" class="rg-corner">Run</th>${opts.showProject ? '<th scope="col">ALFRD project</th>' : ""}<th scope="col">Status</th><th scope="col">Progress</th><th scope="col">Runtime</th><th scope="col">Open run</th><th scope="col">View results</th></tr></thead>
      <tbody>${rows.map(row).join("")}</tbody></table></div>
    ${pages > 1 ? `<div class="row gap rh-pages"><span class="muted small tabular">Runs ${page * size + 1}–${page * size + rows.length} of ${shown.length}</span><span class="grow"></span><button class="btn sm" data-rh-page="${p}" data-step="-1" ${page ? "" : "disabled"}>Previous</button><button class="btn sm" data-rh-page="${p}" data-step="1" ${page < pages - 1 ? "" : "disabled"}>Next</button></div>` : ""}`
    : historyEmpty("nomatch", project);
  const detail = (grid.groups || []).filter((g) => g.rows.length && shown.some((r) => r.id === g.run));
  return wrap(`${toolbar}<p class="muted small rh-window">Loop runs among the newest 20 plans per project.</p>${table}${detail.map((g) => runGroup(project, name, grid, g, runLabel)).join("")}`);
}

/** Detail dialog body. logHtml: pre-rendered View log buttons (or ""). */
export function cellDetail(projectName, found, logHtml = "", now = Date.now()) {
  const { group, row, column, cell } = found;
  const m = GRID_STATUS[cell.status] || GRID_STATUS.pending;
  const dd = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
  return `<dl class="rg-detail">
    ${dd("Project", esc(projectName))}${dd("Run", `<span class="mono">${esc(group.run)}</span>`)}
    ${row.iteration ? dd("Iteration", esc(row.iteration)) : dd("Row", esc(row.label))}
    ${dd("Step", `${esc(column.label)}${column.label !== column.id ? ` <span class="muted mono small">${esc(cell.step || column.id)}</span>` : ""}`)}
    ${dd("Status", `<span class="badge tone-${m.tone}">${esc(m.label)}</span>`)}
    ${dd("Agent / model", esc([cell.agent, cell.model].filter(Boolean).join(" · ") || "—"))}
    ${dd("Elapsed", esc(elapsed(cell, now)))}
    ${dd("Attempts", esc(cell.attempts || 0))}
    ${cell.error ? dd("Error", `<pre class="code small">${esc(cell.error)}</pre>`) : ""}
  </dl>${logHtml ? `<div class="row gap">${logHtml}</div>` : cell.status === "na" ? "" : '<p class="muted small">No log recorded for this step yet.</p>'}`;
}
