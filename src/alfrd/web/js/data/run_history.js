// Agent-loop run history: one summary per plan, for the Overview's progress sheet
// and the CSV / JSON export (schema v1). Loaded with import(); no DOM, so node tests
// can import it. A portable report, not a re-importable execution archive.

import { toCsv } from "../utils/csv_parser.js";

export const HISTORY_FIELDS = ["project_id", "project_name", "run_id", "run_label", "status", "created_at", "started_at",
  "finished_at", "runtime_seconds", "iterations", "completed_turns", "total_turns"];
const ENDED = ["finished", "failed", "cancelled"];

/**
 * A timestamp with an offset becomes UTC ISO 8601. One without (server-local, zone not
 * recorded) or an unparseable one is null: the browser's timezone is not the server's.
 */
export function isoTime(value) {
  if (!value) return null;
  const s = String(value);
  return /(?:Z|[+-]\d\d:?\d\d)$/i.test(s) && Number.isFinite(Date.parse(s)) ? new Date(s).toISOString() : null;
}

/** Turn cells by state: the saved counts of an ended plan, else every row of the run table (never units[-200:]). */
function turnCounts(status) {
  const p = status.plan || {};
  if (ENDED.includes(p.status) && p.counts) return p.counts;
  if (status.table?.error) return null;
  const counts = {};
  (status.table?.rows || []).forEach((r) => Object.values(r.cells || {}).forEach((v) => { if (v && v !== "skip") counts[v] = (counts[v] || 0) + 1; }));
  return counts;
}

/**
 * Summary of one loop plan from planStatus(project, id) and its /handoffs (every turn).
 * Unknown values are null: no finish time while a run is active, no live elapsed time
 * presented as a runtime, no title invented (label is "Run <id>").
 */
export function runSummary(project, projectName, status, handoffs = []) {
  const p = status.plan;
  const started = handoffs.map((h) => h.started).filter(Boolean).sort()[0] || null;
  const finished = ENDED.includes(p.status) ? p.runner?.stopped || null : null;
  const span = started && finished ? (Date.parse(finished) - Date.parse(started)) / 1000 : NaN;
  const counts = turnCounts(status);
  return {
    project_id: project, project_name: projectName, run_id: p.id, run_label: `Run ${p.id}`, status: p.status ?? null,
    created_at: isoTime(p.created), started_at: isoTime(started), finished_at: isoTime(finished),
    runtime_seconds: Number.isFinite(span) && span >= 0 ? Math.round(span) : null,
    iterations: p.loop?.iterations ?? null, completed_turns: counts ? counts.done || 0 : null, total_turns: p.steps?.length ?? null,
    ran: Boolean(started), // not exported: a turn has started, whatever its timestamp's zone
  };
}

/** Read one run (plan + handoffs). Non-loop plans give null. */
export async function loadRunSummary(api, project, projectName, id, status = null) {
  status ||= await api.planStatus(project, id);
  if (!status.plan?.loop) return null;
  const { handoffs } = await api.handoffs(project, status.plan.id);
  return runSummary(project, projectName, status, handoffs);
}

/**
 * Every known loop run of `projects` ([{id, name}]), newest first per project, read when
 * called (a frozen snapshot: later polls cannot mix in). Any failed read throws, so no
 * partial file is ever downloaded.
 */
export async function collectHistory(api, projects) {
  const runs = [];
  for (const { id, name } of projects) {
    const latest = await api.planStatus(id);
    for (const ref of latest.plans || []) {
      const run = await loadRunSummary(api, id, name, ref.id, ref.id === latest.plan?.id ? latest : null);
      if (run) runs.push(run);
    }
  }
  return runs;
}

// Spreadsheets run cells starting with = + - @ (or a tab / CR) as formulas: quote them as text.
const plain = (v) => (typeof v === "string" && /^[=+\-@\t\r]/.test(v) ? `'${v}` : v);

export function historyCsv(runs) {
  return toCsv([HISTORY_FIELDS, ...runs.map((r) => HISTORY_FIELDS.map((f) => plain(r[f])))]);
}

export function historyJson(runs, projectIds, now = new Date()) {
  return `${JSON.stringify({
    schema_version: 1, exported_at: now.toISOString(), scope: { project_ids: projectIds },
    runs: runs.map((r) => Object.fromEntries(HISTORY_FIELDS.map((f) => [f, r[f] ?? null]))),
  }, null, 2)}\n`;
}

/** alfrd-run-history-<project-or-all>-<YYYYMMDD>.<ext> with a filename-safe project part. */
export function historyFilename(scope, ext, now = new Date()) {
  const part = String(scope || "").toLowerCase().replace(/\.{2,}/g, "-").replace(/[^a-z0-9._-]+/g, "-").replace(/-{2,}/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "project";
  return `alfrd-run-history-${part}-${now.toISOString().slice(0, 10).replace(/-/g, "")}.${ext}`;
}
