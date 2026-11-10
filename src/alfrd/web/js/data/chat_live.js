// A shared, bounded activity subscription for the full page and dock.
import { server } from "./server.js";
const sessions = new Map();
export function subscribeActivity(project, sid, listener, options = {}) {
  const key = JSON.stringify([project, sid]);
  if (!sessions.has(key)) sessions.set(key, createActivity(project, sid));
  const shared = sessions.get(key), sub = { listener, ...options };
  shared.subs.add(sub); listener(shared.snapshot()); shared.kick();
  return { kick: shared.kick, stop() { shared.subs.delete(sub); if (!shared.subs.size) { shared.stop(); sessions.delete(key); } } };
}
function createActivity(project, sid) {
  const subs = new Set(), cache = new Map();
  const url = `/api/studio/projects/${encodeURIComponent(project)}/chat/sessions/${encodeURIComponent(sid)}/activity`;
  let telemetry = {};
  let timer, inflight = false, again = false, stopped = false, unsupported = false, stale = false, at = 0;
  const snapshot = () => ({ items: [...cache.values()], stale, unsupported, at, telemetry });
  const wants = () => [...subs].filter((s) => s.visible?.() !== false);
  const visible = () => !document.hidden && wants().length;
  const onVisibility = () => { if (!document.hidden) kick(); };
  document.addEventListener("visibilitychange", onVisibility);
  async function fetchItems(query) {
    const res = await server.request(`${url}?${new URLSearchParams(query)}`, { headers: { Accept: "application/json" } });
    if (res.status === 404 || res.status === 405) { unsupported = true; return; }
    if (!res.ok) throw new Error("Activity unavailable");
    const body = await res.json();
    if (!stopped) {
      for (const key of ["requests", "tasks"]) telemetry[key] = { ...telemetry[key], ...body.telemetry?.[key] };
      const incoming = body.items || body.running || [];
      // A disappearance from the running snapshot needs one final explicit read.
      if (!query.units) for (const [key, old] of cache) if (old.status === "running" && !incoming.some((r) => (r.unit || r.row) === key)) cache.set(key, { ...old, finalPending: true });
      for (const item of incoming) cache.set(item.unit || item.row, item);
    }
  }
  function schedule() {
    clearTimeout(timer);
    if (!stopped && !unsupported && visible() && wants().some((s) => s.busy?.())) timer = setTimeout(load, 2000);
  }
  async function load() {
    if (stopped || unsupported || !visible()) return;
    if (inflight) { again = true; return; }
    inflight = true; again = false;
    try {
      await fetchItems({ lines: 40 });
      const desired = new Map();
      for (const item of cache.values()) if (item.finalPending && item.unit) desired.set(item.unit, 40);
      for (const s of wants()) for (const [unit, lines] of s.units?.() || []) desired.set(unit, Math.max(lines, desired.get(unit) || 0));
      const units = [...desired.keys()];
      for (let i = 0; i < units.length && !unsupported; i += 32) {
        const batch = units.slice(i, i + 32);
        await fetchItems({ units: batch.join(","), lines: Math.min(400, Math.max(40, ...batch.map((u) => desired.get(u)))) });
      }
      stale = false; at = Date.now();
    } catch { stale = true; }
    inflight = false;
    if (!stopped) for (const s of subs) s.listener(snapshot());
    schedule();
    if (again && !stopped) { again = false; clearTimeout(timer); timer = setTimeout(load, 100); }
  }
  function kick() { clearTimeout(timer); timer = setTimeout(load, 80); }
  return { subs, snapshot, kick, stop() { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisibility); } };
}

/** Id-based merge makes cursor replays and resolution races harmless. */
export function mergeMailbox(previous = [], page = {}) {
  const records = new Map(previous.map((m) => [m.id, m]));
  for (const m of page.messages || []) {
    const old = records.get(m.id);
    const merged = { ...old, ...m };
    if (old && ["resolved", "expired", "cancelled"].includes(old.state) && (m.version || 1) <= (old.version || 1)) {
      merged.state = old.state; merged.version = old.version; merged.resolution = old.resolution;
    }
    records.set(m.id, merged);
  }
  for (const e of page.events || []) {
    const m = records.get(e.message_id);
    if (m && ["resolved", "expired", "cancelled"].includes(e.type) && (m.resolution?.event_seq || 0) < e.event_seq) records.set(m.id, { ...m, state: e.type, resolution: e, version: Math.max(2, m.version || 1) });
  }
  return [...records.values()].sort((a, b) => a.seq - b.seq);
}

const mailboxes = new Map();
export function subscribeMailbox(project, sid, rid, listener, options = {}) {
  const key = JSON.stringify([project, sid, rid]);
  if (!mailboxes.has(key)) mailboxes.set(key, createMailbox(project, sid, rid));
  const shared = mailboxes.get(key), sub = { listener, ...options };
  shared.subs.add(sub); listener(shared.snapshot()); shared.kick();
  return { kick: shared.kick, stop() { shared.subs.delete(sub); if (!shared.subs.size) { shared.stop(); mailboxes.delete(key); } } };
}
function createMailbox(project, sid, rid) {
  const subs = new Set();
  const url = `/api/studio/projects/${encodeURIComponent(project)}/chat/sessions/${encodeURIComponent(sid)}/requests/${encodeURIComponent(rid)}/mailbox`;
  let messages = [], cursor = "", timer, loading = false, stopped = false, unsupported = false, stale = false, boot = true;
  const snapshot = () => ({ messages, stale, unsupported });
  const visible = () => !document.hidden && [...subs].some((s) => s.visible?.() !== false);
  async function get(path) {
    const res = await server.request(url + path, { headers: { Accept: "application/json" } });
    if ([404, 405].includes(res.status)) { unsupported = true; return {}; }
    if (!res.ok) throw new Error("Mailbox unavailable");
    return res.json();
  }
  async function load() {
    if (loading || stopped || unsupported || !visible()) return;
    loading = true;
    try {
      if (boot) { messages = mergeMailbox(messages, await get("/pending")); boot = false; }
      let page;
      do {
        page = await get(`/messages?${new URLSearchParams({ after: cursor, limit: 100 })}`);
        if (stopped) return;
        messages = mergeMailbox(messages, page); cursor = page.next_cursor || cursor;
      } while (page.has_more && !unsupported && !stopped);
      stale = false;
    } catch { stale = true; }
    finally {
      loading = false;
      if (!stopped) {
        for (const s of subs) s.listener(snapshot());
        clearTimeout(timer);
        if (!unsupported && visible()) timer = setTimeout(load, messages.some((m) => m.recipient?.kind === "user" && m.state === "pending" && ["question", "permission_request"].includes(m.kind)) || [...subs].some((s) => s.busy?.()) ? 2000 : 10000);
      }
    }
  }
  function kick() { clearTimeout(timer); timer = setTimeout(load, 80); }
  const onVisibility = () => { if (!document.hidden) kick(); };
  document.addEventListener("visibilitychange", onVisibility);
  return { subs, snapshot, kick, stop() { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisibility); } };
}
