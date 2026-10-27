// One lazy tab-wide projection. No timers, streams, or detail subscriptions per project.
import { server } from './server.js';
import { pendingCount, countText } from './inbox.js';
import { activeSession } from './chat_sessions.js';
import { $, routeHash } from '../utils/dom.js';

export function createChatProjects({ request, changed = () => {} }) {
  const cache = new Map(), listeners = new Set();
  let selected = null, running = 0;
  const queue = [];
  function entry(p) {
    if (!cache.has(p)) cache.set(p, { project: p, sessions: null, activity: null, stale: true, error: null, version: 0, pending: null });
    return cache.get(p);
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
    if (e.pending) { e.again ||= invalidate || (full && !e.full); return e.pending; }
    e.full = full;
    e.pending = new Promise((resolve) => {
      queue.push(async () => {
        const version = e.version;
        try {
          if (cache.get(p) !== e) return;
          const res = await request(`/api/studio/projects/${encodeURIComponent(p)}/chat/sessions${full ? '' : '?view=activity'}`);
          if (!res.ok) throw new Error('Chat activity unavailable');
          const body = await res.json();
          if (cache.get(p) !== e) return;
          if (version !== e.version) { e.again = true; return; }
          if (full) {
            if (!Array.isArray(body.sessions)) throw new Error('Sessions unavailable');
            e.sessions = body.sessions;
            e.activity = body.sessions.every((s) => Number.isInteger(s.active_requests) && s.active_requests >= 0 && Number.isInteger(s.pending_user) && s.pending_user >= 0)
              ? { active_sessions: body.sessions.filter(activeSession).length, needs_user_sessions: body.sessions.filter((s) => s.pending_user > 0).length } : null;
          } else {
            const a = body.activity;
            if (!a || ![a.active_sessions, a.needs_user_sessions].every((n) => Number.isInteger(n) && n >= 0)) throw new Error('Chat activity unavailable');
            e.activity = a;
          }
          e.stale = false; e.error = null;
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
      for (const p of ids) if (!cache.has(p)) load(p, p === selected);
    },
    invalidate(p) { if (cache.has(p)) return load(p, p === selected, true); },
    refresh() { for (const p of cache.keys()) load(p, p === selected, true); },
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
    picker.redecorate();
  };
  const unsubscribe = chatProjects.subscribe(publish);
  function paint() {
    const count = pendingCount(chatProjects.snapshot(project)?.sessions);
    const link = $('#chat-inbox'), badge = $('[data-inbox-count]'), label = count > 0 ? `Chat · ${countText(count)}` : 'Chat';
    if (link) { link.hidden = !enabled || !project; link.href = routeHash('chat', project, ctx.state.selectedTarget); link.textContent = label; }
    if (badge) { badge.hidden = !enabled || !(count > 0); badge.textContent = count > 0 ? count : ''; badge.setAttribute('aria-label', label); badge.parentElement.title = label; }
  }
  picker.decorate((item) => {
    if (!enabled) return item;
    const ids = ctx.projects().map((p) => p.id), entries = (item.value === 'all' ? ids : [item.value]).map((p) => chatProjects.snapshot(p));
    const known = entries.every((e) => e?.activity);
    const a = entries.reduce((n, e) => n + (e?.activity?.active_sessions || 0), 0), needs = entries.reduce((n, e) => n + (e?.activity?.needs_user_sessions || 0), 0);
    const watched = followed().slice(0, 20);
    const stale = entries.some((e, i) => e?.stale || !['live', 'polling'].includes(ctx.state.liveStatus?.state) || !watched.includes(item.value === 'all' ? ids[i] : item.value));
    const text = known ? `${a} active chat${a === 1 ? '' : 's'}${needs ? ` · ${needs} need you` : ''}${stale ? ' · stale' : ''}` : item.value === 'all' ? 'Chat activity incomplete' : entries[0]?.error || entries[0]?.sessions ? 'Chat activity unavailable' : 'Chat activity loading…';
    return { ...item, badge: [item.badge, text].filter(Boolean).join(' · ') };
  });
  const events = ({ detail }) => { if (enabled && detail?.events?.some((e) => String(e.plan || '').startsWith('chat-'))) chatProjects.invalidate(detail.project); };
  const refresh = () => { if (enabled && !document.hidden) chatProjects.refresh(); };
  const replied = () => { if (enabled) chatProjects.invalidate(project); };
  window.addEventListener('alfrd-events', events);
  window.addEventListener('alfrd-mail-replied', replied);
  document.addEventListener('visibilitychange', refresh);
  const opened = () => { if (enabled) chatProjects.refresh(); };
  document.querySelector('#pick-project')?.addEventListener('click', opened);
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
      paint(); picker.redecorate();
    },
    invalidate: (p) => enabled && chatProjects.invalidate(p),
    stop() { enabled = false; chatProjects.stop(); unsubscribe(); window.removeEventListener('alfrd-events', events); window.removeEventListener('alfrd-mail-replied', replied); document.removeEventListener('visibilitychange', refresh); document.querySelector('#pick-project')?.removeEventListener('click', opened); picker.decorate(null); paint(); },
  };
}
