// Memory-only bounded stores; representation is part of every snapshot key.
export function createLRU(limit, dispose = () => {}) {
  const entries = new Map();
  return { entries,
    get(key) { const v = entries.get(key); if (v) { entries.delete(key); entries.set(key, v); } return v; },
    set(key, v) { entries.delete(key); entries.set(key, v); while (entries.size > limit) this.delete(entries.keys().next().value); return v; },
    delete(key) { const v = entries.get(key); entries.delete(key); if (v) dispose(v); },
    clear() { for (const key of [...entries.keys()]) this.delete(key); },
  };
}
export function createChatState({ request, now = Date.now, limit = 20, maxBytes = 10 * 1024 * 1024 }) {
  const lru = createLRU(limit, (e) => e.controller?.abort()), cache = lru.entries;
  const key = (p, sid, representation = "") => JSON.stringify([p, sid, representation]);
  return {
    cache, key, clear: () => lru.clear(),
    peek(p, sid, representation = "") { return lru.get(key(p, sid, representation)); },
    invalidate(p, sid, { reset = false } = {}) { for (const e of cache.values()) if (e.project === p && (!sid || e.sid === sid)) { e.epoch++; if (reset) { e.reset = true; e.fullAt = -Infinity; e.controller?.abort(); e.pending = null; } } },
    remove(p, sid) { for (const [k, e] of cache) if (e.project === p && e.sid === sid) lru.delete(k); },
    cancel(p, sid, representation = "") { const e = cache.get(key(p, sid, representation)); if (e?.pending) { e.controller.abort(); e.pending = null; } },
    catalog(ids) { for (const [k, e] of cache) if (!ids.includes(e.project)) lru.delete(k); },
    async load(p, sid, representation = "", { force = false } = {}) {
      const k = key(p, sid, representation);
      const e = lru.get(k) || lru.set(k, { project: p, sid, data: null, bytes: 0, epoch: 0, accepted: -1, at: 0, fullAt: 0 });
      if (e.pending) return e.pending;
      const ttl = e.data?.requests?.some((r) => !r.finished) ? 5000 : 30000;
      if (!force && e.data && e.accepted === e.epoch && now() - e.at < ttl && now() - e.fullAt < 60000) return e.data;
      const controller = e.controller = new AbortController(), epoch = e.epoch;
      const query = new URLSearchParams(representation);
      if (!force && e.data?.rev != null && now() - e.fullAt < 60000) query.set("since_rev", e.data.rev);
      e.pending = (async () => {
        try {
          const res = await request(`/api/studio/projects/${encodeURIComponent(p)}/chat/sessions/${encodeURIComponent(sid)}${query.size ? `?${query}` : ""}`, { signal: controller.signal, headers: { Accept: "application/json" } });
          const data = await res.json();
          if (controller.signal.aborted || cache.get(k) !== e) return null;
          if (!res.ok) { if ([401, 403, 404].includes(res.status)) { e.data = null; e.bytes = 0; } const error = new Error(data?.error?.message || `Snapshot unavailable (${res.status})`); error.status = res.status; throw error; }
          if (!data.unchanged && !( !e.reset && e.data?.rev != null && data.rev != null && data.rev < e.data.rev)) {
            e.data = data; e.bytes = JSON.stringify(data).length * 2; e.fullAt = now(); e.reset = false;
            let bytes = [...cache.values()].reduce((n, row) => n + row.bytes, 0);
            for (const [oldKey, row] of cache) { if (bytes <= maxBytes) break; bytes -= row.bytes; lru.delete(oldKey); }
          }
          e.accepted = epoch; e.at = now(); e.error = null; return e.data;
        } catch (error) { if (controller.signal.aborted) return null; e.error = error; throw error; }
        finally { if (e.controller === controller) e.pending = null; }
      })();
      const result = await e.pending;
      if (result && e.epoch !== epoch && cache.get(k) === e && !controller.signal.aborted) return this.load(p, sid, representation);
      return result;
    },
  };
}
