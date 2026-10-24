// Chat dock: the active chat session beside any project view (Workflow, Results, Overview, plugin
// pages…). Transcript (transcript.js, shared with the Chat page), composer (@-mentions, Enter / Shift+Enter) and one progress line for the active
// request; "Open full" goes to the Chat page (graph, decisions, sessions). Loaded on the first open
// (app.js keeps only the toggle, Alt+Shift+A, and the open/width preference). Live: the session plan's
// events re-read the session; a compact Activity strip (chat_activity.js) polls only while something
// runs. Slash commands: /setup, /help, /new. Narrow windows get an overlay.
import { $, esc, icon, storage, routeHash } from "../utils/dom.js";
import { server } from "../data/server.js";
import { bindComposer, composerHtml } from "./chat_composer.js";
import { openLog } from "./plans.js";
import { mountTranscript } from "./transcript.js";
import { activeRequest, costWarnings, duration, hasActivity, helpText, money, parseMention, requestProgress, staleState, statusInfo, tokenLine, presentMessage } from "../data/chat.js";

const SESSION_KEY = "ui:chat.session"; // shared with the Chat page: the dock follows the session you last opened
const MIN = 300, MAX = 720;
const D = { project: null, sessions: null, sid: null, data: null, error: "", sending: false, sendError: "", gen: 0, drafts: new Map(), key: "", notes: [] };
let el = null, ctxRef = null, opts = {}, timer = 0, clock = 0, slow = 0, composer = null, cssReady = null, act = null;
const base = (p) => `/api/studio/projects/${encodeURIComponent(p)}/chat`;

async function get(url) {
  const res = await server.request(url, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error?.message || `${res.status} ${res.statusText}`);
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
  timer = setTimeout(() => { loadSessions(); if (D.sid) loadSession(); }, 250);
  if (events.some((ev) => ev.plan === `chat-${D.sid}`)) act?.kick();
});

/** Show the dock for the active project. o: {width, focus, isOpen(), onWidth(w), onClose()}. */
export async function render(host, ctx, o = {}) {
  ctxRef = ctx; opts = o;
  const p = ctx.activeProject();
  await loadCssOnce();
  if (o.isOpen && !o.isOpen()) return; // closed while the stylesheet loaded
  if (el !== host || host.dataset.project !== p) mount(host, p);
  host.style.setProperty("--dock-w", `${clamp(o.width || 380)}px`);
  document.documentElement.style.setProperty("--dock-w", `${clamp(o.width || 380)}px`);
  const wasHidden = host.hidden;
  host.hidden = false;
  if (wasHidden) { loadSessions(); if (D.sid) loadSession(); act?.kick(); }
  draw();
  if (!clock) clock = setInterval(() => { if (!el?.hidden) drawProgress(); }, 1000);
  if (!slow) slow = setInterval(() => { // only when live updates are off; the event stream is faster
    const live = ["live", "polling"].includes(ctxRef?.state.liveStatus?.state);
    if (!el?.hidden && !live && activeRequest(D.data)?.finished == null && D.sid) loadSession();
  }, 15000);
  if (o.focus) requestAnimationFrame(() => $("#cd-input", el)?.focus());
}

export function hide() {
  if (el) el.hidden = true;
  clearInterval(clock); clearInterval(slow); clock = slow = 0;
  act?.stop();
}

const clamp = (w) => Math.max(MIN, Math.min(MAX, Math.round(Number(w) || 380)));

function onKey(e) {
  if (e.key === "Escape" && !e.defaultPrevented && matchMedia("(max-width: 900px)").matches) { e.preventDefault(); opts.onClose?.(); }
}

function mount(host, p) {
  act?.stop();
  el = host;
  Object.assign(D, { project: p, sessions: null, sid: null, data: null, error: "", sendError: "", key: "" });
  host.dataset.project = p || "";
  host.className = "chat-dock";
  host.setAttribute("aria-labelledby", "cd-title");
  host.innerHTML = `<div class="cd-grip" role="separator" aria-orientation="vertical" aria-label="Resize the chat dock" tabindex="0" aria-valuemin="${MIN}" aria-valuemax="${MAX}"></div>
    <header class="cd-head"><h2 id="cd-title" tabindex="-1">${icon("chat")} Chat</h2>
      <label class="sr-only" for="cd-session">Session</label><select id="cd-session" class="input sm"></select>
      <span class="grow"></span>
      <a class="btn sm" id="cd-full" href="#/chat" title="Open the full Chat page: task graph, decisions, sessions">Open full</a>
      <button type="button" class="icon-btn sm" data-cd="close" aria-label="Close the chat dock" aria-keyshortcuts="Alt+Shift+A" title="Close (Alt+Shift+A)">${icon("close")}</button></header>
    <p class="cd-progress small" id="cd-progress" role="status" aria-live="polite"></p>
    <div class="chat-log cd-log" id="cd-log" tabindex="0" aria-label="Messages"></div>
    <section class="chat-activity cd-activity" id="cd-activity" aria-label="Running now" hidden></section>
    ${composerHtml("cd", { rows: 2, hint: false })}`;
  act = mountTranscript($("#cd-log", host), { project: () => D.project, sid: () => D.sid, busy: () => hasActivity(D.data), agentName, compact: true, state: () => D.data, error: () => D.error, messageHtml, viewGraph: () => $("#cd-full", el)?.click(), onReply: () => loadSession(), notes: () => D.notes.filter((n) => n.sid === D.sid),
    select: () => { $("#cd-full", el)?.click(); },
    openLog: (rel) => openLog(ctxRef, D.project, rel), cancel: cancelRow });
  composer = bindComposer(host, "cd", { agents, orchestrator: () => D.data?.settings?.orchestrator, send, where: "dock", onCommand: command,
    onError: (m) => { D.sendError = m; draw(); },
    onDraft: (text) => { D.drafts.set(D.sid, text); if (D.sendError) { D.sendError = ""; $("#cd-send-error", el).textContent = ""; } } });
  host.addEventListener("click", onClick);
  host.addEventListener("keydown", onKey); // named handlers: a re-mount on the same host doesn't add a second copy
  $("#cd-session", host).addEventListener("change", (e) => openSession(e.target.value));
  grip($(".cd-grip", host));
}

function grip(g) {
  const set = (w, save) => {
    const v = clamp(w);
    el.style.setProperty("--dock-w", `${v}px`);
    document.documentElement.style.setProperty("--dock-w", `${v}px`);
    g.setAttribute("aria-valuenow", String(v));
    if (save) opts.onWidth?.(v);
  };
  g.setAttribute("aria-valuenow", String(clamp(opts.width || 380)));
  g.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    g.setPointerCapture?.(e.pointerId);
    const move = (ev) => set(window.innerWidth - ev.clientX, false);
    const up = (ev) => { g.removeEventListener("pointermove", move); g.removeEventListener("pointerup", up); set(window.innerWidth - ev.clientX, true); };
    g.addEventListener("pointermove", move);
    g.addEventListener("pointerup", up);
  });
  g.addEventListener("keydown", (e) => {
    const now = Number(g.getAttribute("aria-valuenow")) || 380;
    const step = e.shiftKey ? 80 : 20;
    const to = { ArrowLeft: now + step, ArrowRight: now - step, Home: MAX, End: MIN }[e.key];
    if (to == null) return;
    e.preventDefault();
    set(to, true);
  });
}

// --- data --------------------------------------------------------------------------

async function loadSessions() {
  const p = D.project;
  if (!p) return;
  try {
    const { sessions } = await get(`${base(p)}/sessions`);
    if (p !== D.project) return;
    D.sessions = sessions;
    const remembered = (storage.get(SESSION_KEY, {}) || {})[p];
    const want = sessions.some((s) => s.id === remembered) ? remembered : D.sid && sessions.some((s) => s.id === D.sid) ? D.sid : sessions[0]?.id || null;
    if (want !== D.sid) openSession(want, { remember: false });
  } catch (error) { if (p === D.project) D.error = error.message; }
  draw();
}

async function loadSession() {
  const p = D.project, sid = D.sid, gen = ++D.gen;
  if (!sid) return;
  try {
    const data = await get(`${base(p)}/sessions/${encodeURIComponent(sid)}?messages=200`);
    if (gen !== D.gen || p !== D.project || sid !== D.sid) return;
    if (staleState(D.data, data)) return;
    const was = hasActivity(D.data);
    D.data = data; D.error = "";
    if (hasActivity(data) && !was) act?.kick();
  } catch (error) { if (gen === D.gen) D.error = error.message; }
  draw();
}

function openSession(sid, { remember = true } = {}) {
  D.sid = sid || null; D.data = null; D.gen++; D.key = "";
  if (sid && remember) storage.set(SESSION_KEY, { ...(storage.get(SESSION_KEY, {}) || {}), [D.project]: sid });
  const input = el && $("#cd-input", el);
  if (input) input.value = D.drafts.get(D.sid) || "";
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
  const opts_ = (D.sessions || []).map((s) => `<option value="${esc(s.id)}" ${s.id === D.sid ? "selected" : ""}>${esc(s.title)}${s.active_requests ? " ●" : ""}</option>`).join("");
  const html = opts_ || `<option value="">${D.sessions ? "No session yet" : "Loading…"}</option>`;
  if (sel.dataset.html !== html) { sel.dataset.html = html; sel.innerHTML = html; }
  sel.disabled = !D.sessions?.length;
  const st = ctxRef.state;
  $("#cd-full", el).setAttribute("href", routeHash(D.sid ? `chat/${D.sid}` : "chat", st.selectedProject, st.selectedTarget));
  drawLog();
  drawProgress();
  const send_ = $("#cd-send", el);
  send_.disabled = D.sending || !D.sid;
  send_.setAttribute("aria-busy", String(D.sending));
  $("#cd-send-error", el).textContent = D.sendError || "";
}

function drawLog() {
  const box = $("#cd-log", el);
  if (D.data) { act?.update(D.data, D.notes.filter((n) => n.sid === D.sid)); return; }
  if (D.error) box.innerHTML = `<p class="callout fail small" role="alert">${esc(D.error)} <button type="button" class="btn sm" data-cd="refresh">Retry</button></p>`;
  else if (D.sessions && !D.sessions.length) box.innerHTML = `<div class="empty small"><p>No chat session yet.</p><button type="button" class="btn sm primary" data-cd="new">New session</button></div>`;
  else box.innerHTML = `<p class="muted small" role="status">Loading the session…</p>`;
}

function messageHtml(original) {
  const m = presentMessage(original, D.data?.tasks?.find((t) => t.row === original.task));
  const who = m.role === "user" ? "You" : m.role === "system" ? "ALFRD" : m.task ? `${agentName(m.agent)} · ${D.data?.tasks?.find((t) => t.row === m.task)?.task?.title || "Task result"}` : agentName(m.agent || "alfrd");
  const t = m.task && D.data?.tasks?.find((x) => x.row === m.task);
  const l = t?.logs?.[t.logs.length - 1], log = l?.log;
  return `<article class="chat-msg chat-${esc(m.role)}${m.kind ? ` chat-${esc(m.kind)}` : ""}" aria-label="${esc(who)}">
    <header><b class="small">${esc(who)}</b><time class="muted small" datetime="${esc(m.at)}">${esc(String(m.at || "").slice(11, 16))}</time></header>
    <div class="chat-text">${esc(m.text)}</div>${m.technical ? `<details><summary class="small">Technical details</summary><pre class="log chat-pre">${esc(m.technical)}</pre></details>` : ""}
    ${m.artifacts?.length ? `<p class="muted small mono cd-arts">${m.artifacts.filter((r) => !String(r).includes("/handoffs/")).map((r) => esc(String(r).split("@")[0])).join(" · ")}</p>` : ""}
    ${log ? `<button type="button" class="btn sm link" data-cd-log="${esc(log)}">${icon("log")} Open log</button>` : ""}${l?.handoff ? `<button type="button" class="btn sm link" data-cd-log="${esc(`${l.handoff}/response.md`)}">${icon("file")} Reply</button>` : ""}</article>`;
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
  const text = input.value.trim();
  if (!text || D.sending) return;
  if (!D.sid) {  // nothing to send into yet: the first message starts a session
    try {
      const { session } = await server.mutate(`/studio/projects/${encodeURIComponent(D.project)}/chat/sessions`, {});
      D.sessions = [session, ...(D.sessions || [])];
      openSession(session.id);
    } catch (error) { D.sendError = `Not sent: ${error.message}`; draw(); return; }
  }
  const mention = parseMention(text, agents());
  if (mention?.error) { D.sendError = mention.error; draw(); return; }
  D.sending = true; D.sendError = ""; draw();
  const sid = D.sid;
  try {
    await server.mutate(`/studio/projects/${encodeURIComponent(D.project)}/chat/sessions/${encodeURIComponent(sid)}/messages`, { text });
    if (D.sid === sid) { input.value = ""; D.drafts.delete(sid); composer?.close(); }
    loadSession(); loadSessions();
  } catch (error) { D.sendError = `Not sent: ${error.message}`; }
  D.sending = false; draw();
  input.focus();
}

async function onClick(e) {
  const b = e.target.closest("button, a[data-cd-log]");
  if (!b) return;
  if (b.dataset.cdLog) { openLog(ctxRef, D.project, b.dataset.cdLog); return; }
  const a = b.dataset.cd;
  if (a === "close") opts.onClose?.();
  else if (a === "refresh") { D.error = ""; loadSessions(); loadSession(); }
  else if (a === "new") newSession();
}

async function newSession() {
  try {
    const { session } = await server.mutate(`/studio/projects/${encodeURIComponent(D.project)}/chat/sessions`, {});
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
  D.notes.push({ id: `local:${Date.now()}`, sid: D.sid, role: "system", text, at: new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 19) });
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
