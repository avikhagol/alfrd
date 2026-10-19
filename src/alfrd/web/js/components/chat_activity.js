import { $, esc, icon, storage } from "../utils/dom.js";
import { subscribeActivity } from "../data/chat_live.js";
import { activityKey, activityLabel, activityRows, duration, money, requestProgress, statusInfo } from "../data/chat.js";

// A request card with stable attempt rows. Output is recorded public activity.
export function mountActivity(box, o) {
  let subscription, context = "", snapshot = { items: [] }, run, header, body, notice, paused = false;
  const rows = new Map();
  const state = () => o.state?.() || {};
  const rid = () => o.request?.();
  const pref = (kind, id) => `ui:chat.${kind}:${JSON.stringify([o.project(), o.sid(), id])}`;
  const visible = () => box.isConnected && !box.closest("[hidden]") && !box.closest("details:not([open])");
  function draw() {
    const req = state().requests?.find((r) => r.id === rid()), p = requestProgress(state(), req);
    const active = ["planning", "running", "summarizing", "needs_decision"].includes(req?.status);
    if (!run) {
      box.hidden = false;
      box.innerHTML = `<details class="chat-run"><summary></summary><p class="muted small" role="status"></p><div class="chat-run-rows"></div><section data-team-mailbox></section><button type="button" class="btn sm link" data-run-graph>View graph</button></details>`;
      run = $("details", box); header = $("summary", run); body = $("div", run); notice = $("p", run);
      $("[data-run-graph]", run).onclick = () => o.viewGraph?.(rid());
      run.open = storage.get(pref("run", rid()), active); if (run.open) run.dataset.auto = "1";
      run.addEventListener("toggle", () => { if (!run.dataset.auto) storage.set(pref("run", rid()), run.open); delete run.dataset.auto; subscription?.kick(); });
    }
    const selection = window.getSelection?.();
    const protectedContent = run.contains(document.activeElement) || (String(selection || "") && run.contains(selection?.anchorNode));
    if (run.dataset.active === "1" && !active && !protectedContent) { run.dataset.auto = "1"; run.open = false; }
    run.dataset.active = active ? "1" : "0";
    const label = active ? req.status === "planning" ? "Planning the tasks" : req.status === "needs_decision" ? "Needs your answer" : "Working" : req?.status === "done" ? "Run completed" : statusInfo(req?.status).label;
    header.textContent = [label, p ? `${p.done}/${p.total} tasks complete` : "", p ? duration(p.seconds) : "", money(req?.usage?.total_cost_usd)].filter(Boolean).join(" · ");
    notice.textContent = snapshot.unsupported ? "Live output unavailable on this server. Open the full transcript for details." : snapshot.stale ? "Updates paused — reconnecting" : "";
    const data = activityRows(state(), snapshot.items, rid()), keys = new Set(data.map(activityKey));
    for (const [key, row] of rows) if (!keys.has(key)) { row.el.remove(); rows.delete(key); }
    for (const r of data) {
      const key = activityKey(r);
      let row = rows.get(key);
      if (!row) {
        const el = document.createElement("article"); el.className = "chat-run-row";
        el.innerHTML = `<div class="row gap wrap"><button type="button" class="btn sm link grow chat-task-select"></button><span class="small" data-status></span><span class="small tabular" data-cost></span></div>
          <details class="chat-output"><summary>Show output</summary><div class="row gap wrap small"><label>Tail <select class="input sm" aria-label="Output lines"><option>40</option><option>100</option><option>200</option><option>400</option></select> lines</label><button type="button" class="btn sm link" data-jump hidden>Jump to latest</button><button type="button" class="btn sm link" data-log>Open log</button><button type="button" class="btn sm link danger" data-cancel>Cancel</button></div><pre class="log chat-act-tail" tabindex="0"></pre><p class="muted small" data-tail-note></p><details><summary class="small">Technical details</summary><pre class="log chat-pre" data-raw></pre></details></details>`;
        body.append(el);
        row = { el, details: $(".chat-output", el), pre: $(".chat-act-tail", el), data: r, lines: 40, follow: true };
        rows.set(key, row);
        row.details.open = storage.get(pref("tail", key), false);
        row.details.addEventListener("toggle", () => {
          storage.set(pref("tail", key), row.details.open);
          if (row.details.open) { row.follow = true; row.pre.scrollTop = row.pre.scrollHeight; }
          subscription?.kick();
        });
        row.pre.addEventListener("scroll", () => { row.follow = row.pre.scrollHeight - row.pre.scrollTop - row.pre.clientHeight < 24; $("[data-jump]", el).hidden = row.follow; }, { passive: true });
        $("[data-jump]", el).onclick = () => { row.follow = true; row.pre.scrollTop = row.pre.scrollHeight; $("[data-jump]", el).hidden = true; };
        $("select", el).onchange = (e) => { row.lines = Number(e.target.value); subscription?.kick(); };
        $(".chat-task-select", el).onclick = () => o.select?.(row.data.row);
        $("[data-log]", el).onclick = () => o.openLog(row.data.log);
        $("[data-cancel]", el).onclick = (e) => o.cancel(row.data.row, e.currentTarget);
      }
      row.data = r;
      $(".chat-task-select", row.el).textContent = `${activityLabel(r)} · ${o.agentName(r.owner)}${r.attempt > 1 ? ` · attempt ${r.attempt}` : ""}`;
      const info = statusInfo(r.status), status = `${icon(info.icon)} ${esc(info.label)}${r.seconds != null ? ` · ${duration(Math.round(r.seconds))}` : ""}`;
      const statusEl = $("[data-status]", row.el); if (statusEl.innerHTML !== status) statusEl.innerHTML = status;
      $("[data-cost]", row.el).textContent = money(r.cost_usd);
      $("[data-log]", row.el).hidden = !r.log;
      $("[data-cancel]", row.el).hidden = !["running", "retrying", "waiting", "queued"].includes(r.status);
      const text = Array.isArray(r.display_tail) ? r.display_tail.slice(-row.lines).join("\n") : "No recorded output yet";
      if (row.pre.textContent !== text) {
        const top = row.pre.scrollTop; row.pre.textContent = text || "No recorded output yet";
        row.pre.scrollTop = row.follow ? row.pre.scrollHeight : top;
        $("[data-jump]", row.el).hidden = row.follow;
      }
      row.pre.setAttribute("aria-label", `Recorded output of ${activityLabel(r)}`);
      $("[data-tail-note]", row.el).textContent = r.tail_truncated ? `Showing the last ${row.lines} lines. Open log for more.` : "";
      $("[data-raw]", row.el).textContent = Array.isArray(r.tail) ? r.tail.join("\n") : "";
    }
  }
  const api = {
    kick() {
      const next = JSON.stringify([o.project(), o.sid(), rid()]);
      if (context !== next) { api.stop(); context = next; rows.clear(); run = null; snapshot = { items: [] }; }
      if (!o.project() || !o.sid()) return;
      draw();
      if (paused) {
        run.open = storage.get(pref("run", rid()), run.open);
        for (const [key, row] of rows) row.details.open = storage.get(pref("tail", key), row.details.open);
        paused = false;
      }
      if (!subscription) subscription = subscribeActivity(o.project(), o.sid(), (s) => { if (s.at || s.stale || s.unsupported || !snapshot.at) snapshot = s; draw(); }, {
        visible, busy: o.busy,
        units: () => [...rows.values()].filter((r) => run.open && r.details.open && r.data.unit).map((r) => [r.data.unit, r.lines]),
      });
      else subscription.kick();
    },
    teamMailboxHost() { return $("[data-team-mailbox]", run); },
    reset() { api.stop(); context = ""; api.kick(); },
    stop() { subscription?.stop(); subscription = null; paused = true; },
  };
  return api;
}
