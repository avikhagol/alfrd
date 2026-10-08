
import { $, $$, esc, icon, short, bytes, keepScroll, storage } from "../utils/dom.js";
import { server } from "../data/server.js";
import { addLogSource, dockLog, openFileFull } from "./logview.js";
import { notesAt } from "../data/notes.js";

async function attachFits(ctx, project, root) {
  if (ctx.state.mode !== "server" || ctx.state.trees?.[project]?.defs?.template !== "avica") return;
  try {
    const [{ mountFileAutocomplete }, data] = await Promise.all([import("./file_autocomplete.js"), server.avicaFitsFiles(project)]);
    if (root.isConnected) $$("#ar-files, [data-files]", root).forEach((input) => mountFileAutocomplete(input, data));
  } catch (error) { ctx.log("warn", `FITS suggestions: ${error.message}`, "server"); }
}

const cache = new Map(); // project -> { status, fetched, error, loading }
let pickerMod = null;

const loadPicker = () => (pickerMod ||= import("./step_picker.js").catch((e) => { pickerMod = null; throw e; }));
const defsOf = (ctx, project) => ctx.state.trees?.[project]?.defs || null;
let usageMod = null;
let usageCtx = null;
const loadUsage = () => (usageMod ||= import("./usage_view.js").catch((e) => { usageMod = null; throw e; }));
const unitById = (project, id) => (planOf(project)?.units || []).find((u) => u.id === id) || null;


export function usageButton(project, u) {
  if (!u?.usage_file) return "";
  const label = u.usage ? `${bytes(u.usage.peak_mem || 0)} · ${u.usage.avg_cores ?? "–"} cores` : "usage";
  return `<button class="link-btn small" data-usage="${esc(u.id)}" data-usage-project="${esc(project)}" title="CPU, memory and I/O of this command">${icon("graph")}${esc(label)}</button>`;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest?.("[data-usage]");
  if (!b || !usageCtx) return;
  const project = b.dataset.usageProject;
  const u = unitById(project, b.dataset.usage);
  if (!u) return;
  try { (await loadUsage()).openUsage(usageCtx, project, u, () => unitById(project, u.id) || u); } catch (error) { usageCtx.toast(error.message, "fail"); }
});
const sessionRuns = new Set();
const pollTimers = new Map(); // project -> timeout id

export const CELL = {
  done: { label: "Done", tone: "ok", icon: "checkCircle", step: "completed", mark: "✓" },
  failed: { label: "Failed", tone: "fail", icon: "xCircle", step: "failed", mark: "✗" },
  running: { label: "Running", tone: "run", icon: "sync", step: "running", mark: "▶" },
  todo: { label: "Queued", tone: "muted", icon: "hourglass", step: "queued", mark: "·" },
  blocked: { label: "Blocked", tone: "warn", icon: "minus", step: "skipped", mark: "⊘" },
  interrupted: { label: "Interrupted", tone: "warn", icon: "alert", step: "warning", mark: "!" },
  cancelled: { label: "Cancelled", tone: "muted", icon: "stop", step: "skipped", mark: "–" },
  skip: { label: "Skipped", tone: "muted", icon: "minus", step: null, mark: "" },
};
export const RUN_MODE = { step: "One step at a time (all targets)", target: "One target at a time", batch: "Everything in parallel" };
export const RUN_FAILURE = { stop_target: "Stop that target, continue others", continue: "Keep going", stop_plan: "Stop the whole run" };
export const RUN_STATUS = { running: "Running", paused: "Needs attention", finished: "Done", failed: "Failed", cancelled: "Skipped", interrupted: "Needs attention" };
export const RUN_PHASE = { done: "Done", failed: "Failed", running: "Running", awaiting_response: "Needs attention", awaiting_review: "Needs attention", ready: "Queued" };
export const ACTIVITY_PHASE = {
  running: { label: "Running", tone: "run", icon: "sync" },
  review: { label: "Review pending", tone: "warn", icon: "eye" },
  manual: { label: "Needs response", tone: "warn", icon: "edit" },
  scheduled: { label: "Scheduled", tone: "muted", icon: "clock" },
  waiting: { label: "Waiting", tone: "muted", icon: "clock" },
  paused: { label: "Paused", tone: "muted", icon: "pause" },
};
const isActive = (p) => ["running", "paused"].includes(p?.status);
export function phaseLabel(p, now = new Date()) {
  const label = ACTIVITY_PHASE[p.phase]?.label || RUN_STATUS[p.status] || p.status;
  if (p.phase !== "scheduled" || !p.start_at) return label;
  const d = new Date(p.start_at);
  if (!Number.isFinite(d.getTime())) return label;
  const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${label} ${d.toDateString() === now.toDateString() ? "" : d.toLocaleDateString("en", { month: "short", day: "numeric" }) + " "}${time}`;
}
export function runSwitcher(s) {
  const runs = (s.plans || []).filter(isActive).sort((a, b) => Number(b.working) - Number(a.working) || String(b.created || b.id).localeCompare(String(a.created || a.id)));
  if (runs.length < 2) return "";
  return `<nav class="run-switch" aria-label="Active runs"><span class="small">${runs.length} active runs</span><ul class="run-switch-list" role="list">${runs.slice(0, 6).map((p) => {
    const phase = ACTIVITY_PHASE[p.phase] || { tone: "muted", icon: "info" };
    return `<li><button class="chip run-chip tone-${phase.tone}" data-plan-pick-id="${esc(p.id)}" ${p.id === s.plan?.id ? 'aria-current="true"' : ""}>${icon(p.id === s.plan?.id ? "check" : phase.icon)}<b>${esc(p.target || p.id)}</b> · ${esc(phaseLabel(p))}${p.turn ? ` · <span class="tabular">turn ${esc(p.turn)}/${esc(p.turns || "?")}</span>` : ""}</button></li>`;
  }).join("")}${runs.length > 6 ? `<li><button class="chip" data-plan-more>+${runs.length - 6} more</button></li>` : ""}</ul></nav>`;
}
const PLAN_TONE = { running: "run", paused: "warn", finished: "ok", failed: "fail", cancelled: "muted", interrupted: "warn" };


function planLogFiles(_ctx, project) {
  const s = planOf(project);
  if (!s?.plan) return [];
  const id = s.plan.id;
  const files = (s.units || []).filter((u) => u.log).map((u) => ({
    rel: u.log,
    name: u.log.split("/").pop(),
    target: u.mode === "batch" ? null : u.target || null,
    steps: u.steps || [],
    groups: [...(u.steps || []).map((st) => `step:${st}`), `plan:${id}`],
    plan: id,
    unit: u.id,
  }));
  files.push({ rel: `.alfrd/plans/${id}/runner.log`, name: "runner.log", target: null, steps: [], groups: [`plan:${id}`], plan: id });
  return files;
}
addLogSource(planLogFiles);


export function logButtons(project, rel, { label = "" } = {}) {
  if (!rel) return "";
  return `<span class="log-btns">${label ? `<button class="link-btn small" data-log-zoom="${esc(rel)}" data-log-project="${esc(project)}" title="Open full screen, following new output">${esc(label)}</button>` : ""}<button class="icon-btn xs" data-log-dock="${esc(rel)}" data-log-project="${esc(project)}" title="Follow in the Log Stream panel (keeps running on other views)" aria-label="Follow in the Log Stream panel">${icon("log")}</button><button class="icon-btn xs" data-log-zoom="${esc(rel)}" data-log-project="${esc(project)}" title="Open full screen (follows; Minimize to Log Stream)" aria-label="Open full screen">${icon("expand")}</button></span>`;
}


export function plansAvailable(ctx, project) {
  return ctx.state.mode === "server" && Boolean(project) && ctx.state.trees?.[project]?.provider === "server";
}

export function planOf(project) {
  return cache.get(project)?.status || null;
}

export function activePlan(project, target = null) {
  const s = planOf(project);
  const active = (p) => p && ["running", "paused", "interrupted"].includes(p.status);
  const matches = (p) => !target || p.target === target || p.targets?.includes(target);
  if (active(s?.plan) && (matches(s.plan) || s.table?.rows?.some((r) => r.target === target))) return s;
  const plan = s?.plans?.find((p) => active(p) && matches(p));
  return plan ? { plan } : null;
}

export async function loadPlan(ctx, project, { id = null, quiet = false } = {}) {
  if (!plansAvailable(ctx, project)) return null;
  usageCtx = ctx;
  const entry = cache.get(project) || {};
  // Explicit run selection wins over in-flight reads.
  if (id && entry.pinned !== id) { entry.pinned = id; entry.pinActive = false; }
  if (id && (entry.loading || entry.status?.plan?.id !== id)) { entry.want = id; entry.status = null; entry.error = null; }
  cache.set(project, entry);
  if (entry.loading) return new Promise((resolve) => (entry.waiters ||= []).push(resolve));
  const before = JSON.stringify([entry.status, entry.error]);
  entry.loading = true;
  const asked = entry.want || entry.pinned || null;
  try {
    const status = await server.planStatus(project, asked);
    if (entry.want && status.plan?.id !== entry.want) throw Object.assign(new Error("stale"), { stale: asked !== entry.want, status: 404 });
    delete entry.want;
    if (entry.pinned && entry.pinActive && !isActive(status.plan) && (status.plans || []).some((p) => p.id !== entry.pinned && p.working)) {
      entry.releasedTarget = status.plan?.target || entry.pinned;
      delete entry.pinned;
      entry.pinActive = false;
      entry.loading = false;
      return await loadPlan(ctx, project, { quiet });
    }
    if (entry.pinned) entry.pinActive = isActive(status.plan);
    if (entry.releasedTarget) {
      const shown = (status.plans || []).find((p) => p.id === status.plan?.id) || status.plan;
      ctx.toast?.(`Run ${entry.releasedTarget} finished, now showing ${shown?.target || shown?.id} (${phaseLabel(shown || {})})`, "info");
      delete entry.releasedTarget;
    }
    const run = status.plan?.id, key = `plan:${project}`;
    if (status.plan?.status === "failed") {
      // Only viewed failures raise a banner.
      if (!quiet && entry.failedRun !== run) {
        entry.failedRun = run;
        ctx.showError(`Run ${run} failed. View logs for details or retry failed steps.`,
          async () => { await server.planAction(project, run, "resume", { retry_failed: true }); await loadPlan(ctx, project); },
          "workflow", key);
      }
    } else if (entry.failedRun) { entry.failedRun = null; ctx.clearError?.("workflow", key); }
    entry.status = status;
    entry.error = null;
    entry.fetched = Date.now();
    (status.reconcile || []).forEach((r) => r.action === "needs runner" && server.session?.mutations_enabled
      && server.planReconcile(project).then(() => ctx.log("info", `Run ${r.plan}: runner restarted to follow ${r.alive.length} running command(s).`, "plan")).catch(() => {}));
  } catch (error) {
    if (!error.stale) {
      entry.error = entry.want && error.status === 404 ? "This run is no longer available. Refresh run history." : error.message;
      delete entry.want;
      if (error.status === 404 && asked === entry.pinned) { delete entry.pinned; entry.pinActive = false; }
      if (!quiet) ctx.log("warn", `Plans: ${error.message}`, "plan");
    }
  } finally {
    entry.loading = false;
  }
  if (entry.want) return loadPlan(ctx, project, { quiet });
  schedulePoll(ctx, project);
  if (before !== JSON.stringify([entry.status, entry.error])) ctx.update();
  (entry.waiters || []).splice(0).forEach((resolve) => resolve(entry.status));
  return entry.status;
}

// Poll each project independently.
function schedulePoll(ctx, project) {
  clearTimeout(pollTimers.get(project));
  pollTimers.delete(project);
  const s = planOf(project);
  const busy = s?.plan && (isActive(s.plan) || s.plans?.some(isActive) || s.running?.length || s.runner?.alive);
  if (!busy) return;
  const timer = setTimeout(() => {
    if (document.hidden) { schedulePoll(ctx, project); return; }
    loadPlan(ctx, project, { quiet: true });
  }, 3000);
  timer?.unref?.(); // non-browser runtimes (node tests) must not stay alive for a poll
  pollTimers.set(project, timer);
}

export function forgetPlan(project) {
  clearTimeout(pollTimers.get(project));
  pollTimers.delete(project);
  cache.delete(project);
}

// Active cached runs for Jobs.
export function activeJobs() {
  return [...cache.entries()].map(([project, e]) => ({ project, status: e.status }))
    .filter((j) => j.status?.plan && ["running", "paused", "interrupted"].includes(j.status.plan.status));
}


export function planOverlay(project, target) {
  const s = planOf(project);
  if (!s?.plan || !s.table?.rows?.length) return null;
  const rows = s.table.rows;
  const row = target ? rows.find((r) => r.target === target.name) : null;
  const counts = {};
  (s.table.steps || []).forEach((step) => {
    const c = {};
    rows.forEach((r) => { const v = r.cells[step]; if (v && v !== "skip") c[v] = (c[v] || 0) + 1; });
    counts[step] = c;
  });
  return { plan: s.plan, row, counts, steps: s.table.steps || [] };
}

export function countsLine(c) {
  if (!c) return "";
  return ["running", "done", "failed", "todo", "blocked", "interrupted"].filter((k) => c[k])
    .map((k) => `<span class="pc pc-${k}" title="${esc(CELL[k].label)}">${CELL[k].mark} ${c[k]}</span>`).join("");
}

function ago(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
}

function eta(sec) {
  if (sec == null) return "—";
  return sec < 1 ? "now" : `in ${short(sec)}`;
}

function runnerTitle(s) {
  const r = s.runner || {};
  if (r.alive) return `runner ${r.pid} @ ${r.host}, heartbeat ${ago(r.heartbeat)}`;
  const n = s.running?.length || 0;
  return `no runner${n ? ` · ${n} command(s) still marked running` : ""}`;
}


function moreMenu(ctx, project, anchor) {
  const s = planOf(project);
  const p = s?.plan;
  if (!p) return;
  const canAct = Boolean(server.session?.mutations_enabled);
  const active = ["running", "paused", "interrupted"].includes(p.status);
  const act = (a) => () => planAct(ctx, project, a, anchor);
  const log = `.alfrd/plans/${p.id}/runner.log`;
  ctx.menu(anchor, [
    canAct && !p.loop && { icon: "plus", label: "Add target…", hint: "append a row to this run's CSV", run: act("add-row") },
    p.loop && { icon: "file", label: "Handoffs…", run: async () => { try { const { openHandoffs } = await import("./agent_dialog.js"); await openHandoffs(ctx, project, p.id); } catch (error) { ctx.toast(error.message, "fail"); } } },
    canAct && { icon: "play", label: "New run…", run: act("new") },
    (s.units || []).some((u) => u.usage || u.agent_usage || u.handoff) && { icon: "graph", label: "Usage", hint: "Tokens, cost, CPU / memory", run: act("usage") },
    { icon: "log", label: "Runner log", hint: "follow in the Log Stream", run: () => { dockLog(ctx, project, log); } },
    { icon: "sync", label: "Refresh", run: act("refresh") },
    canAct && active && { icon: "stop", label: "Cancel run…", hint: "Cancel: stops the current turn now", danger: true, run: act("cancel") },
  ].filter(Boolean));
}

const ORDER_KEY = "alfrd.sched.order";
function orderPrefs() {
  try { return { dock: "bottom", folded: false, ...JSON.parse(localStorage.getItem(ORDER_KEY) || "{}") }; } catch { return { dock: "bottom", folded: false }; }
}
function saveOrderPrefs(prefs) {
  try { localStorage.setItem(ORDER_KEY, JSON.stringify(prefs)); } catch { /* private mode */ }
}

// Run order sections the user opened or closed ("project|Done" → open). A running plan
// re-renders every few seconds; without this each refresh reset them to their defaults.
const secOpen = new Map();
const watched = new WeakSet();
function rememberSections(box) {
  if (watched.has(box) || !box.addEventListener) return;
  watched.add(box);
  box.addEventListener("toggle", (e) => { if (e.target.dataset?.sec) secOpen.set(e.target.dataset.sec, e.target.open); }, true);
}


export function renderSchedule(box, ctx, project) {
  if (!plansAvailable(ctx, project)) {
    box.innerHTML = `<div class="empty">${icon("server")}<p>Runs need <code>alfrd serve</code>. From a terminal: <code>alfrd plan new --targets …</code>, <code>alfrd plan run</code>, <code>alfrd plan status</code>.</p></div>`;
    return;
  }
  const entry = cache.get(project);
  if (!entry || entry.want) { box.innerHTML = `<div class="empty">Loading runs…</div>`; if (!entry) loadPlan(ctx, project); return; }
  const s = entry.status;
  if (!s?.plan) {
    box.innerHTML = `<div class="empty">${icon("play")}<p>No run yet for this project.</p><button class="btn primary" data-plan="new">${icon("play")} Run…</button>${entry.error ? `<div class="callout fail" role="alert">${esc(entry.error)}<button class="btn" data-plan="refresh">Retry</button><button class="btn" data-plan="dismiss-error">Dismiss</button></div>` : ""}</div>`;
    return;
  }
  const p = s.plan;
  const canAct = Boolean(server.session?.mutations_enabled);
  const steps = s.table?.steps || [];
  const rows = s.table?.rows || [];
  const waitingBy = new Map((s.waiting || []).map((w) => [w.row, w]));
  const unitsByCell = new Map();
  (s.units || []).forEach((u) => (u.rows || [u.row]).forEach((k) => (u.steps || []).forEach((st) => unitsByCell.set(`${k}\u0000${st}`, u))));
  const tot = {};
  rows.forEach((r) => steps.forEach((st) => { const v = r.cells[st]; if (v && v !== "skip") tot[v] = (tot[v] || 0) + 1; }));
  const all = Object.values(tot).reduce((a, b) => a + b, 0);
  const pct = all ? Math.round(((tot.done || 0) / all) * 100) : 0;
  const failedCells = (tot.failed || 0) + (tot.blocked || 0) + (tot.interrupted || 0) + (tot.cancelled || 0);
  const active = ["running", "paused", "interrupted"].includes(p.status);
  const primary = !canAct ? "" : p.status === "running" ? `<button class="btn sm" data-plan="pause" title="Pause: finishes the current turn, then stops">${icon("pause")} Pause</button>`
    : active ? `<button class="btn sm primary" data-plan="resume">${icon("play")} Resume</button>`
      : tot.todo ? `<button class="btn sm primary" data-plan="resume">${icon("play")} Run remaining</button>`
        : `<button class="btn sm primary" data-plan="new">${icon("play")} New run…</button>`;
  const counts = ["running", "failed", "blocked", "todo"].filter((k) => tot[k]).map((k) => `<span class="pc pc-${k}" title="${esc(CELL[k]?.label || k)}">${tot[k]} ${esc(CELL[k]?.label || k)}</span>`).join("");
  rememberSections(box);
  keepScroll(box, () => { box.innerHTML = `
    ${entry.error ? `<div class="callout fail" role="alert"><span>${esc(entry.error)}</span><button class="btn sm" data-plan="refresh">Retry</button><button class="btn sm" data-plan="dismiss-error">Dismiss</button></div>` : ""}
    ${runSwitcher(s)}
    <div class="sched-h">
      <select class="input sm" data-plan-pick aria-label="Run" title="${esc(`Run ${p.id} · ${phaseLabel(p)}`)}">${(s.plans || []).map((x) => `<option value="${esc(x.id)}" ${x.id === p.id ? "selected" : ""}>Run ${esc(x.id)} · ${esc(phaseLabel(x) + (x.working ? " · working" : ""))}</option>`).join("")}</select>
      ${p.start_at ? "" : `<span class="badge tone-${PLAN_TONE[p.status] || "muted"}" title="${esc(runnerTitle(s))}">${icon(p.status === "running" ? "sync" : p.status === "finished" ? "checkCircle" : "info", p.status === "running" && s.runner?.alive ? "spin" : "")}${esc(RUN_STATUS[p.status] || p.status)}</span>`}
      ${p.start_at ? `<span class="badge tone-warn" title="The runner waits until then (alfrd plan start-now runs it at once)">${icon("clock")}Scheduled · starts ${esc(p.start_at.replace("T", " ").slice(0, 16))}</span>${canAct ? `<button class="btn sm primary" data-plan="start-now">${icon("play")} Run now</button>` : ""}` : ""}
      <div class="plan-bar" title="${pct}% of planned cells done"><i style="width:${pct}%"></i></div><span class="tabular small">${tot.done || 0}/${all}</span>
      <span class="sched-counts">${counts}</span>
      <span class="grow"></span>
      ${!active ? `<a class="btn sm primary" href="#/results">View results</a><button class="btn sm" data-plan="logs">${icon("log")} View logs</button>` : ""}
      ${primary}
      ${s.loop?.phase === "awaiting_response" ? '<button class="btn sm primary" data-plan="handoffs">Submit response</button>' : ''}
      ${s.loop?.phase === "awaiting_review" ? '<button class="btn sm primary" data-plan="handoffs">Review response</button>' : ''}
      ${canAct && failedCells ? `<button class="btn sm" data-plan="retry" title="Failed, blocked, interrupted and cancelled cells back to todo, then resume">${icon("reset")} Retry ${failedCells}</button>` : ""}
      <button class="btn sm" data-plan="more" aria-haspopup="menu" title="Cancel, add a target, new run, usage, runner log …">${icon("more")} More</button>
    </div>
    <p class="sched-meta muted small"><span class="mono" title="Run CSV">${esc(p.csv)}</span> · ${esc(RUN_MODE[p.mode] || p.mode)} · concurrency ${esc(p.concurrency)} · ${esc(RUN_FAILURE[p.on_failure] || p.on_failure)} · ${esc(runnerTitle(s))}</p>
    ${s.loop ? `<p class="sched-meta"><b>Turn ${esc(s.loop.turn)} of ${esc(s.loop.turns)}${s.loop.iteration_unit === "turns" ? "" : ` · Iteration ${esc(s.loop.iteration)}/${esc(s.loop.iterations)}`} · ${esc(s.loop.agent || "ready")}${s.loop.roles?.length ? ` as ${esc(s.loop.roles.join(" + "))}` : ""}</b> · ${esc(RUN_PHASE[s.loop.phase] || "Queued")} · Model: ${esc(s.loop.model || (s.loop.requested_model ? `${s.loop.requested_model} (requested)` : "not reported"))} · ${esc(loopElapsed([{ ...s.loop, status: "running" }]))}</p><p class="muted small">Pause finishes the current turn. Cancel stops it now.</p>` : ""}
    <div class="sched-b dock-${esc(orderPrefs().dock)}${orderPrefs().folded ? " order-folded" : ""}">
      <section class="sched-grid">
        <h4>Targets × steps <span class="muted small">(click a cell: log, retry, skip)</span></h4>
        <div class="grid-scroll" role="region" aria-label="Targets by workflow steps" tabindex="0" data-scroll-key="${esc(`sched|${project}|${p.id}`)}"><table class="tbl plan-grid"><thead><tr><th>Target</th><th>Code / wd</th>${steps.map((st) => `<th title="${esc(st)}"><span class="mono">${esc(st.replace(/^avica_/, ""))}</span></th>`).join("")}</tr></thead><tbody>
          ${rows.map((r) => `<tr><td class="mono"><b>${esc(r.target)}</b>${waitingBy.has(r.key) ? ` <span class="badge tone-warn wait-chip" title="${esc(waitingBy.get(r.key).reason)}">${icon("hourglass")}waiting</span>` : ""}</td><td class="mono small muted">${esc([r.code, r.workdir].filter(Boolean).join(" / ") || "—")}</td>${steps.map((st) => {
            const v = r.cells[st] || "skip";
            const meta = CELL[v] || { label: v, tone: "muted", icon: "info", mark: "?" };
            const u = unitsByCell.get(`${r.key}\u0000${st}`);
            const nn = notesAt(ctx, project, { target: r.target, step: st });
            const tip = [meta.label, u?.started && `started ${u.started}`, u?.finished && `finished ${u.finished}`, u?.error, ...nn.map((n) => `✎ ${n.text.slice(0, 80)}`)].filter(Boolean).join("\n");
            return `<td class="cell c-${esc(v)}${nn.length ? " has-note" : ""}" data-cell="${esc(r.key)}" data-step="${esc(st)}" title="${esc(tip)}">${v === "running" ? icon("sync", "spin") : esc(meta.mark)}</td>`;
          }).join("")}</tr>`).join("")}
        </tbody></table></div>
      </section>
      <section class="sched-list" data-scroll-key="${esc(`order|${project}|${p.id}`)}">${orderList(s, project)}</section>
    </div>`; });
}

function unitRow(project, u, status) {
  const meta = CELL[status === "done" ? "done" : status] || CELL[u.status] || CELL.todo;
  const time = u.finished && u.started ? short((new Date(u.finished) - new Date(u.started)) / 1000)
    : u.status === "running" && u.started ? short((Date.now() - new Date(u.started)) / 1000) : u.estimate ? `~${short(u.estimate)}` : "";
  const tip = [u.code && `${u.code}${u.workdir ? ` / ${u.workdir}` : ""}`, u.started ? `started ${u.started.replace("T", " ")}` : u.eta_start && `starts ${eta(u.eta_start)}`,
    u.pid && `pid ${u.pid}@${u.host || ""}`, u.error].filter(Boolean).join("\n");
  return `<tr class="c-${esc(u.status || status)}" title="${esc(tip)}"><td><span class="mono"><b>${esc(u.target || "*")}</b></span> <span class="mono small muted">${esc((u.steps || [u.step]).join(", "))}</span>${u.error ? `<div class="fail-t small ellip">${esc(String(u.error).slice(0, 80))}</div>` : ""}</td>
    <td class="ord-st">${icon(meta.icon, u.status === "running" ? "spin" : "")}</td>
    <td class="tabular small">${esc(time)}</td>
    <td class="ord-a">${logButtons(project, u.log)}${usageButton(project, u)}</td></tr>`;
}

function orderList(s, project) {
  const units = s.units || [];
  const running = units.filter((u) => u.status === "running");
  const ended = units.filter((u) => u.status !== "running").slice().reverse();
  const failed = ended.filter((u) => u.status !== "done");
  const done = ended.filter((u) => u.status === "done");
  const queued = (s.queue || []).filter((q) => !running.some((u) => (u.rows || [u.row]).includes(q.row) && (u.steps || []).includes(q.step)));
  const prefs = orderPrefs();
  const sec = (title, n, body, open = true) => {
    const key = `${project}|${title}`;
    return n ? `<details class="sched-sec" data-sec="${esc(key)}" ${(secOpen.get(key) ?? open) ? "open" : ""}><summary><b>${title}</b> <span class="muted small">${n}</span></summary><table class="tbl small ord"><tbody>${body}</tbody></table></details>` : "";
  };
  const tally = [["running", running.length], ["queued", queued.length], ["failed", failed.length], ["done", done.length]]
    .filter(([, n]) => n).map(([k, n]) => `<span class="pc pc-${k === "queued" ? "todo" : k}">${n} ${k}</span>`).join("");
  return `<div class="ord-h"><h4>Run order</h4><span class="ord-tally">${tally}</span><span class="grow"></span>
      <button class="icon-btn xs" data-plan="order-dock" title="${prefs.dock === "side" ? "Dock at the bottom" : "Dock at the side"}" aria-label="Move the run order">${icon(prefs.dock === "side" ? "columns" : "sidebar")}</button>
      <button class="icon-btn xs" data-plan="order-fold" title="${prefs.folded ? "Show" : "Hide"} the list" aria-label="Fold the run order">${icon(prefs.folded ? "unfold" : "fold")}</button></div>
    <div class="ord-b">
    ${sec("Running", running.length, running.map((u) => unitRow(project, u, "running")).join(""))}
    ${sec("Queued", queued.length, queued.slice(0, 300).map((q) => unitRow(project, { ...q, steps: [q.step], status: "todo" }, "todo")).join(""), queued.length < 30)}
    ${sec("Failed / stopped", failed.length, failed.slice(0, 200).map((u) => unitRow(project, u, u.status)).join(""))}
    ${sec("Done", done.length, done.slice(0, 200).map((u) => unitRow(project, u, "done")).join(""), false)}
    ${units.length || queued.length ? "" : '<p class="muted small">Nothing has run yet.</p>'}</div>`;
}


export async function planAct(ctx, project, action, el, opts = {}) {
  if (action === "logs") { ctx.openLogs({ group: `plan:${planOf(project)?.plan?.id}` }); return; }
  if (action === "dismiss-error") { cache.get(project).error = null; ctx.update(); return; }
  const s = planOf(project);
  const id = s?.plan?.id;
  try {
    if (action === "task" || action === "agents") {
      const dialog = await import("./agent_settings_dialog.js");
      if (action === "task") return dialog.openTask(ctx, project);
      const text = ctx.state.trees?.[project]?.manifestText || ctx.state.workflowFile.text;
      return dialog.openAgentSettings(ctx, project, text, async (updated) => {
        if (activePlan(project)) throw new Error("Finish or cancel the current run before changing agent settings.");
        await ctx.saveManifest(project, updated);
        await ctx.refreshProject(project);
        ctx.toast("Agent and review settings saved", "ok");
      });
    }
    if (action === "handoffs") return (await import("./agent_dialog.js")).openHandoffs(ctx, project, id);
    if (action === "new") return openRunDialog(ctx, project, opts);
    if (action === "dry") return openRunDialog(ctx, project, { ...opts, dry: true });
    if (action === "refresh") return loadPlan(ctx, project);
    if (action === "usage") return (await loadUsage()).openUsageTable(ctx, project, s);
    if (action === "schedule") return null;
    if (action === "more") return moreMenu(ctx, project, el);
    if (action === "order-dock" || action === "order-fold") {
      const prefs = orderPrefs();
      if (action === "order-dock") prefs.dock = prefs.dock === "side" ? "bottom" : "side";
      else prefs.folded = !prefs.folded;
      saveOrderPrefs(prefs);
      const body = el?.closest(".sched-b");
      if (body) {
        body.className = `sched-b dock-${prefs.dock}${prefs.folded ? " order-folded" : ""}`;
        const list = body.querySelector(".sched-list");
        if (list && s) list.innerHTML = orderList(s, project);
      }
      return null;
    }
    if (!id) return null;
    if (action === "add-row") return openAddRowDialog(ctx, project);
    if (action === "cancel") {
      if (!window.confirm(`Cancel run ${id}? Running commands get SIGTERM (then SIGKILL).`)) return null;
      await server.planAction(project, id, "cancel", { confirm: true });
    } else if (action === "retry") {
      await server.planAction(project, id, "resume", { retry_failed: true });
    } else {
      await server.planAction(project, id, action);
    }
    ctx.toast(`Run ${id}: ${action} requested`, "ok");
  } catch (error) {
    ctx.showError(`Run action failed: ${error.message}`, () => planAct(ctx, project, action, el, opts), "workflow");
    ctx.toast(`Run: ${error.message}`, "fail");
  }
  return loadPlan(ctx, project);
}

/** "YYYY-MM-DDTHH:MM" in local time (datetime-local inputs). */
function localStamp(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Hold a step of a running plan until a clock time (or a delay); empty clears it. Applies to every target. */
async function setStart(ctx, project, planId, step) {
  const value = window.prompt(`Start ${step} at (all targets):\n02:00 = the next 02:00 · 2026-10-07 02:00 · +1h = after the previous step · empty = no wait`, "");
  if (value === null) return;
  try {
    await server.planTurnSet(project, planId, step, { after: value.trim() || null });
    ctx.toast(value.trim() ? `${step} starts at ${value.trim()}` : `${step}: start time cleared`, "ok");
    loadPlan(ctx, project);
  } catch (error) { ctx.toast(error.message, "fail"); }
}

export function cellMenu(ctx, project, anchor) {
  const s = planOf(project);
  const key = anchor.dataset.cell;
  const step = anchor.dataset.step;
  const u = (s?.units || []).slice().reverse().find((x) => (x.rows || [x.row]).includes(key) && (x.steps || []).includes(step));
  const row = s?.table?.rows?.find((r) => r.key === key);
  const v = row?.cells?.[step];
  const can = Boolean(server.session?.mutations_enabled) && v !== "running";
  const set = async (value) => {
    try {
      await server.planCells(project, s.plan.id, [{ row: key, step, value }]);
      if (value === "todo" && !["running"].includes(s.plan.status)) ctx.toast("Cell set to todo — Resume / Run remaining to start it", "ok");
      loadPlan(ctx, project);
    } catch (error) { ctx.toast(error.message, "fail"); }
  };
  ctx.menu(anchor, [
    { label: u?.log ? "Open log" : "No log yet", icon: "log", disabled: !u?.log, run: () => openLog(ctx, project, u.log) },
    { label: "Follow in Log Stream", icon: "log", disabled: !u?.log, run: () => dockLog(ctx, project, u.log) },
    { label: "Resource usage", icon: "graph", disabled: !u?.usage_file, run: () => loadUsage().then((m) => m.openUsage(ctx, project, u, () => unitById(project, u.id) || u)) },
    { label: "Retry (todo)", icon: "reset", disabled: !can || v === "todo", run: () => set("todo") },
    { label: "Start step at… (all targets)", icon: "clock", disabled: !can || !["running", "paused"].includes(s?.plan?.status), run: () => setStart(ctx, project, s.plan.id, step) },
    { label: "Skip", icon: "minus", disabled: !can || Boolean(s?.plan?.loop) || v === "skip", run: () => set("skip") },
    ...(u?.argv ? [{ label: "Copy command", icon: "copy", run: () => navigator.clipboard?.writeText(u.argv.join(" ")) }] : []),
    { label: "Notes…", icon: "file", run: () => ctx.openNotes(project, { target: row?.target, step, project_code: row?.code || undefined, workdir: row?.workdir || undefined }) },
  ]);
}


export async function openLog(ctx, project, rel) {
  try {
    await openFileFull(ctx, project, rel);
  } catch (error) {
    ctx.toast(`${rel}: ${error.message}`, "warn");
  }
}


function addRowWhen(status) {
  return status === "running"
    ? "Picked up on the runner's next pass if it's still active; otherwise use Run remaining."
    : "Use Resume / Run remaining to run it.";
}


export async function openAddRowDialog(ctx, project) {
  const s = planOf(project);
  const p = s?.plan;
  if (!p) return;
  const { mountStepPicker } = await loadPicker();
  const steps = s.table?.steps || [];
  const rows = s.table?.rows || [];
  ctx.modal(`
    <header class="modal-h"><h2>${icon("plus")} Add target · <span class="mono">${esc(p.csv)}</span></h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b run-dlg">
      <p class="muted small">Adds one target to the run CSV. Run is <b>${esc(p.status)}</b>. ${esc(addRowWhen(p.status))}</p>
      <div class="row gap wrap">
        <label class="field grow"><span>Target</span><input class="input mono" id="ar-target" placeholder="J0742+103"></label>
        <label class="field"><span>Project code</span><input class="input mono" id="ar-code" size="8" placeholder="BV019"></label>
        <label class="field"><span>Work dir</span><input class="input mono" id="ar-workdir" size="8" placeholder="(auto)"></label>
      </div>
      <label class="field"><span>FITS file names <span class="muted small">(comma or one per line)</span></span><textarea class="input mono" id="ar-files" rows="2" placeholder="a.idifits&#10;b.idifits"></textarea></label>
      <fieldset><legend>Steps <span class="muted small">(marked todo)</span></legend>
        <div id="ar-steps">${steps.length ? "" : '<p class="muted small">This run CSV has no step columns.</p>'}</div>
      </fieldset>
      <div id="ar-err" class="bad small"></div>
    </div>
    <footer class="modal-f row gap right"><button class="btn primary" id="ar-go" ${server.session?.mutations_enabled ? "" : "disabled title='Only from a browser on the same machine as alfrd serve'"}>${icon("plus")} Add</button></footer>`,
    (root, close) => {
      attachFits(ctx, project, root);
      const go = $("#ar-go", root);
      const canWrite = !go.disabled;
      const sync = (ids) => {
        go.disabled = !canWrite || !ids.length;
        if (canWrite) go.title = ids.length ? "" : "Select at least one step";
      };
      const picker = steps.length ? mountStepPicker($("#ar-steps", root), { steps, defs: defsOf(ctx, project), onChange: sync }) : null;
      if (!picker) sync([]);
      go.addEventListener("click", async () => {
        const target = $("#ar-target", root).value.trim();
        const code = $("#ar-code", root).value.trim();
        const err = $("#ar-err", root);
        err.textContent = "";
        if (!target) { err.textContent = "target is required"; return; }
        if (rows.some((r) => r.target === target && (r.code || "") === code)) {
          err.textContent = `${target}${code ? `@${code}` : ""} is already a target in this run`;
          return;
        }
        const chosen = picker ? picker.selected() : [];
        if (!chosen.length) { err.textContent = "select at least one step"; return; }
        try {
          await server.planAddRow(project, p.id, {
            target, code, workdir: $("#ar-workdir", root).value.trim(),
            files: $("#ar-files", root).value.trim(), steps: chosen,
          });
          ctx.toast(`${target}${code ? `@${code}` : ""} added to ${p.csv}`, "ok");
          close();
          loadPlan(ctx, project);
        } catch (error) {
          err.textContent = error.message;
        }
      });
    });
}


async function execInfo(project, target = null) {
  return server.executionInfo(project, target);
}

function selectionRows(ctx, project, info) {
  const fcol = info.files_column || "FILENAMES";
  return ctx.state.targets.filter((t) => t.project === project).map((t) => ({
    target: t.name,
    files: t.columns?.[fcol] || t.fitsidi || "",
    code: (t.codes || []).filter((c) => !c.auto).map((c) => c.code)[0] || (t.codes || [])[0]?.code || "",
  }));
}

export async function openRunDialog(ctx, project, { dry = false, only = null, targets = null, target = null, onStarted = null } = {}) {
  const selectedTarget = target || targets?.[0] || storage.get(`task:selected:${project}`, null) || ctx.target()?.name || null;
  let info;
  try { info = await execInfo(project, selectedTarget); } catch (error) { ctx.toast(`Run settings: ${error.message}`, "fail"); return; }
  if (info.error || !info.configured) {
    ctx.modal(`<header class="modal-h"><h2>${icon("play")} Start a run</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
      <div class="modal-b"><p>${info.error ? esc(info.error.message || info.error) : "No step has a command yet."}</p>
      <p class="muted small">Add an <code>entrypoint:</code> and point <code>execution.step_entrypoint</code> (or <code>workflows[].entrypoint</code>, or a step's <code>cmd:</code>) at it in alfrd.yaml. The avica template ships <code>avica-step</code>: <code>avica pipe run --t {target} --f {FILENAMES} {step}</code>.</p></div>`);
    return;
  }
  const steps = info.steps.map((s) => s.id);
  let mountStepPicker;
  try { ({ mountStepPicker } = await loadPicker()); } catch (error) { ctx.toast(`Run: ${error.message}`, "fail"); return; }
  const isLoop = info.steps.every((s) => s.iteration && s.handoff?.input);
  let sel = selectionRows(ctx, project, info), preferNew = Boolean(only || targets);
  if (isLoop) {
    const defaults = (await import("../data/agent_settings.js")).loopRunSelection(info, selectedTarget);
    sel = defaults.rows; preferNew = true;
  }
  const hasCsv = info.plan_csv_exists && info.table?.rows?.length;
  const st = info.settings;
  const current = ctx.target();
  ctx.modal(`
    <header class="modal-h"><h2>${icon("play")} ${dry ? "Preview a run" : "Start a run"} · ${esc(ctx.projectName(project))}</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b run-dlg">
      <p class="muted small">Commands from alfrd.yaml run in <code>${esc(info.cwd)}</code> on the server. Runs continue after Studio or <code>alfrd serve</code> closes; reopening reconnects.</p>
      <fieldset><legend>Targets <button class="link-btn small" id="run-import" title="Add sources and their FITS file names to the targets CSV">${icon("upload")} Import targets CSV…</button></legend>
        ${hasCsv && !isLoop ? `<label class="check"><input type="radio" name="src" value="csv" ${preferNew ? "" : "checked"}> <span>Use <b class="mono">${esc(info.plan_csv_name)}</b> as it is (${info.table.rows.length} rows; edit it in any editor, even while it runs)</span></label>` : ""}
        <label class="check"><input type="radio" name="src" value="sel" ${hasCsv && !preferNew ? "" : "checked"}> <span>New run from these targets${hasCsv ? ` <em class="muted">(${isLoop ? "keeps other tasks in" : "replaces"} ${esc(info.plan_csv_name)})</em>` : ""}:</span></label>
        <div class="run-targets" id="run-targets">
          <div class="row gap small"><button class="link-btn" data-all="1">all</button><button class="link-btn" data-all="0">none</button><input class="input sm grow" id="run-filter" placeholder="Filter targets"></div>
          <table class="tbl small"><thead><tr><th></th><th>Target</th><th>${esc(info.files_column)}</th><th>${esc(info.code_column)}</th></tr></thead><tbody>
          ${sel.map((r, i) => `<tr data-name="${esc(r.target.toLowerCase())}"><td><input type="checkbox" data-i="${i}" ${(isLoop || (targets ? targets.includes(r.target) : !current || current.name === r.target)) ? "checked" : ""}></td><td class="mono">${esc(r.target)}</td><td><input class="input sm mono" data-files="${i}" value="${esc(r.files)}" placeholder="a.idifits,b.idifits"></td><td><input class="input sm mono" data-code="${i}" value="${esc(r.code)}" size="7"></td></tr>`).join("") || '<tr><td colspan="4" class="muted">No targets in this project yet — add targets to the run CSV, or use <code>alfrd plan new --targets …</code>.</td></tr>'}
          </tbody></table>
        </div>
      </fieldset>
      <fieldset><legend>Steps <span class="muted small">(queued in the new run)</span></legend>
        <div id="run-steps"></div>
      </fieldset>
      <div class="row gap wrap">
        <label class="field" ${isLoop ? "hidden" : ""}><span>Mode</span><select class="input" id="run-mode">${["step", "target", "batch"].map((m) => `<option value="${m}" ${st.mode === m ? "selected" : ""} ${m !== "step" && !info.settings[`${m}_entrypoint`] ? "disabled" : ""}>${esc(RUN_MODE[m])}</option>`).join("")}</select><small class="muted">Order of work. Batch sends all selected work to one command.</small></label>
        <label class="field" ${isLoop ? "hidden" : ""}><span>Targets at once</span><input class="input" id="run-conc" type="number" min="1" max="64" value="${esc(st.concurrency)}"></label>
        <label class="field" ${isLoop ? "hidden" : ""}><span>On failure</span><select class="input" id="run-fail">${["stop_target", "continue", "stop_plan"].map((m) => `<option value="${m}" ${st.on_failure === m ? "selected" : ""}>${esc(RUN_FAILURE[m])}</option>`).join("")}</select><small class="muted">What happens after a step fails.</small></label>
        <label class="field"><span>Start at <span class="muted">(optional)</span></span><input class="input" id="run-at" type="datetime-local" min="${esc(localStamp(new Date()))}" max="${esc(localStamp(new Date(Date.now() + 7 * 864e5)))}"><small class="muted">Empty starts now. Up to 7 days ahead; the machine must be on then.</small></label>
        <label class="field"><span>Status from</span><input class="input" value="${esc({ exit_code: "Command exit code", result_csv: "Results CSV", both: "Exit code and results CSV" }[st.status_from] || st.status_from)}" disabled title="execution.status_from in alfrd.yaml"></label>
      </div>
      <div id="run-error" class="callout fail" role="alert" hidden></div>
      <div id="run-preview" class="run-preview"></div>
    </div>
    <footer class="modal-f row gap right"><button class="btn" id="run-dry">${icon("list")} Preview commands</button>${dry ? "" : `<button class="btn primary" id="run-go" ${server.session?.mutations_enabled ? "" : "disabled title='Only from a browser on the same machine'"}>${icon("play")} Start run</button>`}</footer>`, (root, close) => {
    attachFits(ctx, project, root);
    const go = $("#run-go", root);
    const canStart = go && !go.disabled;
    const picker = mountStepPicker($("#run-steps", root), {
      steps, defs: defsOf(ctx, project), selected: isLoop ? steps : only, remember: isLoop || only ? null : `runsteps:${project}`,
      onChange: (ids) => {
        if (!canStart) return;
        const needSteps = ($("input[name=src]:checked", root)?.value || "sel") === "sel";
        go.disabled = needSteps && !ids.length;
        go.title = go.disabled ? "Select at least one step" : "";
      },
    });
    $$("input[name=src]", root).forEach((r) => r.addEventListener("change", () => {
      if (canStart) go.disabled = r.value === "sel" && r.checked && !picker.selected().length;
    }));
    const payload = () => {
      const src = $("input[name=src]:checked", root)?.value || "sel";
      const chosen = picker.selected();
      const out = isLoop ? {} : { mode: $("#run-mode", root).value, concurrency: Number($("#run-conc", root).value) || 1, on_failure: $("#run-fail", root).value };
      const at = $("#run-at", root)?.value;
      if (at) out.start_at = at.replace("T", " ");
      if (src === "sel") {
        if (isLoop && chosen.length !== steps.length) throw new Error("Agent loops require all turns. Use Agents & review to select review checkpoints.");
        out.rows = $$("input[data-i]", root).filter((c) => c.checked).map((c) => {
          const i = c.dataset.i;
          return { ...sel[i], files: $(`[data-files="${i}"]`, root).value.trim(), code: $(`[data-code="${i}"]`, root).value.trim() };
        });
        out.steps = chosen;
      }
      if (isLoop) {
        if (out.rows?.length !== 1) throw new Error("Select exactly one task for an agent loop.");
        out.target = out.rows[0].target;
      }
      return out;
    };
    const showPreview = async () => {
      const box = $("#run-preview", root);
      box.innerHTML = '<p class="muted small">Checking…</p>';
      try {
        const body = payload();
        if (body.rows && !body.rows.length) throw new Error("select at least one target");
        if (body.rows && !body.steps.length) throw new Error("select at least one step");
        const r = await server.planPreview(project, body);
        box.innerHTML = `<h5>${r.units.length} command(s), in order ${r.errors.length ? `<span class="badge tone-fail">${r.errors.length} problem(s)</span>` : '<span class="badge tone-ok">ready</span>'}</h5>
          ${r.conflicts?.text ? `<p class="muted small" title="${esc(r.conflicts.serialize_on?.length ? `Rows run one at a time when they share: ${r.conflicts.serialize_on.join(r.conflicts.serialize_match === "all" ? " and " : " or ")}` : "No execution.serialize_on (only the work dir lock)")}">${icon("list")} ${esc(r.conflicts.text)}</p>` : ""}
          ${r.missing_step_columns?.length ? `<p class="muted small">Not in the run CSV (not run): ${esc(r.missing_step_columns.join(", "))}</p>` : ""}
          <ol class="cmd-list">${r.units.slice(0, 200).map((u) => `<li class="${u.error ? "bad" : ""}"><b class="mono">${esc(u.target || "*")}</b> · <span class="mono small">${esc(u.steps.join(", "))}</span><pre class="code">${esc(u.error || u.argv.join(" "))}</pre></li>`).join("")}</ol>
          ${r.units.length > 200 ? `<p class="muted small">+${r.units.length - 200} more</p>` : ""}`;
        return r;
      } catch (error) {
        box.innerHTML = `<p class="bad">${esc(error.message)}</p>`;
        return null;
      }
    };
    $("#run-dry", root).addEventListener("click", showPreview);
    $("#run-import", root).addEventListener("click", () => { close(); ctx.openTargets("import", project); });
    $("#run-filter", root)?.addEventListener("input", (e) => {
      const q = e.target.value.trim().toLowerCase();
      $$("tr[data-name]", root).forEach((tr) => { tr.hidden = Boolean(q) && !tr.dataset.name.includes(q); });
    });
    $$("[data-all]", root).forEach((b) => b.addEventListener("click", () => $$("input[data-i]", root).forEach((c) => {
      if (!c.closest("tr").hidden) c.checked = b.dataset.all === "1";
    })));
    go?.addEventListener("click", async () => {
      const r = await showPreview();
      if (!r) return;
      if (r.errors.length && !window.confirm(`${r.errors.length} problem(s) — those cells will fail. Start anyway?`)) return;
      const body = payload();
      if (body.rows && hasCsv && !isLoop) {
        if (!window.confirm(`Replace ${info.plan_csv_name} (${info.table.rows.length} rows) with ${body.rows.length} target(s)?`)) return;
        body.overwrite = true;
      }
      try {
        const res = await server.planStart(project, body);
        ctx.toast(res.plan.start_at ? `Run ${res.plan.id} scheduled for ${res.plan.start_at.replace("T", " ")}` : `Run ${res.plan.id} started`, "ok");
        ctx.log("info", `Run ${res.plan.id} started (${res.plan.mode}, ${res.plan.csv}).`, "plan");
        close();
        onStarted?.(res.plan);
        loadPlan(ctx, project, { id: res.plan.id });
      } catch (error) {
        const alert = $("#run-error", root);
        alert.hidden = false; alert.textContent = `Run could not start: ${error.message}. Try Start run again.`;
        ctx.toast(`Not started: ${error.message}`, "fail");
      }
    });
    if (dry) showPreview();
  }, "wide");
}

function loopElapsed(units) {
  const current = [...units].reverse().find((u) => u.status === "running") || units.at(-1);
  if (!current?.started) return "not started";
  const seconds = Math.max(0, Math.floor(((current.finished ? Date.parse(current.finished) : Date.now()) - Date.parse(current.started)) / 1000));
  return Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed` : "";
}


export async function openLinkedRun(ctx, project, plan, unit = null) {
  const status = await loadPlan(ctx, project, { id: plan });
  if (ctx.state.selectedProject && ctx.state.selectedProject !== project) return;
  if (status?.plan?.id !== plan) { ctx.toast?.("This run is no longer available. Refresh run history.", "warn"); return; }
  if (!unit) return;
  const item = status.units?.find((u) => u.id === unit);
  if (!item) { ctx.toast?.("This turn is no longer available.", "warn"); return; }
  if (item.handoff) {
    const { openHandoffs } = await import("./agent_dialog.js");
    await openHandoffs(ctx, project, plan, { unit });
  } else if (item.log) await openLog(ctx, project, item.log);
}

export function releasePlanPin(project) {
  const entry = cache.get(project);
  if (!entry) return;
  delete entry.pinned;
  delete entry.want;
  entry.pinActive = false;
}
