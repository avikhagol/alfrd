// Observer-only presentation helpers. No mutation or current-run inference in replay.
export const stateWord = (state) => ({ running: 'working', idle: 'idle', blocked: 'waiting', stuck: 'possibly stuck', failed: 'failed', done: 'done' })[state] || state;
export function bounds(state) {
  const start = Date.parse(state.coverage.from || state.clock.at);
  const end = Math.max(start + 1000, Date.parse(state.clock.mode === 'live' ? state.clock.server_now : state.coverage.to || state.clock.server_now));
  return [start, end];
}
export function feedSentence(e) {
  if (e.text && e.text !== String(e.kind).replaceAll('.', ' ').replaceAll('_', ' ')) return e.text;
  const who = e.agent || 'An agent', task = e.task || 'a task';
  if (e.severity === 'error' && e.kind.endsWith('.finished')) return `${e.agent || e.request || 'The task'} could not finish ${e.task || 'the work'}.`;
  const words = {
    'task.created': `${task} was added for ${who}.`, 'task.started': `${who} started ${task}.`,
    'task.finished': `${who} finished ${task}.`, 'task.failed': `${who} could not finish ${task}.`,
    'task.blocked': `${task} is waiting for help.`, 'task.stuck': `${task} may be stuck.`,
    'task.reassigned': `${task} moved to ${who}.`, 'handoff.published': `${who} published outputs for ${task}.`,
    'decision.needed': `${e.request || 'A request'} needs your decision.`, 'decision.resolved': 'A decision was made.',
    'turn.started': `${who} began a turn on ${task}.`, 'turn.finished': `${who} ended a turn on ${task}.`,
    'turn.failed': `${who}'s turn on ${task} stopped.`, 'turn.retrying': `${who} is retrying ${task}.`,
    'request.finished': `${e.request} finished.`, 'request.failed': `${e.request} did not finish.`,
  };
  return words[e.kind] || `${String(e.kind).replaceAll('.', ' ').replaceAll('_', ' ')}${e.agent ? ` (${who})` : ''}.`;
}
export function filterFeed(events, { agent = '', type = '', errors = false, search = '' } = {}) {
  return events.filter((e) => (!agent || e.agent === agent) && (!type || e.kind.split('.')[0] === type)
    && (!errors || e.severity === 'error') && (!search || feedSentence(e).toLowerCase().includes(search.toLowerCase())))
    .sort((a, b) => b.seq - a.seq);
}

const ACTIVE = new Set(["preparing", "planning", "running", "needs_decision", "summarizing"]);
export const requestTitle = (r, messages = []) => r.title || messages.find((m) => m.role === "user" && m.request === r.id)?.text?.split("\n")[0] || `Request ${Number(r.id?.replace(/^r/, "")) || r.id}`;
export const defaultRequest = (requests, wanted) => requests.find((r) => r.id === wanted) || [...requests].reverse().find((r) => ACTIVE.has(r.status)) || requests.at(-1) || null;
export function runRows(state) {
  const rows = state?.view?.tasks || [];
  return { tasks: rows.filter((t) => t.kind === "task"), phases: rows.filter((t) => t.kind !== "task") };
}
export const taskTitle = (t) => t?.task?.title || t?.title || "Untitled task";
export const waitingFor = (task, byRow) => (task.depends_on || []).map((id) => byRow.get(id) || byRow.get(`${task.request}.${id}`)).filter((t) => t && t.status !== "done");
export const statusLabel = (s) => ({ pending: "Not started", queued: "Not started", blocked: "Needs you", running: "Running", done: "Done", failed: "Failed", cancelled: "Cancelled" })[s] || String(s || "Not started").replaceAll("_", " ");
export const statusIcon = (s) => ({ running: "◐", done: "✓", blocked: "⚑", failed: "✕", cancelled: "—" })[s] || "○";
export function activitySentence(task, byRow = new Map()) {
  const e = task.latest;
  if (!e) { const deps = waitingFor(task, byRow); return deps.length ? `Waiting for ${deps.map(taskTitle).join(", ")}` : statusLabel(task.status); }
  const words = { "task.created": "Queued", "task.started": `Started attempt ${e.attempt || task.attempts || 1}`, "turn.started": `Started attempt ${e.attempt || task.attempts || 1}`, "turn.retrying": "Retrying", "task.blocked": "Blocked", "task.failed": "Failed", "turn.failed": "Failed", "task.finished": "Finished", "turn.finished": "Finished", "handoff.published": "Published its output", "task.reassigned": "Reassigned" };
  return `${words[e.kind] || e.kind.replaceAll(".", " ")}${e.at ? ` · ${new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}`;
}
export function elapsed(task, segments = [], clock) {
  const segment = segments.findLast((s) => s.task === task.row && s.open);
  if (!segment) return "";
  const seconds = Math.max(0, (Date.parse(typeof clock === "string" ? clock : clock.at) - Date.parse(segment.start)) / 1000);
  return `running ${seconds < 60 ? `${Math.floor(seconds)}s` : `${Math.floor(seconds / 60)}m`} (this attempt)`;
}
export function requestStatusWord(request, rows, replay = false) {
  const tasks = Array.isArray(rows) ? rows : rows.tasks;
  const allRows = Array.isArray(rows) ? rows : [...rows.tasks, ...rows.phases];
  if (replay) return allRows.some((t) => t.status === "running") ? "Running at this time" : tasks.length && tasks.every((t) => t.status === "done") ? "Done at this time" : `${tasks.filter((t) => t.status === "done").length}/${tasks.length} done at this time`;
  if (request?.status === "failed" && !tasks.length) return "Planning failed";
  return ({ planning: "Planning", preparing: "Planning", needs_decision: "Needs you", summarizing: "Summarizing" })[request?.status] || statusLabel(request?.status);
}
export function attentionFor(attention, request) {
  const items = attention?.items || [];
  return { selected: items.filter((i) => i.request === request), other: items.filter((i) => i.request !== request) };
}
export function costText(usage = {}, maxCost, warnings = []) {
  const value = usage?.total_cost_usd, budget = Number(maxCost) > 0 ? Number(maxCost) : null;
  if (value == null || !Number.isFinite(value)) return { text: "Cost unavailable", value: null, budget, ratio: null, tone: "", partial: false };
  const partial = usage.complete === false || warnings.includes("legacy_usage_not_sampled"), amount = `$${value.toFixed(2)}`, limit = budget == null ? "" : `$${budget}`;
  const ratio = budget ? value / budget : null;
  return { text: partial ? `At least ${amount}${limit ? ` of ${limit}` : " reported"}` : `${amount}${limit ? ` / ${limit}` : ""}`, value, budget, ratio, tone: ratio >= 1 ? "fail" : ratio >= .8 ? "warn" : "", partial };
}
export function warningSentences(codes = []) {
  const result = { cost: [], checklist: [], summary: [] };
  for (const code of new Set(codes)) {
    if (code === "legacy_usage_not_sampled") result.cost.push("Older runs did not record every cost sample, so this is a lower bound.");
    else if (code === "legacy_history_partial") result.checklist.push("Some older activity was not recorded; times and activity may be incomplete.");
    else if (code === "event_sequence_gap") result.checklist.push("Some events are missing from the history.");
    else result.summary.push(code === "before_first_event" ? "This time is before the first recorded event." : `Some history is unavailable (${code}).`);
  }
  return result;
}
export function homeSections(sessions) {
  const groups = { needs: [], running: [], recent: [] };
  for (const s of sessions) groups[s.pending_user > 0 ? "needs" : s.active_requests > 0 ? "running" : "recent"].push(s);
  const time = (s) => Date.parse(s.last_message?.at) || Date.parse(s.created) || 0;
  for (const list of Object.values(groups)) list.sort((a, b) => time(b) - time(a));
  groups.recent = groups.recent.slice(0, 20); return groups;
}
