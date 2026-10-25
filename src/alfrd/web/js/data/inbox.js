// Session summaries only: never load every conversation to count mail.
export function pendingCount(sessions) {
  if (!Array.isArray(sessions) || sessions.some((s) => !Number.isInteger(s.pending_user) || s.pending_user < 0)) return null;
  return sessions.reduce((n, s) => n + s.pending_user, 0);
}
export const needsMe = (sessions) => sessions.filter((s) => s.pending_user == null || s.pending_user > 0);
export const countText = (n) => n == null ? "Pending count unavailable" : `${n} pending`;

/** A project-scoped shell poll; old requests cannot overwrite a newly selected project. */
export function createInbox({ request, publish, visible = () => !document.hidden }) {
  let project = null, generation = 0, timer, loading = false, again = false;
  async function load() {
    clearTimeout(timer);
    if (!project || !visible()) return;
    if (loading) { again = true; return; }
    const p = project, gen = generation;
    loading = true; again = false;
    try {
      const res = await request(`/api/studio/projects/${encodeURIComponent(p)}/chat/sessions`);
      if (!res.ok) throw new Error("Inbox unavailable");
      const body = await res.json();
      if (gen === generation) publish(p, body.sessions, pendingCount(body.sessions));
    } catch { if (gen === generation) publish(p, null, null); }
    finally {
      loading = false;
      if (project) timer = setTimeout(load, again || gen !== generation ? 80 : 10000);
    }
  }
  return {
    select(p) { if (p === project) return; project = p; generation++; clearTimeout(timer); publish(p, null, null); if (p) load(); },
    kick() { if (project) { clearTimeout(timer); timer = setTimeout(load, 80); } },
    stop() { project = null; generation++; clearTimeout(timer); },
  };
}
