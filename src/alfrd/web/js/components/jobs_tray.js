// Jobs tray (loaded on first use): every active run across projects, with Open project
// and View log. Switching projects never stops a run or its log subscriptions.

import { esc, icon, short, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";
import { activeJobs } from "./plans.js";
import { dockLog } from "./logview.js";

const TONE = { Running: "run", Waiting: "warn", Paused: "warn", Interrupted: "warn" };

function elapsedOf(s) {
  const started = (s.units || []).map((u) => u.started).filter(Boolean).sort()[0] || s.plan.created || s.plan.started;
  const t = started ? (Date.now() - Date.parse(started)) / 1000 : NaN;
  return Number.isFinite(t) ? short(Math.max(0, t)) : "—";
}

/** The log a job is writing now (its running unit), else the runner log. */
function currentLog(s) {
  const u = [...(s.units || [])].reverse().find((x) => x.status === "running" && x.log);
  return u?.log || `.alfrd/plans/${s.plan.id}/runner.log`;
}

/** Rows for the tray; exported for tests. Duplicate project names get their root label. */
export function jobRows(ctx, jobs = activeJobs()) {
  const names = jobs.map((j) => ctx.projectName(j.project));
  const rows = jobs.map((j, i) => {
    const s = j.status;
    const dup = names.filter((n) => n === names[i]).length > 1;
    return {
      project: j.project,
      name: names[i],
      root: dup ? String(ctx.state.scans?.[j.project]?.root || j.project).split("/").slice(-2).join("/") : "",
      run: s.plan.id,
      workflow: s.plan.workflow || s.loop?.workflow || "",
      status: ctx.jobLabel ? ctx.jobLabel(s) : s.plan.status,
      elapsed: elapsedOf(s),
      log: currentLog(s),
    };
  });
  const p = server.pluginJob;
  if (p?.status === "running") rows.push({ name: `Plugin: ${{ install: "installing", "install-pinned": "installing", update: "updating", remove: "removing" }[p.action]} ${p.target}`, run: p.id, status: "Running", elapsed: "", plugin: true });
  return rows;
}

export function openJobs(ctx) {
  loadCss("css/lazy.css");
  const draw = (root) => {
    const rows = jobRows(ctx);
    root.querySelector("#jobs-list").innerHTML = rows.length ? rows.map((r) => `<li class="job-row">
        <div class="job-main"><b>${esc(r.name)}</b>${r.root ? ` <span class="muted small mono">${esc(r.root)}</span>` : ""}
          <div class="muted small"><span class="mono">Run ${esc(r.run)}</span>${r.workflow ? ` · ${esc(r.workflow)}` : ""} · ${esc(r.elapsed)}</div></div>
        <span class="badge tone-${TONE[r.status] || "muted"}">${icon(r.status === "Running" ? "sync" : "hourglass")}${esc(r.status)}</span>
        ${r.plugin ? '<button class="btn sm" data-plugin-open>Open Plugins</button>' : `<button class="btn sm" data-job-open="${esc(r.project)}">${icon("folder")} Open project</button>
        <button class="btn sm" data-job-log="${esc(r.log)}" data-job-project="${esc(r.project)}">${icon("log")} View log</button>`}</li>`).join("")
      : '<li class="muted">No active runs.</li>';
  };
  ctx.modal(`<header class="modal-h"><h2>${icon("play")} Jobs</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p class="muted small">Runs keep going when you switch projects. View log follows it in the Log Stream without changing the open project.</p><ul class="jobs-list" id="jobs-list"></ul></div>`, (root, close) => {
    draw(root);
    root.addEventListener("click", (e) => {
      if (e.target.closest("[data-plugin-open]")) {
        close(); document.querySelector("#btn-settings")?.click();
        setTimeout(() => document.querySelector('.set-tabs [data-tab="plugins"]')?.click(), 0); return;
      }
      const open = e.target.closest("[data-job-open]");
      if (open) { close(); ctx.openRun(open.dataset.jobOpen); return; }
      const log = e.target.closest("[data-job-log]");
      if (log) { close(); dockLog(ctx, log.dataset.jobProject, log.dataset.jobLog); }
    });
  });
}
