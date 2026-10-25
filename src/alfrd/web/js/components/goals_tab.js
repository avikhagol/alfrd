import { esc } from "../utils/dom.js";
import { empty, loading, error } from "./state_view.js";

export function goalsHtml(overview, state, request = null) {
  const tasks = (state.tasks || []).filter((t) => t.kind === "task" && (!request || t.request === request));
  const goal = overview.goal || {};
  const requests = (state.requests?.length ? state.requests : goal.text ? [{ id: goal.source?.slice(8) || "", text: goal.text }] : []).filter((r) => !request || r.id === request);
  const requestHtml = requests.map((r) => `<section><h3>Request ${esc(r.id)}</h3><p class="chat-text">${esc(r.text || (goal.source === `request:${r.id}` ? goal.text : "") || "Not specified")}</p></section>`).join("");
  if (!requestHtml && !tasks.length) return empty("No goals yet", "No request or criteria recorded.");
  return requestHtml + tasks.map((t) => {
    const criteria = (overview.criteria || []).filter((c) => c.task_rows?.includes(t.row));
    return `<section><h3>${esc(t.task?.title || t.row)}</h3><p class="small">${esc(t.row)} · Request ${esc(t.request || "Not specified")}</p><p>Task status: ${esc(t.status || "Unknown")}</p>` +
      (criteria.length ? `<ul>${criteria.map((c) => `<li><span class="chat-text">${esc(c.text)}</span> <span class="badge">Criterion: ${esc(["verified", "failed"].includes(c.status) ? c.status : "unverified")}</span></li>`).join("")}</ul>` : "<p>Not specified</p>") + "</section>";
  }).join("");
}

export function createGoalsTab({ get, refresh = () => {}, timeoutMs = 10000 }) {
  let key, box, generation = 0, timer;
  function stop() { generation++; clearTimeout(timer); if (box) box.onclick = null; box = null; key = null; }
  function update({ host, state, project, sid, request = null }) {
    const next = JSON.stringify([project, sid, state?.rev, request]);
    if (host === box && next === key) return;
    stop(); box = host; key = next;
    if (!box) return;
    if (!sid) { box.innerHTML = empty("No goals yet", "Choose or start a session."); return; }
    if (!state) { box.innerHTML = loading("Loading session…"); return; }
    const target = box;
    const load = async () => {
      const own = ++generation;
      clearTimeout(timer);
      target.innerHTML = loading("Loading goals…");
      try {
        const overview = await Promise.race([
          get(`/api/studio/projects/${encodeURIComponent(project)}/chat/sessions/${encodeURIComponent(sid)}/overview`),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Goals timed out. Retry.")), timeoutMs); }),
        ]);
        if (own !== generation) return;
        if (overview.rev !== state.rev) throw new Error("Session changed. Retry.");
        target.innerHTML = goalsHtml(overview, state, request);
      } catch (err) {
        if (own === generation) target.innerHTML = error({ status: err.status ?? 500, message: err.message }, { onRetry: load });
      } finally { if (own === generation) clearTimeout(timer); }
    };
    target.onclick = (event) => { if (event.target.closest?.("[data-state-retry]")) { refresh(); load(); } };
    load();
  }
  return { update, stop };
}
