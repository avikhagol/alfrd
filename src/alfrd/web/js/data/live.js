// Live updates: keeps the Studio in sync with the project folder without Re-scan.
//
// Server mode (`alfrd serve`): one Server-Sent Events stream per tab
// (/api/studio/events). The server polls the files alfrd.yaml declares (stat
// only) and sends `tree` events (files to re-read), log growth, and `runtime`
// events (the runtime database changed). If the stream keeps failing (a proxy
// that buffers, an old browser) the same events are polled from
// /api/studio/changes. Folder mode (File System Access): a stat-only scan of
// the remembered folder every few seconds.
//
// Nothing runs while the tab is hidden; the server stops its watchers when no
// tab listens. When the tab comes back, versions tell what was missed.

import { server } from "./server.js";
import { folderFingerprint, diffFingerprints } from "./folder_scan.js";

const RETRY_SSE_MS = 60000;
const POLL_MIN_MS = 3000;
const FOLDER_MIN_MS = 5000;
const FOLDER_IDLE_MS = 30000;
const HOT_MS = 120000;
const RUNTIME = "@runtime";

/**
 * @param {object} hooks
 *   mode(): "server" | "browser"
 *   projects(): project names to follow (server mode)
 *   folders(): [{project, handle}] (browser mode)
 *   scanState(project): {ts, epoch, version} of the scan on screen (server `generated_ts`, `live`)
 *   onTree(project, {changed, removed}), onLogs(project, {rel: [size, mtime]}),
 *   onRuntime(), onResync(project), onStatus(status)
 */
export function createLive(hooks) {
  const live = {
    enabled: true,
    status: { state: "off", detail: "", last: null, interval: null },
    known: {}, // key -> {epoch, version}
    prints: {}, // browser mode: project -> fingerprint
    es: null,
    transport: "sse",
    timer: null,
    retryTimer: null,
    interval: 2,
    idle: 5,
    folderMs: FOLDER_MIN_MS,
    lastChange: 0,
    watching: "",
    gen: 0, // bumped by restart(): a loop from an older generation stops after its await
  };

  const set = (state, detail = "") => {
    live.status = { ...live.status, state, detail };
    hooks.onStatus?.(live.status);
  };
  const touched = () => {
    live.status = { ...live.status, last: new Date() };
    hooks.onStatus?.(live.status);
  };

  function clear() {
    if (live.es) { live.es.close(); live.es = null; }
    clearTimeout(live.timer);
    clearTimeout(live.retryTimer);
    live.timer = live.retryTimer = null;
  }

  // -- events (shared by SSE and polling) ------------------------------------
  function hello(state) {
    Object.entries(state || {}).forEach(([key, st]) => {
      const prev = live.known[key];
      if (key === RUNTIME) {
        if (prev && (prev.epoch !== st.epoch || prev.version !== st.version)) hooks.onRuntime?.();
      } else if (!prev) {
        // First contact: did anything change after the scan on screen was made?
        const scan = hooks.scanState?.(key) || {};
        const stale = scan.epoch ? scan.epoch !== st.epoch || scan.version < st.version : scan.ts == null || scan.ts < st.baseline_ts;
        if (stale) hooks.onResync?.(key);
      } else if (prev.epoch !== st.epoch || prev.version !== st.version) {
        hooks.onResync?.(key);
      }
      live.known[key] = { epoch: st.epoch, version: st.version };
      if (st.interval) live.status.interval = st.interval;
    });
    touched();
  }

  function event(ev) {
    const key = ev.key;
    const prev = live.known[key];
    if (prev && prev.epoch === ev.epoch && ev.version <= prev.version) return; // already seen
    live.known[key] = { epoch: ev.epoch, version: ev.version };
    touched();
    if (ev.type === "runtime") { hooks.onRuntime?.(); return; }
    if (prev && (prev.epoch !== ev.epoch || ev.version !== prev.version + 1)) {
      hooks.onResync?.(key); // missed some: re-read the project
      return;
    }
    live.lastChange = Date.now();
    if ((ev.changed || []).length || (ev.removed || []).length) hooks.onTree?.(key, { changed: ev.changed || [], removed: ev.removed || [] });
    if (ev.logs && Object.keys(ev.logs).length) hooks.onLogs?.(key, ev.logs);
  }

  // -- server: SSE, then polling ---------------------------------------------
  function connectSse(projects) {
    const es = new EventSource(server.eventsUrl(projects));
    live.es = es;
    es.addEventListener("hello", (e) => {
      const data = JSON.parse(e.data);
      live.interval = data.interval || live.interval;
      live.idle = data.idle || live.idle;
      set("live", "event stream");
      hello(data.state);
    });
    ["tree", "runtime"].forEach((type) => es.addEventListener(type, (e) => { try { event(JSON.parse(e.data)); } catch { /* ignore */ } }));
    es.addEventListener("reset", () => hooks.projects().forEach((p) => hooks.onResync?.(p)));
    es.onerror = () => {
      // EventSource hides HTTP status and reconnects automatically. Close it
      // and use polling to distinguish a lost connection from a 401.
      es.close();
      if (live.es !== es) return;
      live.es = null;
      if (!server.authRequired) startPolling("event stream unavailable — polling");
    };
  }

  function startPolling(detail) {
    live.transport = "poll";
    set("polling", detail);
    pollTick();
    // Try the stream again now and then.
    clearTimeout(live.retryTimer);
    live.retryTimer = setTimeout(() => { live.transport = "sse"; restart(); }, RETRY_SSE_MS);
  }

  async function pollTick() {
    clearTimeout(live.timer);
    if (!running()) return;
    const gen = live.gen;
    const projects = hooks.projects();
    const since = Object.fromEntries(Object.entries(live.known).map(([k, v]) => [k, `${v.epoch}:${v.version}`]));
    try {
      const res = await server.changes(projects, since);
      live.interval = res.interval || live.interval;
      live.idle = res.idle || live.idle;
      const fresh = Object.fromEntries(Object.entries(res.state || {}).filter(([k]) => !live.known[k]));
      hello(fresh);
      if (gen !== live.gen) return;
      (res.reset || []).forEach((k) => (k === RUNTIME ? hooks.onRuntime?.() : hooks.onResync?.(k)));
      (res.events || []).sort((a, b) => a.version - b.version).forEach(event);
      Object.entries(res.state || {}).forEach(([k, st]) => { live.known[k] = { epoch: st.epoch, version: st.version }; });
      if (live.status.state !== "polling") set("polling", "polling for changes");
    } catch (error) {
      if (gen !== live.gen) return;
      set(error.status === 404 ? "unavailable" : "reconnecting", error.message);
      if (error.status === 404) return;
    }
    if (gen !== live.gen) return;
    const hot = Date.now() - live.lastChange < HOT_MS;
    live.timer = setTimeout(pollTick, Math.max(POLL_MIN_MS, 1000 * (hot ? live.interval : live.idle)));
  }

  // -- browser: folder fingerprints ------------------------------------------
  async function folderTick() {
    clearTimeout(live.timer);
    if (!running()) return;
    const gen = live.gen;
    const folders = hooks.folders();
    if (!folders.length) { set("off", "open a project folder to follow it"); return; }
    let slowest = 0;
    let needPermission = false;
    for (const { project, handle } of folders) {
      try {
        if (handle.queryPermission && (await handle.queryPermission({ mode: "read" })) !== "granted") { needPermission = true; continue; }
        const { prints, ms } = await folderFingerprint(handle);
        if (gen !== live.gen) return;
        slowest = Math.max(slowest, ms);
        const prev = live.prints[project];
        live.prints[project] = prints;
        if (!prev) continue;
        const diff = diffFingerprints(prev, prints);
        if (diff.changed.length || diff.removed.length) { live.lastChange = Date.now(); hooks.onTree?.(project, diff); }
        if (Object.keys(diff.logs).length) { live.lastChange = Date.now(); hooks.onLogs?.(project, diff.logs); }
      } catch (error) {
        set("reconnecting", `${project}: ${error.message}`);
      }
    }
    if (gen !== live.gen) return;
    if (needPermission) set("permission", "click to allow reading the folder again");
    else { set("live", "checking the folder"); touched(); }
    const hot = Date.now() - live.lastChange < HOT_MS;
    live.folderMs = Math.max(hot ? FOLDER_MIN_MS : FOLDER_IDLE_MS, 10 * slowest);
    live.status.interval = live.folderMs / 1000;
    live.timer = setTimeout(folderTick, live.folderMs);
  }

  // -- lifecycle ---------------------------------------------------------------
  function running() {
    return live.enabled && !server.authRequired && !document.hidden;
  }

  function restart() {
    clear();
    live.gen += 1;
    live.lastChange = Date.now(); // start busy: the reader just arrived
    if (server.authRequired) { set("auth-required", "access link needed"); return; }
    if (!live.enabled) { set("off", "live updates are off"); return; }
    if (document.hidden) { set("paused", "tab hidden"); return; }
    if (hooks.mode() === "server") {
      if (hooks.available && !hooks.available()) { set("unavailable", "this server has live updates off"); return; }
      const projects = hooks.projects();
      live.watching = projects.join(",");
      if (!projects.length) { set("off", "no project"); return; }
      if (live.transport === "sse" && typeof EventSource === "function") connectSse(projects);
      else startPolling("polling for changes");
    } else {
      folderTick();
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clear(); if (live.enabled) set("paused", "tab hidden"); }
    else restart();
  });
  window.addEventListener("pagehide", clear);
  server.onAuthRequired(() => {
    clear();
    live.gen += 1;
    set("auth-required", "access link needed");
  });

  Object.assign(live, {
    started: false,
    start() { live.started = true; restart(); },
    stop: () => { live.enabled = false; restart(); },
    setEnabled(on) { live.enabled = Boolean(on); restart(); },
    /** Projects changed (connect/forget): reconnect when the set is different. */
    sync() {
      if (live.started && hooks.mode() === "server" && hooks.projects().join(",") !== live.watching) restart();
    },
    /** A folder was (re)opened: take a fresh baseline. */
    resetFolder(project) { delete live.prints[project]; if (hooks.mode() !== "server") restart(); },
    /** Check now (folder mode) — e.g. after a permission prompt. */
    kick() { restart(); },
  });
  return live;
}
