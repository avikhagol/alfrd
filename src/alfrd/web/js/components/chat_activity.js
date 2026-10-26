import { taskIslands, islandKey, islandProgress, isTask, outputUnits, ownerSlot } from "../data/chat_islands.js";
import { statusIcon } from "./status_icons.js";
import { quietCounts, usageUnits, usageCountsHtml } from "./usage_counts.js";
import { currentSteps } from "../data/chat_steps.js";
import { stepsHtml } from "./chat_steps.js";
import { $, esc, icon, storage } from "../utils/dom.js";
import { subscribeActivity } from "../data/chat_live.js";
import { activityKey, activityLabel, activityRows, costWarnings, duration, money, requestProgress, statusInfo } from "../data/chat.js";

const setText = (el, value) => { if (el.textContent !== String(value)) el.textContent = String(value); };

const PURIFY = { USE_PROFILES: { html: true }, FORBID_TAGS: ["style"], FORBID_ATTR: ["style", "id", "name"] };
const purify = await import("../vendor/purify.es.mjs").then((m) => m.default, () => null);

/** DOMPurify-clean HTML for text-bearing fragments. Without a usable DOMPurify the markup is shown as text. */
export function sanitizeHtml(html, p = purify) {
  if (p?.isSupported) return p.sanitize(String(html), PURIFY);
  return esc(String(html));
}

// One request's activity line and its single Details disclosure: the line says where the run is;
// Details holds the attempt rows with their recorded output, team messages and raw protocol.
// Pending decisions sit above Details (see decisionsHost), so they never need it opened.
// Output is recorded public activity.
export function mountActivity(box, o) {
  let subscription, context = "", snapshot = { items: [] }, run, header, body, notice, usage, paused = false, clock;
  const rows = new Map(), islands = new Map();
  const state = () => o.state?.() || {};
  const rid = () => o.request?.();
  const pref = (kind, id) => `ui:chat.${kind}:${JSON.stringify([o.project(), o.sid(), id])}`;
  const visible = () => box.isConnected && !box.closest("[hidden]");
  function draw() {
    const req = state().requests?.find((r) => r.id === rid()), p = requestProgress(state(), req);
    const tasks = taskIslands(state(), snapshot.items, rid()), progress = islandProgress(tasks);
    const active = ["planning", "running", "summarizing", "needs_decision"].includes(req?.status);
    if (!run) {
      box.hidden = false;
      box.innerHTML = `<button type="button" class="btn sm link chat-run-head" aria-controls="chat-inspector" title="Open run inspector"></button><span class="small warn-t" role="status" data-run-warning></span><span class="small" data-current-steps></span><section class="chat-decisions" data-decisions></section><details data-usage><summary>Usage</summary><button type="button" class="btn sm link" data-usage-refresh>Refresh usage</button><div data-usage-counts></div></details><details class="chat-details"><summary>Details</summary><p class="muted small" data-notice role="status"></p><div data-task-progress></div><div class="chat-run-rows"><div class="chat-islands"></div><section class="chat-orchestrator-steps" aria-label="Orchestrator steps"></section></div><section data-team-mailbox></section><section data-raw-protocol></section><button type="button" class="btn sm link" data-run-graph>View graph</button></details>`;
      run = $(".chat-details", box); usage = $("[data-usage]", box);
      usage.open = storage.get(pref("usage", rid()), false);
      usage.addEventListener("toggle", () => { storage.set(pref("usage", rid()), usage.open); draw(); subscription?.kick(); });
      $("[data-usage-refresh]", box).onclick = () => subscription?.kick();
      header = $(".chat-run-head", box); body = $(".chat-run-rows", run); notice = $("[data-notice]", run);
      header.onclick = () => o.inspect ? o.inspect(rid()) : o.viewGraph?.(rid());
      $("[data-run-graph]", run).onclick = () => o.viewGraph?.(rid());
      run.open = storage.get(pref("run", rid()), active);
      run.addEventListener("toggle", () => { storage.set(pref("run", rid()), run.open); subscription?.kick(); });
    }
    const label = active ? req.status === "planning" ? "Planning the tasks" : req.status === "needs_decision" ? "Needs your answer" : "Working" : req?.status === "done" ? "Run completed" : statusInfo(req?.status).label;
    const started = Number(req?.created_ts) ? req.created_ts * 1000 : Date.parse(req?.created);
    const ended = req?.finished ? Date.parse(req.finished) : Date.now();
    header.textContent = [req?.status ? label : "", p && Array.isArray(state().tasks) ? (progress.total ? `${progress.done}/${progress.total} tasks complete` : "Planning tasks…") : "",
      Number.isFinite(started) && Number.isFinite(ended) ? duration(Math.max(0, Math.round((ended - started) / 1000))) : "",
      (req?.usage?.total_cost_usd == null ? "Cost — / Not reported" : money(req.usage.total_cost_usd)), quietCounts(snapshot.telemetry?.requests?.[rid()] || req?.telemetry)].filter((s) => s && s !== "—").join(" · ");
    const warned = costWarnings(state(), rid());
    $("[data-run-warning]", box).textContent = [p?.costRatio >= 0.8 ? `${p.costRatio >= 1 ? "Cost cap reached" : "Approaching cost cap"}: ${money(req?.usage?.total_cost_usd)} / ${money(p.maxCost)}` : "",
      p?.timeRatio >= 0.8 ? `${p.timeRatio >= 1 ? "Time limit reached" : "Approaching time limit"}: ${duration(p.seconds)} / ${duration(p.maxSeconds)}` : "",
      warned.length ? `${warned.length} task${warned.length === 1 ? "" : "s"} over cost warning` : ""].filter(Boolean).join(" · ");
    if (usage.open) {
      const host = $("[data-usage-counts]", box), html = usageCountsHtml(req, snapshot.telemetry?.requests?.[rid()], snapshot.items, usageUnits(state(), rid()), snapshot);
      if (host.innerHTML !== html) host.innerHTML = html;
    }
    const stepBox = $("[data-current-steps]", box), stepHtml = stepsHtml(active ? currentSteps(req) : [], rid(), state().blockers);
    if (stepBox.innerHTML !== stepHtml) stepBox.innerHTML = stepHtml;
    notice.textContent = snapshot.unsupported ? "Live output unavailable on this server. Open the full transcript for details." : snapshot.stale ? "Updates paused — reconnecting" : "";
    const progressHost = $("[data-task-progress]", run);
    const progressHtml = progress.total ? `<label>${progress.done} of ${progress.total} tasks complete <progress max="${progress.total}" value="${progress.done}"></progress></label>${["failed", "blocked", "cancelled"].filter((k) => progress[k]).map((k) => `<span>${progress[k]} ${k}</span>`).join(" · ")}` : "Planning tasks…";
    if (progressHost.innerHTML !== progressHtml) progressHost.innerHTML = progressHtml;
    const taskKeys = new Set(tasks.map(islandKey));
    for (const [key, island] of islands) if (!taskKeys.has(key)) { island.el.remove(); islands.delete(key); }
    for (const task of tasks) {
      const key = islandKey(task);
      let island = islands.get(key);
      if (!island) {
        const el = document.createElement("article"); el.className = "chat-task-island";
        el.innerHTML = `<header><span class="chat-island-avatar" aria-hidden="true"></span><div><button type="button" class="btn sm link" data-task-title></button><div data-owner></div></div><span data-task-status></span></header><div class="chat-island-step"></div><p data-dependencies></p><p class="chat-island-metrics" aria-live="off"></p><p data-task-error></p><div class="chat-island-attempts"></div>`;
        $(".chat-islands", body).append(el);
        island = { el }; islands.set(key, island);
        $("[data-task-title]", el).onclick = () => o.select?.(island.task.row);
      }
      island.task = task;
      const el = island.el, owner = task.owner || "Unknown owner";
      setText($("[data-task-title]", el), task.task?.title || task.title || task.row);
      $(".chat-island-avatar", el).dataset.ownerSlot = ownerSlot(owner);
      setText($(".chat-island-avatar", el), owner.replace(/^@/, "").slice(0, 2).toUpperCase());
      setText($("[data-owner]", el), [o.agentName?.(owner) || owner, `@${owner}`, task.role || task.task?.role, task.model || task.attempts.at(-1)?.model].filter(Boolean).join(" · "));
      const status = statusIcon(task.status, statusInfo(task.status).label), statusEl = $("[data-task-status]", el);
      if (statusEl.innerHTML !== status) statusEl.innerHTML = status;
      const steps = stepsHtml(currentSteps(req, task.row), rid(), state().blockers) || "Step — / Not reported", stepEl = $(".chat-island-step", el);
      if (stepEl.innerHTML !== steps) stepEl.innerHTML = steps;
      const deps = Array.isArray(task.dependencies) ? task.dependencies : [task.dependencies];
      setText($("[data-dependencies]", el), deps.length ? `Depends on: ${deps.join(", ")}` : "No dependencies");
      setText($(".chat-island-metrics", el), `Elapsed ${task.metrics.seconds == null ? "— / Not reported" : duration(Math.round(task.metrics.seconds))} · Cost ${task.metrics.cost == null ? "— / Not reported" : money(task.metrics.cost)}`);
      setText($("[data-task-error]", el), ["failed", "blocked"].includes(task.status) ? String(task.error || task.result?.summary || statusInfo(task.status).label) : "");
    }
    const data = activityRows(state(), snapshot.items, rid()), keys = new Set(data.map(activityKey));
    for (const [key, row] of rows) if (!keys.has(key)) { row.el.remove(); rows.delete(key); }
    for (const r of data) {
      const key = activityKey(r);
      let row = rows.get(key);
      if (!row) {
        const el = document.createElement("article"); el.className = "chat-run-row";
        el.innerHTML = `<div class="row gap wrap"><button type="button" class="btn sm link grow chat-task-select"></button><span class="small" data-status></span><span class="small tabular" data-cost></span></div>
          <details class="chat-output"><summary>Recorded output</summary><div class="row gap wrap small"><label>Tail <select class="input sm" aria-label="Output lines"><option>40</option><option>100</option><option>200</option><option>400</option></select> lines</label><button type="button" class="btn sm link" data-jump hidden>Jump to latest</button><button type="button" class="btn sm link" data-log>Open log</button><button type="button" class="btn sm link danger" data-cancel>Cancel</button></div><pre class="log chat-act-tail" tabindex="0"></pre><p class="muted small" data-tail-note></p><details class="chat-raw"><summary>Raw protocol</summary><pre class="log chat-pre" data-raw></pre></details></details>`;
        body.append(el); // Stage new attempt nodes before assigning their stable task host.
        row = { el, pre: $(".chat-act-tail", el), data: r, output: $(".chat-output", el), lines: 40, follow: true };
        rows.set(key, row);
        row.output.open = storage.get(pref("output", key), false);
        const raw = $(".chat-raw", el);
        raw.open = storage.get(pref("raw", key), false);
        raw.addEventListener("toggle", () => storage.set(pref("raw", key), raw.open));
        row.output.addEventListener("toggle", () => { storage.set(pref("output", key), row.output.open); subscription?.kick(); });
        row.pre.addEventListener("scroll", () => { row.follow = row.pre.scrollHeight - row.pre.scrollTop - row.pre.clientHeight < 24; $("[data-jump]", el).hidden = row.follow; }, { passive: true });
        $("[data-jump]", el).onclick = () => { row.follow = true; row.pre.textContent = row.data.display_tail?.slice(-row.lines).join("\n") || "No recorded output yet"; row.pre.scrollTop = row.pre.scrollHeight; row.pre.focus?.({ preventScroll: true }); $("[data-jump]", el).hidden = true; };
        $("select", el).onchange = (e) => { row.lines = Number(e.target.value); subscription?.kick(); };
        $(".chat-task-select", el).onclick = () => o.select?.(row.data.row);
        $("[data-log]", el).onclick = () => {
          const { unit, attempt } = row.data, rows = activityRows(state(), snapshot.items, rid());
          o.openLog(row.data.log, { unit, attempt, latest: !rows.some((x) => x.row === row.data.row && x.request === row.data.request && (x.attempt || 0) > (attempt || 0)) });
        };
        $("[data-cancel]", el).onclick = (e) => o.cancel(row.data.row, e.currentTarget);
      }
      row.data = r;
      const parent = isTask(r) ? $(".chat-island-attempts", islands.get(islandKey(r)).el) : $(".chat-orchestrator-steps", body);
      if (row.el.parentElement !== parent) parent.append(row.el);
      setText($(".chat-task-select", row.el), `${isTask(r) ? activityLabel(r) + " · Attempt " + (r.attempt || 1) : "Orchestrator · " + activityLabel(r)}${r.unit ? " · " + r.unit : ""}`);
      const info = statusInfo(r.status), status = `${icon(info.icon)} ${esc(info.label)}${r.seconds != null ? ` · ${duration(Math.round(r.seconds))}` : ""}`;
      const statusEl = $("[data-status]", row.el); if (statusEl.dataset.html !== status) { statusEl.dataset.html = status; statusEl.innerHTML = sanitizeHtml(status); }
      setText($("[data-cost]", row.el), r.cost_usd == null ? "— / Not reported" : money(r.cost_usd));
      $("[data-log]", row.el).hidden = !r.log;
      $("[data-cancel]", row.el).hidden = !["running", "retrying", "waiting", "queued"].includes(r.status);
      const text = Array.isArray(r.display_tail) ? r.display_tail.slice(-row.lines).join("\n") : "No recorded output yet";
      const selection = window.getSelection?.();
      const protectedTail = row.el.contains(document.activeElement) || (String(selection || "") && row.el.contains(selection?.anchorNode));
      if (row.pre.textContent !== text && !protectedTail) {
        const top = row.pre.scrollTop; row.pre.textContent = text || "No recorded output yet";
        row.pre.scrollTop = row.follow ? row.pre.scrollHeight : top;
        $("[data-jump]", row.el).hidden = row.follow;
      } else if (row.pre.textContent !== text) $("[data-jump]", row.el).hidden = false;
      row.pre.setAttribute("aria-label", `Recorded output of ${activityLabel(r)}`);
      setText($("[data-tail-note]", row.el), r.tail_truncated ? `Showing the last ${row.lines} lines. Open log for more.` : "");
      if (!protectedTail) setText($("[data-raw]", row.el), Array.isArray(r.tail) ? r.tail.join("\n") : "");
    }
  }
  const api = {
    kick() {
      const next = JSON.stringify([o.project(), o.sid(), rid()]);
      if (context !== next) { api.stop(); context = next; rows.clear(); islands.clear(); run = null; snapshot = { items: [] }; }
      if (!o.project() || !o.sid()) return;
      draw();
      if (!clock) clock = setInterval(() => { if (!document.hidden && visible() && ["planning", "running", "summarizing", "needs_decision"].includes(state().requests?.find((r) => r.id === rid())?.status)) draw(); }, 1000);
      if (paused) {
        run.open = storage.get(pref("run", rid()), run.open);
        paused = false;
      }
      if (!subscription) subscription = subscribeActivity(o.project(), o.sid(), (s) => { if (s.at || s.stale || s.unsupported || !snapshot.at) snapshot = s; draw(); }, {
        visible, busy: o.busy,
        units: () => [...outputUnits(rows.values(), run.open),
          ...(usage.open ? usageUnits(state(), rid()).map((unit) => [unit, 40]) : [])],
      });
      else subscription.kick();
    },
    teamMailboxHost() { return $("[data-team-mailbox]", box); },
    rawHost() { return $("[data-raw-protocol]", box); },
    decisionsHost() { return $("[data-decisions]", box); },
    reset() { api.stop(); context = ""; api.kick(); },
    stop() { clearInterval(clock); clock = null; subscription?.stop(); subscription = null; paused = true; },
  };
  return api;
}
