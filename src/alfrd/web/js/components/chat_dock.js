import { viewerFor, openViewer } from "./viewers.js";
import { mountPanelResize } from './panel_resize.js';
// Chat dock: the active chat session beside any project view (Workflow, Results, Overview, plugin
// pages…). Transcript (transcript.js, shared with the Chat page), composer (@-mentions, Enter / Shift+Enter) and one progress line for the active
// request; "Open full" goes to the Chat page (graph, decisions, sessions). Loaded on the first open
// (app.js keeps only the toggle, Alt+Shift+A, and the open/width preference). Live: the session plan's
// events re-read the session; a compact Activity strip (chat_activity.js) polls only while something
// runs. Slash commands: /setup, /help, /new. Narrow windows get an overlay.
import { $, esc, icon, storage, routeHash } from "../utils/dom.js";
import { server } from "../data/server.js";
import { mountComposerDraft } from "./composer_draft.js";
import { bindComposer, composerHtml } from "./chat_composer.js";
import { openLog } from "./plans.js";
import { mountTranscript } from "./transcript.js";
import { messageHtml as sharedMessageHtml } from "./chat_message.js";
import { destinationSession, chatSheet, conversationStarter } from "../data/chat_sessions.js";
import { activeRequest, costWarnings, duration, hasActivity, helpText, money, parseMention, requestProgress, staleState, statusInfo, tokenLine, createRequestStop } from "../data/chat.js";

import { chatProjects } from "../data/chat_projects.js";

const SESSION_KEY = "ui:chat.session"; // shared with the Chat page: the dock follows the session you last opened
const MIN = 300, MAX = 720;
const D = { project: null, sessions: null, sid: null, data: null, error: "", sendError: "", gen: 0, key: "", notes: [] };
let el = null, ctxRef = null, opts = {}, timer = 0, clock = 0, slow = 0, composer = null, draft = null, cssReady = null, act = null;
let sheet = null, resizer, railObserver;
const syncSheet = () => {
  const rail = document.querySelector(".rail")?.getBoundingClientRect().width || 0;
  const modal = el && !el.hidden && (window.innerWidth <= 900 || window.innerWidth - rail < 708);
  document.querySelector("#app")?.classList.toggle("chat-dock-sheet", Boolean(modal));
  el?.classList.toggle("cd-sheet", Boolean(modal));
  resizer?.sync();
  if (modal && !sheet) sheet = chatSheet(el, () => opts.onClose?.());
  else if (!modal && sheet) { sheet(); sheet = null; }
  if (el && !modal) el.setAttribute("role", "complementary");
};
window.addEventListener("resize", syncSheet);
const stop = createRequestStop({ state: () => D.data, context: () => ({ project: D.project, sid: D.sid }),
  cancel: (row, ctx, task) => server.mutate(`/studio/projects/${encodeURIComponent(ctx.project)}/chat/sessions/${encodeURIComponent(ctx.sid)}/tasks/${encodeURIComponent(row)}/cancel`, { expected_status: task.status, ...(task.units?.at(-1) ? { expected_unit: task.units.at(-1) } : {}) }),
  refresh: loadSession, changed: () => composer?.refresh(), report: (text, tone) => ctxRef.toast(text, tone) });
const base = (p) => `/api/studio/projects/${encodeURIComponent(p)}/chat`;

async function get(url) {
  const res = await server.request(url, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => null);
  if (!res.ok) { const e = new Error(body?.error?.message || `${res.status} ${res.statusText}`); e.status = res.status; throw e; }
  return body;
}

function loadCssOnce() {
  if (cssReady) return cssReady;
  const href = "css/chat.css";
  const found = document.querySelector(`link[data-lazy="${href}"]`);
  if (found?.sheet) return (cssReady = Promise.resolve());
  const link = found || Object.assign(document.createElement("link"), { rel: "stylesheet", href });
  link.dataset.lazy = href;
  cssReady = new Promise((resolve) => { link.addEventListener("load", resolve, { once: true }); link.addEventListener("error", resolve, { once: true }); });
  if (!found) document.head.appendChild(link);
  return cssReady;
}

window.addEventListener("alfrd-events", (e) => {
  const { project, events } = e.detail || {};
  if (!el || el.hidden || project !== D.project) return;
  if (!(events || []).some((ev) => String(ev.plan || "").startsWith("chat-"))) return;
  clearTimeout(timer);
  timer = setTimeout(() => { if (D.sid) loadSession(); }, 250);
  if (events.some((ev) => ev.plan === `chat-${D.sid}`)) act?.kick();
});

/** Show the dock for the active project. o: {width, focus, isOpen(), onWidth(w), onClose()}. */
export async function render(host, ctx, o = {}) {
  ctxRef = ctx; opts = o;
  const p = ctx.activeProject();
  if (host.dataset.project !== (p || "")) { hide(); host.replaceChildren(); }
  await loadCssOnce();
  if (p !== ctx.activeProject()) return;
  if (o.isOpen && !o.isOpen()) return; // closed while the stylesheet loaded
  if (el !== host || host.dataset.project !== (p || "")) mount(host, p);
  resizer?.sync();
  const wasHidden = host.hidden;
  host.hidden = false;
  if (wasHidden) { loadSessions(false); if (D.sid) loadSession(); act?.kick(); }
  draw(); syncSheet();
  if (!clock) clock = setInterval(() => { if (!el?.hidden) drawProgress(); }, 1000);
  if (!slow) slow = setInterval(() => { // only when live updates are off; the event stream is faster
    const live = ["live", "polling"].includes(ctxRef?.state.liveStatus?.state);
    if (!el?.hidden && !live && activeRequest(D.data)?.finished == null && D.sid) loadSession();
  }, 15000);
  if (o.focus) requestAnimationFrame(() => $("#cd-input", el)?.focus());
}

export function hide() {
  sheet?.(); sheet = null;
  if (el) el.hidden = true;
  clearInterval(clock); clearInterval(slow); clock = slow = 0;
  act?.stop();
  draft?.suspend?.();
}


function onKey(e) {
  if (e.key === "Escape" && !e.defaultPrevented && matchMedia("(max-width: 900px)").matches) { e.preventDefault(); opts.onClose?.(); }
}

function mount(host, p) {
  resizer?.destroy(); railObserver?.disconnect();
  draft?.destroy();
  act?.stop();
  el = host;
  Object.assign(D, { project: p, sessions: null, sid: null, data: null, error: "", sendError: "", key: "", gen: D.gen + 1 });
  host.dataset.project = p || "";
  host.className = "chat-dock";
  host.setAttribute("aria-labelledby", "cd-title");
  host.innerHTML = `<div class="cd-grip" role="separator" aria-orientation="vertical" aria-label="Resize the chat dock" tabindex="0" aria-valuemin="${MIN}" aria-valuemax="${MAX}"></div>
    <header class="cd-head"><h2 id="cd-title" tabindex="-1">${icon("chat")} Chat</h2>

      <span class="grow"></span>
      <a class="btn sm" id="cd-full" href="#/chat" title="Open the full Chat page: task graph, decisions, sessions">Open full</a>
      <button type="button" class="icon-btn sm" data-cd="close" aria-label="Close the chat dock" aria-keyshortcuts="Alt+Shift+A" title="Close (Alt+Shift+A)">${icon("close")}</button></header>
    <div class="cd-session-row"><label class="sr-only" for="cd-session">Session</label><select id="cd-session" class="input sm"></select><span id="cd-running" class="chat-running" role="img" aria-label="Running" title="Running" hidden></span><button type="button" class="btn sm" data-cd="new">${icon("plus")} New</button></div>
    <p class="cd-progress small" id="cd-progress" aria-live="off"></p>
    <div class="chat-log cd-log" id="cd-log" tabindex="0" aria-label="Messages"></div>
    <section class="chat-activity cd-activity" id="cd-activity" aria-label="Running now" hidden></section>
    <p class="chat-stop-status muted small" role="status"></p>
    ${composerHtml("cd", { attachments: true })}`;
  act = mountTranscript($("#cd-log", host), { project: () => D.project, sid: () => D.sid, busy: () => hasActivity(D.data), agentName, compact: true, state: () => D.data, error: () => D.error, messageHtml, viewGraph: () => $("#cd-full", el)?.click(), onReply: () => loadSession(), notes: () => D.notes.filter((n) => n.sid === D.sid && n.project === D.project),
    select: () => { $("#cd-full", el)?.click(); },
    openLog: (rel) => openLog(ctxRef, D.project, rel), cancel: cancelRow });
  draft = mountComposerDraft(host, "cd", { settings: () => D.data?.settings, createSession: newSession, onError: (m) => { D.sendError = m; $("#cd-send-error", host).textContent = m; } });
  composer = bindComposer(host, "cd", { agents, orchestrator: () => D.data?.settings?.orchestrator, send, stopState: stop.state, onStop: stop.stop, where: "dock", onCommand: command,
    onError: (m) => { D.sendError = m; draw(); },
    onDraft: (text) => { draft.edit(text); if (D.sendError) { D.sendError = ""; $("#cd-send-error", el).textContent = ""; } } });
  host.addEventListener("click", onClick);
  $("#cd-full", host).addEventListener("click", () => opts.onClose?.());
  host.addEventListener("keydown", onKey); // named handlers: a re-mount on the same host doesn't add a second copy
  $("#cd-session", host).addEventListener("change", (e) => openSession(e.target.value));
  grip($(".cd-grip", host));
  railObserver = typeof ResizeObserver === "function" ? new ResizeObserver(syncSheet) : null;
  const rail = document.querySelector(".rail"); if (rail) railObserver?.observe(rail);
}

function grip(g) {
  resizer = mountPanelResize({ panel: el, handle: g, width: opts.width, min: MIN, max: MAX, defaultWidth: 380, side: "right",
    label: "Resize the chat dock", enabled: () => !el.classList.contains("cd-sheet") && window.innerWidth > 900,
    available: () => window.innerWidth - (document.querySelector(".rail")?.getBoundingClientRect().width || 0) - 408,
    apply: (w) => { el.style.setProperty("--dock-w", `${w}px`); document.documentElement.style.setProperty("--dock-w", `${w}px`); },
    save: (w) => opts.onWidth?.(w) });
}

// --- data --------------------------------------------------------------------------

function applySessions(entry) {
  if (!entry || entry.project !== D.project || !el || el.hidden) return;
  D.sessions = entry.sessions; D.error = entry.error || "";
  if (D.sessions) {
    const remembered = (storage.get(SESSION_KEY, {}) || {})[D.project];
    const want = destinationSession(D.sessions, D.sid || remembered);
    if (want !== D.sid) openSession(want);
  }
  draw();
}
chatProjects.subscribe(applySessions);
async function loadSessions(refresh = true) {
  const p = D.project;
  if (p) { chatProjects.select(p); applySessions(await chatProjects.load(p, true, refresh)); }
}

async function loadSession() {
  const p = D.project, sid = D.sid, gen = ++D.gen;
  if (!sid) return false;
  let accepted = false;
  try {
    const data = await get(`${base(p)}/sessions/${encodeURIComponent(sid)}?messages=200`);
    if (gen !== D.gen || p !== D.project || sid !== D.sid) return false;
    if (staleState(D.data, data)) return false;
    const was = hasActivity(D.data);
    D.data = data; D.error = "";
    accepted = true;
    if (hasActivity(data) && !was) act?.kick();
  } catch (error) { if (gen === D.gen) D.error = error.status === 404 ? "This session is no longer available. Refresh sessions or start a New session." : `Couldn’t load this conversation. ${error.message}`; }
  draw();
  return accepted;
}

function openSession(sid, { remember = true } = {}) {
  D.sid = sid || null; D.data = null; D.gen++; D.key = "";
  if (sid && remember) storage.set(SESSION_KEY, { ...(storage.get(SESSION_KEY, {}) || {}), [D.project]: sid });
  draft?.select(D.project, D.sid);
  act?.reset();
  if (sid) loadSession();
  draw();
}

// --- drawing -----------------------------------------------------------------------

const agents = () => D.data?.settings?.agents || {};
const agentName = (key) => (key === "alfrd" ? "ALFRD" : agents()[key]?.label || key);

function draw() {
  if (!el || el.hidden) return;
  const sel = $("#cd-session", el);
  const runningDot = $("#cd-running", el);
  if (runningDot) runningDot.hidden = !D.sessions?.some((s) => s.id === D.sid && s.plan_status === "running");
  const opts_ = (D.sessions || []).map((s) => `<option value="${esc(s.id)}" ${s.id === D.sid ? "selected" : ""}>${esc(s.title)}${s.schedule ? ` · ${s.schedule.queued_count || 0} queued` : ""}</option>`).join("");
  const html = opts_ || `<option value="">${D.sessions ? "No session yet" : "Loading…"}</option>`;
  if (sel.dataset.html !== html) { sel.dataset.html = html; sel.innerHTML = html; }
  sel.disabled = !D.sessions?.length;
  const st = ctxRef.state;
  $("#cd-full", el).setAttribute("href", routeHash(D.sid ? `chat/${D.sid}` : "chat", st.selectedProject, st.selectedTarget));
  drawLog();
  drawProgress();
  draft?.select(D.project, D.sid);
  $("#cd-send-error", el).textContent = D.sendError || "";
  composer?.refresh();
  $(".chat-stop-status", el).textContent = stop.state().reason || "";
}

function drawLog() {
  const box = $("#cd-log", el);
  box.setAttribute("aria-busy", String(Boolean(!D.data && !D.error && (!D.sessions || D.sid))));
  if (D.data) { act?.update(D.data, D.notes.filter((n) => n.sid === D.sid && n.project === D.project)); conversationStarter(box); return; }
  if (D.error) box.innerHTML = `<p class="callout fail small" role="alert">${esc(D.error)} <button type="button" class="btn sm" data-cd="refresh">Retry</button></p>`;
  else if (D.sessions && !D.sessions.length) box.innerHTML = `<div class="empty small"><p>No conversations in ${esc(ctxRef?.projectName?.(D.project) || D.project)}.</p><button type="button" class="btn sm primary" data-cd="new">New session</button></div>`;
  else box.innerHTML = `<p class="muted small" role="status">Loading conversation…</p>`;
}

function messageHtml(original) {
  const t = original.task && D.data?.tasks?.find((x) => x.row === original.task), l = t?.logs?.at(-1);
  const controls = `${l?.log ? `<button type="button" class="btn sm link" data-cd-log="${esc(l.log)}">${icon("log")} Open log</button>` : ""}${l?.handoff ? `<button type="button" class="btn sm link" data-cd-log="${esc(`${l.handoff}/response.md`)}">${icon("file")} Reply</button>` : ""}`;
  return sharedMessageHtml(original, { agentName, orchestrator: D.data?.settings?.orchestrator, agents: agents(), compact: true }).replace("</article>", `${controls}</article>`);
}

function drawProgress() {
  const box = el && $("#cd-progress", el);
  if (!box) return;
  const req = activeRequest(D.data);
  const pr = requestProgress(D.data, req);
  let html = "";
  if (pr) {
    const info = statusInfo(pr.status === "summarizing" ? "running" : pr.status === "planning" ? "running" : pr.status);
    const warned = costWarnings(D.data, pr.id).length;
    html = `<span class="badge tone-${info.tone}">${["planning", "running", "summarizing"].includes(pr.status) ? icon("sync", "spin") : ""}<span>${esc(pr.id)} ${esc(pr.status.replace("_", " "))}</span></span>
      ${pr.total ? `<span>${pr.done}/${pr.total} tasks${pr.running ? ` · ${pr.running} running` : ""}${pr.failed ? ` · ${pr.failed} failed` : ""}</span>` : `<span>${pr.mode === "direct" ? "direct" : "planning"}</span>`}
      <span class="tabular ${pr.tone === "fail" ? "fail-t" : pr.tone === "warn" ? "warn-t" : ""}">${money(req?.usage?.total_cost_usd)}${pr.maxCost ? ` / ${money(pr.maxCost)}` : ""} · ${duration(pr.seconds)}</span>
      ${req?.usage ? `<span class="muted" data-tokens="dock">${esc(tokenLine({ ...req.usage, total_cost_usd: null }))}</span>` : ""}
      ${warned ? `<span class="badge tone-warn" title="Tasks over chat.warn_task_cost_usd">${icon("alert")}<span>${warned} over cost warning</span></span>` : ""}
      ${D.data?.blockers?.some((b) => b.needs === "user") ? `<a class="badge tone-warn" href="${esc($("#cd-full", el).getAttribute("href"))}">${icon("alert")}<span>decision needed</span></a>` : ""}`;
  } else if (D.data) html = `<span class="muted">No request yet.</span>`;
  if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
}

// --- actions -----------------------------------------------------------------------

async function send() {
  const input = $("#cd-input", el);
  const mention = parseMention(input.value.trim(), agents());
  if (mention?.error && $(`#cd-form`, el).dataset?.scheduled !== "true") { D.sendError = mention.error; draw(); return; }
  D.sendError = "";
  try {
    if (await draft.send()) { composer?.close(); loadSession(); loadSessions(); }
  } catch (error) { D.sendError = `Not sent: ${error.message}`; }
  draw(); input.focus();
}

async function onClick(e) {
  const b = e.target.closest("button, a[data-cd-log]");
  if (b?.dataset.artifact) { viewArtifact(b.dataset.artifact); return; }
  if (!b) return;
  if (b.dataset.cdLog) { openLog(ctxRef, D.project, b.dataset.cdLog); return; }
  if (b.dataset.chatPrompt) { composer.input.value = b.dataset.chatPrompt; draft.edit(composer.input.value); composer.refresh(); composer.input.focus(); return; }
  const a = b.dataset.cd;
  if (a === "close") opts.onClose?.();
  else if (a === "refresh") { D.error = ""; loadSessions(); loadSession(); }
  else if (a === "new") newSession();
}

async function viewArtifact(ref) {
  const project = D.project, sid = D.sid;
  try {
    const page = await get(`${base(project)}/sessions/${encodeURIComponent(sid)}/artifacts?${new URLSearchParams({ ref, offset: 0 })}`);
    const viewer = /\.(md|markdown)$/i.test(page.path) ? viewerFor(page.path, "text/markdown") : null;
    if (viewer && viewer.id !== "text") {
      if (!page.eof) ctxRef.toast("Partial text; Open full Chat for more", "warn");
      return openViewer(ctxRef, project, page.path, viewer, { text: page.content, mime: "text/markdown" });
    }
    const { openFull } = await import("./logview.js");
    await openFull(ctxRef, page.path, page.content, page.eof ? ref : `${ref} · Partial text; Open full Chat for more`, null);
  } catch (error) { ctxRef.toast(`Artifact: ${error.message}`, "fail"); }
}

async function newSession() {
  const p = D.project;
  try {
    const { session } = await server.mutate(`/studio/projects/${encodeURIComponent(p)}/chat/sessions`, {});
    chatProjects.load(p, true, true);
    if (p !== D.project) return;
    D.sessions = [session, ...(D.sessions || [])];
    openSession(session.id);
    $("#cd-input", el)?.focus();
  } catch (error) { ctxRef.toast(`New session: ${error.message}`, "fail"); }
}

async function cancelRow(row, button) {
  button.disabled = true;
  try {
    await server.mutate(`/studio/projects/${encodeURIComponent(D.project)}/chat/sessions/${encodeURIComponent(D.sid)}/tasks/${encodeURIComponent(row)}/cancel`, {});
    ctxRef.toast(`Task ${row} cancelled`, "ok"); loadSession();
  } catch (error) { ctxRef.toast(`Cancel: ${error.message}`, "fail"); button.disabled = false; }
}

// --- slash commands (never sent) --------------------------------------------------------

function note(text) {
  D.notes.push({ id: `local:${Date.now()}`, sid: D.sid, project: D.project, role: "system", text, at: new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 19) });
  D.key = ""; draw();
  const box = $("#cd-log", el); box.scrollTop = box.scrollHeight;
}

async function command(cmd) {
  D.sendError = "";
  if (cmd === "help") note(helpText(D.data?.settings || D.settings, "dock"));
  else if (cmd === "new") newSession();
  else if (cmd === "setup") {
    const p = D.project;
    try {
      const m = await import("./chat_setup.js");
      m.openSetup(ctxRef, p, { onSaved: (settings, summary) => { if (p !== D.project) return; D.settings = settings; loadSession(); note(summary); } });
    } catch (error) { ctxRef.toast(`Setup: ${error.message}`, "fail"); }
  }
  draw();
}
