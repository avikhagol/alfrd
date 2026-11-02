import { esc } from "../utils/dom.js";
import { refreshHTML } from "../utils/refresh.js";
import { requestTitle, runRows, taskTitle, activitySentence, waitingFor, elapsed, requestStatusWord, attentionFor, costText, warningSentences, statusLabel, statusIcon } from "../data/harness.js";

const info = (lines) => lines.length ? `<button type="button" class="h-info" title="${esc(lines.join(" "))}" aria-label="${esc(lines.join(" "))}">ⓘ</button>` : "";
export function createRunCard(host, { onTask, onRequest, onAttention, onToggle = () => {} }) {
  let opened = false, session, last;
  const toggle = (value = !opened) => { if (!last) return; opened = value; paint(); onToggle(opened); };
  function paint() {
    if (!last) return;
    const [B, A, { replay }] = last, r = B.view.requests.find((r) => r.id === B.scope.request) || B.view.requests[0];
    if (!r) { host.innerHTML = ""; return; }
    const rows = runRows(B), byRow = new Map(B.view.tasks.map((t) => [t.row, t]));
    const warnings = B.coverage?.warnings || [], notes = warningSentences(warnings), cost = costText(B.summary?.usage, r.max_cost_usd, warnings);
    const share = attentionFor(A.view.attention, r.id), done = rows.tasks.filter((t) => t.status === "done").length;
    refreshHTML(host, `<div class="h-run"><div class="h-run-summary"><select data-h-request aria-label="Request" title="${esc(r.id)}">${A.view.requests.map((q) => `<option value="${esc(q.id)}" ${q.id === r.id ? "selected" : ""}>${esc(requestTitle(q, A.view.messages))}</option>`).join("")}</select><span class="h-run-status">${esc(requestStatusWord(r, rows, replay))}</span>${r.mode === "direct" ? `<span>Direct to @${esc(rows.tasks[0]?.owner || "agent")}</span>` : ""}${rows.tasks.length ? `<button type="button" data-h-checklist aria-expanded="${opened}" aria-controls="h-checklist">${done}/${rows.tasks.length} done ${opened ? "▴" : "▾"}</button>` : ""}${share.selected.length ? `<button type="button" data-h-attention>⚑ ${share.selected.length}</button>` : ""}${share.other.length ? `<button type="button" data-h-other="${esc(share.other[0].request)}">+ ${share.other.length} in other requests</button>` : ""}<span class="h-run-cost ${cost.tone}">${esc(cost.text)}${cost.budget && cost.value != null ? `<meter min="0" max="${cost.budget}" value="${Math.min(cost.value, cost.budget)}" aria-label="Reported cost against budget"></meter>` : ""}</span>${info([...notes.summary, ...notes.cost])}</div><div class="h-checklist" id="h-checklist" ${opened ? "" : "hidden"}>${info(notes.checklist)}${rows.phases.map((t) => `<button type="button" class="h-phase" data-h-task="${esc(t.row)}">${esc(({ plan: "Planning", repair: "Repairing plan", replan: "Replanning", check: "Checking progress", summary: "Writing summary" })[t.kind] || "Orchestrator step")} · ${esc(statusLabel(t.status))}${B.view.blockers?.some((d) => d.task === t.row && d.needs !== "user") ? " · Orchestrator deciding" : ""}</button>`).join("")}<ul>${rows.tasks.map((t) => `<li class="h-task state-${esc(t.status)}"><button type="button" data-h-task="${esc(t.row)}"><span class="h-task-title">${statusIcon(t.status)} ${esc(taskTitle(t))} <small>@${esc(t.owner || "agent")}</small></span><span>${esc(statusLabel(t.status))}${replay ? " at this time" : ""} · ${esc(activitySentence(t, byRow))}</span>${elapsed(t, B.timeline_segments, B.clock) ? `<small>${esc(elapsed(t, B.timeline_segments, B.clock))}</small>` : ""}</button>${waitingFor(t, byRow).map((d) => `<button class="h-dependency" type="button" data-h-dependency="${esc(d.row)}">Waiting for ${esc(taskTitle(d))}</button>`).join("")}</li>`).join("")}</ul></div>${!rows.tasks.length ? `<div class="h-phases">${rows.phases.map((t) => `<button type="button" data-h-task="${esc(t.row)}">${esc(t.kind === "plan" ? "Planning" : "Orchestrator step")} · ${esc(statusLabel(t.status))}</button>`).join("")}</div>` : ""}</div>`);
  }
  const click = (e) => { const b = e.target.closest("button"); if (!b) return;
    if (b.hasAttribute("data-h-checklist")) toggle();
    else if (b.dataset.hTask) onTask(b.dataset.hTask);
    else if (b.hasAttribute("data-h-attention")) onAttention();
    else if (b.dataset.hOther) onRequest(b.dataset.hOther);
    else if (b.dataset.hDependency) host.querySelector(`[data-h-task="${CSS.escape(b.dataset.hDependency)}"]`)?.focus();
  };
  const change = (e) => { if (e.target.matches("[data-h-request]")) onRequest(e.target.value); };
  host.addEventListener("click", click); host.addEventListener("change", change);
  return { update(B, A, options = {}) { if (session !== A.session) { session = A.session; opened = false; } if (!B) { last = null; host.replaceChildren(); return; } last = [B, A, options]; paint(); }, toggle, isOpen: () => opened,
    destroy() { host.removeEventListener("click", click); host.removeEventListener("change", change); host.replaceChildren(); } };
}
