import { activityRows } from "./chat.js";

export const islandKey = (t) => JSON.stringify([t.request, t.row]);
export function ownerSlot(owner = "") {
  return [...String(owner)].reduce((hash, c) => (hash * 31 + c.codePointAt(0)) >>> 0, 0) % 2 ? "sky" : "purple";
}
export const isTask = (t) => !t.kind || t.kind === "task";
const reported = (n) => n != null && n !== "" && Number.isFinite(Number(n));
const terminal = (s) => ["done", "failed", "blocked", "cancelled", "stopped"].includes(s);

/** Use recorded task totals, without adding attempt totals a second time. */
export function taskMetrics(task, attempts = [], now = Date.now()) {
  const last = attempts.at(-1) || {};
  const start = task.started_ts != null ? Number(task.started_ts) * 1000 : Date.parse(task.started || last.started);
  const end = task.finished_ts != null ? Number(task.finished_ts) * 1000 : Date.parse(task.finished || last.finished);
  const seconds = reported(task.seconds) ? Number(task.seconds)
    : Number.isFinite(start) && (Number.isFinite(end) || !terminal(task.status)) ? Math.max(0, ((Number.isFinite(end) ? end : now) - start) / 1000)
    : reported(last.seconds) ? Number(last.seconds) : null;
  const cost = task.usage?.total_cost_usd ?? task.total_cost_usd ?? task.cost_usd;
  return { seconds, cost: reported(cost) ? Number(cost) : null };
}

export function taskIslands(state, items, request) {
  const groups = new Map();
  for (const task of state?.tasks || []) if (task.request === request && isTask(task)) groups.set(islandKey(task), { ...task, attempts: [] });
  for (const attempt of activityRows(state, items, request)) {
    if (!isTask(attempt)) continue;
    const key = islandKey(attempt);
    if (!groups.has(key)) groups.set(key, { ...attempt, attempts: [] });
    groups.get(key).attempts.push(attempt);
  }
  return [...groups.values()].map((task) => ({ ...task,
    dependencies: task.depends_on ?? task.task?.depends_on ?? task.task?.needs ?? task.needs ?? [],
    metrics: taskMetrics(task, task.attempts) }));
}

export function islandProgress(tasks) {
  const count = (status) => tasks.filter((t) => t.status === status).length;
  return { total: tasks.length, done: count("done"), failed: count("failed"), blocked: count("blocked"), cancelled: count("cancelled") + count("stopped") };
}

/** Closed output never asks the shared subscription for enlarged tails. */
export function outputUnits(rows, open) {
  return open ? [...rows].filter((r) => r.output.open && r.data.unit).map((r) => [r.data.unit, r.lines]) : [];
}
