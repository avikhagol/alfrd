import { skeletonRows } from "../utils/loading.js";
import { refreshHTML, refreshProtected, sourceHTML } from "../utils/refresh.js";
import { mountPanelResize } from './panel_resize.js';
// Chat section (#/chat/<session>?p=<project>): sessions, transcript, composer with @agent
// autocomplete, task graph (graph_view.js, read-only), task detail with Cancel/Retry, decisions,
// progress and usage against the request's budget. Lazy: loaded on the first visit.
// Session snapshots follow the event stream; the shared transcript (transcript.js) and run components poll bounded
// recorded output and durable mailbox messages. Slash commands
// (/setup → chat_setup.js, /help, /new, /graph) run here. Full transcripts and artifacts load when opened.
import { $, $$, esc, icon, keyLabel, loadCss, storage, routeHash, setRoute } from "../utils/dom.js";
import { server } from "../data/server.js";
import { taskMenuApi } from "./task_menu_api.js";
// The closed inspector owns no graph work or optional panel requests.
function mountGraph(el, options) {
  let graph = null, pending = null, model = null, selected = null;
  return {
    update(m, task) {
      model = m; selected = task;
      if (graph) return graph.update(m, task);
      el.innerHTML = skeletonRows("details");
      pending ||= import("./graph_view.js").then((mod) => {
        if (!el.isConnected) { pending = null; return; }
        el.replaceChildren(); graph = mod.mountGraph(el, options); graph.update(model, selected);
      }).catch((e) => { pending = null; if (el.isConnected) el.textContent = e.message; });
    },
    fit() { if (pending) pending.then(() => graph?.fit()); },
    focus() { if (pending) pending.then(() => graph?.focus()); },
  };
}
import { mountComposerDraft } from "./composer_draft.js";
import { bindComposer, composerHtml } from "./chat_composer.js";
import { bindChatTitle } from "./chat_title.js";
import { openLog } from "./plans.js";
import { mountTranscript } from "./transcript.js";
import { messageHtml as sharedMessageHtml } from "./chat_message.js";
import { activeSession, destinationSession, paintSessionList, filterSessions, chatSheet, conversationStarter } from "../data/chat_sessions.js";
import { createInspector, tabsHtml, createRequestScope } from "./inspector.js";
import {
  activeRequest, canCancel, canRetry, costWarnings, decisionPayload, duration, graphModel, hasActivity, helpText, money,
  parseMention, promotable, requestProgress, requestTotals, staleState, statusInfo, tokenLine, createRequestStop, checkStatus, taskOutputs,
} from "../data/chat.js";

import { chatProjects } from "../data/chat_projects.js";

import { createLRU } from "../data/chat_state.js";
import { snapshots, prefetchSnapshot } from "../data/chat_cache.js";
const views = createLRU(3, (view) => view.destroy());
let activeView = null, identity = null;
function clearViews() { views.clear(); activeView = null; snapshots.clear(); }
server.onAuthRequired(clearViews);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { for (const e of snapshots.cache.values()) snapshots.invalidate(e.project, e.sid); activeView?.refresh(); }
});
export function leave() { return activeView?.suspend(); }
// Navigation can attach an already-mounted destination before the batched shell paint.
export function restore(el, ctx) {
  const p = ctx.activeProject(), sid = ctx.state.chatSession || (storage.get("ui:chat.session", {}) || {})[p] || "";
  if (!views.entries.has(JSON.stringify([p, sid]))) return false;
  render(el, ctx); return true;
}
export function render(el, ctx) {
  const auth = JSON.stringify([location.origin, server.session?.csrf_token, server.session?.projects]);
  if (identity !== auth) { clearViews(); identity = auth; }
  const projects = ctx.projects?.().map((p) => p.id) || [...new Set([ctx.activeProject(), ...[...views.entries.values()].map((v) => v.project())])];
  snapshots.catalog(projects);
  for (const [key, view] of views.entries) if (!projects.includes(view.project())) { views.delete(key); if (view === activeView) activeView = null; }
  const p = ctx.activeProject(), sid = ctx.state.chatSession || (storage.get("ui:chat.session", {}) || {})[p] || "";
  for (const [oldKey, retained] of [...views.entries]) {
    const actual = JSON.stringify([retained.project(), retained.sid() || ""]);
    if (retained.sid() && oldKey !== actual) { views.entries.delete(oldKey); views.set(actual, retained); }
  }
  const key = JSON.stringify([p, sid]);
  let view = views.get(key);
  if (view !== activeView) {
    if (activeView?.suspend() === false) { ctx.state.selectedProject = activeView.project(); ctx.state.chatSession = activeView.sid(); return; }
    if (!view) { view = createChatView(); views.set(key, view); }
    activeView = view; el.replaceChildren(view.root); view.resume();
  } else if (view && (!view.root.isConnected || view.isSuspended())) { el.replaceChildren(view.root); view.resume(); }
  view.render(view.root, ctx);
  if (view.sid()) {
    ctx.state.chatSession = view.sid();
    const remembered = storage.get("ui:chat.session", {}) || {};
    if (remembered[p] !== view.sid()) storage.set("ui:chat.session", { ...remembered, [p]: view.sid() });
    setRoute(routeHash(`chat/${view.sid()}`, ctx.state.selectedProject, ctx.state.selectedTarget), { replace: true });
  }
  const resolved = JSON.stringify([p, view.sid() || ""]);
  if (view.sid() && resolved !== key) { views.entries.delete(key); views.set(resolved, view); }
  el.dataset.chat = p || "";
}

export const canDelegate = (d) => (!d.mailbox_message_id || d.kind === "question") && !d.repeat && !d.delegated;

export function createChatView() {
const root = document.createElement("div");
root.style.display = "contents";
let suspended = false, destroyed = false, savedScroll = [], settingsController = null, prefetchTimer = 0, prefetchSid = null, focusIntent = null;
const S = { renaming: null, project: null, sessions: null, sessionsError: null, settings: null, settingsError: null, sid: null, data: null,
  error: null, loading: false, checking: null, task: null, open: new Map(), sendError: "", gen: 0, keys: {}, notes: [] };
let scope = createRequestScope();
let inspTab = "graph";
let insp = null; // the inspector shell (inspector.js): closed unless the user opened it
let goals = null, goalsLoading = null;
function drawGoals(options) {
  if (goals) return goals.update(options);
  if (!options.host) return;
  options.host.innerHTML = skeletonRows("details");
  goalsLoading ||= import("./goals_tab.js").then((m) => { goals = m.createGoalsTab({ get, refresh: () => loadSession() }); ctxRef?.update(); })
    .catch((e) => { goalsLoading = null; ctxRef?.toast(e.message, "fail"); });
}
let host = null, graph = null, ctxRef = null, refreshTimer = 0, slowTimer = 0, clock = 0, lastLoaded = 0, composer = null, draft = null, act = null, full = null;
const base = (p) => `/api/studio/projects/${encodeURIComponent(p)}/chat`;
const SESSION_KEY = "ui:chat.session";
const COLLAPSE_KEY = "ui:chat.sessionsCollapsed"; // the chat list folded to a rail (wide screens; per browser)
const wide = () => window.innerWidth > 900 && (!host || ($(".chat", host)?.clientWidth || host.clientWidth) >= 572);
let panelResizers = [], layoutObserver;
let needsOnly = false, sessionSheet = null, inspectorSheet = null;
const queries = new Map();
const stop = createRequestStop({ state: () => S.data, context: () => ({ project: S.project, sid: S.sid }),
  cancel: (row, ctx, task) => post(ctx.project, `/sessions/${encodeURIComponent(ctx.sid)}/tasks/${encodeURIComponent(row)}/cancel`, { expected_status: task.status, ...(task.units?.at(-1) ? { expected_unit: task.units.at(-1) } : {}) }),
  refresh: loadSession, changed: () => composer?.refresh(), report: (text, tone) => ctxRef.toast(text, tone) });
const unsubscribeProjects = chatProjects.subscribe((entry) => {
  if (host?.isConnected && entry.project === S.project) {
    S.sessions = entry.sessions; S.sessionsError = entry.error; ctxRef?.update();
  }
});

async function get(url, signal) {
  const res = await server.request(url, { signal, headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => null);
  if (!res.ok) { const e = new Error(body?.error?.message || `${res.status} ${res.statusText}`); e.status = res.status; throw e; }
  return body;
}
const post = (p, path, body = {}, method = "POST") => server.mutate(`/studio/projects/${encodeURIComponent(p)}/chat${path}`, body, method);

const onEvents = (e) => {
  const { project, events } = e.detail || {};
  if (!host || project !== S.project) return;
  const chatEvents = (events || []).filter((ev) => String(ev.plan || "").startsWith("chat-"));
  if (!chatEvents.length) return;
  const mine = chatEvents.some((ev) => ev.plan === `chat-${S.sid}`);
  if (suspended) return;
  scheduleRefresh(mine);
  if (mine) act?.kick();
};
window.addEventListener("alfrd-events", onEvents);
const onResync = ({ detail }) => { if (!suspended && (!detail?.project || detail.project === S.project)) { S.revisionReset = true; loadSession(); } };
window.addEventListener("alfrd-chat-resync", onResync);

function scheduleRefresh(session = true) {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { if (session && S.sid) loadSession(); }, 250);
}

const liveFlowing = (ctx) => ["live", "polling"].includes(ctx.state.liveStatus?.state);

let closeSkills = null;

function leave() {
  if (closeSkills?.() === false) return false; closeSkills = null;
  sessionSheet?.({ restore: false }); inspectorSheet?.({ restore: false }); sessionSheet = inspectorSheet = null;
  filesModule?.leaveFiles(); filesEpoch++;
  goals?.stop();
  clearInterval(slowTimer); clearInterval(clock); slowTimer = clock = 0;
  act?.suspend();
  draft?.suspend?.();
  clearTimeout(refreshTimer); clearTimeout(prefetchTimer); settingsController?.abort();
  snapshots.cancel(S.project, S.sid);
  if (prefetchSid) snapshots.cancel(S.project, prefetchSid);
  layoutObserver?.disconnect(); panelResizers.forEach((r) => r.suspend());
  return true;
}

function render(el, ctx) {
  if (destroyed) return; suspended = false;
  loadCss("css/loading.css");
  loadCss("css/chat.css");
  ctxRef = ctx;
  if (ctx.state.mode !== "server") { host = null; el.innerHTML = `<div class="empty chat-empty"><h2>Chat</h2><p role="status">Chat needs <code>alfrd serve</code>: agents run on the server.</p></div>`; delete el.dataset.chat; return; }
  const p = ctx.activeProject();
  if (!p) return; // app.js asks for a project
  if (el.dataset.chat !== p && mount(el, ctx, p) === false) { ctx.state.selectedProject=S.project; return; }
  host = el;
  const wanted = ctx.state.chatSession || (storage.get(SESSION_KEY, {}) || {})[p] || null;
  if (S.sessions && !S.sessionsError) {
    const sid = destinationSession(S.sessions, S.sid || wanted, ctx.state.chatSession);
    if (sid !== S.sid) openSession(sid, { replace: !ctx.state.chatSession || ctx.state.chatSession !== sid });
  }
  draw(ctx);
  if (!clock) clock = setInterval(() => {
    if (!host?.isConnected) return;
    if (insp?.isOpen() && activeRequest(S.data)?.finished == null) drawProgress(ctxRef);
    drawCheckStatus();
  }, 1000);
  if (!slowTimer) slowTimer = setInterval(() => {
    // Idle checks intentionally write no event. Refresh their timestamps even with a live stream.
    if (host?.isConnected && S.sid && (Date.now() - lastLoaded >= 60000 || (!liveFlowing(ctxRef) && hasActivity(S.data)))) loadSession();
  }, 15000);
}

function graphTaskMenu(close = () => {}) {
  return taskMenuApi({ project: () => S.project, sid: () => S.sid, state: () => S.data,
    refresh: () => loadSession(), openLog: (rel) => openLog(ctxRef, S.project, rel),
    files: (row) => { close(); S.task = row; inspTab = "files"; S.keys.task = null; draw(ctxRef); openInspector(); } });
}

function closeSessions() {
  sessionSheet?.(); sessionSheet = null;
  $(".chat", host)?.classList.remove("chat-sessions-open");
  $("[data-chat=sessions]", host)?.setAttribute("aria-expanded", "false");
}
function syncSheets() {
  if (wide()) closeSessions();
  const chat = $(".chat", host);
  const room = chat?.clientWidth || host?.clientWidth || window.innerWidth;
  chat?.classList.toggle("chat-sessions-sheet", !wide());
  const overlay = insp?.isOpen() && (window.innerWidth <= 1280 || room < (chat?.classList.contains("chat-sessions-collapsed") ? 692 : 868));
  chat?.classList.toggle("chat-inspector-sheet", Boolean(overlay));
  panelResizers.forEach((r) => r.sync());
  if (insp?.isOpen()) drawInspector(ctxRef);
  else { goals?.stop(); filesModule?.leaveFiles(); filesEpoch++; }
  if (overlay && !inspectorSheet) { closeSessions(); inspectorSheet = chatSheet($("#chat-inspector", host), () => { insp.close(); syncSheets(); }); }
  else if (!overlay && inspectorSheet) { inspectorSheet({ restore: false }); inspectorSheet = null; }
}
function openInspector() { closeSessions(); insp?.open(); syncSheets(); }
function closeInspector() { insp?.close({ restoreFocus: false }); syncSheets(); }
const onResize = () => { if (!suspended && host?.isConnected) syncSheets(); };
window.addEventListener("resize", onResize);

function mount(el, ctx, p) {
  if (closeSkills?.() === false) return false; closeSkills = null;
  panelResizers.forEach((r) => r.destroy()); panelResizers = []; layoutObserver?.disconnect();
  host = el;
  sessionSheet?.({ restore: false }); inspectorSheet?.({ restore: false }); sessionSheet = inspectorSheet = null;
  goals?.stop();
  draft?.destroy();
  Object.assign(S, { project: p, sessions: null, sessionsError: null, settings: null, settingsError: null, sid: null, data: null, error: null,
    task: null, sendError: "", keys: {}, renaming: null, gen: S.gen + 1 });
  act?.stop();
  scope = createRequestScope(); inspTab = "graph";
  S.sessionQuery = queries.get(p) || "";
  S.open.clear();
  el.dataset.chat = p;
  const folded = storage.get(COLLAPSE_KEY, false) === true;
  el.innerHTML = `<div class="chat${folded ? " chat-sessions-collapsed" : ""}">
    <nav class="chat-sessions" id="chat-sessions" aria-label="Chat sessions"><div class="chat-sessions-resize"></div><div class="row gap chat-sessions-top"><span class="sr-only" tabindex="-1" aria-label="Chat sessions"></span>
      <button type="button" class="chat-new" data-chat="new" aria-label="Start a new chat session" title="Start a new chat session">${icon("plus")}<span aria-hidden="true">New chat</span></button>
      <button type="button" class="icon-btn sm chat-collapse" data-chat="collapse" aria-controls="chat-sessions" aria-expanded="true" aria-label="Hide the chat list" title="Hide the chat list">${icon("sidebar")}</button><button type="button" class="icon-btn chat-close-sessions" data-chat="close-sessions" aria-label="Close chat list">${icon("close")}</button></div>
      <div class="chat-search"><label class="sr-only" for="chat-session-search">Search sessions</label>${icon("search")}<input class="input sm" type="search" id="chat-session-search" placeholder="Search chats" value="${esc(queries.get(p) || "")}"><button type="button" class="icon-btn sm" data-chat="clear-search" aria-label="Clear search" title="Clear search">${icon("close")}</button></div>
      <div class="chat-sec-h chat-recents-h"><span class="grow">Chats</span><button type="button" class="chat-chip" data-chat="needs-me" aria-pressed="${needsOnly}">Needs me</button></div>
      <p class="sr-only" id="chat-session-count" role="status"></p>
      <ul class="chat-list" id="chat-list" role="list"></ul></nav>
    <section class="chat-main" aria-label="Conversation">
      <header class="chat-head" id="chat-head"></header>
      <p class="sr-only" id="chat-check-feedback" role="status" aria-live="polite" aria-atomic="true"></p>
      <div class="chat-log" id="chat-log" tabindex="0" aria-label="Messages"></div>
      <p class="chat-stop-status muted small" role="status"></p>
      ${composerHtml("chat", { attachments: true })}
    </section>
    <aside class="chat-side" id="chat-inspector" tabindex="-1" aria-label="Inspector: request progress, decisions and tasks"><div class="chat-inspector-resize"></div><div class="chat-inspector-content">
      <section class="card chat-insp" id="chat-insp" aria-label="Graph, files and goals"><div id="chat-request-scope"></div><div id="chat-insp-tabs">${tabsHtml()}</div></section>
      <section class="card" id="chat-progress" aria-labelledby="chat-progress-h"></section>
      <section class="card" id="chat-decisions" aria-labelledby="chat-decisions-h"></section>
      <section class="card chat-graph-card" aria-labelledby="chat-graph-h"><h3 id="chat-graph-h"><span class="grow">Task graph</span><button type="button" class="icon-btn sm" id="chat-graph-full" data-chat="graph-full" aria-label="Task graph full screen" aria-keyshortcuts="F" title="Full screen (F)">${icon("expand")}</button></h3><div id="chat-graph"></div></section>
      <section class="card" id="chat-task" aria-live="polite" hidden></section>
    </div></aside></div>`;
  graph = mountGraph($("#chat-graph", el), { onMail: (request, id) => { closeInspector(); act?.jumpMail(request, id); }, taskMenu: graphTaskMenu(), groups: true, label: "Task graph", empty: "Tasks appear here when the orchestrator plans a request.",
    onSelect: (row) => { S.task = row; drawTask(ctxRef); }, onOpen: (row) => { S.task = row; drawTask(ctxRef); $("#chat-task", host)?.focus(); } });
  act = mountTranscript($("#chat-log", el), { project: () => S.project, sid: () => S.sid, busy: () => hasActivity(S.data), agentName, state: () => S.data, error: () => S.error, messageHtml, viewGraph: (request) => openGraphFull({ request }), onReply: () => loadSession(), notes: () => S.notes.filter((n) => n.sid === S.sid && n.project === S.project),
    select: (row) => { S.task = row; S.keys.task = null; drawTask(ctxRef); openInspector(); $("#chat-task", host)?.focus(); },
    inspect: openInspector,
    openLog: (rel, meta) => openLog(ctxRef, S.project, rel, meta), cancel: (row, b) => taskAction("cancel", row, b) });
  $(".chat-graph-card", el).addEventListener("keydown", (e) => {
    if ((e.key === "f" || e.key === "F") && !e.ctrlKey && !e.metaKey && !e.altKey && e.target.closest(".gv-canvas")) { e.preventDefault(); openGraphFull(); }
  });
  bind(el);
  const toggle = () => $("[data-chat=inspect]", el);
  insp = createInspector({ chat: $(".chat", el), panel: $("#chat-inspector", el), button: { setAttribute: (k, v) => toggle()?.setAttribute(k, v) },
    focus: (target) => { inspectorSheet?.({ restore: false }); inspectorSheet = null; (target?.isConnected ? target : toggle())?.focus(); } });
  insp.restore();
  const chat = $(".chat", el), sessions = $("#chat-sessions", el), inspector = $("#chat-inspector", el);
  panelResizers = [
    mountPanelResize({ panel: sessions, handle: $(".chat-sessions-resize", el), key: "ui:chat.sessionsWidth", min: 160, max: 360, defaultWidth: 220,
      label: "Resize chat sessions", enabled: () => wide() && !chat.classList.contains("chat-sessions-collapsed"),
      available: () => chat.clientWidth - 52 - 360 - (insp.isOpen() && !chat.classList.contains("chat-inspector-sheet") ? inspector.offsetWidth + 16 : 0),
      apply: (w) => chat.style.setProperty("--sessions-width", `${w}px`) }),
    mountPanelResize({ panel: inspector, handle: $(".chat-inspector-resize", el), key: "ui:chat.inspectorWidth", min: 280, max: 560, defaultWidth: 320, side: "right",
      label: "Resize chat inspector", enabled: () => insp.isOpen() && !chat.classList.contains("chat-inspector-sheet"),
      available: () => chat.clientWidth - 52 - 360 - (wide() && !chat.classList.contains("chat-sessions-collapsed") ? sessions.offsetWidth + 16 : 0),
      apply: (w) => chat.style.setProperty("--inspector-width", `${w}px`) })];
  layoutObserver = typeof ResizeObserver === "function" ? new ResizeObserver(syncSheets) : null; layoutObserver?.observe($(".chat", el));
  syncSheets();
  const cachedProject = chatProjects.snapshot(p);
  S.sessions = cachedProject?.sessions || null; S.sessionsError = cachedProject?.error || null;
  loadSessions(false);
  loadSettings();
}

// --- loading ----------------------------------------------------------------------

async function loadSessions(refresh = true) {
  const p = S.project;
  if (suspended || destroyed) { if (refresh) chatProjects.invalidate(p); return; }
  chatProjects.select(p);
  const entry = await chatProjects.load(p, true, refresh);
  if (p !== S.project || !entry) return;
  S.sessions = entry.sessions; S.sessionsError = entry.error;
  ctxRef?.update();
}

async function loadSettings() {
  const p = S.project;
  settingsController?.abort(); settingsController = new AbortController();
  try { const s = await get(`${base(p)}/settings`, settingsController.signal); if (p === S.project) { S.settings = s; S.settingsError = null; } }
  catch (error) { if (error.name === "AbortError") return; if (p === S.project) S.settingsError = error.message; }
  ctxRef?.update();
}

async function loadSession(force = true) {
  const p = S.project, sid = S.sid, gen = ++S.gen;
  if (!sid) return false;
  let accepted = false;
  S.loading = !S.data;
  try {
    const data = await snapshots.load(p, sid, "", { force });
    if (!data || suspended || destroyed) return false;
    if (gen !== S.gen || p !== S.project || sid !== S.sid) return false; // a newer request or another session won
    if (!S.revisionReset && staleState(S.data, data)) { S.loading = false; return false; }
    const was = hasActivity(S.data);
    S.data = data; S.revisionReset = false; S.error = null; lastLoaded = Date.now();
    accepted = true;
    if (hasActivity(data) && !was) act?.kick();
  } catch (error) { if (gen === S.gen && !suspended) { if ([401, 403, 404].includes(error.status)) { S.data = null; act?.reset(); } S.error = error.status === 404 ? "This session is no longer available. Refresh sessions or start a New session." : `Couldn’t load this conversation. ${error.message}`; } }
  S.loading = false;
  ctxRef?.update();
  return accepted;
}

function openSession(sid, { replace = false } = {}) {
  scope = createRequestScope();
  closeSessions();
  S.sid = sid; S.data = snapshots.peek(S.project, sid)?.data || null; S.error = null; S.task = null; S.open.clear(); S.gen++; S.keys = {};
  const feedback = $("#chat-check-feedback", host);
  if (feedback) feedback.textContent = "";
  const remembered = storage.get(SESSION_KEY, {}) || {};
  if (sid) storage.set(SESSION_KEY, { ...remembered, [S.project]: sid });
  const st = ctxRef.state;
  st.chatSession = sid || "";
  setRoute(routeHash(sid ? `chat/${sid}` : "chat", st.selectedProject, st.selectedTarget), { replace });
  draft?.select(S.project, sid);
  act?.reset();
  if (sid) loadSession(false);
}

// --- drawing ---------------------------------------------------------------------

const changed = (name, value) => { const k = JSON.stringify(value); if (S.keys[name] === k) return false; S.keys[name] = k; return true; };
const agents = () => S.data?.settings?.agents || S.settings?.agents || {};
const orchestrator = () => S.data?.settings?.orchestrator || S.settings?.orchestrator;
const agentName = (key) => (key === "alfrd" ? "ALFRD" : `${agents()[key]?.label || key}`);

let filesModule = null, filesEpoch = 0;
function drawFilesTab(ctx, request) {
  const panel = $("#chat-insp-panel", host), project = S.project, sid = S.sid;
  const key = JSON.stringify([project, sid, request, S.data?.rev]);
  const epoch = ++filesEpoch;
  if (inspTab !== "files") { filesModule?.leaveFiles(); return; }
  const current = () => panel.isConnected && inspTab === "files" && S.project === project && S.sid === sid && scope.request(S.data) === request;
  import("./files_tab.js").then((m) => {
    if (epoch !== filesEpoch || !current()) return;
    filesModule = m;
    const url = `${base(project)}/sessions/${encodeURIComponent(sid)}`;
    m.drawFiles(panel, { key, isCurrent: current,
      rows: request ? (S.data?.graph?.nodes || []).filter((n) => n.request === request).map((n) => n.id) : null,
      getOverview: () => sid ? get(`${url}/overview`) : Promise.resolve({ files: [] }),
      raw: (ref) => m.rawBlob(url, ref), view: (path, blob, guard) => m.openBlob(ctx, path, blob, guard), reveal: (path, guard) => m.revealFile(ctx, project, path, guard) });
  }).catch((err) => { if (current()) panel.textContent = err.message; });
}

function draw(ctx) {
  if (suspended || destroyed) return;
  drawSessions();
  drawHead(ctx);
  drawLog();
  if (full?.host.isConnected) full.g.update({ ...graphModel(S.data?.graph, agents(), scope.request(S.data), S.data?.requests), stream: S.sid, scope: `${S.sid}:${scope.request(S.data) || "all"}` }, S.task);
  draft?.select(S.project, S.sid);
  $("#chat-send-error", host).textContent = S.sendError || "";
  composer?.refresh();
  $(".chat-stop-status", host).textContent = stop.state().reason || "";
  syncSheets();
}

function drawInspector(ctx) {
  drawProgress(ctx);
  drawDecisions();
  const scopeHtml = scope.html(S.data);
  refreshHTML($("#chat-request-scope", host), scopeHtml);
  const request = scope.request(S.data);
  $("#chat-insp-panel", host).dataset.request = request || "all";
  $("#chat-insp-panel", host).hidden = inspTab === "graph";
  $(".chat-graph-card", host).hidden = inspTab !== "graph";
  const model = { ...graphModel(S.data?.graph, agents(), request, S.data?.requests), stream: S.sid, scope: `${S.sid}:${request || "all"}` };
  if (inspTab === "graph") graph?.update(model, S.data ? S.task : null);
  if (full?.host.isConnected) full.g.update(model, S.task); else full = null;
  drawFilesTab(ctx, request);
  drawGoals({ host: inspTab === "goals" ? $("#chat-insp-panel", host) : null, state: S.data, project: S.project, sid: S.sid, request });
  drawTask(ctx);
}

function drawSessions() {
  const box = $("#chat-list", host);
  box.setAttribute?.("aria-busy", String(!S.sessions && !S.sessionsError));
  // a refresh while a name is being edited keeps the input (and what was typed) as it is
  if (S.renaming && box.querySelector(`[data-rename="${CSS.escape(S.renaming)}"]`) && S.sessions?.some((x) => x.id === S.renaming)) return;
  if (!changed("sessions", [S.sessions, S.sessionsError, S.sid, S.renaming, needsOnly, S.sessionQuery])) return;
  if (S.sessionsError) { box.innerHTML = `<li class="callout fail small" role="alert">${esc(S.sessionsError)} <button type="button" class="btn sm" data-chat="reload">Retry</button></li>`; return; }
  if (!S.sessions) { box.innerHTML = `<li>${skeletonRows("sessions")}</li>`; return; }
  const name = ctxRef.projectName?.(S.project) || S.project; // the identifier is not for people
  if (!S.sessions.length) { box.innerHTML = `<li class="muted small">No conversations in ${esc(name)}. <button type="button" class="btn sm" data-chat="new">New session</button><button type="button" class="btn sm" data-chat="setup">Setup</button></li>`; return; }
  const query = S.sessionQuery || "";
  const sessions = typeof filterSessions === "function" ? filterSessions(S.sessions, query, needsOnly) : S.sessions;
  const count = $("#chat-session-count", host);
  if (count) count.textContent = `${sessions.length} of ${S.sessions.length} sessions${S.sessions.some((s) => activeSession(s)) ? "" : ` · No active chats in ${name}`}`;
  const hiddenCurrent = S.sid && !sessions.some((s) => s.id === S.sid);
  const explanation = hiddenCurrent ? `<li class="muted small">Current conversation is outside the current filter. <button type="button" class="btn sm" data-chat="clear-filters">Clear filters</button></li>` : "";
  paintSessionList(box, explanation + (sessions.map((s) => s.id === S.renaming ? `<li class="chat-session-item"><form class="chat-rename" data-rename="${esc(s.id)}">
      <label class="sr-only" for="chat-rename-input">Chat name</label><input class="input sm" id="chat-rename-input" name="title" maxlength="120" value="${esc(s.title)}" required>
      <button type="submit" class="icon-btn sm" aria-label="Save the name" title="Save (Enter)">${icon("check")}</button><button type="button" class="icon-btn sm" data-chat="rename-cancel" aria-label="Keep the old name" title="Cancel (Esc)">${icon("close")}</button></form></li>`
    : `<li class="chat-session-item"><a href="${esc(routeHash(`chat/${s.id}`, S.project, ctxRef.state.selectedTarget))}" class="chat-session${s.id === S.sid ? " on" : ""}" ${s.id === S.sid ? 'aria-current="page"' : ""} data-sid="${esc(s.id)}">
    <b title="${esc(s.title)}">${esc(s.title)}</b>${s.schedule ? `<span class="badge" title="${esc(s.schedule.run_at || s.schedule.error || "Schedule")}">${s.schedule.queued_count || 0} queued</span>` : ""}${s.plan_status === "running" ? '<span class="chat-running" role="img" aria-label="Running" title="Running"></span>' : ""}</a></li>`).join("") || `<li class="muted small" role="status">${query ? `No sessions match “${esc(query)}”.` : "No sessions need you."} <button type="button" class="btn sm" data-chat="clear-filters">Clear filters</button></li>`));
  if (S.renaming) { const input = $("#chat-rename-input", box); if (input && document.activeElement !== input) { input.focus(); input.select(); } }
}

// --- session list: rename, delete, fold ----------------------------------------

function startRename(sid) { S.renaming = sid; S.keys.sessions = null; drawSessions(); }
function endRename(focusSid) {
  S.renaming = null; S.keys.sessions = null; drawSessions();
  if (focusSid) $(`.chat-session[data-sid="${CSS.escape(focusSid)}"]`, host)?.focus();
}

async function saveRename(sid, title) {
  const s = S.sessions?.find((x) => x.id === sid);
  const text = String(title || "").trim();
  if (!s || !text || text === s.title) { endRename(sid); return; }
  try {
    const { session } = await post(S.project, `/sessions/${encodeURIComponent(sid)}`, { title: text }, "PATCH");
    s.title = session.title; S.keys.head = null;
    ctxRef.toast(`Chat renamed to “${session.title}”`, "ok");
  } catch (error) {
    const form = $(".chat-rename", host);
    let err = form?.querySelector("[data-rename-error]");
    if (form && !err) { err = document.createElement("p"); err.dataset.renameError = ""; err.setAttribute("role", "alert"); form.append(err); }
    if (err) err.textContent = `Rename failed: ${error.message}. Edit and save to retry.`;
    return;
  }
  endRename(sid);
  ctxRef.update();
  loadSessions();
}

async function deleteSession(sid) {
  const s = S.sessions?.find((x) => x.id === sid);
  if (!s || s.active_requests || !confirm(`Delete the chat “${s.title}”?\n\nIts messages, tasks, logs and mailbox are removed from .alfrd/ and can't be restored.`)) return;
  try {
    await post(S.project, `/sessions/${encodeURIComponent(sid)}`, {}, "DELETE");
  } catch (error) { ctxRef.toast(`Delete: ${error.message}`, "fail"); return; }
  snapshots.remove(S.project, sid);
  if (suspended || destroyed) { chatProjects.invalidate(S.project); return; }
  const i = S.sessions.findIndex((x) => x.id === sid);
  S.sessions = S.sessions.filter((x) => x.id !== sid); S.keys.sessions = null;
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
  syncSheets();
  $("[data-chat=collapse]", host)?.setAttribute("aria-expanded", String(!on));
  requestAnimationFrame(() => (on ? $("[data-chat=sessions]", host) : $("[data-chat=collapse]", host))?.focus());
}

function drawHead(ctx) {
  const live = ctx.state.liveStatus?.state || "off";
  const s = S.sessions?.find((x) => x.id === S.sid);
  if ($("#chat-head", host).dataset.sid === (S.sid || "") && refreshProtected($("#chat-head", host))) { drawCheckStatus(); return; }
  const titleInput = $("#chat-title-input", host);
  // Live metadata updates must not replace an in-progress title draft or its cursor.
  if (titleInput?.dataset.sid === S.sid && (document.activeElement === titleInput || titleInput.dataset.saving)) { drawCheckStatus(); return; }
  if (!changed("head", [s?.title, s?.active_requests, S.sid, live, S.settingsError, S.data?.plan?.runner_alive, S.data?.plan?.status])) { drawCheckStatus(); return; }
  const focused = $("#chat-head", host).contains(document.activeElement) ? document.activeElement.dataset.chat : null;
  const flowing = ["live", "polling"].includes(live);
  $("#chat-head", host).innerHTML = `<div class="chat-head-title"><button type="button" class="btn sm chat-nav-toggle" data-chat="sessions" aria-controls="chat-sessions" aria-expanded="false" title="Show the chat list">${icon("sidebar")} Chats</button><h3 class="grow">${s ? `<input id="chat-title-input" class="chat-title-input" data-sid="${esc(s.id)}" aria-label="Rename chat" title="Rename chat (Enter to save, Escape to cancel)" maxlength="120" value="${esc(s.title)}">` : esc(S.sid || "No session")}</h3>
    <button type="button" class="icon-btn sm chat-inspect-toggle" aria-label="Show or hide the inspector" data-chat="inspect" aria-controls="chat-inspector" aria-expanded="${Boolean(insp?.isOpen())}" aria-keyshortcuts="Alt+Shift+I" title="Show or hide the inspector (${keyLabel("Alt+Shift+I")})">${icon("sidebar")}</button><button type="button" class="icon-btn sm" data-chat="refresh" aria-label="Refresh this session">${icon("sync")}</button><details class="chat-session-menu"><summary aria-label="Conversation actions" title="Conversation actions">${icon("more")}</summary><div><button type="button" class="btn sm" data-chat="skills" aria-haspopup="dialog">Skills</button>${s ? `<button type="button" class="btn sm" data-chat="rename" data-sid-act="${esc(s.id)}">${icon("edit")} Rename</button><button type="button" class="btn sm" data-chat="delete" data-sid-act="${esc(s.id)}" ${s.active_requests || s.schedule ? 'disabled title="Cancel the schedule before deleting this session"' : ""}>${icon("trash")} Delete</button>` : ""}</div></details></div>
    <div class="chat-head-status"><span class="chat-live tone-${flowing ? "ok" : "warn"}" role="status" title="${esc([flowing ? "Live" : live === "off" ? "Live updates off" : "Disconnected", ctx.state.liveStatus?.detail, S.data?.plan ? `runner ${S.data.plan.runner_alive ? "active" : "idle"} (plan ${S.data.plan.id})` : ""].filter(Boolean).join(" · "))}"><span class="sr-only">${flowing ? "Live" : live === "off" ? "Live updates off" : "Disconnected"}${S.data?.plan ? `, runner ${S.data.plan.runner_alive ? "active" : "idle"}` : ""}</span></span>
    ${S.sid ? `<button type="button" class="icon-btn sm" data-chat="check" aria-label="${S.checking === `${S.project}/${S.sid}` ? "Checking" : "Check for interrupted work and resume it; paused runners stay paused"}" aria-busy="${S.checking === `${S.project}/${S.sid}`}" ${S.checking === `${S.project}/${S.sid}` ? "disabled" : ""} aria-describedby="chat-check-feedback" aria-disabled="${S.checking === `${S.project}/${S.sid}`}" title="Check for interrupted work and resume it; paused runners stay paused">${icon(S.checking === `${S.project}/${S.sid}` ? "sync" : "check")}</button>` : ""}</div>
    ${S.settingsError ? `<p class="callout fail small chat-wide" role="alert">Chat settings: ${esc(S.settingsError)} — fix the <code>chat:</code> block in <a href="${esc(routeHash("config", S.project, ctx.state.selectedTarget))}">Settings</a>.</p>` : ""}`;
  $("#chat-head", host).dataset.sid = S.sid || "";
  drawCheckStatus();
  if (s) {
    const project = S.project, sid = s.id;
    bindChatTitle($("#chat-title-input", host), {
      save: async (title) => {
        const { session } = await post(project, `/sessions/${encodeURIComponent(sid)}`, { title }, "PATCH");
        if (S.project === project) {
          const row = S.sessions?.find((x) => x.id === sid);
          if (row) row.title = session.title;
          if (S.sid === sid && S.data?.session) S.data.session.title = session.title;
          S.keys.head = null; S.keys.sessions = null;
        }
        return session.title;
      },
      report: (message) => ctx.toast(message, "fail"),
      settled: () => {
        if (S.project !== project) return;
        ctx.update();
        loadSessions();
      },
    });
  }
  if (focused) $(`[data-chat="${CSS.escape(focused)}"]`, $("#chat-head", host))?.focus();
}

function drawCheckStatus() {
  const tick = $('[data-chat="check"]', host);
  if (tick && !tick.disabled) {
    tick.title = checkStatus(S.data?.check);
    tick.setAttribute("aria-label", tick.title);
  }
}

async function checkNow(button) {
  const project = S.project, sid = S.sid, key = `${project}/${sid}`;
  if (!sid || S.checking === key) return;
  S.checking = key;
  button.setAttribute("aria-disabled", "true"); button.innerHTML = icon("sync"); button.setAttribute("aria-label", "Checking"); button.setAttribute("aria-busy", "true"); button.disabled = true;
  const current = () => host?.isConnected && S.project === project && S.sid === sid;
  $("#chat-check-feedback", host).textContent = "Checking this session for interrupted work.";
  try {
    const report = await post(project, `/sessions/${encodeURIComponent(sid)}/check`);
    if (!current()) return;
    const text = report.findings?.length ? report.findings.map((f) => f.text).join(" ") : "Check complete. No issues found.";
    $("#chat-check-feedback", host).textContent = text;
    ctxRef.toast(text, "ok");
    if (S.data) S.data.check = report;
    drawCheckStatus();
    loadSession();
  } catch (error) {
    if (current()) { $("#chat-check-feedback", host).textContent = `Check failed: ${error.message}`; ctxRef.toast(`Check failed: ${error.message}`, "fail"); }
  } finally {
    if (S.checking === key) S.checking = null;
    if (current()) {
      const b = $('[data-chat="check"]', host);
      if (b) { b.setAttribute("aria-disabled", "false"); b.innerHTML = icon("check"); b.setAttribute("aria-label", "Check for interrupted work and resume it; paused runners stay paused"); b.setAttribute("aria-busy", "false"); b.disabled = false; drawCheckStatus(); }
    }
  }
}

function messageHtml(original) {
  const open = original.task && S.open.get(original.task);
  const t = original.task && S.data?.tasks?.find((x) => x.row === original.task), l = t?.logs?.at(-1);
  const controls = `${l?.log ? `<button type="button" class="btn sm link" data-open-log="${esc(l.log)}">${icon("log")} Open log</button>` : ""}${l?.handoff ? `<button type="button" class="btn sm link" data-open-log="${esc(`${l.handoff}/response.md`)}">${icon("file")} Reply</button>` : ""}
    ${original.task ? `<button type="button" class="btn sm link" data-transcript="${esc(original.task)}" aria-expanded="${Boolean(open)}">${icon(open ? "fold" : "unfold")} ${open ? "Hide" : "Full"} transcript</button>${open ? transcriptHtml(original.task) : ""}` : ""}`;
  return sharedMessageHtml(original, { agentName, orchestrator: orchestrator(), agents: agents() }).replace("</article>", `${controls}</article>`);
}

// Inspector artifacts remain caller-owned; the thread uses the shared message renderer.
const artifactItem = (ref) => {
  const [path, sha] = String(ref).split("@");
  return `<li><span class="mono small" title="${esc(ref)}">${esc(path)}</span> <span class="muted small mono">${esc((sha || "").slice(0, 8))}</span> <button type="button" class="btn sm" data-artifact="${esc(ref)}" aria-label="View ${esc(path)}">View</button></li>`;
};

// Frozen outputs: the path ALFRD checks now, and where it was before an agent moved it (newest first).
function outputsHtml(outputs) {
  if (!outputs.length) return "";
  const earlier = (o) => {
    const h = [...(o.history || [])].reverse();
    return h.length ? `<details class="chat-output-history"><summary>Earlier path${h.length === 1 ? "" : "s"} (${h.length})</summary><ol>${h.map((e) =>
      `<li><span class="mono">${esc(e.path)}</span> <span class="muted">rev ${esc(e.rev)}${e.by ? ` · moved by @${esc(e.by)}` : ""}${e.at ? ` · <time datetime="${esc(e.at)}">${esc(String(e.at).slice(0, 16).replace("T", " "))}</time>` : ""}</span>${e.reason ? `<div>${esc(e.reason)}</div>` : ""}</li>`).join("")}</ol></details>` : "";
  };
  return `<h4>Expected outputs</h4><ul class="chat-outputs small">${outputs.map((o) => `<li>
    <div class="row gap wrap"><span class="badge tone-muted"><span>${esc(o.id)}</span></span>${o.rev > 1 ? `<span class="badge tone-warn" title="This path differs from the plan; the earlier paths are listed below"><span>moved · rev ${esc(o.rev)}</span></span>` : ""}<span class="mono chat-path">${esc(o.path)}${o.folder ? "/" : ""}</span></div>
    ${o.description ? `<div class="muted">${esc(o.description)}</div>` : ""}${earlier(o)}</li>`).join("")}</ul>`;
}

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
  box.setAttribute("aria-busy", String(Boolean(!S.sessions && !S.sessionsError || S.sid && !S.data && !S.error)));
  if (S.data) { act?.update(S.data, S.notes.filter((n) => n.sid === S.sid && n.project === S.project)); conversationStarter(box); return; }
  if (!S.sessions && !S.sessionsError || S.sid && !S.data && !S.error) { sourceHTML(box, skeletonRows("messages")); return; }
  if (!S.sid) sourceHTML(box, `<div class="empty"><p>Start a session with <b>New</b>, then ask for something.</p></div>`);
  else if (S.error) sourceHTML(box, `<div class="callout fail" role="alert">${esc(S.error)} <button type="button" class="btn sm" data-chat="refresh">Retry</button></div>`);
  else sourceHTML(box, `<p class="muted" role="status">Loading conversation…</p>`);
}

function drawProgress(ctx) {
  const box = $("#chat-progress", host);
  if (!box) return;
  const req = (S.data?.requests || []).find((r) => r.id === scope.request(S.data));
  const pr = requestProgress(S.data, req);
  const u = S.data?.usage;
  const warned = req ? costWarnings(S.data, req.id) : [];
  const promo = server.session?.mutations_enabled ? promotable(S.data) : null;
  const totals = requestTotals(S.data);
  const html = scope.isAll() ? `<h3 id="chat-progress-h">All requests</h3>
      <p class="small">${totals.count} request${totals.count === 1 ? "" : "s"}${totals.ongoing ? ` · ${totals.ongoing} ongoing` : ""}</p>
      <p class="small" data-tokens="all">Total tokens: in ${totals.usage.input_tokens ?? "—"} · out ${totals.usage.output_tokens ?? "—"}</p>
      <p class="small">Total cost: ${money(totals.usage.total_cost_usd)}</p>
      <p class="small">Total run time: ${duration(totals.seconds)}${totals.ongoing ? " (ongoing)" : ""}</p>
      <p class="muted small">${esc(tokenLine(totals.usage) || "no usage reported yet")}</p>`
    : !pr ? `<h3 id="chat-progress-h">Progress</h3><p class="muted small">No request yet.</p>`
    : `<h3 id="chat-progress-h">Request ${esc(pr.id)} <span class="badge tone-${statusInfo(pr.status === "summarizing" ? "running" : pr.status).tone}"><span>${esc(pr.status.replace("_", " "))}</span></span></h3>
      <p class="small chat-req-text" title="${esc(pr.text)}">${esc(pr.text)}</p>
      ${pr.total ? `<div class="chat-bar" role="progressbar" aria-label="Tasks done" aria-valuemin="0" aria-valuemax="${pr.total}" aria-valuenow="${pr.done}" aria-valuetext="${pr.done} of ${pr.total} tasks done"><i style="width:${Math.round((pr.done / pr.total) * 100)}%"></i></div>
        <p class="small">${pr.done}/${pr.total} done${pr.running ? ` · ${pr.running} running` : ""}${pr.failed ? ` · ${pr.failed} failed or stopped` : ""}</p>` : `<p class="muted small">${pr.mode === "direct" ? "Direct message" : "Planning…"}</p>`}
      ${meter("Cost", pr.cost, pr.maxCost, pr.costRatio, money(req?.usage?.total_cost_usd), pr.maxCost ? money(pr.maxCost) : "no limit", pr.tone)}
      ${meter("Time", pr.seconds, pr.maxSeconds, pr.timeRatio, duration(pr.seconds) + (pr.ongoing ? " (ongoing)" : ""), pr.maxSeconds ? duration(pr.maxSeconds) : "no limit", pr.tone)}
      <p class="muted small" data-tokens="request">${esc(tokenLine(req?.usage) || "no tokens reported yet")}</p>
      <p class="muted small">${pr.calls} orchestrator call${pr.calls === 1 ? "" : "s"}${S.data?.settings?.orchestrator_model ? ` (${esc(S.data.settings.orchestrator_model)}, escalation ${esc(S.data.settings.escalation_model || "agent model")})` : ""}${req?.usage?.total_cost_usd == null ? " · cost not reported by these agents" : ""}</p>
      ${warned.length ? `<p class="small"><span class="badge tone-warn" role="status">${icon("alert")}<span>${warned.length} task${warned.length === 1 ? "" : "s"} over the ${money(S.data?.settings?.warn_task_cost_usd)} warning</span></span></p>` : ""}
      ${promo ? `<button type="button" class="btn sm" data-chat="promote" data-request="${esc(promo.id)}" title="Add request ${esc(promo.id)}'s tasks to the active workflow (preview first)">${icon("workflow")} Promote to workflow</button>` : ""}`;
  const total = u ? `<p class="muted small chat-total" data-tokens="session">Session: ${u.turns} turn${u.turns === 1 ? "" : "s"} · ${esc(tokenLine(u) || money(u.total_cost_usd))} · ${u.orchestrator_calls} orchestrator call${u.orchestrator_calls === 1 ? "" : "s"}</p>` : "";
  refreshHTML(box, html + total);
}

const meter = (label, value, max, ratio, shown, limit, tone) => `<div class="chat-meter"><span class="small">${label}</span>
  <meter min="0" max="${max || 1}" value="${max ? Math.min(value, max) : 0}" low="${(max || 1) * 0.6}" high="${(max || 1) * 0.8}" optimum="0" aria-label="${label} used: ${esc(shown)} of ${esc(limit)}"></meter>
  <span class="small tabular ${ratio >= 1 ? "fail-t" : ratio >= 0.8 ? "warn-t" : ""}">${esc(shown)} / ${esc(limit)}</span></div>`;

// "Let orchestrator decide" (chat.delegate_decision): ASK is allowed; PRM, a repeated
// blocker (it would only loop) or one the orchestrator already handed back.

function drawDecisions() {
  const box = $("#chat-decisions", host);
  const list = S.data?.blockers || [];
  const done = (S.data?.decisions || []).filter((d) => d.status !== "pending").slice(-5);
  if (refreshProtected(box)) return;
  if (!changed("decisions", [list, done.map((d) => d.id)])) return;
  if (box.contains(document.activeElement) && list.some((d) => box.querySelector(`[data-decision="${CSS.escape(d.id)}"]`))) {
    // Someone is answering: keep their input; only the list of pending ids decides a rebuild.
    if (JSON.stringify([...$$("[data-decision]", box)].map((f) => f.dataset.decision)) === JSON.stringify(list.filter((d) => d.needs === "user").map((d) => d.id))) return;
  }
  box.innerHTML = `<h3 id="chat-decisions-h">Blockers and decisions ${list.length ? `<span class="badge tone-warn"><span>${list.length}</span></span>` : ""}</h3>
    ${list.length ? list.map((d) => d.mailbox_message_id ? `<p class="small"><b>${esc(d.question || d.reason)}</b> <button type="button" class="btn sm link" data-mail-jump="${esc(d.mailbox_message_id)}">Answer in conversation</button>${d.needs === "user" && canDelegate(d) ? `<button type="button" class="btn sm" data-delegate="${esc(d.id)}">Let orchestrator decide</button>` : ""}</p>` : d.needs === "user" ? `<form class="chat-decision" data-decision="${esc(d.id)}" aria-label="Decision for task ${esc(d.task)}">
      <p class="small"><b>${esc(d.question || d.reason)}</b></p>${d.question && d.reason !== d.question ? `<p class="muted small">${esc(d.reason)}</p>` : ""}
      <p class="muted small">Task <span class="mono">${esc(d.task)}</span></p>${d.delegated ? `<p class="small warn-t">The orchestrator handed this back to you (it did not decide, or its replans per request ran out).</p>` : ""}${d.kind === "permission" ? `<p class="small warn-t">${d.repeat ? `Blocked ${esc(d.repeat)}× with the same access. ` : ""}An approval cannot reach a running agent: grant access, reassign or cancel to change the next run.</p>` : ""}
      ${d.options?.length ? `<fieldset><legend class="sr-only">Options</legend>${d.options.map((o, i) => `<label class="row gap small"><input type="radio" name="choice" value="${esc(o)}" ${i ? "" : ""}> ${esc(o)}</label>`).join("")}</fieldset>` : ""}
      <label class="field small"><span>${d.options?.length ? "Or answer (optional)" : "Answer"}</span><textarea class="input" name="answer" rows="2" maxlength="8000"></textarea></label>
      <p class="fail-t small" role="alert" data-decision-error></p>
      <div class="row gap wrap"><button type="submit" class="btn sm primary">Send decision</button>${canDelegate(d) ? `<button type="button" class="btn sm" data-delegate="${esc(d.id)}" aria-describedby="dlg-${esc(d.id)}">Let orchestrator decide</button>` : ""}</div>
      ${canDelegate(d) ? `<p class="muted small" id="dlg-${esc(d.id)}">${d.options?.length ? "It picks one of these options for you" : "It answers for you"}${d.kind === "permission" ? ", which may grant this access" : ""}; you see its choice in the chat.</p>` : ""}</form>`
    : `<p class="small muted">${esc(d.question || d.reason)} — the orchestrator is deciding.${d.delegated ? ` <span class="badge tone-muted"><span>delegated</span></span>` : ""}</p>`).join("")
    : `<p class="muted small">Nothing needs a decision.</p>`}
    ${done.length ? `<details><summary class="small">Decided (${done.length})</summary><ul class="small">${done.map((d) => `<li>${esc(d.question || d.reason)} → <b>${esc(d.choice || d.answer || d.status)}</b>${d.decided_by ? ` <span class="muted">(${esc(d.decided_by)}${d.delegated && d.decided_by === "orchestrator" ? " · delegated" : ""})</span>` : ""}</li>`).join("")}</ul></details>` : ""}`;
}

function drawTask(ctx) {
  const box = host && $("#chat-task", host);
  if (!box) return;
  const t = S.data?.tasks?.find((x) => x.row === S.task);
  if (!changed("task", [t, S.open.get(S.task)])) return;
  box.hidden = !t;
  if (!t) { box.innerHTML = ""; delete box.dataset.html; return; }
  const task = t.task || {}, info = statusInfo(t.status), b = task.budget || {};
  const open = S.open.get(t.row);
  box.tabIndex = -1;
  const html = `<div class="row gap"><h3 class="grow">${esc(task.title || t.row)}</h3><button type="button" class="icon-btn sm" data-chat="close-task" aria-label="Close task details">${icon("close")}</button></div>
    <p><span class="badge tone-${info.tone}">${icon(info.icon)}<span>${esc(info.label)}</span></span> <span class="small">@${esc(t.owner)} · ${esc(agentName(t.owner))}</span> <span class="muted small mono">${esc(t.row)}</span></p>
    ${task.role ? `<p class="small muted">Role: ${esc(task.role)}</p>` : ""}
    ${task.description ? `<p class="small chat-text">${esc(task.description)}</p>` : ""}
    ${task.acceptance_criteria?.length ? `<h4>Acceptance criteria</h4><ul class="small">${task.acceptance_criteria.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    <dl class="chat-dl small">
      <dt>Budget</dt><dd>${[b.max_cost_usd != null ? `${money(b.max_cost_usd)}` : "", b.max_seconds ? duration(b.max_seconds) : "", b.max_retries != null ? `${b.max_retries} retr${b.max_retries === 1 ? "y" : "ies"}` : ""].filter(Boolean).join(" · ") || "request bounds"}</dd>
      <dt>Depends on</dt><dd>${(t.depends_on || []).map((d) => `<span class="mono">${esc(d)}</span>`).join(", ") || "nothing"}</dd>
      <dt>Attempts</dt><dd>${t.attempts || 0}${t.model ? ` · model ${esc(t.model)}` : ""}</dd>
      <dt>Tokens</dt><dd data-tokens="task">${esc(tokenLine(t.usage) || "not reported")}${t.cost_warning ? ` <span class="badge tone-warn">${icon("alert")}<span>over the ${money(t.cost_warning.bound)} warning</span></span>` : ""}</dd>
    </dl>
    ${outputsHtml(taskOutputs(t))}
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
  refreshHTML(box, html);
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
  const { viewerFor, renderViewer, fileObject } = await import("./viewers.js");
  const viewer = /\.(md|markdown)$/i.test(info.path) ? viewerFor(info.path, "text/markdown") : null;
  const markdown = viewer && viewer.id !== "text";
  ctx.modal(`<header class="modal-h"><h2 class="mono">${esc(info.path)}</h2><span class="muted small mono">${esc(String(info.sha256).slice(0, 12))} · ${Math.ceil(info.size / 1024)} KiB</span><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b">${markdown ? '<div class="viewer viewer-full" id="chat-art"></div>' : '<pre class="log log-full" id="chat-art"></pre>'}<button type="button" class="btn sm" id="chat-art-more" hidden>Load more</button></div>`, (root) => {
    const show = async () => {
      const el = $("#chat-art", root);
      el.textContent = text; $("#chat-art-more", root).hidden = eof;
      // Render the verified artifact bytes, including any pages already loaded.
      if (markdown) {
        try { await renderViewer(viewer, fileObject(S.project, info.path, { text, mime: "text/markdown" }), el); }
        catch (error) { el.textContent = text; ctx.toast(error.message, "warn"); }
      }
    };
    $("#chat-art-more", root).onclick = async () => { try { await more(); await show(); } catch (error) { ctx.toast(error.message, "fail"); } };
    show();
  }, "full");
}

async function send() {
  const input = $("#chat-input", host);
  const mention = parseMention(input.value.trim(), agents());
  if (mention?.error && $(`#chat-form`, host).dataset?.scheduled !== "true") { S.sendError = mention.error; draw(ctxRef); return; }
  S.sendError = "";
  try {
    if (await draft.send()) { composer?.close(); loadSession(); loadSessions(); }
  } catch (error) { S.sendError = `Not sent: ${error.message}`; }
  draw(ctxRef); input.focus();
}

async function newSession() {
  const p = S.project;
  try {
    const { session } = await post(p, "/sessions", {});
    chatProjects.load(p, true, true);
    if (p !== S.project || suspended || destroyed) return;
    S.sessions = [session, ...(S.sessions || [])]; S.keys.sessions = null;
    openSession(session.id);
    ctxRef.update();
    requestAnimationFrame(() => $("#chat-input", host)?.focus());
  } catch (error) { ctxRef.toast(`New session: ${error.message}`, "fail"); }
}

const bound = new WeakSet(); // the view element outlives mounts (project switches): delegate on it only once

function bind(el) {
  // The composer's own elements are new on every mount, so it is bound every time.
  draft = mountComposerDraft(el, "chat", { settings: () => S.data?.settings || S.settings, createSession: newSession, blocked: () => !!S.settingsError, onError: (m) => { S.sendError = m; $("#chat-send-error", el).textContent = m; } });
  composer = bindComposer(el, "chat", { agents, orchestrator, send, stopState: stop.state, onStop: stop.stop, onCommand: command, onError: (m) => { S.sendError = m; draw(ctxRef); }, onDraft: (text) => { draft.edit(text); if (S.sendError) { S.sendError = ""; $("#chat-send-error", host).textContent = ""; } } });
  if (bound.has(el)) return;
  bound.add(el);
  el.addEventListener("input", (e) => {
    if (e.target.id === "chat-session-search") { queries.set(S.project, e.target.value); S.sessionQuery = e.target.value; drawSessions(); }
  });
  el.addEventListener("toggle", (e) => { if (e.target.matches?.(".chat-session-menu") && !e.target.open) drawHead(ctxRef); }, true);
  el.addEventListener("focusout", (e) => { if (e.target.closest("#chat-head")) setTimeout(() => drawHead(ctxRef), 0); if (e.target.closest("#chat-decisions")) setTimeout(drawDecisions, 0); if (e.target.closest(".chat-list")) setTimeout(() => drawSessions(), 0); });
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button, [data-agent], a[data-sid]");
    if (!b) return;
    if (b.matches("a[data-sid]")) { e.preventDefault(); if (b.dataset.sid !== S.sid) { ctxRef.state.chatSession = b.dataset.sid; setRoute(routeHash(`chat/${b.dataset.sid}`, ctxRef.state.selectedProject, ctxRef.state.selectedTarget)); restore(root.parentElement, ctxRef); ctxRef.update(); } return; }
    if (b.dataset.chatPrompt) { composer.input.value = b.dataset.chatPrompt; draft.edit(composer.input.value); composer.refresh(); composer.input.focus(); return; }
    const a = b.dataset.chat;
    if (a === "clear-filters") {
      needsOnly = false; S.sessionQuery = ""; queries.set(S.project, "");
      $("#chat-session-search", host).value = "";
      $("[data-chat=needs-me]", host).setAttribute("aria-pressed", "false");
      ctxRef.update(); return;
    }
    if (a === "new") newSession();
    else if (a === "needs-me") { needsOnly = !needsOnly; b.setAttribute("aria-pressed", String(needsOnly)); drawSessions(); }
    else if (a === "clear-search") { queries.set(S.project, ""); S.sessionQuery = ""; $("#chat-session-search", host).value = ""; drawSessions(); $("#chat-session-search", host).focus(); }
    else if (a === "rename") startRename(b.dataset.sidAct);
    else if (a === "rename-cancel") endRename(S.renaming);
    else if (a === "delete") deleteSession(b.dataset.sidAct);
    else if (a === "close-sessions") closeSessions();
    else if (a === "collapse") setCollapsed(true);
    else if (a === "sessions" && wide() && $(".chat", host).classList.contains("chat-sessions-collapsed")) setCollapsed(false);
    else if (a === "refresh") { server.invalidateChatProbes(); loadSession(); loadSessions(); loadSettings(); }
    else if (a === "check") checkNow(b);
    else if (a === "skills") {
      const menu = b.closest("details"), trigger = menu.querySelector("summary");
      menu.open = false;
      trigger.focus();
      // Bind to the mounted chat project, not a later global project selection.
      let modalRoot;
      const close = ctxRef.modal(`<header class="modal-h"><h2 id="skills-modal-title">Skills Explorer</h2><button type="button" class="icon-btn" data-close aria-label="Close Skills Explorer">${icon("close")}</button></header><div class="modal-b" data-skills-body></div>`, (root) => {
        modalRoot = root;
        root.setAttribute("aria-labelledby", "skills-modal-title");
        root.addEventListener("beforeclose", (event) => {
          // Live header redraws can replace the original overflow trigger.
          if (!trigger.isConnected) root.parentElement.returnFocus = $(".chat-session-menu summary", host);
          queueMicrotask(() => { if (!event.defaultPrevented) closeSkills = null; });
        });
        const project = S.project, body = root.querySelector("[data-skills-body]");
        body.innerHTML = skeletonRows("details");
        import("./skills_explorer.js").then((m) => {
          if (root.isConnected) m.render(body, { state: { mode: "server", selectedProject: project } });
        }).catch((e) => { if (root.isConnected) body.textContent = e.message; });
        root.querySelector("[data-close]").focus();
      }, "wide skills-modal");
      closeSkills = () => { if (modalRoot.isConnected) close(); return !modalRoot.isConnected; };
    }
    else if (a === "setup") command("setup");
    else if (a === "reload") loadSessions();
    else if (a === "close-task") { S.task = null; S.keys.task = null; ctxRef.update(); }
    else if (a === "promote") promoteRequest(b.dataset.request);
    else if (a === "graph-full") openGraphFull();
    else if (a === "inspect") { closeSessions(); insp?.toggle(); syncSheets(); }
    else if (a === "sessions") {
      const open = $(".chat", host).classList.toggle("chat-sessions-open");
      b.setAttribute("aria-expanded", String(open));
      if (open) { insp?.close({ restoreFocus: false }); syncSheets(); sessionSheet = chatSheet($(".chat-sessions", host), closeSessions); }
      else closeSessions();
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
      if (card) { closeInspector(); card.scrollIntoView({ block: "center" }); card.querySelector("button,textarea")?.focus(); }
    }
    else if (b.dataset.openLog) openLog(ctxRef, S.project, b.dataset.openLog);
    else if (b.dataset.transcript) toggleTranscript(b.dataset.transcript);
    else if (b.dataset.part) loadPart(...b.dataset.part.split("|"));
    else if (b.dataset.more) loadPart(...b.dataset.more.split("|"), true);
    else if (b.dataset.artifact) viewArtifact(b.dataset.artifact);
    else if (b.dataset.taskAct) taskAction(b.dataset.taskAct, b.dataset.row, b);
    else if (b.dataset.delegate) delegateDecision(b.dataset.delegate, b);
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
    if (insp?.onKeydown(e)) { syncSheets(); return; }
    if (e.key === "F2" && e.target.matches?.("a[data-sid]")) { e.preventDefault(); startRename(e.target.dataset.sid); return; }
    if (e.key === "Delete" && e.target.matches?.("a[data-sid]")) { e.preventDefault(); deleteSession(e.target.dataset.sid); return; }
    if (e.key !== "Escape" || e.defaultPrevented) return;
    if (e.target.closest("[data-rename]")) { e.preventDefault(); endRename(S.renaming); return; }
    const chat = $(".chat", host);
    if (e.target.closest(".chat-sessions") && chat.classList.contains("chat-sessions-open")) {
      closeSessions(); e.preventDefault();
    }
  });
}

async function delegateDecision(id, button) {
  const err = button.closest("[data-decision]")?.querySelector("[data-decision-error]");
  button.disabled = true;
  try {
    const out = await post(S.project, `/sessions/${encodeURIComponent(S.sid)}/decisions/${encodeURIComponent(id)}`, { delegate: true });
    ctxRef.toast(out?.decision?.needs === "user" ? "The orchestrator's replans ran out; this decision stays with you" : "The orchestrator will decide; its choice shows in the chat", out?.decision?.needs === "user" ? "warn" : "ok");
    S.keys.decisions = null; loadSession();
  } catch (error) { if (err) err.textContent = error.message; button.disabled = false; }
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
  S.notes.push({ id: `local:${Date.now()}:${S.notes.length}`, sid: S.sid, project: S.project, role: "system", text, at: new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 19) });
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
    const g = mountGraph($("#chat-gfull", root), { onMail: (request, id) => { close(); closeInspector(); act?.jumpMail(request, id); }, taskMenu: graphTaskMenu(close), groups: true, label: "Task graph (full screen)", empty: "Tasks appear here when the orchestrator plans a request.",
      onSelect: (row) => { S.task = row; drawTask(ctxRef); },
      onOpen: (row) => { close(); S.task = row; S.keys.task = null; draw(ctxRef); $("#chat-task", host)?.focus(); } });
    full = { host: root, g };
    g.update({ ...graphModel(S.data?.graph, agents(), scope.request(S.data), S.data?.requests), stream: S.sid, scope: `${S.sid}:${scope.request(S.data) || "all"}` }, S.task);
    requestAnimationFrame(() => { g.fit(); g.focus(); });
  }, "full");
}

function suspend() {
  if (suspended) return true;
  focusIntent = root.contains(document.activeElement) && document.activeElement?.matches("textarea,input") ? document.activeElement : null;
  savedScroll = [...root.querySelectorAll("*")].filter((e) => e.scrollTop || e.scrollLeft).map((e) => [e, e.scrollTop, e.scrollLeft]);
  if (leave() === false) return false;
  suspended = true; return true;
}
function resume() {
  suspended = false; panelResizers.forEach((r) => r.resume()); if ($(".chat", root)) layoutObserver?.observe($(".chat", root)); act?.resume(); draft?.resume?.();
  for (const [el, top, left] of savedScroll) { el.scrollTop = top; el.scrollLeft = left; }
  if (focusIntent?.isConnected) focusIntent.focus({ preventScroll: true });
  if (S.project) { S.sessions = chatProjects.snapshot(S.project)?.sessions || S.sessions; loadSessions(false); loadSettings(); }
  if (S.sid) { const cached = snapshots.peek(S.project, S.sid); if (cached?.data) S.data = cached.data; loadSession(false); }
}
const prefetch = (event) => {
  const sid = event.target.closest?.("a[data-sid]")?.dataset.sid;
  clearTimeout(prefetchTimer);
  if (!sid || sid === S.sid || document.hidden || navigator.connection?.saveData) return;
  prefetchTimer = setTimeout(() => { if (!suspended) { prefetchSid = sid; prefetchSnapshot(S.project, sid); } }, 125);
};
root.addEventListener("pointerover", prefetch); root.addEventListener("focusin", prefetch);
return { root, render, suspend, resume, isSuspended: () => suspended, refresh: () => { if (!suspended) loadSession(); }, project: () => S.project, sid: () => S.sid,
  destroy() { suspend(); destroyed = true; unsubscribeProjects(); draft?.destroy(); act?.stop(); panelResizers.forEach((r) => r.destroy()); layoutObserver?.disconnect(); window.removeEventListener("alfrd-events", onEvents); window.removeEventListener("alfrd-chat-resync", onResync); window.removeEventListener("resize", onResize); root.remove(); },
};
}
