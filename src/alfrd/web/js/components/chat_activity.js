// Live activity of a chat session (Chat page and dock): one card per running unit — orchestrator
// calls included — with owner, what it is, model, elapsed time and a following tail of its log.
// Polls GET chat/sessions/<sid>/activity every 2 s only while something runs and the page is visible;
// a 404 (server without the endpoint) hides the strip for good. Lazy (with chat_view / chat_dock).
import { $, esc, icon } from "../utils/dom.js";
import { server } from "../data/server.js";
import { activityLabel, duration } from "../data/chat.js";

/** Mount on `box`. o: {project(), sid(), busy() → bool, agentName(key), compact, openLog(rel), cancel(row, button)}.
 *  Returns {kick() (refresh now and poll while busy), stop()}. */
export function mountActivity(box, o) {
  let runs = [], at = 0, timer = 0, tick = 0, off = false, inflight = false, key = "";
  box.hidden = true;
  const visible = () => box.isConnected && !document.hidden && !box.parentElement?.closest("[hidden]");
  async function load() {
    const p = o.project(), sid = o.sid();
    if (off || !p || !sid || inflight) return;
    inflight = true;
    try {
      const res = await server.request(`/api/studio/projects/${encodeURIComponent(p)}/chat/sessions/${encodeURIComponent(sid)}/activity?lines=40`, { headers: { Accept: "application/json" } });
      if (res.status === 404 || res.status === 405) { off = true; runs = []; }
      else if (res.ok) {
        const body = await res.json();
        if (p === o.project() && sid === o.sid()) { runs = Array.isArray(body?.running) ? body.running : []; at = Date.now(); }
      }
    } catch { /* network hiccup: keep the last cards, try again on the next tick */ }
    inflight = false;
    draw();
  }
  function schedule() {
    clearTimeout(timer); timer = 0;
    if (off || !visible() || !(runs.length || o.busy())) return;
    timer = setTimeout(async () => { await load(); schedule(); }, 2000);
  }
  function elapsed() {
    $$("[data-el]").forEach((el) => { el.textContent = duration(Math.round(Number(el.dataset.el) + (Date.now() - at) / 1000)); });
  }
  const $$ = (sel) => [...box.querySelectorAll(sel)];
  function draw() {
    box.hidden = off || !runs.length;
    clearInterval(tick); tick = 0;
    if (box.hidden) { box.innerHTML = ""; key = ""; return; }
    const shape = runs.map((r) => r.row).join("|");
    if (shape !== key) {
      key = shape;
      box.innerHTML = `<h3 class="chat-act-h small">${icon("sync", "spin")} Running now (${runs.length})</h3>` + runs.map((r) => `<article class="chat-act${r.kind && r.kind !== "task" ? " chat-act-orch" : ""}" data-run="${esc(r.row)}">
        <header class="row gap wrap"><b class="small">${esc(o.agentName(r.owner))}</b>${r.kind && r.kind !== "task" ? '<span class="badge tone-muted"><span>Orchestrator</span></span>' : ""}
          <span class="small grow chat-act-what" data-what></span><span class="muted small mono" data-model></span><span class="small tabular" data-el="0"></span>
          ${r.log ? `<button type="button" class="btn sm link" data-act-log="${esc(r.log)}" aria-label="Open the full log of ${esc(r.row)}">${icon("log")} Open log</button>` : ""}
          <button type="button" class="btn sm link danger" data-act-cancel="${esc(r.row)}" aria-label="Cancel ${esc(r.row)}">${icon("stop")} Cancel</button></header>
        ${o.compact ? "" : `<pre class="log chat-act-tail" tabindex="0" data-follow="1" aria-label="Log tail of ${esc(r.row)}"></pre>`}</article>`).join("");
      $$("pre[data-follow]").forEach((pre) => pre.addEventListener("scroll", () => {
        pre.dataset.follow = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24 ? "1" : "0";
      }, { passive: true }));
    }
    for (const r of runs) {
      const card = box.querySelector(`[data-run="${CSS.escape(r.row)}"]`);
      if (!card) continue;
      $("b", card).textContent = o.agentName(r.owner);
      $("[data-what]", card).textContent = `${activityLabel(r)}${r.attempt > 1 ? ` · attempt ${r.attempt}` : ""}`;
      $("[data-model]", card).textContent = r.model || "";
      $("[data-el]", card).dataset.el = String(Number(r.seconds) || 0);
      const pre = $("pre", card), text = Array.isArray(r.tail) ? r.tail.join("\n") : String(r.tail || "");
      if (pre && pre.textContent !== text) {
        pre.textContent = text || "(no output yet)";
        if (pre.dataset.follow !== "0") pre.scrollTop = pre.scrollHeight;
      }
    }
    elapsed();
    tick = setInterval(() => { if (box.isConnected) elapsed(); else clearInterval(tick); }, 1000);
  }
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b?.dataset.actLog) o.openLog(b.dataset.actLog);
    else if (b?.dataset.actCancel) o.cancel(b.dataset.actCancel, b);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && box.isConnected) api.kick(); });
  const api = {
    kick() { if (off || !visible()) return; load().then(schedule); },
    reset() { runs = []; key = ""; draw(); api.kick(); },
    stop() { clearTimeout(timer); clearInterval(tick); timer = tick = 0; },
  };
  return api;
}
