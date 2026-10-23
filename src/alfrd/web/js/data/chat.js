// Project chat, pure helpers (no DOM): @-mentions, the task graph for the canvas, decisions,
// task actions, progress and budget. Used by components/chat_view.js and components/chat_settings.js.
// The server is the authority (alfrd/chat.py); these mirror its rules so the UI can explain them early.

export const AGENT_KEY = /^[a-z0-9][a-z0-9_-]{0,39}$/;
const MENTION = /^@([A-Za-z0-9][A-Za-z0-9_-]{0,39})(?:\s+|$)([\s\S]*)$/;
export const TERMINAL = ["done", "failed", "cancelled", "blocked", "stopped"];
export const RETRYABLE = ["failed", "blocked", "cancelled", "stopped"];
export const REQUEST_ACTIVE = ["planning", "running", "needs_decision", "summarizing"];

/** Mirrors chat.DEFAULTS (the presets the chat: block edits). */
export const DEFAULTS = {
  orchestrator: "claude", orchestrator_model: "sonnet", escalation_model: "opus", final_summary: false, warn_task_cost_usd: null,
  concurrency: 2, history_messages: 8,
  bounds: { max_depth: 2, max_retries: 1, max_cost_usd: 5, max_seconds: 7200, max_tasks: 20, max_replans: 3 },
  agents: {
    claude: { label: "Claude (Opus)", entrypoint: "claude", model: "opus", roles: ["orchestration"], lean: true },
    sonnet: { label: "Claude (Sonnet)", entrypoint: "claude", model: "sonnet", roles: ["test planning", "verification", "failure analysis"], lean: true },
    codex: { label: "GPT (Codex)", entrypoint: "codex", roles: ["design", "development"] },
    agy: { label: "Antigravity", entrypoint: "agy", adapter: "generic", roles: ["research", "image generation"] },
  },
};

// --- @-mentions ------------------------------------------------------------------

/** A leading `@agent` (the only form the server sends straight to an agent): {agent, body} | {error} | null. */
export function parseMention(text, agents = {}) {
  const m = MENTION.exec(String(text || "").trim());
  if (!m) return null;
  const agent = m[1].toLowerCase();
  if (!(agent in agents)) return { error: `Unknown agent @${m[1]} (agents: ${Object.keys(agents).map((a) => `@${a}`).join(", ")})` };
  return { agent, body: m[2].trim() };
}

/** The `@word` being typed at the start of the message, when the caret is inside it: {start, end, query} | null. */
export function mentionAt(text, caret) {
  const t = String(text || "");
  const lead = t.length - t.trimStart().length;
  if (t[lead] !== "@") return null;
  const m = /^@[A-Za-z0-9_-]*/.exec(t.slice(lead));
  const end = lead + m[0].length;
  if (caret < lead + 1 || caret > end) return null;
  return { start: lead, end, query: t.slice(lead + 1, end).toLowerCase() };
}

/** Agents for the autocomplete list: prefix matches first, then key/label/role matches. */
export function agentOptions(agents = {}, query = "", orchestrator = null) {
  const q = String(query || "").toLowerCase();
  return Object.entries(agents).map(([key, a]) => {
    const roles = (a.roles || []).join(", ");
    const hay = `${key} ${a.label || ""} ${roles}`.toLowerCase();
    const rank = !q ? 0 : key.startsWith(q) ? 0 : (a.label || "").toLowerCase().startsWith(q) ? 1 : hay.includes(q) ? 2 : -1;
    return { key, label: a.label || key, roles, orchestrator: key === orchestrator, rank };
  }).filter((o) => o.rank >= 0).sort((a, b) => a.rank - b.rank || a.key.localeCompare(b.key));
}

/** Replace the typed `@word` with `@key `; the new text and caret. */
export function applyMention(text, at, key) {
  const t = String(text || "");
  const insert = `@${key} `;
  const rest = t.slice(at.end).replace(/^\s+/, "");
  return { text: t.slice(0, at.start) + insert + rest, caret: at.start + insert.length };
}

// --- statuses, graph ---------------------------------------------------------------

const STATUS = {
  queued: { st: "queued", tone: "muted", label: "Queued", icon: "hourglass" },
  waiting: { st: "queued", tone: "muted", label: "Waiting on dependencies", icon: "hourglass" },
  retrying: { st: "pending", tone: "warn", label: "Retrying", icon: "sync" },
  running: { st: "running", tone: "run", label: "Running", icon: "sync" },
  needs_decision: { st: "warning", tone: "warn", label: "Needs a decision", icon: "alert" },
  done: { st: "completed", tone: "ok", label: "Done", icon: "checkCircle" },
  failed: { st: "failed", tone: "fail", label: "Failed", icon: "xCircle" },
  blocked: { st: "failed", tone: "fail", label: "Blocked (dependency failed)", icon: "alert" },
  cancelled: { st: "skipped", tone: "muted", label: "Cancelled", icon: "stop" },
  stopped: { st: "skipped", tone: "fail", label: "Stopped (budget)", icon: "stop" },
};
export const statusInfo = (s) => STATUS[s] || { st: "queued", tone: "muted", label: s || "Unknown", icon: "info" };

/** Canvas edge class from the two ends' statuses (the Workflow view's colours). */
export function edgeTone(from, to) {
  if (["failed", "blocked", "stopped"].includes(from) || ["failed", "blocked"].includes(to)) return "fail";
  if (to === "running") return "run";
  if (from === "done") return to === "done" ? "ok" : "ready";
  return "idle";
}

export const ORCH_KIND = { plan: "Plan", repair: "Repair", replan: "Replan", summary: "Summary" };

/** The session graph ({nodes, edges}) as canvas nodes: owner label, status, dependency hint. `request` filters.
 *  Orchestrator rows (kind plan/repair/replan/summary) are nodes too (`orch`); `planned` edges link them to their tasks. */
export function graphModel(graph, agents = {}, request = null) {
  const nodes = (graph?.nodes || []).filter((n) => !request || n.request === request);
  const ids = new Set(nodes.map((n) => n.id));
  const status = Object.fromEntries(nodes.map((n) => [n.id, n.status]));
  const edges = (graph?.edges || []).filter((e) => ids.has(e.from) && ids.has(e.to))
    .map((e) => ({ ...e, tone: ["delegated", "planned"].includes(e.kind) ? "idle" : edgeTone(status[e.from], status[e.to]) }));
  return {
    nodes: nodes.map((n) => {
      const info = statusInfo(n.status), orch = Boolean(n.kind && n.kind !== "task");
      const deps = edges.filter((e) => e.to === n.id && e.kind === "depends_on").map((e) => e.from);
      const made = edges.filter((e) => e.from === n.id && e.kind === "planned").length;
      return { id: n.id, request: n.request, title: n.title || (orch ? `${ORCH_KIND[n.kind] || n.kind} ${n.request || ""}`.trim() : n.id),
        sub: `@${n.owner} · ${agents[n.owner]?.label || n.owner}`, status: n.status, ...info,
        ...(orch ? { orch: true, kind: n.kind, badge: "Orchestrator", nodeIcon: "graph", hint: made ? `Planned ${made} task${made === 1 ? "" : "s"}` : `${ORCH_KIND[n.kind] || n.kind} call` }
          : { hint: deps.length ? `Waits for ${deps.join(", ")}` : "No dependencies" }) };
    }),
    edges,
  };
}

/** Top-down layered layout (longest dependency path); positions in canvas units. */
export function layoutGraph(nodes, edges, { w = 260, h = 128, gapX = 36, gapY = 56 } = {}) {
  const deps = Object.fromEntries(nodes.map((n) => [n.id, []]));
  edges.forEach((e) => { if (e.to in deps && e.from in deps) deps[e.to].push(e.from); });
  const level = {};
  const depth = (id, seen = new Set()) => {
    if (id in level) return level[id];
    if (seen.has(id)) return 0; // a cycle (never valid): flatten it instead of looping
    seen.add(id);
    level[id] = Math.max(-1, ...deps[id].map((d) => depth(d, seen))) + 1;
    return level[id];
  };
  nodes.forEach((n) => depth(n.id));
  // A summary call comes after its request's tasks (it has no edges of its own).
  nodes.filter((n) => n.kind === "summary" && !deps[n.id].length && !edges.some((e) => e.from === n.id))
    .forEach((n) => { level[n.id] = Math.max(0, ...nodes.filter((m) => m.request === n.request && m.kind !== "summary").map((m) => level[m.id] + 1)); });
  const rows = [];
  nodes.forEach((n) => (rows[level[n.id]] ||= []).push(n.id));
  const widest = Math.max(1, ...rows.map((r) => r?.length || 0));
  const width = widest * w + (widest - 1) * gapX;
  const pos = {};
  rows.forEach((row, y) => (row || []).forEach((id, i) => {
    const left = (width - (row.length * w + (row.length - 1) * gapX)) / 2;
    pos[id] = { x: left + i * (w + gapX), y: y * (h + gapY), w };
  }));
  return { pos, width, height: Math.max(h, rows.length * (h + gapY) - gapY), order: rows.flat().filter(Boolean) };
}

/** Messages plus each specialist's result (summary, artifacts) that has no message of its own, in time order. */
export function timeline(state) {
  const msgs = state?.messages || [];
  const told = new Set(msgs.map((m) => m.task).filter(Boolean));
  const results = (state?.tasks || []).filter((t) => t.kind === "task" && t.result && !told.has(t.row)).map((t) => ({
    id: `task:${t.row}`, role: "assistant", kind: t.result.status === "done" ? "result" : "error", agent: t.owner, task: t.row,
    request: t.request, text: t.result.summary, artifacts: t.result.artifacts || [], at: t.result.at || "" }));
  return [...msgs, ...results].map((m, i) => [m, i]).sort(([a, i], [b, j]) => (a.at && b.at && a.at !== b.at ? (a.at < b.at ? -1 : 1) : i - j)).map(([m]) => m);
}

/** Public chat copy. Only complete, recognizable protocol envelopes are interpreted;
 * user JSON and ordinary code examples always remain untouched. Originals stay in details. */
export function presentMessage(message, task) {
  const m = message || {}, original = String(m.text || "");
  const identity = m.role === "user" ? "You" : m.role === "system" ? "ALFRD" : m.agent || "ALFRD";
  if (m.role !== "assistant") return { ...m, identity, text: original };
  let text = original, envelope = null, prefix = "";
  const fences = [...original.matchAll(/(?:^|\n)```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```/g)];
  const last = fences.at(-1), fenced = last && !original.slice(last.index + last[0].length).trim() ? last : null;
  let machine = false;
  try {
    const parsed = JSON.parse(fenced ? fenced[1] : original);
    machine = parsed?.protocol === "alfrd.orchestration/1";
    if (parsed?.protocol === "alfrd.orchestration/1" && ["result", "plan", "summary"].includes(parsed.kind)) {
      const valid = parsed.kind === "result" ? ["done", "failed", "blocked"].includes(parsed.result?.status) && typeof parsed.result?.summary === "string" && (parsed.result.artifacts == null || (Array.isArray(parsed.result.artifacts) && parsed.result.artifacts.every((a) => typeof a === "string")))
        : parsed.kind === "plan" ? Array.isArray(parsed.tasks) || Array.isArray(parsed.decisions) : typeof parsed.message === "string";
      if (valid) { envelope = parsed; prefix = fenced ? original.slice(0, fenced.index).trim() : ""; }
    }
  } catch { /* not a complete protocol result */ }
  const result = task?.result || envelope?.result;
  if (envelope) text = [prefix, result?.summary || envelope.message || (envelope.kind === "plan" ? "Planning the tasks" : "")].filter(Boolean).join("\n\n");
  else if (machine || (/^\s*(?:```json\s*)?\{/.test(fenced ? fenced[1] : original) && /"protocol"\s*:\s*"alfrd\.orchestration\/1"/.test(fenced ? fenced[1] : original))) {
    text = result?.summary || "The agent’s reply could not be read. You can inspect the output or retry.";
  } else if (!text.trim() && result) text = result.summary || "The agent finished without a readable summary. Open details to inspect its output.";
  if (!text.trim() && envelope) text = "The agent finished without a readable summary. Open details to inspect its output.";
  return { ...m, identity, text, artifacts: result?.artifacts || m.artifacts || [], state: result?.status,
    technical: text !== original ? original : null };
}

/** Request membership is explicit, never inferred from timestamps. */
export function threadGroups(state) {
  const requests = state?.requests || [], newest = requests.at(-1)?.id;
  const groups = new Map(requests.map((request) => [request.id, { id: request.id, request, messages: [] }]));
  const ordered = [];
  for (const m of timeline(state)) {
    let group = m.request && groups.get(m.request);
    if (!group) group = { id: `message:${m.id}`, messages: [] };
    if (!ordered.includes(group)) ordered.push(group);
    group.messages.push(m);
  }
  for (const group of groups.values()) if (!ordered.includes(group)) ordered.push(group);
  return ordered.map((g) => {
    const pending = (state?.blockers || []).some((d) => d.request === g.id || (state?.tasks || []).some((t) => t.row === d.task && t.request === g.id))
      || Number(g.request?.mailbox?.pending_user) > 0;
    const active = REQUEST_ACTIVE.includes(g.request?.status) || (state?.tasks || []).some((t) => t.request === g.id && !TERMINAL.includes(t.status));
    return { ...g, active, pending, open: active || pending || !g.request || g.id === newest,
      summary: g.request ? requestSummary(state, g.request) : "" };
  });
}

export function requestSummary(state, request) {
  const p = requestProgress(state, request), files = new Set((state?.tasks || []).filter((t) => t.request === request.id).flatMap((t) => t.result?.artifacts || []));
  const title = String(request.text || "Request").replace(/\s+/g, " ").trim().slice(0, 90);
  return [title, `${p.done}/${p.total} tasks complete`, ["failed", "stopped", "cancelled"].includes(request.status) ? statusInfo(request.status).label : "",
    duration(p.seconds), money(request.usage?.total_cost_usd), files.size ? `${files.size} files` : ""].filter(Boolean).join(" · ");
}

/** Stable attempt identity: parallel tasks and retries never share a disclosure. */
export const activityKey = (r) => `${r.request || ""}|${r.row}|${r.unit || "queued"}`;
export function activityRows(state, items = [], request = null) {
  const rows = [];
  for (const t of state?.tasks || []) {
    if (request && t.request !== request) continue;
    const logs = t.logs?.length ? t.logs : [{ unit: t.units?.at(-1) }];
    logs.forEach((l, i) => {
      const live = items.find((r) => r.unit === l.unit && r.row === t.row);
      rows.push({ ...t, title: t.task?.title || t.title, ...l, cost_usd: l.total_cost_usd,
        status: l.status || t.status, attempt: i + 1, ...live,
        ...((TERMINAL.includes(l.status) || (!l.status && TERMINAL.includes(t.status))) ? { status: l.status || t.status } : {}) });
    });
  }
  for (const r of items) if ((!request || r.request === request) && !rows.some((t) => activityKey(t) === activityKey(r))) rows.push(r);
  return rows;
}

// --- tasks, decisions ------------------------------------------------------------

export const canCancel = (task) => Boolean(task) && !TERMINAL.includes(task.status);
export const canRetry = (task) => Boolean(task) && RETRYABLE.includes(task.status);

/** The POST body for answering a blocker, or {error}. A blocker with options needs one of them or a written answer. */
export function decisionPayload(blocker, { choice = "", answer = "" } = {}) {
  const c = String(choice || "").trim(), a = String(answer || "").trim();
  if (!c && !a) return { error: blocker?.options?.length ? "Pick an option or write an answer." : "Write an answer." };
  if (c && blocker?.options?.length && !blocker.options.includes(c)) return { error: `“${c}” is not one of the options.` };
  if (a.length > 8000) return { error: "The answer is longer than 8000 characters." };
  return { ...(c ? { choice: c } : {}), ...(a ? { answer: a } : {}) };
}

// --- progress, usage ---------------------------------------------------------------

/** The request the progress card follows: the newest active one, else the newest. */
export function activeRequest(state) {
  const list = state?.requests || [];
  return [...list].reverse().find((r) => REQUEST_ACTIVE.includes(r.status)) || list[list.length - 1] || null;
}

/** Progress and budget use of one request: task counts, cost and time against its bounds. */
export function requestProgress(state, req, now = Date.now()) {
  if (!req) return null;
  const tasks = (state?.tasks || []).filter((t) => t.request === req.id && t.kind === "task");
  const count = (...s) => tasks.filter((t) => s.includes(t.status)).length;
  const bounds = req.bounds || state?.settings?.bounds || {};
  const cost = Number(req.usage?.total_cost_usd) || 0;
  const started = Number(req.created_ts) ? req.created_ts * 1000 : Date.parse(req.created);
  const ended = req.finished ? Date.parse(req.finished) : now;
  const seconds = Number.isFinite(started) ? Math.max(0, Math.round((ended - started) / 1000)) : 0;
  const ratio = (v, max) => (Number(max) > 0 ? Math.min(1, v / Number(max)) : 0);
  const worst = Math.max(ratio(cost, bounds.max_cost_usd), ratio(seconds, bounds.max_seconds));
  return {
    id: req.id, status: req.status, mode: req.mode, text: req.text,
    total: tasks.length, done: count("done"), failed: count("failed", "blocked", "stopped", "cancelled"), running: count("running"),
    cost, maxCost: Number(bounds.max_cost_usd) || null, costRatio: ratio(cost, bounds.max_cost_usd),
    seconds, maxSeconds: Number(bounds.max_seconds) || null, timeRatio: ratio(seconds, bounds.max_seconds),
    tokens: (Number(req.usage?.input_tokens) || 0) + (Number(req.usage?.output_tokens) || 0),
    calls: Number(req.orchestrator_calls) || 0,
    tone: worst >= 1 ? "fail" : worst >= 0.8 ? "warn" : "ok",
  };
}

/** The token report of one usage record (agent_totals / a unit's usage): new input, cache read, cache write,
 *  output and cost; a part nobody reported stays null ("—"). */
export function tokenReport(u) {
  const n = (v) => (Number.isFinite(Number(v)) && v !== null && v !== "" ? Number(v) : null);
  const fresh = n(u?.input_uncached_tokens) ?? n(u?.input_tokens);
  return { fresh, read: n(u?.cache_read_input_tokens), write: n(u?.cache_creation_input_tokens), out: n(u?.output_tokens),
    cost: n(u?.total_cost_usd) };
}

/** "new 1.2k · cache read 40k · cache write 3k · out 900 · $0.042" (only the parts that were reported). */
export function tokenLine(u) {
  const r = tokenReport(u);
  const k = (v) => (v >= 10000 ? `${Math.round(v / 1000)}k` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v));
  const parts = [["new", r.fresh], ["cache read", r.read], ["cache write", r.write], ["out", r.out]].filter(([, v]) => v != null).map(([l, v]) => `${l} ${k(v)}`);
  if (r.cost != null) parts.push(money(r.cost));
  return parts.join(" · ");
}

/** Tasks of a request (or all) over their cost warning (chat.warn_task_cost_usd). */
export const costWarnings = (state, request = null) => (state?.tasks || []).filter((t) => t.cost_warning && (!request || t.request === request));

/** The newest request with tasks (what "Promote to workflow" offers), or null. */
export const promotable = (state) => [...(state?.requests || [])].reverse().find((r) => (r.tasks || []).length) || null;

export const money = (v) => (v == null || !Number.isFinite(Number(v)) ? "—" : `$${Number(v).toFixed(Number(v) < 1 ? 3 : 2)}`);
export function duration(s) {
  if (!Number.isFinite(s)) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${r}s` : `${r}s`;
}

// --- chat: settings (Settings → Agent chat) --------------------------------------------

const AGENT_FIELDS = ["label", "entrypoint", "cmd", "model", "adapter", "roles", "persona", "timeout", "lean", "env", "fallback"];
const SETTING_KEYS = ["orchestrator", "orchestrator_model", "escalation_model", "final_summary", "warn_task_cost_usd", "concurrency", "history_messages"];

/** The effective chat settings of a `chat:` block (presets merged under it, `agent: null` removes one). */
export function mergeChat(raw = {}) {
  const out = JSON.parse(JSON.stringify(DEFAULTS));
  const r = raw && typeof raw === "object" ? raw : {};
  for (const k of SETTING_KEYS) if (k in r) out[k] = r[k];
  Object.assign(out.bounds, r.bounds || {});
  for (const [key, a] of Object.entries(r.agents || {})) {
    if (a == null) delete out.agents[key];
    else out.agents[key] = { ...(out.agents[key] || {}), ...a };
  }
  for (const a of Object.values(out.agents)) if (typeof a.roles === "string") a.roles = [a.roles];
  return out;
}

/** The `chat:` block to write for effective settings `s`: every agent spelled out, removed presets as null. */
export function chatBlock(s) {
  const agents = {};
  for (const key of Object.keys(DEFAULTS.agents)) if (!(key in s.agents)) agents[key] = null;
  for (const [key, a] of Object.entries(s.agents)) {
    const clean = {};
    for (const f of AGENT_FIELDS) {
      const v = a[f];
      if (v == null || v === "" || (Array.isArray(v) && !v.length) || (f === "env" && (typeof v !== "object" || !Object.keys(v).length))) continue;
      if (f === "fallback" && !(v in s.agents)) continue; // its agent was removed
      if (f === "lean" && v === false && !DEFAULTS.agents[key]?.lean) continue; // off is the default for new agents
      clean[f] = v;
    }
    agents[key] = clean;
  }
  const model = (v) => (String(v ?? "").trim() || null); // empty: the orchestrator agent's own model
  const warn = Number(s.warn_task_cost_usd) > 0 ? Number(s.warn_task_cost_usd) : null;
  return { orchestrator: s.orchestrator, orchestrator_model: model(s.orchestrator_model), escalation_model: model(s.escalation_model),
    final_summary: Boolean(s.final_summary), warn_task_cost_usd: warn, concurrency: Number(s.concurrency),
    history_messages: Number(s.history_messages), bounds: { ...s.bounds }, agents };
}

/** Problems with effective settings (mirrors chat.merge_settings); `personas` are the project's persona keys. */
export function chatProblems(s, personas = []) {
  const errors = [];
  const keys = Object.keys(s.agents || {});
  if (!keys.length) errors.push("Add at least one agent.");
  for (const key of keys) {
    const a = s.agents[key];
    if (!AGENT_KEY.test(key)) errors.push(`Agent key “${key}”: lowercase letters, digits, _ or - (up to 40).`);
    if (!a.cmd && !String(a.entrypoint || key).trim()) errors.push(`@${key}: give it an entrypoint.`);
    if (a.persona && !personas.includes(a.persona)) errors.push(`@${key}: persona “${a.persona}” is not in project_settings.personas.`);
    if (!(a.roles || []).every((r) => String(r).trim())) errors.push(`@${key}: roles must be words.`);
    if (a.fallback && (a.fallback === key || !keys.includes(a.fallback))) errors.push(`@${key}: the fallback must be another agent.`);
  }
  if (!keys.includes(s.orchestrator)) errors.push(`The orchestrator must be one of the agents (${keys.map((k) => `@${k}`).join(", ") || "none"}).`);
  const int = (v, low) => Number.isInteger(Number(v)) && Number(v) >= low && v !== "" && v !== null;
  if (!int(s.concurrency, 1)) errors.push("Concurrency: a whole number ≥ 1.");
  if (!int(s.history_messages, 0)) errors.push("History messages: a whole number ≥ 0.");
  const b = s.bounds || {};
  for (const k of ["max_depth", "max_tasks"]) if (!int(b[k], 1)) errors.push(`${k}: a whole number ≥ 1.`);
  if (!int(b.max_retries, 0) || Number(b.max_retries) > 5) errors.push("max_retries: a whole number from 0 to 5.");
  if (b.max_replans != null && !int(b.max_replans, 0)) errors.push("max_replans: a whole number ≥ 0.");
  for (const k of ["max_cost_usd", "max_seconds"]) if (!(Number(b[k]) > 0)) errors.push(`${k}: a positive number.`);
  const w = s.warn_task_cost_usd;
  if (w != null && w !== "" && !(Number(w) > 0)) errors.push("Task cost warning: a positive number (empty: no warning).");
  return errors;
}

// --- slash commands (handled in the browser, never sent) -------------------------------

export const SLASH = [
  { cmd: "setup", hint: "Who does what: orchestrator, roles, models, fallbacks" },
  { cmd: "help", hint: "Commands and agents (not sent)" },
  { cmd: "new", hint: "Start a new session" },
  { cmd: "graph", hint: "Task graph full screen", where: "chat" },
];
const slashList = (where) => SLASH.filter((c) => !c.where || c.where === where);

/** Text starting with "/" (at the very start; " /x" sends as text): {cmd, args} | {error} | null. */
export function parseSlash(text, where = "chat") {
  const t = String(text || "");
  if (t[0] !== "/") return null;
  const m = /^\/(\S*)\s*([\s\S]*)$/.exec(t.trim());
  const cmd = m[1].toLowerCase();
  if (slashList(where).some((c) => c.cmd === cmd)) return { cmd, args: m[2].trim() };
  return { error: `Unknown command /${m[1]} (${slashList(where).map((c) => `/${c.cmd}`).join(", ")}). Start with a space to send text that begins with /.` };
}

/** The `/word` typed at the start under the caret: {query} | null. */
export function slashAt(text, caret) {
  const t = String(text || "");
  const m = /^\/[A-Za-z]*/.exec(t);
  return m && caret >= 1 && caret <= m[0].length && !/\s/.test(t.slice(0, caret)) ? { start: 0, end: m[0].length, query: m[0].slice(1).toLowerCase() } : null;
}

export const slashOptions = (query = "", where = "chat") => slashList(where).filter((c) => c.cmd.startsWith(String(query).toLowerCase()));

/** The local /help text: commands, then each agent with its roles, model and fallback. */
export function helpText(settings, where = "chat") {
  const ag = settings?.agents || {}, o = settings?.orchestrator;
  return ["Commands (handled here, never sent):", ...slashList(where).map((c) => `  /${c.cmd} — ${c.hint}`),
    "", "Agents (start a message with @agent to skip the orchestrator):",
    ...Object.entries(ag).map(([k, a]) => `  @${k} — ${a.label || k}${k === o ? " · orchestrator" : ""}${a.model ? ` · model ${a.model}` : ""}${(a.roles || []).length ? ` · ${a.roles.join(", ")}` : ""}${a.fallback ? ` · falls back to @${a.fallback}` : ""}`),
    ...(o ? ["", `Orchestrator: @${o} (plans ${settings.orchestrator_model || "agent model"}, escalation ${settings.escalation_model || "agent model"})`] : [])].join("\n");
}

// --- live activity (GET chat/sessions/<sid>/activity) -----------------------------------

/** What a running unit is: "Planning request r0002", "Repair r0002", or the task title. */
export function activityLabel(r) {
  if (!r?.kind || r.kind === "task") return r?.title || r?.row || "Task";
  return { plan: "Planning the tasks", repair: "Updating the plan", replan: "Updating the plan after your answer", summary: "Preparing the final answer" }[r.kind] || ORCH_KIND[r.kind] || "Orchestrator activity";
}

/** Whether to poll activity: a running/retrying row or an active request in the session state. */
export const hasActivity = (state) => (state?.tasks || []).some((t) => ["running", "retrying"].includes(t.status))
  || (state?.requests || []).some((r) => REQUEST_ACTIVE.includes(r.status) && r.status !== "needs_decision");

// --- /setup: roles, models, availability -----------------------------------------------

/** Role words for the setup dialog: every agent's roles, then suggestions, without duplicates (orchestration is its own row). */
export function roleWords(agents = {}, suggested = []) {
  const all = [...Object.values(agents).flatMap((a) => a.roles || []), ...suggested].map((r) => String(r).trim()).filter(Boolean);
  return [...new Set(all)].filter((r) => r !== "orchestration");
}

/** The agent holding `role` (first one), or "". */
export const roleOwner = (agents = {}, role) => Object.keys(agents).find((k) => (agents[k].roles || []).includes(role)) || "";

/** Move `role` to agent `key` ("" unassigns): removed from every other agent. Mutates and returns agents. */
export function assignRole(agents, role, key) {
  for (const [k, a] of Object.entries(agents)) {
    const has = (a.roles || []).includes(role);
    if (k === key && !has) a.roles = [...(a.roles || []), role];
    else if (k !== key && has) a.roles = a.roles.filter((r) => r !== role);
  }
  return agents;
}

/** The backend of an agent: "ollama" | "hf" | "claude" | "codex" | "llm" | "" from its entrypoint, cmd and env. */
export function backendOf(a = {}) {
  const cmd = [].concat(a.cmd || []).join(" "), ep = String(a.entrypoint || "");
  if (/llm[_-]chat/.test(cmd + ep)) return "llm";
  if (/oss|ollama/i.test(cmd + ep) || /11434|ollama/i.test(JSON.stringify(a.env || {}))) return "ollama";
  if (/huggingface|hf/i.test(cmd + ep)) return "hf";
  return /claude/.test(ep + cmd) ? "claude" : /codex/.test(ep + cmd) ? "codex" : "";
}

const KNOWN_MODELS = { claude: ["opus", "sonnet", "haiku"], codex: ["gpt-5-codex", "gpt-5"], hf: ["openai/gpt-oss-120b", "openai/gpt-oss-20b"] };
/** Model suggestions for an agent: Ollama's installed models for Ollama agents, else known names per CLI. */
export function modelHints(a, probe) {
  const b = backendOf(a);
  if (b === "ollama") return [...(probe?.ollama?.models || [])];
  if (b === "llm") return [...(probe?.ollama?.models || [])];
  const known = KNOWN_MODELS[b] || KNOWN_MODELS[a?.adapter] || [];
  // Codex: the models this machine's Codex knows (its config.toml / models cache) come first.
  return b === "codex" ? [...new Set([...(probe?.codex?.models || []), ...known])] : known;
}

/** Availability badge of an agent from GET chat/probe: {tone, label, detail} | null (no probe). */
export function probeBadge(key, a, probe) {
  const p = probe?.agents?.[key];
  if (!p) return null;
  const b = backendOf(a);
  if (b === "ollama" && probe.ollama && !probe.ollama.reachable) return { tone: "fail", label: "Ollama not reachable", detail: probe.ollama.error || probe.ollama.url || "" };
  if (b === "hf" && probe.huggingface && !probe.huggingface.token) return { tone: "warn", label: "HF_TOKEN missing", detail: "Set HF_TOKEN in the server's environment" };
  return p.available ? { tone: "ok", label: "Available", detail: p.path || p.program || "" } : { tone: "fail", label: "Not found", detail: p.detail || p.program || "" };
}

/** A free agent key for a preset: the preset key, else key-2, key-3 … */
export function freeKey(agents, key) {
  let k = key, i = 1;
  while (k in agents) k = `${key}-${++i}`;
  return k;
}

/** "Setup saved: orchestrator @x (model), research → @y, …" */
export function setupSummary(s) {
  const roles = roleWords(s.agents).map((r) => `${r} → @${roleOwner(s.agents, r)}`);
  const fb = Object.entries(s.agents).filter(([, a]) => a.fallback).map(([k, a]) => `@${k} falls back to @${a.fallback}`);
  return [`Setup saved: orchestrator @${s.orchestrator} (${s.orchestrator_model || s.agents[s.orchestrator]?.model || "agent model"})`, ...roles, ...fb].join(", ");
}
