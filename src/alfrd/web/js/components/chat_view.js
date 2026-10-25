// Chat section (#/chat/<session>?p=<project>): sessions, transcript, composer with @agent
// autocomplete, task graph (graph_view.js, read-only), task detail with Cancel/Retry, decisions,
// progress and usage against the request's budget. Lazy: loaded on the first visit.
// Session snapshots follow the event stream; the shared transcript (transcript.js) and run components poll bounded
// recorded output and durable mailbox messages. Slash commands
// (/setup → chat_setup.js, /help, /new, /graph) run here. Full transcripts and artifacts load when opened.
import { $, $$, esc, icon, keyLabel, loadCss, storage, routeHash, setRoute } from "../utils/dom.js";
import { server } from "../data/server.js";
import { needsMe, countText } from "../data/inbox.js";
import { taskMenuApi } from "./task_menu_api.js";
import { mountGraph } from "./graph_view.js";
import { bindComposer, composerHtml } from "./chat_composer.js";
import { openLog } from "./plans.js";
import { mountTranscript } from "./transcript.js";
import { createInspector, tabsHtml, createRequestScope } from "./inspector.js";
import {
  activeRequest, canCancel, canRetry, costWarnings, decisionPayload, duration, graphModel, hasActivity, helpText, money,
  parseMention, promotable, requestProgress, staleState, statusInfo, tokenLine, presentMessage,
} from "../data/chat.js";

const S = { renaming: null, project: null, sessions: null, sessionsError: null, settings: null, settingsError: null, sid: null, data: null,
  error: null, loading: false, task: null, open: new Map(), drafts: new Map(), sending: false, sendError: "", gen: 0, keys: {}, notes: [] };
let scope = createRequestScope();
let inspTab = "graph";
let insp = null; // the inspector shell (inspector.js): closed unless the user opened it
let host = null, graph = null, ctxRef = null, refreshTimer = 0, slowTimer = 0, clock = 0, composer = null, act = null, full = null;
const base = (p) => `/api/studio/projects/${encodeURIComponent(p)}/chat`;
const SESSION_KEY = "ui:chat.session";
const COLLAPSE_KEY = "ui:chat.sessionsCollapsed"; // the chat list folded to a rail (wide screens; per browser)
const wide = () => !window.matchMedia || window.matchMedia("(min-width: 901px)").matches;
let needsOnly = false;
window.addEventListener("alfrd-inbox", ({ detail }) => {
  if (host?.isConnected && detail.project === S.project && detail.sessions) {
    S.sessions = detail.sessions; S.sessionsError = null; ctxRef?.update();
  }
});

async function get(url) {
  const res = await server.request(url, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => null);
  if (!res.ok) { const e = new Error(body?.error?.message || `${res.status} ${res.statusText}`); e.status = res.status; throw e; }
  return body;
}
const post = (p, path, body = {}, method = "POST") => server.mutate(`/studio/projects/${encodeURIComponent(p)}/chat${path}`, body, method);

window.addEventListener("alfrd-events", (e) => {
  const { project, events } = e.detail || {};
  if (!host || project !== S.project) return;
  const chatEvents = (events || []).filter((ev) => String(ev.plan || "").startsWith("chat-"));
  if (!chatEvents.length) return;
  const mine = chatEvents.some((ev) => ev.plan === `chat-${S.sid}`);
  scheduleRefresh(mine);
  if (mine) act?.kick();
});

function scheduleRefresh(session = true) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { if (session && S.sid) loadSession(); loadSessions(); }, 250);
}

const liveFlowing = (ctx) => ["live", "polling"].includes(ctx.state.liveStatus?.state);

export function leave() {
  clearInterval(slowTimer); clearInterval(clock); slowTimer = clock = 0;
  act?.stop();
}

export function render(el, ctx) {
  loadCss("css/chat.css");
  ctxRef = ctx;
  if (ctx.state.mode !== "server") { host = null; el.innerHTML = `<div class="empty chat-empty"><h2>Chat</h2><p role="status">Chat needs <code>alfrd serve</code>: agents run on the server.</p></div>`; delete el.dataset.chat; return; }
  const p = ctx.activeProject();
  if (!p) return; // app.js asks for a project
  if (el.dataset.chat !== p) mount(el, ctx, p);
  host = el;
  const wanted = ctx.state.chatSession || (storage.get(SESSION_KEY, {}) || {})[p] || null;
  if (S.sessions && !S.sessionsError) {
    const sid = S.sessions.some((s) => s.id === wanted) ? wanted : S.sessions[0]?.id || null;
    if (sid !== S.sid) openSession(sid, { replace: !ctx.state.chatSession || ctx.state.chatSession !== sid });
  }
  draw(ctx);
  if (!clock) clock = setInterval(() => { if (host?.isConnected && activeRequest(S.data)?.finished == null) drawProgress(ctxRef); }, 1000);
  if (!slowTimer) slowTimer = setInterval(() => { // the existing live fallback is faster; this only covers "live off"
    if (host?.isConnected && !liveFlowing(ctxRef) && S.data?.requests?.some((r) => ["planning", "running", "needs_decision", "summarizing"].includes(r.status))) loadSession();
  }, 15000);
}

function graphTaskMenu(close = () => {}) {
  return taskMenuApi({ project: () => S.project, sid: () => S.sid, state: () => S.data,
    refresh: () => loadSession(), openLog: (rel) => openLog(ctxRef, S.project, rel),
    files: (row) => { close(); S.task = row; inspTab = "files"; S.keys.task = null; draw(ctxRef); openInspector(); } });
}

function openInspector() { insp?.open(); }

function mount(el, ctx, p) {
  Object.assign(S, { project: p, sessions: null, sessionsError: null, settings: null, settingsError: null, sid: null, data: null, error: null,
    task: null, sendError: "", keys: {}, renaming: null });
  act?.stop();
  scope = createRequestScope(); inspTab = "graph";
  S.open.clear();
  el.dataset.chat = p;
  const folded = storage.get(COLLAPSE_KEY, false) === true;
  el.innerHTML = `<div class="chat${folded ? " chat-sessions-collapsed" : ""}">
    <nav class="chat-sessions" id="chat-sessions" aria-label="Chat sessions"><div class="row gap"><h2 class="grow" tabindex="-1">Chat</h2>
      <button type="button" class="btn sm" data-chat="new" title="Start a new chat session">${icon("plus")} New</button>
      <button type="button" class="icon-btn sm chat-collapse" data-chat="collapse" aria-controls="chat-sessions" aria-expanded="true" aria-label="Hide the chat list" title="Hide the chat list">${icon("sidebar")}</button></div>
      <button type="button" class="btn sm" data-chat="needs-me" aria-pressed="${needsOnly}">Needs me</button>
      <ul class="chat-list" id="chat-list" role="list"></ul></nav>
    <section class="chat-main" aria-label="Conversation">
      <header class="chat-head" id="chat-head"></header>
      <div class="chat-log" id="chat-log" tabindex="0" aria-label="Messages"></div>
      <section class="chat-activity" id="chat-activity" aria-label="Running now" hidden></section>
      ${composerHtml("chat")}
    </section>
    <aside class="chat-side" id="chat-inspector" tabindex="-1" aria-label="Inspector: request progress, decisions and tasks">
      <section class="card chat-insp" id="chat-insp" aria-label="Graph, files and goals"><div id="chat-request-scope"></div><div id="chat-insp-tabs">${tabsHtml()}</div></section>
      <section class="card" id="chat-progress" aria-labelledby="chat-progress-h"></section>
      <section class="card" id="chat-decisions" aria-labelledby="chat-decisions-h"></section>
      <section class="card chat-graph-card" aria-labelledby="chat-graph-h"><h3 id="chat-graph-h"><span class="grow">Task graph</span><button type="button" class="icon-btn sm" id="chat-graph-full" data-chat="graph-full" aria-label="Task graph full screen" aria-keyshortcuts="F" title="Full screen (F)">${icon("expand")}</button></h3><div id="chat-graph"></div></section>
      <section class="card" id="chat-task" aria-live="polite" hidden></section>
    </aside></div>`;
  graph = mountGraph($("#chat-graph", el), { taskMenu: graphTaskMenu(), groups: true, label: "Task graph", empty: "Tasks appear here when the orchestrator plans a request.",
    onSelect: (row) => { S.task = row; drawTask(ctxRef); }, onOpen: (row) => { S.task = row; drawTask(ctxRef); $("#chat-task", host)?.focus(); } });
  act = mountTranscript($("#chat-log", el), { project: () => S.project, sid: () => S.sid, busy: () => hasActivity(S.data), agentName, state: () => S.data, error: () => S.error, messageHtml, viewGraph: (request) => openGraphFull({ request }), onReply: () => loadSession(), notes: () => S.notes.filter((n) => n.sid === S.sid),
    select: (row) => { S.task = row; S.keys.task = null; drawTask(ctxRef); openInspector(); $("#chat-task", host)?.focus(); },
    inspect: openInspector,
    openLog: (rel, meta) => openLog(ctxRef, S.project, rel, meta), cancel: (row, b) => taskAction("cancel", row, b) });
  $(".chat-graph-card", el).addEventListener("keydown", (e) => {
    if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey && !e.altKey && e.target.closest(".gv-canvas")) { e.preventDefault(); openGraphFull(); }
  });
  bind(el);
  const toggle = () => $("[data-chat=inspect]", el);
  insp = createInspector({ chat: $(".chat", el), panel: $("#chat-inspector", el), button: { setAttribute: (k, v) => toggle()?.setAttribute(k, v) },
    focus: (target) => (target?.isConnected ? target : toggle())?.focus() });
  insp.restore();
  loadSessions();
  loadSettings();
}

// --- loading ----------------------------------------------------------------------

async function loadSessions() {
  const p = S.project;
  try {
    const { sessions } = await get(`${base(p)}/sessions`);
    if (p !== S.project) return;
    S.sessions = sessions; S.sessionsError = null;
  } catch (error) { if (p === S.project) S.sessionsError = error.message; }
  ctxRef?.update();
}

async function loadSettings() {
  const p = S.project;
  try { const s = await get(`${base(p)}/settings`); if (p === S.project) { S.settings = s; S.settingsError = null; } }
  catch (error) { if (p === S.project) S.settingsError = error.message; }
  ctxRef?.update();
}

async function loadSession() {
  const p = S.project, sid = S.sid, gen = ++S.gen;
  if (!sid) return;
  S.loading = !S.data;
  try {
    const data = await get(`${base(p)}/sessions/${encodeURIComponent(sid)}`);
    if (gen !== S.gen || p !== S.project || sid !== S.sid) return; // a newer request or another session won
    if (staleState(S.data, data)) { S.loading = false; return; }
    const was = hasActivity(S.data);
    S.data = data; S.error = null;
    if (hasActivity(data) && !was) act?.kick();
  } catch (error) { if (gen === S.gen) S.error = error.message; }
  S.loading = false;
  ctxRef?.update();
}

function openSession(sid, { replace = false } = {}) {
  scope = createRequestScope();
  S.sid = sid; S.data = null; S.error = null; S.task = null; S.open.clear(); S.gen++; S.keys = {};
  const remembered = storage.get(SESSION_KEY, {}) || {};
  if (sid) storage.set(SESSION_KEY, { ...remembered, [S.project]: sid });
  const st = ctxRef.state;
  st.chatSession = sid || "";
  setRoute(routeHash(sid ? `chat/${sid}` : "chat", st.selectedProject, st.selectedTarget), { replace });
  const input = host && $("#chat-input", host);
  if (input) input.value = S.drafts.get(sid) || "";
  act?.reset();
  if (sid) loadSession();
}

// --- drawing ---------------------------------------------------------------------

const changed = (name, value) => { const k = JSON.stringify(value); if (S.keys[name] === k) return false; S.keys[name] = k; return true; };
const agents = () => S.data?.settings?.agents || S.settings?.agents || {};
const orchestrator = () => S.data?.settings?.orchestrator || S.settings?.orchestrator;
const agentName = (key) => (key === "alfrd" ? "ALFRD" : `${agents()[key]?.label || key}`);

function draw(ctx) {
  drawSessions();
  drawHead(ctx);
  drawLog();
  drawProgress(ctx);
  drawDecisions();
  const scopeHtml = scope.html(S.data);
  if (changed("scope", scopeHtml)) $("#chat-request-scope", host).innerHTML = scopeHtml;
  const request = scope.request(S.data);
  $("#chat-insp-panel", host).dataset.request = request || "all";
  $("#chat-insp-panel", host).hidden = inspTab === "graph";
  $(".chat-graph-card", host).hidden = inspTab !== "graph";
  const model = { ...graphModel(S.data?.graph, agents(), request), stream: S.sid, scope: `${S.sid}:${request || "all"}` };
  graph?.update(model, S.data ? S.task : null);
  if (full?.host.isConnected) full.g.update(model, S.task); else full = null;
  drawTask(ctx);
  const send = $("#chat-send", host);
  if (send) { send.disabled = S.sending || !S.sid || Boolean(S.settingsError); send.setAttribute("aria-busy", String(S.sending)); }
  $("#chat-send-error", host).textContent = S.sendError || "";
}

function drawSessions() {
  const box = $("#chat-list", host);
  // a refresh while a name is being edited keeps the input (and what was typed) as it is
  if (S.renaming && box.querySelector(`[data-rename="${CSS.escape(S.renaming)}"]`) && S.sessions?.some((x) => x.id === S.renaming)) return;
  if (!changed("sessions", [S.sessions, S.sessionsError, S.sid, S.renaming, needsOnly])) return;
  if (S.sessionsError) { box.innerHTML = `<li class="callout fail small" role="alert">${esc(S.sessionsError)} <button type="button" class="btn sm" data-chat="reload">Retry</button></li>`; return; }
  if (!S.sessions) { box.innerHTML = `<li class="muted small" role="status">Loading sessions…</li>`; return; }
  if (!S.sessions.length) { box.innerHTML = `<li class="muted small">No sessions yet. <b>New</b> starts one.</li>`; return; }
  const sessions = needsOnly ? needsMe(S.sessions) : S.sessions;
  box.innerHTML = sessions.map((s) => s.id === S.renaming ? `<li class="chat-session-item"><form class="chat-rename" data-rename="${esc(s.id)}">
      <label class="sr-only" for="chat-rename-input">Chat name</label><input class="input sm" id="chat-rename-input" name="title" maxlength="120" value="${esc(s.title)}" required>
      <button type="submit" class="icon-btn sm" aria-label="Save the name" title="Save (Enter)">${icon("check")}</button><button type="button" class="icon-btn sm" data-chat="rename-cancel" aria-label="Keep the old name" title="Cancel (Esc)">${icon("close")}</button></form></li>`
    : `<li class="chat-session-item"><a href="${esc(routeHash(`chat/${s.id}`, S.project, ctxRef.state.selectedTarget))}" class="chat-session${s.id === S.sid ? " on" : ""}" ${s.id === S.sid ? 'aria-current="page"' : ""} data-sid="${esc(s.id)}">
    <b>${esc(s.title)}</b><span class="badge">${countText(s.pending_user)}</span>${s.active_requests ? `<span class="badge tone-run">${icon("sync")}<span>${s.active_requests} active</span></span>` : ""}
    <span class="muted small">${esc(s.last_message ? `${s.last_message.role === "user" ? "You" : agentName(s.last_message.agent || "alfrd")}: ${s.last_message.text}` : "No messages")}</span></a>
    <span class="chat-session-acts"><button type="button" class="icon-btn sm" data-chat="rename" data-sid-act="${esc(s.id)}" aria-label="Rename ${esc(s.title)}" title="Rename">${icon("edit")}</button><button type="button" class="icon-btn sm" data-chat="delete" data-sid-act="${esc(s.id)}" aria-label="Delete ${esc(s.title)}" title="${s.active_requests ? "Cancel its running tasks before deleting" : "Delete"}" ${s.active_requests ? "disabled" : ""}>${icon("trash")}</button></span></li>`).join("") || '<li class="muted small" role="status">No sessions need you.</li>';
  if (S.renaming) { const input = $("#chat-rename-input", box); if (input && document.activeElement !== input) { input.focus(); input.select(); } }
}

// --- session list: rename, delete, fold ----------------------------------------

function startRename(sid) { S.renaming = sid; S.keys.sessions = null; drawSessions(); }
function endRename(focusSid) {
  S.renaming = null; S.keys.sessions = null; drawSessions();
  if (focusSid) $(`[data-sid-act="${CSS.escape(focusSid)}"][data-chat=rename]`, host)?.focus();
}

async function saveRename(sid, title) {
  const s = S.sessions?.find((x) => x.id === sid);
  const text = String(title || "").trim();
  if (!s || !text || text === s.title) { endRename(sid); return; }
  try {
    const { session } = await post(S.project, `/sessions/${encodeURIComponent(sid)}`, { title: text }, "PATCH");
    s.title = session.title; S.keys.head = null;
    ctxRef.toast(`Chat renamed to “${session.title}”`, "ok");
  } catch (error) { ctxRef.toast(`Rename: ${error.message}`, "fail"); }
  endRename(sid);
  ctxRef.update();
  loadSessions();
}

async function deleteSession(sid) {
  const s = S.sessions?.find((x) => x.id === sid);
  if (!s || !confirm(`Delete the chat “${s.title}”?\n\nIts messages, tasks, logs and mailbox are removed from .alfrd/ and can't be restored.`)) return;
  try {
    await post(S.project, `/sessions/${encodeURIComponent(sid)}`, {}, "DELETE");
  } catch (error) { ctxRef.toast(`Delete: ${error.message}`, "fail"); return; }
  const i = S.sessions.findIndex((x) => x.id === sid);
  S.sessions = S.sessions.filter((x) => x.id !== sid); S.keys.sessions = null; S.drafts.delete(sid);
  const remembered = storage.get(SESSION_KEY, {}) || {};
  if (remembered[S.project] === sid) { delete remembered[S.project]; storage.set(SESSION_KEY, remembered); }
  if (sid === S.sid) { const next = S.sessions[Math.min(i, S.sessions.length - 1)]?.id || null; openSession(next, { replace: true }); if (!next) S.data = null; }
  ctxRef.toast(`Chat “${s.title}” deleted`, "ok");
  ctxRef.update();
  requestAnimationFrame(() => ($("#chat-list .chat-session.on", host) || $("[data-chat=new]", host))?.focus());
  loadSessions();
}

function setCollapsed(on) {
  const chat = $(".chat", host);
  chat.classList.toggle("chat-sessions-collapsed", on);
  storage.set(COLLAPSE_KEY, on);
  $("[data-chat=collapse]", host)?.setAttribute("aria-expanded", String(!on));
  requestAnimationFrame(() => (on ? $("[data-chat=sessions]", host) : $("[data-chat=collapse]", host))?.focus());
}

function drawHead(ctx) {
  const live = ctx.state.liveStatus?.state || "off";
  const s = S.sessions?.find((x) => x.id === S.sid);
  if (!changed("head", [s?.title, S.sid, live, S.settingsError, S.data?.plan?.runner_alive, S.data?.plan?.status])) return;
  const flowing = ["live", "polling"].includes(live);
  $("#chat-head", host).innerHTML = `<button type="button" class="btn sm chat-nav-toggle" data-chat="sessions" aria-controls="chat-sessions" aria-expanded="false" title="Show the chat list">${icon("sidebar")} Chats</button><h3 class="grow">${esc(s?.title || (S.sid ? S.sid : "No session"))}</h3>
    ${S.data?.plan ? `<span class="muted small" title="Session plan ${esc(S.data.plan.id)}">runner ${S.data.plan.runner_alive ? "active" : "idle"}</span>` : ""}
    <span class="badge tone-${flowing ? "ok" : "warn"}" role="status" title="${esc(ctx.state.liveStatus?.detail || "")}">${icon(flowing ? "sync" : "alert")}<span>${flowing ? "Live" : live === "off" ? "Live updates off" : "Disconnected"}</span></span>
    <button type="button" class="btn sm chat-inspect-toggle" data-chat="inspect" aria-controls="chat-inspector" aria-expanded="${Boolean(insp?.isOpen())}" aria-keyshortcuts="Alt+Shift+I" title="Show or hide the inspector (${keyLabel("Alt+Shift+I")})">Inspect</button><button type="button" class="icon-btn sm" data-chat="refresh" aria-label="Refresh this session">${icon("sync")}</button>
    ${S.settingsError ? `<p class="callout fail small chat-wide" role="alert">Chat settings: ${esc(S.settingsError)} — fix the <code>chat:</code> block in <a href="${esc(routeHash("config", S.project, ctx.state.selectedTarget))}">Settings</a>.</p>` : ""}`;
}

function messageHtml(original) {
  const m = presentMessage(original, S.data?.tasks?.find((t) => t.row === original.task));
  const who = m.role === "user" ? "You" : m.role === "system" ? "ALFRD" : m.task ? `${agentName(m.agent)} · ${S.data?.tasks?.find((t) => t.row === m.task)?.task?.title || "Task result"}` : m.agent === orchestrator() ? `Orchestrator · ${agentName(m.agent)}` : agentName(m.agent || "alfrd");
  const open = m.task && S.open.get(m.task);
  const t = m.task && S.data?.tasks?.find((x) => x.row === m.task), l = t?.logs?.[t.logs.length - 1];
  return `<article class="chat-msg chat-${esc(m.role)}${m.kind ? ` chat-${esc(m.kind)}` : ""}" data-msg="${esc(m.id)}" aria-label="${esc(who)}">
    <header><b>${esc(who)}</b><time class="muted small" datetime="${esc(m.at)}">${esc(String(m.at || "").slice(11, 16))}</time></header>
    <div class="chat-text">${esc(m.text)}</div>${m.technical ? `<details><summary class="small">Technical details</summary><pre class="log chat-pre">${esc(m.technical)}</pre></details>` : ""}
    ${m.artifacts?.length ? `<ul class="chat-arts" aria-label="Artifacts">${m.artifacts.map((r) => artifactItem(r)).join("")}</ul>` : ""}
    ${l?.log ? `<button type="button" class="btn sm link" data-open-log="${esc(l.log)}">${icon("log")} Open log</button>` : ""}${l?.handoff ? `<button type="button" class="btn sm link" data-open-log="${esc(`${l.handoff}/response.md`)}">${icon("file")} Reply</button>` : ""}
    ${m.task ? `<button type="button" class="btn sm link" data-transcript="${esc(m.task)}" aria-expanded="${Boolean(open)}">${icon(open ? "fold" : "unfold")} ${open ? "Hide" : "Full"} transcript</button>${open ? transcriptHtml(m.task) : ""}` : ""}
  </article>`;
}

const artifactItem = (ref) => {
  const [path, sha] = String(ref).split("@");
  return `<li><span class="mono small" title="${esc(ref)}">${esc(path)}</span> <span class="muted small mono">${esc((sha || "").slice(0, 8))}</span> <button type="button" class="btn sm" data-artifact="${esc(ref)}" aria-label="View ${esc(path)}">View</button></li>`;
};

function transcriptHtml(row) {
  const t = S.open.get(row);
  if (t.error) return `<p class="callout fail small" role="alert">${esc(t.error)}</p>`;
  if (!t.attempts) return `<p class="muted small" role="status">Loading attempts…</p>`;
  if (!t.attempts.length) return `<p class="muted small">No attempt has started yet.</p>`;
  return `<div class="chat-transcript">${t.attempts.map((a) => `<div class="chat-attempt"><div class="row gap wrap"><b class="small">Attempt ${a.attempt}</b><span class="badge tone-${a.status === "done" ? "ok" : a.status === "failed" ? "fail" : "muted"}"><span>${esc(a.status || "")}</span></span><span class="muted small mono">${esc(a.unit)}</span>
      ${["prompt", "response"].map((part) => `<button type="button" class="btn sm" data-part="${esc(row)}|${esc(a.unit)}|${part}" ${a[`${part}_bytes`] ? "" : "disabled"} title="Open with Copy and Find">${part === "prompt" ? "Prompt" : "Reply"} (${Math.ceil((a[`${part}_bytes`] || 0) / 1024)} KiB)</button>`).join("")}</div>
      ${["prompt", "response"].map((part) => { const c = t.parts[`${a.unit}|${part}`]; return c && !c.eof ? `<button type="button" class="btn sm" data-more="${esc(row)}|${esc(a.unit)}|${part}">Load more ${part === "prompt" ? "Prompt" : "Reply"}</button>` : ""; }).join("")}</div>`).join("")}</div>`;
}

function drawLog() {
  const box = $("#chat-log", host);
  if (S.data) { act?.update(S.data, S.notes.filter((n) => n.sid === S.sid)); return; }
  if (!S.sid) box.innerHTML = `<div class="empty"><p>Start a session with <b>New</b>, then ask for something.</p></div>`;
  else if (S.error) box.innerHTML = `<div class="callout fail" role="alert">${esc(S.error)} <button type="button" class="btn sm" data-chat="refresh">Retry</button></div>`;
  else box.innerHTML = `<p class="muted" role="status">Loading the session…</p>`;
}

function drawProgress(ctx) {
  const box = $("#chat-progress", host);
  if (!box) return;
  const req = activeRequest(S.data);
  const pr = requestProgress(S.data, req);
  const u = S.data?.usage;
  const warned = req ? costWarnings(S.data, req.id) : [];
  const promo = server.session?.mutations_enabled ? promotable(S.data) : null;
  const html = !pr ? `<h3 id="chat-progress-h">Progress</h3><p class="muted small">No request yet.</p>`
    : `<h3 id="chat-progress-h">Request ${esc(pr.id)} <span class="badge tone-${statusInfo(pr.status === "summarizing" ? "running" : pr.status).tone}"><span>${esc(pr.status.replace("_", " "))}</span></span></h3>
      <p class="small chat-req-text" title="${esc(pr.text)}">${esc(pr.text)}</p>
      ${pr.total ? `<div class="chat-bar" role="progressbar" aria-label="Tasks done" aria-valuemin="0" aria-valuemax="${pr.total}" aria-valuenow="${pr.done}" aria-valuetext="${pr.done} of ${pr.total} tasks done"><i style="width:${Math.round((pr.done / pr.total) * 100)}%"></i></div>
        <p class="small">${pr.done}/${pr.total} done${pr.running ? ` · ${pr.running} running` : ""}${pr.failed ? ` · ${pr.failed} failed or stopped` : ""}</p>` : `<p class="muted small">${pr.mode === "direct" ? "Direct message" : "Planning…"}</p>`}
      ${meter("Cost", pr.cost, pr.maxCost, pr.costRatio, money(req?.usage?.total_cost_usd), pr.maxCost ? money(pr.maxCost) : "no limit", pr.tone)}
      ${meter("Time", pr.seconds, pr.maxSeconds, pr.timeRatio, duration(pr.seconds), pr.maxSeconds ? duration(pr.maxSeconds) : "no limit", pr.tone)}
      <p class="muted small" data-tokens="request">${esc(tokenLine(req?.usage) || "no tokens reported yet")}</p>
      <p class="muted small">${pr.calls} orchestrator call${pr.calls === 1 ? "" : "s"}${S.data?.settings?.orchestrator_model ? ` (${esc(S.data.settings.orchestrator_model)}, escalation ${esc(S.data.settings.escalation_model || "agent model")})` : ""}${req?.usage?.total_cost_usd == null ? " · cost not reported by these agents" : ""}</p>
      ${warned.length ? `<p class="small"><span class="badge tone-warn" role="status">${icon("alert")}<span>${warned.length} task${warned.length === 1 ? "" : "s"} over the ${money(S.data?.settings?.warn_task_cost_usd)} warning</span></span></p>` : ""}
      ${promo ? `<button type="button" class="btn sm" data-chat="promote" data-request="${esc(promo.id)}" title="Add request ${esc(promo.id)}'s tasks to the active workflow (preview first)">${icon("workflow")} Promote to workflow</button>` : ""}`;
  const total = u ? `<p class="muted small chat-total" data-tokens="session">Session: ${u.turns} turn${u.turns === 1 ? "" : "s"} · ${esc(tokenLine(u) || money(u.total_cost_usd))} · ${u.orchestrator_calls} orchestrator call${u.orchestrator_calls === 1 ? "" : "s"}</p>` : "";
  if (box.dataset.html !== html + total) { box.dataset.html = html + total; box.innerHTML = html + total; }
}

const meter = (label, value, max, ratio, shown, limit, tone) => `<div class="chat-meter"><span class="small">${label}</span>
  <meter min="0" max="${max || 1}" value="${max ? Math.min(value, max) : 0}" low="${(max || 1) * 0.6}" high="${(max || 1) * 0.8}" optimum="0" aria-label="${label} used: ${esc(shown)} of ${esc(limit)}"></meter>
  <span class="small tabular ${ratio >= 1 ? "fail-t" : ratio >= 0.8 ? "warn-t" : ""}">${esc(shown)} / ${esc(limit)}</span></div>`;

function drawDecisions() {
  const box = $("#chat-decisions", host);
  const list = S.data?.blockers || [];
  const done = (S.data?.decisions || []).filter((d) => d.status !== "pending").slice(-5);
  if (!changed("decisions", [list, done.map((d) => d.id)])) return;
  if (box.contains(document.activeElement) && list.some((d) => box.querySelector(`[data-decision="${CSS.escape(d.id)}"]`))) {
    // Someone is answering: keep their input; only the list of pending ids decides a rebuild.
    if (JSON.stringify([...$$("[data-decision]", box)].map((f) => f.dataset.decision)) === JSON.stringify(list.filter((d) => d.needs === "user").map((d) => d.id))) return;
  }
  box.innerHTML = `<h3 id="chat-decisions-h">Blockers and decisions ${list.length ? `<span class="badge tone-warn"><span>${list.length}</span></span>` : ""}</h3>
    ${list.length ? list.map((d) => d.mailbox_message_id ? `<p class="small"><b>${esc(d.question || d.reason)}</b> <button type="button" class="btn sm link" data-mail-jump="${esc(d.mailbox_message_id)}">Answer in conversation</button></p>` : d.needs === "user" ? `<form class="chat-decision" data-decision="${esc(d.id)}" aria-label="Decision for task ${esc(d.task)}">
      <p class="small"><b>${esc(d.question || d.reason)}</b></p>${d.question && d.reason !== d.question ? `<p class="muted small">${esc(d.reason)}</p>` : ""}
      <p class="muted small">Task <span class="mono">${esc(d.task)}</span></p>${d.kind === "permission" ? `<p class="small warn-t">${d.repeat ? `Blocked ${esc(d.repeat)}× with the same access. ` : ""}An approval cannot reach a running agent: grant access, reassign or cancel to change the next run.</p>` : ""}
      ${d.options?.length ? `<fieldset><legend class="sr-only">Options</legend>${d.options.map((o, i) => `<label class="row gap small"><input type="radio" name="choice" value="${esc(o)}" ${i ? "" : ""}> ${esc(o)}</label>`).join("")}</fieldset>` : ""}
      <label class="field small"><span>${d.options?.length ? "Or answer (optional)" : "Answer"}</span><textarea class="input" name="answer" rows="2" maxlength="8000"></textarea></label>
      <p class="fail-t small" role="alert" data-decision-error></p>
      <button type="submit" class="btn sm primary">Send decision</button></form>`
    : `<p class="small muted">${esc(d.question || d.reason)} — the orchestrator is deciding.</p>`).join("")
    : `<p class="muted small">Nothing needs a decision.</p>`}
    ${done.length ? `<details><summary class="small">Decided (${done.length})</summary><ul class="small">${done.map((d) => `<li>${esc(d.question || d.reason)} → <b>${esc(d.choice || d.answer || d.status)}</b>${d.decided_by ? ` <span class="muted">(${esc(d.decided_by)})</span>` : ""}</li>`).join("")}</ul></details>` : ""}`;
}

function drawTask(ctx) {
  const box = host && $("#chat-task", host);
  if (!box) return;
  const t = S.data?.tasks?.find((x) => x.row === S.task);
  if (!changed("task", [t, S.open.get(S.task)])) return;
  box.hidden = !t;
  if (!t) { box.innerHTML = ""; return; }
  const task = t.task || {}, info = statusInfo(t.status), b = task.budget || {};
  const open = S.open.get(t.row);
  box.tabIndex = -1;
  box.innerHTML = `<div class="row gap"><h3 class="grow">${esc(task.title || t.row)}</h3><button type="button" class="icon-btn sm" data-chat="close-task" aria-label="Close task details">${icon("close")}</button></div>
    <p><span class="badge tone-${info.tone}">${icon(info.icon)}<span>${esc(info.label)}</span></span> <span class="small">@${esc(t.owner)} · ${esc(agentName(t.owner))}</span> <span class="muted small mono">${esc(t.row)}</span></p>
    ${task.role ? `<p class="small muted">Role: ${esc(task.role)}</p>` : ""}
    ${task.description ? `<p class="small chat-text">${esc(task.description)}</p>` : ""}
    ${task.acceptance_criteria?.length ? `<h4>Acceptance criteria</h4><ul class="small">${task.acceptance_criteria.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    <dl class="chat-dl small">
      <dt>Budget</dt><dd>${[b.max_cost_usd != null ? `${money(b.max_cost_usd)}` : "", b.max_seconds ? duration(b.max_seconds) : "", b.max_retries != null ? `${b.max_retries} retr${b.max_retries === 1 ? "y" : "ies"}` : ""].filter(Boolean).join(" · ") || "request bounds"}</dd>
      <dt>Depends on</dt><dd>${(t.depends_on || []).map((d) => `<span class="mono">${esc(d)}</span>`).join(", ") || "nothing"}</dd>
      <dt>Attempts</dt><dd>${t.attempts || 0}${t.model ? ` · model ${esc(t.model)}` : ""}</dd>
      <dt>Tokens</dt><dd data-tokens="task">${esc(tokenLine(t.usage) || "not reported")}${t.cost_warning ? ` <span class="badge tone-warn">${icon("alert")}<span>over the ${money(t.cost_warning.bound)} warning</span></span>` : ""}</dd>
      ${task.expected_outputs?.length ? `<dt>Expected outputs</dt><dd>${task.expected_outputs.map((o) => `<span class="mono">${esc(typeof o === "string" ? o : o.path)}</span>`).join(", ")}</dd>` : ""}
    </dl>
    ${t.result?.summary ? `<h4>Result</h4><p class="small chat-text">${esc(t.result.summary)}</p>` : ""}
    ${t.result?.artifacts?.length ? `<h4>Artifacts</h4><ul class="chat-arts">${t.result.artifacts.map(artifactItem).join("")}</ul>` : ""}
    ${t.error ? `<p class="callout fail small">${esc(t.error)}</p>` : ""}
    ${t.logs?.length ? `<h4>Attempts</h4><ul class="small chat-logs">${t.logs.map((l, i) => `<li><span class="mono">${i + 1}</span> ${esc(l.status || "")}${l.model ? ` · ${esc(l.model)}` : ""} <span class="muted">${esc(tokenLine({ ...l.usage, total_cost_usd: l.total_cost_usd }))}</span>
      ${l.log ? `<button type="button" class="btn sm link" data-open-log="${esc(l.log)}" aria-label="Open the log of attempt ${i + 1}">${icon("log")} Open log</button>` : ""}<button type="button" class="btn sm link" data-open-log="${esc(`${l.handoff}/response.md`)}" aria-label="Open the reply (handoff) of attempt ${i + 1}">${icon("file")} Handoff</button></li>`).join("")}</ul>` : ""}
    <div class="row gap wrap">
      ${canCancel(t) ? `<button type="button" class="btn sm danger" data-task-act="cancel" data-row="${esc(t.row)}">${icon("stop")} Cancel</button>` : ""}
      ${canRetry(t) ? `<button type="button" class="btn sm" data-task-act="retry" data-row="${esc(t.row)}">${icon("reset")} Retry</button>` : ""}
      <button type="button" class="btn sm" data-transcript="${esc(t.row)}" aria-expanded="${Boolean(open)}">${icon(open ? "fold" : "unfold")} ${open ? "Hide" : "Full"} transcript</button>
    </div>${open ? transcriptHtml(t.row) : ""}`;
}

// --- actions ---------------------------------------------------------------------

async function toggleTranscript(row) {
  if (S.open.has(row)) { S.open.delete(row); ctxRef.update(); return; }
  const t = { attempts: null, parts: {}, error: null };
  S.open.set(row, t); ctxRef.update();
  try { t.attempts = (await get(`${base(S.project)}/sessions/${encodeURIComponent(S.sid)}/tasks/${encodeURIComponent(row)}/transcript`)).attempts; }
  catch (error) { t.error = error.message; }
  S.keys.log = S.keys.task = null; ctxRef.update();
}

async function viewPart(row, unit, part) {
  const t = S.open.get(row), a = t?.attempts?.find((x) => x.unit === unit), c = t?.parts[`${unit}|${part}`];
  if (!a || !c) return;
  try {
    const { openFull } = await import("./logview.js");
    await openFull(ctxRef, part === "prompt" ? "Prompt" : "Reply", c.content,
      c.eof ? row : `${row} · Partial text; Load more in the transcript`, null,
      { unit, attempt: a.attempt, latest: !t.attempts.some((x) => x.attempt > a.attempt) });
  } catch (error) { ctxRef.toast(error.message, "warn"); }
}

async function loadPart(row, unit, part, more = false) {
  const t = S.open.get(row);
  if (!t) return;
  const key = `${unit}|${part}`;
  if (!more && t.parts[key]) return viewPart(row, unit, part);
  const prev = more ? t.parts[key] : null;
  try {
    const page = await get(`${base(S.project)}/sessions/${encodeURIComponent(S.sid)}/tasks/${encodeURIComponent(row)}/transcript/${encodeURIComponent(unit)}/${part}?offset=${prev ? prev.offset : 0}`);
    t.parts[key] = { content: (prev?.content || "") + page.content, offset: page.offset + page.length, eof: page.eof };
    if (S.open.get(row) === t) await viewPart(row, unit, part);
  } catch (error) { t.error = error.message; }
  S.keys.log = S.keys.task = null; ctxRef.update();
}

async function viewArtifact(ref) {
  const ctx = ctxRef;
  let text = "", offset = 0, eof = false, info = null;
  const more = async () => {
    const page = await get(`${base(S.project)}/sessions/${encodeURIComponent(S.sid)}/artifacts?${new URLSearchParams({ ref, offset })}`);
    info = page; text += page.content ?? ""; offset = page.offset + page.length; eof = page.eof;
  };
  try { await more(); } catch (error) { ctx.toast(`Artifact: ${error.status === 409 ? "the file changed since this version was recorded" : error.message}`, "fail"); return; }
  ctx.modal(`<header class="modal-h"><h2 class="mono">${esc(info.path)}</h2><span class="muted small mono">${esc(String(info.sha256).slice(0, 12))} · ${Math.ceil(info.size / 1024)} KiB</span><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><pre class="log log-full" id="chat-art"></pre><button type="button" class="btn sm" id="chat-art-more" hidden>Load more</button></div>`, (root) => {
    const show = () => { $("#chat-art", root).textContent = text; $("#chat-art-more", root).hidden = eof; };
    $("#chat-art-more", root).onclick = async () => { try { await more(); show(); } catch (error) { ctx.toast(error.message, "fail"); } };
    show();
  }, "full");
}

async function send() {
  const input = $("#chat-input", host);
  const text = input.value.trim();
  if (!text || S.sending || !S.sid) return;
  const mention = parseMention(text, agents());
  if (mention?.error) { S.sendError = mention.error; draw(ctxRef); return; }
  S.sending = true; S.sendError = ""; draw(ctxRef);
  const sid = S.sid;
  try {
    await post(S.project, `/sessions/${encodeURIComponent(sid)}/messages`, { text });
    if (S.sid === sid) { input.value = ""; S.drafts.delete(sid); composer?.close(); }
    loadSession(); loadSessions();
  } catch (error) { S.sendError = `Not sent: ${error.message}`; }
  S.sending = false; draw(ctxRef);
  input.focus();
}

async function newSession() {
  try {
    const { session } = await post(S.project, "/sessions", {});
    S.sessions = [session, ...(S.sessions || [])]; S.keys.sessions = null;
    openSession(session.id);
    ctxRef.update();
    requestAnimationFrame(() => $("#chat-input", host)?.focus());
    loadSessions();
  } catch (error) { ctxRef.toast(`New session: ${error.message}`, "fail"); }
}

const bound = new WeakSet(); // the view element outlives mounts (project switches): delegate on it only once

function bind(el) {
  // The composer's own elements are new on every mount, so it is bound every time.
  composer = bindComposer(el, "chat", { agents, orchestrator, send, onCommand: command, onError: (m) => { S.sendError = m; draw(ctxRef); }, onDraft: (text) => { S.drafts.set(S.sid, text); if (S.sendError) { S.sendError = ""; $("#chat-send-error", host).textContent = ""; } } });
  if (bound.has(el)) return;
  bound.add(el);
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button, [data-agent], a[data-sid]");
    if (!b) return;
    if (b.matches("a[data-sid]")) { e.preventDefault(); if (b.dataset.sid !== S.sid) { openSession(b.dataset.sid); ctxRef.update(); } return; }
    const a = b.dataset.chat;
    if (a === "new") newSession();
    else if (a === "needs-me") { needsOnly = !needsOnly; b.setAttribute("aria-pressed", String(needsOnly)); drawSessions(); }
    else if (a === "rename") startRename(b.dataset.sidAct);
    else if (a === "rename-cancel") endRename(S.renaming);
    else if (a === "delete") deleteSession(b.dataset.sidAct);
    else if (a === "collapse") setCollapsed(true);
    else if (a === "sessions" && wide() && $(".chat", host).classList.contains("chat-sessions-collapsed")) setCollapsed(false);
    else if (a === "refresh") { loadSession(); loadSessions(); loadSettings(); }
    else if (a === "reload") loadSessions();
    else if (a === "close-task") { S.task = null; S.keys.task = null; ctxRef.update(); }
    else if (a === "promote") promoteRequest(b.dataset.request);
    else if (a === "graph-full") openGraphFull();
    else if (a === "inspect") insp?.toggle();
    else if (a === "sessions") {
      const open = $(".chat", host).classList.toggle("chat-sessions-open");
      b.setAttribute("aria-expanded", String(open));
    }
    else if (b.dataset.inspTab) {
      inspTab = b.dataset.inspTab;
      const box = $("#chat-insp-tabs", host);
      box.innerHTML = tabsHtml(inspTab);
      draw(ctxRef);
      $(`[data-insp-tab="${b.dataset.inspTab}"]`, box)?.focus();
    }
    else if (b.dataset.mailJump) {
      const card = host.querySelector(`[data-mail-id="${CSS.escape(b.dataset.mailJump)}"]`);
      if (card) { card.scrollIntoView({ block: "center" }); card.querySelector("button,textarea")?.focus(); insp?.close({ restoreFocus: false }); }
    }
    else if (b.dataset.openLog) openLog(ctxRef, S.project, b.dataset.openLog);
    else if (b.dataset.transcript) toggleTranscript(b.dataset.transcript);
    else if (b.dataset.part) loadPart(...b.dataset.part.split("|"));
    else if (b.dataset.more) loadPart(...b.dataset.more.split("|"), true);
    else if (b.dataset.artifact) viewArtifact(b.dataset.artifact);
    else if (b.dataset.taskAct) taskAction(b.dataset.taskAct, b.dataset.row, b);
  });
  el.addEventListener("change", (e) => {
    if (!e.target.matches("[data-request-scope]")) return;
    scope.select(e.target.value); S.task = null; draw(ctxRef);
  });
  el.addEventListener("submit", async (e) => {
    e.preventDefault();
    const rename = e.target.closest("[data-rename]");
    if (rename) { saveRename(rename.dataset.rename, rename.elements.title.value); return; }
    const form = e.target.closest("[data-decision]");
    if (!form) return;
    const d = S.data?.blockers?.find((x) => x.id === form.dataset.decision);
    const body = decisionPayload(d, { choice: form.querySelector("[name=choice]:checked")?.value, answer: form.querySelector("[name=answer]").value });
    const err = form.querySelector("[data-decision-error]");
    if (body.error) { err.textContent = body.error; return; }
    const button = form.querySelector("button[type=submit]"); button.disabled = true;
    try { await post(S.project, `/sessions/${encodeURIComponent(S.sid)}/decisions/${encodeURIComponent(d.id)}`, body); ctxRef.toast("Decision sent; the task continues", "ok"); S.keys.decisions = null; loadSession(); }
    catch (error) { err.textContent = error.message; button.disabled = false; }
  });
  el.addEventListener("keydown", (e) => {
    if (insp?.onKeydown(e)) return;
    if (e.key === "F2" && e.target.matches?.("a[data-sid]")) { e.preventDefault(); startRename(e.target.dataset.sid); return; }
    if (e.key === "Delete" && e.target.matches?.("a[data-sid]")) { e.preventDefault(); deleteSession(e.target.dataset.sid); return; }
    if (e.key !== "Escape" || e.defaultPrevented) return;
    if (e.target.closest("[data-rename]")) { e.preventDefault(); endRename(S.renaming); return; }
    const chat = $(".chat", host);
    if (e.target.closest(".chat-sessions") && chat.classList.contains("chat-sessions-open")) {
      chat.classList.remove("chat-sessions-open"); $("[data-chat=sessions]", host)?.setAttribute("aria-expanded", "false"); $("[data-chat=sessions]", host)?.focus(); e.preventDefault();
    }
  });
}

async function taskAction(action, row, button) {
  button.disabled = true;
  try {
    const out = await post(S.project, `/sessions/${encodeURIComponent(S.sid)}/tasks/${encodeURIComponent(row)}/${action}`);
    ctxRef.toast(action === "cancel" ? `Task ${row} cancelled` : `Task ${row} queued again${out.requeued?.length > 1 ? ` with ${out.requeued.length - 1} dependent task(s)` : ""}`, "ok");
    loadSession();
  } catch (error) { ctxRef.toast(`${action === "cancel" ? "Cancel" : "Retry"}: ${error.message}`, "fail"); button.disabled = false; }
}

// --- promote to workflow ---------------------------------------------------------

/** Preview request `rid`'s tasks as workflow steps (server: chat.promotion), then save them into the active alfrd.yaml. */
async function promoteRequest(rid) {
  const ctx = ctxRef, p = S.project, sid = S.sid;
  const url = `/sessions/${encodeURIComponent(sid)}/requests/${encodeURIComponent(rid)}/promote`;
  let preview;
  try { preview = await post(p, url, { preview: true }); } catch (error) { ctx.toast(`Promote: ${error.message}`, "fail"); return; }
  ctx.modal(`<header class="modal-h"><h2 id="promote-h">${icon("workflow")} Promote request ${esc(rid)} to workflow steps</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p class="small">Adds ${preview.steps.length} Agent step${preview.steps.length === 1 ? "" : "s"} to workflow <b>${esc(preview.workflow)}</b> in <code>${esc(preview.file)}</code>, each with a prompt file written from its task. Nothing is saved until you press Save.</p>
      <table class="tbl small"><caption class="sr-only">Steps to add</caption><thead><tr><th scope="col">Step</th><th scope="col">Agent</th><th scope="col">Waits on</th><th scope="col">Task</th></tr></thead><tbody>
      ${preview.steps.map((st) => `<tr><td class="mono">${esc(st.id)}</td><td>@${esc(st.owner)}${st.model ? ` · ${esc(st.model)}` : ""}</td><td class="mono">${esc(st.depends_on.join(", ") || "—")}</td><td>${esc(st.label)} <span class="muted">(${esc(st.status || "")})</span></td></tr>`).join("")}</tbody></table>
      ${preview.added_entrypoints?.length ? `<p class="muted small">Also adds the entrypoint(s) ${preview.added_entrypoints.map((e) => `<code>${esc(e)}</code>`).join(", ")}.</p>` : ""}
      <details><summary class="small">Changes to ${esc(preview.file)}</summary><pre class="log chat-pre" aria-label="Diff">${esc(preview.diff)}</pre></details>
      <p id="promote-error" class="fail-t small" role="alert"></p></div>
    <footer class="modal-f row gap"><span class="grow"></span><button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn primary" id="promote-save">${icon("save")} Save to alfrd.yaml</button></footer>`, (root, close) => {
    root.setAttribute("aria-labelledby", "promote-h");
    const save = $("#promote-save", root);
    save.focus();
    save.onclick = async () => {
      save.disabled = true;
      try {
        const out = await post(p, url, { preview: false, base_hash: preview.base_hash });
        close();
        ctx.toast(`Added ${out.steps.length} workflow step${out.steps.length === 1 ? "" : "s"} to ${out.workflow}`, "ok");
        await ctx.refreshProject?.(p);
        loadSession();
      } catch (error) {
        $("#promote-error", root).textContent = /changed|409/.test(error.message) ? "alfrd.yaml changed since this preview. Close and promote again to see the current file." : error.message;
        save.disabled = false;
      }
    };
  }, "wide");
}

// --- slash commands, setup, full-screen graph --------------------------------------------

/** A local ALFRD note in the transcript (not sent, not stored: gone on reload). */
function note(text) {
  S.notes.push({ id: `local:${Date.now()}:${S.notes.length}`, sid: S.sid, role: "system", text, at: new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 19) });
  S.keys.log = null; draw(ctxRef);
  const box = $("#chat-log", host); if (box) box.scrollTop = box.scrollHeight;
}

function command(cmd) {
  S.sendError = "";
  if (cmd === "help") note(helpText({ ...(S.settings || {}), ...(S.data?.settings || {}) }, "chat"));
  else if (cmd === "new") newSession();
  else if (cmd === "graph") openGraphFull({ fromComposer: true });
  else if (cmd === "setup") openSetup();
  draw(ctxRef);
}

async function openSetup() {
  const p = S.project;
  try {
    const { openSetup: open } = await import("./chat_setup.js");
    open(ctxRef, p, { onSaved: (settings, summary) => {
      if (p !== S.project) return;
      S.settings = settings || S.settings; loadSettings(); loadSession(); note(summary);
    } });
  } catch (error) { ctxRef.toast(`Setup: ${error.message}`, "fail"); }
}

function openGraphFull({ fromComposer = false, request = null } = {}) {
  if (request) { scope.select(request); draw(ctxRef); }
  if (!fromComposer) $("#chat-graph-full", host)?.focus(); // Esc returns here
  ctxRef.modal(`<header class="modal-h"><h2 id="chat-gfull-h">${icon("graph")} Task graph</h2><span class="muted small">Arrows pick a task, Enter opens it · 0 fits</span><span class="grow"></span><button class="icon-btn" data-close aria-label="Close full screen">${icon("close")}</button></header>
    <div class="modal-b chat-gfull"><div id="chat-gfull"></div></div>`, (root, close) => {
    root.setAttribute("aria-labelledby", "chat-gfull-h");
    const g = mountGraph($("#chat-gfull", root), { taskMenu: graphTaskMenu(close), groups: true, label: "Task graph (full screen)", empty: "Tasks appear here when the orchestrator plans a request.",
      onSelect: (row) => { S.task = row; drawTask(ctxRef); },
      onOpen: (row) => { close(); S.task = row; S.keys.task = null; draw(ctxRef); $("#chat-task", host)?.focus(); } });
    full = { host: root, g };
    g.update({ ...graphModel(S.data?.graph, agents(), scope.request(S.data)), stream: S.sid, scope: `${S.sid}:${scope.request(S.data) || "all"}` }, S.task);
    requestAnimationFrame(() => { g.fit(); g.focus(); });
  }, "full");
}
