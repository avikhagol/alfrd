import { esc, routeHash, setRoute } from "../utils/dom.js";
import { refreshHTML } from "../utils/refresh.js";
import { server } from "../data/server.js";
import { homeSections, costText } from "../data/harness.js";

export function renderHome(el, ctx, project, { onStatus = () => {} } = {}) {
  const base = `/studio/projects/${encodeURIComponent(project)}/chat`;
  let stopped = false, timer, controller, generation = 0, failures = 0;
  el.innerHTML = `<section class="h-home" aria-label="Harness dashboard"><h1>What should ALFRD do?</h1><form data-h-goal><label class="sr-only" for="h-goal">Describe the work</label><textarea id="h-goal" class="input" rows="2" placeholder="Describe a goal…" required></textarea><button class="btn primary" type="submit">Send</button><p data-h-error role="alert"></p></form><div data-h-sessions></div></section>`;
  const box = el.querySelector("[data-h-sessions]"), input = el.querySelector("textarea");
  const open = (sid, attention = false) => { ctx.state.chatSession = sid; setRoute(`${routeHash(`harness/${encodeURIComponent(sid)}`, project, ctx.state.selectedTarget)}${attention ? "&attention=1" : ""}`); ctx.update(); };
  const click = (e) => { const b = e.target.closest("[data-h-open], [data-h-example]"); if (b?.dataset.hOpen) open(b.dataset.hOpen, b.dataset.hNeeds === "true"); else if (b?.dataset.hExample) { input.value = b.dataset.hExample; input.focus(); } };
  const submit = async (e) => {
    e.preventDefault(); const text = input.value.trim(); if (!text) return;
    const button = el.querySelector("[type=submit]"); button.disabled = true;
    try { const { session } = await server.mutate(`${base}/sessions`, {}); await server.mutate(`${base}/sessions/${encodeURIComponent(session.id)}/messages`, { text }); if (!stopped) open(session.id); }
    catch (error) { el.querySelector("[data-h-error]").textContent = error.message; button.disabled = false; }
  };
  async function load() {
    if (stopped || document.hidden) { if (!stopped) timer = setTimeout(load, 10000); return; }
    controller?.abort(); controller = new AbortController(); const gen = ++generation;
    const get = async (path) => { const r = await server.request(`/api${base}${path}`, { signal: controller.signal }); const body = await r.json(); if (!r.ok) throw Error(body?.error?.message || `HTTP ${r.status}`); return body; };
    try {
      const { sessions = [] } = await get("/sessions"); if (stopped || gen !== generation) return;
      failures = 0; onStatus(`Connected · updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
      const groups = homeSections(sessions);
      refreshHTML(box, sessions.length ? Object.entries(groups).filter(([, rows]) => rows.length).map(([group, rows]) => `<section><h2>${({ needs: "Needs you", running: "Running", recent: "Recent" })[group]} <small>${rows.length}</small></h2><ul>${rows.map((s) => `<li><div><b>${esc(s.title || "Untitled conversation")}</b><p>${s.pending_user > 0 ? `⚑ ${s.pending_user} ${s.pending_user === 1 ? "needs" : "need"} you · ` : s.active_requests > 0 ? "◐ Running · " : ""}<time>${esc(new Date(s.last_message?.at || s.created).toLocaleString())}</time>${group !== "recent" ? `<span data-h-cost="${esc(s.id)}"> · Session cost: loading…</span>` : ""}</p></div><button class="btn sm" data-h-open="${esc(s.id)}" data-h-needs="${group === "needs"}">Open</button></li>`).join("")}</ul></section>`).join("") : `<div class="h-examples">${["Build a small game", "Review this project", "Plan my next feature"].map((s) => `<button class="btn" data-h-example="${s}">${s}</button>`).join("")}</div>`);
      const queue = [...groups.needs, ...groups.running].slice(0, 6);
      await Promise.all([0, 1].map(async () => { while (queue.length && !stopped && gen === generation) { const s = queue.shift(); try { const data = await get(`/sessions/${encodeURIComponent(s.id)}/harness/state?scope=all&limit=1`); const target = box.querySelector(`[data-h-cost="${CSS.escape(s.id)}"]`); if (target && !stopped && gen === generation) target.textContent = ` · Session cost: ${costText(data.summary?.usage, null, data.coverage?.warnings).text}`; } catch { const target = box.querySelector(`[data-h-cost="${CSS.escape(s.id)}"]`); if (target) target.textContent = " · Session cost: unavailable"; } } }));
    } catch (e) { if (!stopped && e.name !== "AbortError") { failures++; onStatus(failures >= 3 ? "Offline · retrying" : "Reconnecting…"); el.querySelector("[data-h-error]").textContent = e.message; } }
    if (!stopped) timer = setTimeout(load, 10000);
  }
  el.addEventListener("click", click); el.querySelector("form").addEventListener("submit", submit); load(); input.focus();
  return { destroy() { stopped = true; clearTimeout(timer); controller?.abort(); el.removeEventListener("click", click); el.querySelector("form")?.removeEventListener("submit", submit); } };
}
