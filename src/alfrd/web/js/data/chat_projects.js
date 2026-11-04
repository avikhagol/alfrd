// One lazy tab-wide projection. No timers, streams, or detail subscriptions per project.
import { server } from './server.js';
import { pendingCount, countText } from './inbox.js';
import { $ } from '../utils/dom.js';

export function createChatProjects({ request, changed = () => {}, now = Date.now, freshMs = 30000, maxProjects = 2 }) {
  const cache = new Map(), listeners = new Set();
  let selected = null, running = 0;
  const queue = [];
  function entry(p) {
    if (!cache.has(p)) cache.set(p, { project: p, sessions: null, stale: true, error: null, version: 0, fetchedAt: null, pending: null });
    const value = cache.get(p); cache.delete(p); cache.set(p, value);
    while (cache.size > maxProjects) cache.delete(cache.keys().next().value);
    return value;
  }
  function emit(e) { for (const fn of listeners) fn(e); changed(); }
  function pump() {
    while (running < 4 && queue.length) {
      const job = queue.shift(); running++;
      job().finally(() => { running--; pump(); });
    }
  }
  function load(p, full = p === selected, invalidate = false) {
    if (!p) return Promise.resolve(null);
    const e = entry(p);
    if (invalidate) { e.version++; e.stale = true; }
    if (p !== selected) return Promise.resolve(null);
    if (e.pending) { e.again ||= invalidate || (full && !e.full); return e.pending; }
    if (!e.stale && e.fetchedAt !== null && now() - e.fetchedAt < freshMs) return Promise.resolve(e);
    e.full = full;
    e.pending = new Promise((resolve) => {
      queue.push(async () => {
        const version = e.version;
        try {
          if (cache.get(p) !== e) return;
          const res = await request(`/api/studio/projects/${encodeURIComponent(p)}/chat/sessions`);
          if (!res.ok) throw new Error('Sessions unavailable');
          const body = await res.json();
          if (cache.get(p) !== e) return;
          if (version !== e.version) { e.again = true; return; }
          if (!Array.isArray(body.sessions)) throw new Error('Sessions unavailable');
          e.sessions = body.sessions;
          e.stale = false; e.error = null; e.fetchedAt = now();
        } catch (error) { if (cache.get(p) === e) { e.error = error.message; e.stale = true; } }
        finally {
          e.pending = null;
          if (cache.get(p) === e) { emit(e); if (e.again) { e.again = false; load(p, p === selected); } }
          resolve(e);
        }
      }); pump();
    });
    return e.pending;
  }
  return {
    cache,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    snapshot: (p) => cache.get(p),
    load,
    select(p) { if (p === selected) return; selected = p; if (p) load(p, true); },
    catalog(ids) {
      for (const p of cache.keys()) if (!ids.includes(p)) cache.delete(p);
    },
    invalidate(p) { if (cache.has(p)) return load(p, p === selected, true); },
    refresh() { if (selected) load(selected, true, true); },
    stop() { selected = null; cache.clear(); },
  };
}

export const chatProjects = createChatProjects({ request: (url) => server.request(url) });

export function mountChatProjects(ctx, picker, followed = () => ctx.projects().map((p) => p.id).sort()) {
  let enabled = false, identity, project = null;
  const publish = (e) => {
    if (e.project === project) {
      window.dispatchEvent(new CustomEvent('alfrd-inbox', { detail: { project, sessions: e.sessions, error: e.error } }));
      paint();
    }
  };
  const unsubscribe = chatProjects.subscribe(publish);
  function paint() {
    const count = pendingCount(chatProjects.snapshot(project)?.sessions);
    const badge = $('[data-inbox-count]'), label = count > 0 ? `Chat · ${countText(count)}` : 'Chat';
    if (badge) { badge.hidden = !enabled || !(count > 0); badge.textContent = count > 0 ? count : ''; badge.setAttribute('aria-label', label); badge.parentElement.title = label; }
  }
  const events = ({ detail }) => { if (enabled && detail?.events?.some((e) => String(e.plan || '').startsWith('chat-'))) chatProjects.invalidate(detail.project); };
  const refresh = () => { if (enabled && !document.hidden) chatProjects.refresh(); };
  const replied = () => { if (enabled) chatProjects.invalidate(project); };
  window.addEventListener('alfrd-events', events);
  window.addEventListener('alfrd-mail-replied', replied);
  document.addEventListener('visibilitychange', refresh);
  let liveState;
  return {
    sync() {
      const on = ctx.chatOn() && ctx.state.mode === 'server', key = JSON.stringify([location.origin, server.session?.csrf_token, server.session?.projects]);
      if (identity !== key || !on) chatProjects.stop();
      identity = key; enabled = on; project = on ? ctx.activeProject() : null;
      if (on) {
        chatProjects.select(project); chatProjects.catalog(ctx.projects().map((p) => p.id));
        const next = ctx.state.liveStatus?.state;
        if (liveState !== undefined && next !== liveState && ['live', 'polling'].includes(next)) chatProjects.refresh();
        liveState = next;
      }
      paint();
    },
    invalidate: (p) => enabled && chatProjects.invalidate(p),
    stop() { enabled = false; chatProjects.stop(); unsubscribe(); window.removeEventListener('alfrd-events', events); window.removeEventListener('alfrd-mail-replied', replied); document.removeEventListener('visibilitychange', refresh); paint(); },
  };
}
