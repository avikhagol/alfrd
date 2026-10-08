// Shared log rendering: collapsible log files that load on open, scroll inside
// a fixed-height box, and a small expand button that opens any log (or any
// inline log snippet) full-screen in a modal.
//
// Live tails: an open log that is on screen, and the full-screen log, follow
// the file as it grows (only the new bytes are fetched). At most MAX_TAILS
// files are followed at once (full screen first), each checked every second
// while it grows and less often while it is quiet; nothing runs while the tab
// is hidden. Scrolled up = paused on that position ("↓ New output" jumps back).
//
// Docked logs: the ⤓ button on a log (or "Minimize" in full screen) docks it as
// a tab of the Log Stream panel, where it keeps following while you switch to
// Overview, Workflow, … (like a terminal tab in VS Code). The docked tab is
// followed first; docked files are remembered across reloads.

import { esc, icon, bytes, when, loadUi, saveUi } from "../utils/dom.js";
import { groupLabel } from "../data/defs.js";
import { notesAt } from "../data/notes.js";

const MAX_INLINE = 400000;
const MAX_TAILS = 3;
const MAX_DOCKED = 6;
const MAX_CACHED = 24;
const TICK_MS = 1000;
const QUIET_MAX_MS = 5000;

const openState = new Map(); // key -> open (remembered across re-renders)
const cache = new Map(); // key -> {project, rel, text, offset, id, live, follow, scrollTop, decoder, idle, nextAt, size, mtime}
let tailsOn = true;

const keyOf = (project, rel) => `${project}::${rel}`;
const dockUi = loadUi("logdock", { keys: [] });
const docked = Array.isArray(dockUi.keys) ? dockUi.keys.filter((k) => typeof k === "string" && k.includes("::")).slice(-MAX_DOCKED) : [];
// Docked tabs keep streaming while the panel is minimized or another project is open.
// Paused = still receiving; the reading position stays and new lines are counted.
const dockFollow = new Map(); // key -> {follow, unread}
const dockOf = (key) => { if (!dockFollow.has(key)) dockFollow.set(key, { follow: true, unread: 0 }); return dockFollow.get(key); };
let onDockChange = null;

// ANSI escape sequences (SGR color/style codes, cursor moves, OSC window-title
// codes, …): AVICA and its libraries (rich/colorama/click) write these when
// they think stdout is a terminal, but the shim always redirects it to a
// plain file, so the log ends up with the raw codes ("\x1b[1m…\x1b[0m") — the
// escape character itself is invisible in a browser <pre>, so only the
// "[1m"/"[0m" part is visible. Stripped before anything is shown or cached.
const ANSI_RE = /\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])/g;

/** Drop ANSI escape sequences a subprocess wrote to its log. */
function stripAnsi(text) {
  return text.includes("\x1b") ? text.replace(ANSI_RE, "") : text;
}

// An escape sequence cut off at the end of a tail read ("\x1b[1" + "m…" next time).
const PARTIAL_ANSI_RE = /\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b]*)?$/;
const MAX_PENDING = 256; // an OSC that never ends is shown rather than held forever

/** New tail text for cache entry `c`, completed with the escape held back last time; holds back a new partial one. */
function takeChunk(c, raw) {
  const text = (c.pending || "") + raw;
  const m = PARTIAL_ANSI_RE.exec(text);
  if (!m || text.length - m.index > MAX_PENDING) {
    c.pending = "";
    return text;
  }
  c.pending = m[0];
  return text.slice(0, m.index);
}

/** Collapse carriage-return progress lines (keep what the terminal would show). */
function clean(text) {
  text = stripAnsi(text);
  if (!text.includes("\r")) return text;
  return text.replace(/\r\n/g, "\n").split("\n").map((line) => (line.includes("\r") ? line.split("\r").filter(Boolean).pop() || "" : line)).join("\n");
}

function trim(text) {
  if (text.length <= MAX_INLINE * 1.25) return text;
  const cut = text.slice(-MAX_INLINE);
  const nl = cut.indexOf("\n");
  return nl >= 0 && nl < 4096 ? cut.slice(nl + 1) : cut;
}

const atBottom = (el) => el.scrollHeight - el.scrollTop - el.clientHeight < 24;

/** Files for a project (step logs + log artifacts from alfrd.yaml). */
const sources = []; // extra log lists (e.g. plans.js: command logs of scheduled runs)

/** Add a source of log files: fn(ctx, project) → [{rel, name, groups, target, steps, size, mtime}]. */
export function addLogSource(fn) {
  if (!sources.includes(fn)) sources.push(fn);
}

export function projectLogs(ctx, project) {
  const base = ctx.state.trees?.[project]?.logFiles || [];
  if (!sources.length) return base;
  const seen = new Set(base.map((f) => f.rel));
  const extra = sources.flatMap((fn) => { try { return fn(ctx, project) || []; } catch { return []; } }).filter((f) => f?.rel && !seen.has(f.rel) && seen.add(f.rel));
  return extra.length ? [...base, ...extra] : base;
}

/** Does a listed log belong to this target? (own target, its work dirs, or project-wide). */
export function logForTarget(ctx, f, t, codes = []) {
  if (!t) return true;
  if (f.target) return f.target === t.name;
  if (f.workdir) return codes.some((c) => f.workdir === c || String(f.workdir).startsWith(`${c}/`) || c.startsWith(`${f.workdir}/`));
  const crash = ctx.state.avica?.[t.project]?.logs?.find((l) => l.kind === "crash" && f.rel.endsWith(`/${l.name}`));
  if (crash?.crash?.target) return crash.crash.target === t.name;
  return true;
}

function metaText(f) {
  return `${f.size ? bytes(f.size) : ""}${f.mtime ? ` · ${when(new Date(f.mtime).toISOString())}` : ""}`;
}

/** One collapsible log file. The body is filled on first open (see bindLogs). */
export function logItem(f, { project, open = false, showGroups = false, defs = null } = {}) {
  const key = keyOf(project, f.rel);
  const isOpen = openState.has(key) ? openState.get(key) : open;
  const dir = f.rel.includes("/") ? f.rel.slice(0, f.rel.lastIndexOf("/")) : "";
  const tags = [f.band && `<span class="code-chip">${esc(f.band)}</span>`, f.target && `<span class="code-chip auto">${esc(f.target)}</span>`].filter(Boolean).join("");
  const groups = showGroups && f.groups?.length ? `<span class="muted small">${esc(f.groups.map((g) => groupLabel(g, defs)).join(" · "))}</span>` : "";
  return `<details class="log-item" data-log-rel="${esc(f.rel)}" data-log-project="${esc(project)}" ${isOpen ? "open" : ""}>
    <summary><span class="caret">${icon("caret")}</span><span class="mono log-name" title="${esc(f.rel)}">${esc(f.name || f.rel.split("/").pop())}</span>${tags}
      <span class="muted small mono trunc log-dir">${esc(dir)}</span>${groups}<span class="grow"></span>
      <span class="live-dot" data-log-live hidden title="Following this file as it grows"></span>
      <span class="muted small tabular" data-log-meta="${esc(key)}">${esc(metaText(f))}</span>
      <button class="icon-btn xs" data-log-dock="${esc(f.rel)}" data-log-project="${esc(project)}" title="Follow in the Log Stream panel (keeps running on other views)" aria-label="Follow ${esc(f.name || f.rel)} in the Log Stream panel">${icon("log")}</button>
      <button class="icon-btn xs" data-log-zoom="${esc(f.rel)}" data-log-project="${esc(project)}" title="Open full screen" aria-label="Open ${esc(f.name || f.rel)} full screen">${icon("expand")}</button></summary>
    <div class="log-wrap"><pre class="log log-box" data-log-body data-log-key="${esc(key)}">${isOpen ? "Loading…" : ""}</pre><button class="log-jump" data-log-jump hidden>${icon("chevron")} New output</button></div></details>`;
}

/** An inline snippet (not a file) with the same expand button. */
export function zoomablePre(text, { title = "Log", cls = "log small log-box", empty = "" } = {}) {
  const body = text ? esc(text) : empty;
  return `<div class="zoomable"><button class="icon-btn xs zoom-btn" data-zoom-pre data-title="${esc(title)}" title="Open full screen" aria-label="Open ${esc(title)} full screen">${icon("expand")}</button><pre class="${cls}">${body}</pre></div>`;
}

/** Read a log into the cache (first time), or return what is cached. */
async function load(ctx, project, rel) {
  const key = keyOf(project, rel);
  let c = cache.get(key);
  if (c && typeof c.text === "string") return c;
  const decoder = typeof TextDecoder === "function" ? new TextDecoder() : null;
  const res = await ctx.readLogRange(project, rel, null, { decoder });
  c = { project, rel, pending: "", offset: res.offset, id: res.id, live: res.live !== false && res.offset != null, follow: true, scrollTop: 0, decoder, idle: 0, nextAt: Date.now() + TICK_MS, size: res.size, mtime: res.mtime };
  c.text = trim(clean(takeChunk(c, res.text || "")));
  cache.set(key, c);
  evict();
  return c;
}

function evict() {
  if (cache.size <= MAX_CACHED) return;
  for (const [key] of cache) {
    if (cache.size <= MAX_CACHED) break;
    if (!openState.get(key)) cache.delete(key);
  }
}

/** Show a cached log in a <pre>, keeping the reader's place. */
function show(pre, c, { follow = c.follow, scrollTop = c.scrollTop } = {}) {
  pre.textContent = c.text || "(empty file)";
  pre.dataset.loaded = "1";
  pre.dataset.follow = follow ? "1" : "0";
  pre.scrollTop = follow ? pre.scrollHeight : scrollTop;
  bindScroll(pre, c);
  liveMark(pre, c);
}

function liveMark(pre, c) {
  const d = pre.closest("details.log-item");
  const dot = d?.querySelector("[data-log-live]");
  if (dot) dot.hidden = !(c.live && tailsOn);
}

function bindScroll(pre, c) {
  if (pre.dataset.bound) return;
  pre.dataset.bound = "1";
  pre.addEventListener("scroll", () => {
    const follow = atBottom(pre);
    pre.dataset.follow = follow ? "1" : "0";
    if (!pre.closest("#modal-host, #console")) { c.follow = follow; c.scrollTop = pre.scrollTop; }
    const jump = pre.parentElement?.querySelector("[data-log-jump]");
    if (jump && follow) jump.hidden = true;
  }, { passive: true });
}

async function fill(ctx, d) {
  const pre = d.querySelector("[data-log-body]");
  if (!pre || pre.dataset.loaded) return;
  pre.dataset.loaded = "loading";
  const project = d.dataset.logProject;
  const rel = d.dataset.logRel;
  const key = keyOf(project, rel);
  if (!cache.has(key)) pre.textContent = "Loading…";
  try {
    show(pre, await load(ctx, project, rel));
  } catch (error) {
    pre.textContent = `(${error.message})`;
    pre.dataset.loaded = "error";
  }
}

export function openFull(ctx, title, text, sub = "", live = null) {
  const key = live ? keyOf(live.project, live.rel) : "";
  ctx.modal(`<header class="modal-h"><h2 class="mono trunc">${esc(title)}</h2>${sub ? `<span class="muted small mono trunc">${esc(sub)}</span>` : ""}<span class="grow"></span>
      ${live ? `<label class="follow-toggle" title="Keep the newest lines in view"><input type="checkbox" data-follow checked> Follow</label><span class="live-dot" data-log-live ${live.c?.live && tailsOn ? "" : "hidden"}></span>
      <button class="btn sm" data-log-dock="${esc(live.rel)}" data-log-project="${esc(live.project)}" title="Minimize to a Log Stream tab: keep following it while you use the other views">${icon("minus")} Minimize to Log Stream</button>` : ""}
      <button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><div class="log-wrap full"><pre class="log log-full" ${live ? `data-live-log data-log-key="${esc(key)}"` : ""}>${esc(text)}</pre><button class="log-jump" data-log-jump hidden>${icon("chevron")} New output</button></div></div>`, (root) => {
    const pre = root.querySelector("pre");
    pre.dataset.follow = "1";
    pre.scrollTop = pre.scrollHeight;
    if (!live) return;
    bindScroll(pre, live.c);
    const box = root.querySelector("[data-follow]");
    box.addEventListener("change", () => {
      pre.dataset.follow = box.checked ? "1" : "0";
      if (box.checked) pre.scrollTop = pre.scrollHeight;
    });
    pre.addEventListener("scroll", () => { box.checked = pre.dataset.follow === "1"; }, { passive: true });
  }, "full");
}

/** Full-screen view of a file, following it as it grows. */
export async function openFileFull(ctx, project, rel) {
  const v = await import("./viewers.js"), viewer = v.viewerFor(rel);
  if (viewer.id !== "text") return v.openViewer(ctx, project, rel, viewer);
  const c = await load(ctx, project, rel);
  c.nextAt = 0;
  openFull(ctx, rel.split("/").pop(), c.text, rel, { project, rel, c });
  const n = notesAt(ctx, project, { file: rel });
  if (n.length) {
    const h = document.querySelector("#modal-host .modal-h .grow");
    h?.insertAdjacentHTML("beforebegin", `<button class="btn sm" data-notes="${esc(JSON.stringify({ file: rel }))}" data-notes-project="${esc(project)}" title="${esc(n.map((x) => `${x.anchor.line ? `line ${x.anchor.line}: ` : ""}${x.text.slice(0, 80)}`).join("\n"))}">✎ ${n.length} note(s)</button>`);
  }
}

// ---------------------------------------------------------------------------
// Tails

function visible(el) {
  if (!el.isConnected || !el.offsetParent) return false;
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < (window.innerHeight || 0) && r.height > 0;
}

/** The <pre> elements to follow right now: full screen first, then open logs on screen. */
function followed(root) {
  const host = document.getElementById("modal-host");
  const full = host && !host.hidden ? [...host.querySelectorAll("pre[data-live-log]")] : [];
  if (host && !host.hidden) return full; // the rest is behind the modal
  // The docked Log Stream tab first (always followed while the panel is shown).
  const dock = [...document.querySelectorAll("#console:not([hidden]) pre[data-dock-log][data-loaded='1']")];
  return [...dock, ...[...root.querySelectorAll("details.log-item[open] pre[data-log-body][data-loaded='1']")].filter(visible)];
}

const shown = (c) => c.text || "(empty file)";

/** Bring a <pre> up to date: append the new part when it matches, else replace. */
function paint(pre, c, appended, before) {
  const follow = pre.dataset.follow !== "0";
  if (appended != null && pre.textContent.length === before.length && before) pre.appendChild(document.createTextNode(appended));
  else pre.textContent = shown(c);
  if (follow) pre.scrollTop = pre.scrollHeight;
  else {
    const jump = pre.parentElement?.querySelector("[data-log-jump]");
    if (jump) jump.hidden = false;
  }
}

function paintMeta(key, size, mtime) {
  document.querySelectorAll("[data-log-meta]").forEach((el) => {
    if (el.dataset.logMeta === key) el.textContent = metaText({ size, mtime });
  });
}

async function tick(ctx, root) {
  if (!tailsOn || document.hidden) return;
  const now = Date.now();
  const pres = followed(root);
  const byKey = new Map();
  pres.forEach((pre) => {
    const key = pre.dataset.logKey;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(pre);
  });
  // A <pre> that missed updates (e.g. while the full-screen view had them) catches up.
  byKey.forEach((list, key) => {
    const c = cache.get(key);
    if (c) list.forEach((pre) => { if (pre.textContent.length !== shown(c).length) paint(pre, c, null, ""); });
  });
  // Docked logs are always followed (they are few); other on-screen logs share MAX_TAILS.
  docked.forEach((key) => {
    const d = dockOf(key);
    if (cache.has(key) || d.loading || (d.retryAt || 0) > now) return;
    const { project, rel } = splitKey(key);
    d.loading = load(ctx, project, rel)
      .then(() => { d.error = null; onDockChange?.(key); })
      .catch((error) => { // refused files are not retried; anything else in 10 s
        d.error = error.message;
        d.retryAt = error.status === 403 || error.status === 404 ? Infinity : Date.now() + 10000;
        onDockChange?.(key);
      })
      .finally(() => { d.loading = null; });
  });
  const keys = [...docked.filter((k) => cache.has(k)), ...[...byKey.keys()].filter((k) => !docked.includes(k)).slice(0, MAX_TAILS)];
  await Promise.all(keys.map(async (key) => {
    const c = cache.get(key);
    if (!c || !c.live || now < c.nextAt || c.busy) return;
    c.busy = true;
    try {
      const res = await ctx.readLogRange(c.project, c.rel, c.offset, { id: c.id, decoder: c.decoder });
      const grew = Boolean(res.text) || res.reset;
      const before = shown(c);
      if (res.reset) c.pending = "";
      const raw = takeChunk(c, res.text || "");
      let next;
      let added = null; // text that can simply be appended to the <pre>
      if (res.reset) next = clean(raw);
      else if (raw.includes("\r")) {
        // A progress line continues across reads: redo the unfinished last line.
        const nl = c.text.lastIndexOf("\n");
        next = c.text.slice(0, nl + 1) + clean(c.text.slice(nl + 1) + raw);
      } else {
        added = stripAnsi(raw);
        next = c.text + added;
      }
      c.text = trim(next);
      const appended = added != null && c.text === next && c.text.length > 0 && before !== "(empty file)" ? added : null;
      c.offset = res.offset;
      c.id = res.id;
      c.idle = grew ? 0 : c.idle + 1;
      c.nextAt = Date.now() + (grew ? TICK_MS : Math.min(QUIET_MAX_MS, TICK_MS * (1 + c.idle / 2)));
      const wasDown = c.reconnecting;
      c.reconnecting = false;
      if (docked.includes(key) && (grew || wasDown)) {
        const d = dockOf(key);
        if (!d.follow && added) d.unread += (added.match(/\n/g) || []).length || 1;
        onDockChange?.(key);
      }
      if (grew) {
        (byKey.get(key) || []).forEach((pre) => paint(pre, c, appended, before));
        c.size = res.size;
        c.mtime = res.mtime || c.mtime;
        paintMeta(key, c.size, c.mtime);
        ctx.onLogGrew?.(c.project, c.rel, c.size, c.mtime);
      }
    } catch (error) {
      c.nextAt = Date.now() + 10000;
      if (error.status === 404 || error.status === 403) { c.live = false; byKey.get(key)?.forEach((pre) => liveMark(pre, c)); }
      else if (!c.reconnecting) { c.reconnecting = true; if (docked.includes(key)) onDockChange?.(key); } // received output is kept
    } finally {
      c.busy = false;
    }
  }));
}

/** Live updates said these logs grew: refresh their size labels and check them now. */
export function nudgeLogs(project, logs) {
  Object.entries(logs || {}).forEach(([rel, [size, mtime]]) => {
    const key = keyOf(project, rel);
    paintMeta(key, size, mtime * 1000);
    const c = cache.get(key);
    if (c) c.nextAt = 0;
  });
}

/** A re-scan replaced the file list: forget cached text of files that are gone. */
export function forgetLogs(project, rels) {
  (rels || []).forEach((rel) => cache.delete(keyOf(project, rel)));
}

/** Turn following on/off (Settings → Live updates). */
export function setTails(on) {
  tailsOn = Boolean(on);
  document.querySelectorAll("[data-log-live]").forEach((dot) => {
    const pre = dot.closest("details.log-item")?.querySelector("pre[data-log-key]") || dot.closest(".modal")?.querySelector("pre[data-log-key]");
    const c = pre && cache.get(pre.dataset.logKey);
    dot.hidden = !(c?.live && tailsOn);
  });
}

/** Install once: lazy loading on open, the expand buttons (files and snippets), tails. */
export function bindLogs(ctx, root = document) {
  root.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("log-item")) return;
    openState.set(keyOf(d.dataset.logProject, d.dataset.logRel), d.open);
    if (d.open) fill(ctx, d);
  }, true);
  root.addEventListener("click", async (e) => {
    const jump = e.target.closest("[data-log-jump]");
    if (jump) {
      const pre = jump.parentElement.querySelector("pre");
      pre.dataset.follow = "1";
      pre.scrollTop = pre.scrollHeight;
      jump.hidden = true;
      return;
    }
    const dk = e.target.closest("[data-log-dock]");
    if (dk) {
      e.preventDefault();
      e.stopPropagation();
      if (dk.closest("#modal-host")) document.querySelector("#modal-host [data-close]")?.click();
      dockLog(ctx, dk.dataset.logProject, dk.dataset.logDock);
      return;
    }
    const z = e.target.closest("[data-log-zoom]");
    if (z) {
      e.preventDefault();
      e.stopPropagation();
      try {
        await openFileFull(ctx, z.dataset.logProject, z.dataset.logZoom);
      } catch (error) {
        ctx.toast(error.message, "warn");
      }
      return;
    }
    const p = e.target.closest("[data-zoom-pre]");
    if (p) {
      e.preventDefault();
      const pre = p.parentElement.querySelector("pre");
      openFull(ctx, p.dataset.title || "Log", pre ? pre.textContent : "");
    }
  });
  // Details rendered already open (remembered, or the first step log) need their body too.
  new MutationObserver(() => {
    root.querySelectorAll("details.log-item[open] [data-log-body]:not([data-loaded])").forEach((pre) => fill(ctx, pre.closest("details")));
  }).observe(root, { childList: true, subtree: true });
  // Clicks from the modal (jump button) happen outside `root` when root is a view.
  const host = document.getElementById("modal-host");
  if (host && root !== document.body && !root.contains(host)) {
    host.addEventListener("click", (e) => {
      const jump = e.target.closest("[data-log-jump]");
      if (!jump) return;
      const pre = jump.parentElement.querySelector("pre");
      pre.dataset.follow = "1";
      pre.scrollTop = pre.scrollHeight;
      jump.hidden = true;
    });
  }
  const loop = async () => {
    try { await tick(ctx, root); } catch { /* keep the loop alive */ }
    setTimeout(loop, TICK_MS);
  };
  setTimeout(loop, TICK_MS);
}

// ---------------------------------------------------------------------------
// Docked logs (Log Stream tabs)

function saveDocked() {
  saveUi("logdock", { keys: docked }, ["keys"]);
}

/** Split a dock key back into project and path. */
export function splitKey(key) {
  const i = String(key).indexOf("::");
  return i < 0 ? { project: "", rel: String(key) } : { project: key.slice(0, i), rel: key.slice(i + 2) };
}

/** Docked logs, in tab order: [{key, project, rel, name, live}]. */
export function dockedLogs() {
  return docked.map((key) => {
    const { project, rel } = splitKey(key);
    const c = cache.get(key);
    return { key, project, rel, name: rel.split("/").pop() || rel, live: Boolean(c?.live && tailsOn) };
  });
}

/**
 * Dock a log as a Log Stream tab. At MAX_DOCKED nothing is evicted silently: the user is
 * asked to close a tab first and null is returned. Returns the key otherwise.
 */
export function dockLog(ctx, project, rel) {
  const key = keyOf(project, rel);
  if (!docked.includes(key)) {
    if (docked.length >= MAX_DOCKED) {
      ctx.toast?.(`The Log Stream already follows ${MAX_DOCKED} logs. Close a tab before following another.`, "warn");
      ctx.renderConsole?.();
      return null;
    }
    docked.push(key);
    saveDocked();
  }
  const c = cache.get(key);
  if (c) c.nextAt = 0;
  ctx.showDock?.(key);
  return key;
}

/** Close a Log Stream tab (never cancels the job that writes it). */
export function undockLog(key) {
  const i = docked.indexOf(key);
  if (i >= 0) {
    docked.splice(i, 1);
    dockFollow.delete(key);
    saveDocked();
  }
}

/** Called when a docked log grows, pauses or reconnects (the minimized strip re-renders). */
export function onDock(fn) { onDockChange = fn; }

/** Preview of a docked log: latest line, follow state, unread count, connection. */
export function dockInfo(key) {
  const c = cache.get(key);
  const d = dockOf(key);
  const text = (c?.text || "").replace(/\s+$/, "");
  return { last: text.slice(text.lastIndexOf("\n") + 1).slice(-240), follow: d.follow, unread: d.unread, reconnecting: Boolean(c?.reconnecting), live: Boolean(c?.live && tailsOn), loaded: Boolean(c), error: c ? null : d.error || null };
}

/** Pause or resume following a docked log; resuming jumps to the tail. */
export function setDockFollow(key, follow) {
  const d = dockOf(key);
  d.follow = Boolean(follow);
  if (d.follow) d.unread = 0;
  document.querySelectorAll(`pre[data-dock-log]`).forEach((pre) => {
    if (pre.dataset.logKey !== key) return;
    pre.dataset.follow = d.follow ? "1" : "0";
    if (d.follow) pre.scrollTop = pre.scrollHeight;
  });
  onDockChange?.(key);
}

/** Show a docked log in `body` (the Log Stream panel), following it. */
export async function mountDock(ctx, body, key) {
  const { project, rel } = splitKey(key);
  body.innerHTML = `<div class="log-wrap dock"><pre class="log log-dock" data-dock-log data-live-log data-log-key="${esc(key)}" tabindex="0" aria-label="${esc(rel)}">Loading…</pre><button class="log-jump" data-log-jump hidden>${icon("chevron")} New output</button></div>`;
  const pre = body.querySelector("pre");
  try {
    const c = await load(ctx, project, rel);
    if (!pre.isConnected) return;
    c.nextAt = 0;
    const d = dockOf(key);
    show(pre, c, { follow: d.follow, scrollTop: d.scrollTop || 0 });
    // Scrolling up pauses following (output keeps arriving); back at the bottom resumes it.
    pre.addEventListener("scroll", () => {
      const at = atBottom(pre);
      d.scrollTop = pre.scrollTop;
      if (at !== d.follow) setDockFollow(key, at);
    }, { passive: true });
  } catch (error) {
    if (pre.isConnected) {
      pre.textContent = `(${error.message})`;
      pre.dataset.loaded = "error";
    }
  }
}

// Exposed for tests.
export const _internals = { clean, stripAnsi, takeChunk, trim, cache, openState, keyOf, docked, MAX_DOCKED, tick, dockFollow };
