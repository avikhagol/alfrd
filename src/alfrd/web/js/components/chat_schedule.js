import { refreshHTML, refreshProtected } from "../utils/refresh.js";
import { esc } from "../utils/dom.js";
import { pendingItems, scheduleCountdown, schedulePayload, scheduleSummary, timeCandidates } from "../data/chat_schedule.js";

export function mountChatSchedule(root, controller, { onChange = () => {}, toggle = null } = {}) {
  let notified = "";
  let rendered = "", activeBatch = null;
  const batches = () => state?.schedules || (state?.schedule ? [state.schedule] : []);
  const findBatch = (id) => batches().find((b) => b.id === id);
  let state = null, editing = null, dirty = false, busy = false, destroyed = false;
  root.hidden = true;
  const syncToggle = () => {
    toggle?.setAttribute("aria-pressed", String(!!state?.schedule));
    toggle?.setAttribute("aria-expanded", String(!root.hidden));
    if (toggle) toggle.disabled = !state || (!state.schedule && state.schedule_service?.available === false);
  };
  const error = (message) => { const el = root.querySelector("dialog[open] [data-picker-error]") || root.querySelector("[data-schedule-error]"); if (el) el.textContent = message; };
  const apply = async (action, payload = {}, item = null) => {
    busy = true;
    try { await controller.mutate(action, payload, item, activeBatch); return true; }
    catch (e) { error(e.message); return false; }
    finally { busy = false; }
  };
  const picker = (change) => {
    const dialog = root.querySelector("dialog"), local = dialog.querySelector("[name=time]"), zone = dialog.querySelector("[name=zone]"), offset = dialog.querySelector("[name=offset]");
    zone.value = (change ? findBatch(activeBatch) : state?.schedule)?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    local.value = ""; offset.innerHTML = "";
    dialog.dataset.change = change ? activeBatch : "";
    root.hidden = false; syncToggle();
    dialog.showModal();
  };
  const render = (next) => {
    if (destroyed) return;
    state = next;
    const notification = JSON.stringify([state.schedule, state.schedules, state.schedule_revision, state.schedule_service]);
    if (notification !== notified) { notified = notification; onChange(next); }
    syncToggle();
    if (root.querySelector("dialog[open]")) return;
    const key = JSON.stringify([state.schedule, state.schedules, state.last_schedule, state.schedule_service, editing?.id, dirty]);
    if (key === rendered) { countdown(); return; }
    if (editing && root.querySelector("[data-edit-text]")) {
      const status = root.querySelector(".chat-schedule-status"), summary = scheduleSummary(state.schedule) || state.last_schedule?.status || "";
      if (status && status.textContent !== summary) status.textContent = summary;
      if (!findBatch(editing.batchId)) error("The saved queue was dispatched or cancelled. Your unsaved edit is retained here for copying.");
      countdown(); return;
    }
    if (refreshProtected(root)) return;
    rendered = key;
    const batch = state.schedule;
    // Keep unsaved text available when an authoritative deadline wins the race.
    const savedEdit = editing ? root.querySelector("[data-edit-text]")?.value : null;
    refreshHTML(root, `<div class="chat-schedule-status" role="status" aria-live="polite">${esc(scheduleSummary(batch) || (state.last_schedule ? `${state.last_schedule.status}${state.last_schedule.request_id ? ` as ${state.last_schedule.request_id}` : ""}` : ""))}</div>
      ${batches().map((batch) => { const items = pendingItems(batch); return `<section class="chat-schedule-block" data-batch="${batch.id}" aria-label="Scheduled message">
      <p class="small">${esc(scheduleSummary(batch))}${batch.id === state.schedule?.id ? " · Composer target" : ""}</p><p data-countdown="${batch.id}" class="muted small"></p>
      <p class="small">Queued for one orchestrator plan; @mentions are preferences. Current agent settings apply at dispatch.</p>${batch.error ? `<p class="fail-t">Not sent: ${esc(batch.error)}</p>` : ""}
      <ol class="chat-schedule-items">${items.map((item) => `<li><p>${esc(item.text)}</p><small>Added ${esc(item.created_at)}${item.version > 1 ? ` · edited ${esc(item.updated_at)}` : ""}</small><p>${(item.attachments || []).map((a) => `<span class="badge">${esc(a.name || a.ref)}</span>`).join(" ")}</p><button type="button" class="icon-btn" aria-label="Edit scheduled item" title="Edit scheduled item" data-edit="${item.id}">✎</button> <button type="button" class="btn sm" data-remove="${item.id}">Remove</button></li>`).join("")}</ol>
      <button type="button" class="icon-btn" aria-label="Append to scheduled message" title="Append to scheduled message" aria-pressed="${batch.id === state.schedule?.id}" data-append>＋</button>
      <button type="button" class="icon-btn" aria-label="Change scheduled time" title="Change scheduled time" data-time>◷</button>
      <button type="button" class="btn sm" data-run ${!items.length || dirty || batch.status === "dispatching" ? "disabled" : ""}>${batch.status === "error" ? "Retry" : "Run now"}</button> <button type="button" class="btn sm" data-cancel>Cancel schedule</button></section>`; }).join("")}
      <button type="button" class="btn sm" data-enable ${state.schedule_service?.available === false ? "disabled" : ""}>${batches().length ? "Schedule another message" : "Schedule"}</button>
      <div data-editor ${editing ? "" : "hidden"}><label>Queued item <textarea data-edit-text class="input"></textarea></label><p class="muted small">Unsaved edits are not queued. The deadline sends the last saved version.</p><button type="button" class="btn sm" data-save>Save</button> <button type="button" class="btn sm" data-discard>Cancel edit</button></div>
      <p data-schedule-error class="fail-t small" role="alert"></p>
      <dialog aria-label="Schedule date and time"><label>Date and time <input type="datetime-local" name="time"></label><label>Time zone <input name="zone"></label><label>UTC offset <select name="offset"></select></label><p data-resolved class="small"></p><p class="muted small">ALFRD Studio must be running. Missed schedules run when it returns.</p><button type="button" class="btn sm" data-save-time>Save schedule</button> <button type="button" class="btn sm" data-close>Cancel</button><p data-picker-error role="alert"></p></dialog>`);
    if (editing && savedEdit !== null) { root.querySelector("[data-edit-text]").value = savedEdit; if (!batch) error("The saved queue was dispatched or cancelled. Your unsaved edit is retained here for copying."); }
    root.querySelectorAll("[data-edit], [data-remove], [data-time], [data-cancel], [data-append]").forEach((el) => { el.disabled = findBatch(el.closest("[data-batch]")?.dataset.batch)?.status === "dispatching"; });
    countdown();
  };
  const countdown = () => { root.querySelectorAll("[data-countdown]").forEach((el) => { const text = scheduleCountdown(findBatch(el.dataset.countdown), controller.now()); if (el.textContent !== text) el.textContent = text; }); };
  const click = async (event) => {
    const el = event.target.closest("button"); if (!el || el.disabled || busy) return;
    const block = el.closest("[data-batch]");
    if (block) activeBatch = block.dataset.batch;
    if (el.hasAttribute("data-append")) { controller.select(activeBatch); const input = root.closest("form")?.querySelector("textarea"); input?.focus(); }
    if (el.hasAttribute("data-enable") || el.hasAttribute("data-time")) picker(el.hasAttribute("data-time"));
    if (el.hasAttribute("data-close")) root.querySelector("dialog").close();
    if (el.hasAttribute("data-save-time")) {
      const dialog = root.querySelector("dialog");
      try {
        const select = dialog.querySelector("[name=offset]");
        const payload = schedulePayload(dialog.querySelector("[name=time]").value, dialog.querySelector("[name=zone]").value, select.value === "" ? null : Number(select.value));
        activeBatch = dialog.dataset.change || null;
        if (await apply(dialog.dataset.change ? "time" : "create", payload)) { dialog.close(); render(controller.read() || state); }
      } catch (e) { dialog.querySelector("[data-picker-error]").textContent = e.message; }
    }
    if (el.dataset.edit) {
      editing = { ...findBatch(activeBatch).items.find((i) => i.id === el.dataset.edit), batchId: activeBatch }; dirty = false;
      root.querySelector("[data-editor]").hidden = false; root.querySelector("[data-edit-text]").value = editing.text; root.querySelector("[data-edit-text]").focus();
    }
    if (el.dataset.remove) await apply("remove", {}, el.dataset.remove);
    if (el.hasAttribute("data-save") && editing) {
      const text = root.querySelector("[data-edit-text]").value;
      busy = true; el.textContent = "Saving…";
      try { await controller.mutate("edit", { text, attachments: (editing.attachments || []).map((a) => a.ref) }, editing.id, editing.batchId); editing = null; dirty = false; rendered = ""; render(controller.read()); }
      catch (e) { error(e.message); }
      finally { busy = false; if (el.isConnected) el.textContent = "Save"; }
    }
    if (el.hasAttribute("data-discard")) { editing = null; dirty = false; rendered = ""; render(state); }
    if (el.hasAttribute("data-run") && !dirty) await apply("run");
    if (el.hasAttribute("data-cancel") && (!pendingItems(findBatch(activeBatch)).length || globalThis.confirm(`Cancel this schedule and discard ${pendingItems(findBatch(activeBatch)).length} pending items?`))) await apply("cancel");
  };
  const input = (event) => {
    if (event.target.hasAttribute("data-edit-text")) { dirty = true; root.querySelectorAll("[data-run]").forEach((run) => { run.disabled = true; }); }
    if (["time", "zone"].includes(event.target.name)) {
      const dialog = root.querySelector("dialog"), select = dialog.querySelector("[name=offset]");
      try {
        const choices = timeCandidates(dialog.querySelector("[name=time]").value, dialog.querySelector("[name=zone]").value);
        select.innerHTML = (choices.length > 1 ? '<option value="">Choose offset</option>' : '') + choices.map((c, n) => `<option value="${n}">UTC${c.offset < 0 ? "−" : "+"}${String(Math.floor(Math.abs(c.offset) / 3600000)).padStart(2, "0")}:${String(Math.abs(c.offset) / 60000 % 60).padStart(2, "0")}</option>`).join("");
        dialog.querySelector("[data-resolved]").textContent = choices.length ? `${dialog.querySelector("[name=zone]").value} · ${choices.map((c) => new Date(c.instant).toISOString()).join(" or ")}` : "This time does not exist in this zone.";
      } catch { select.innerHTML = ""; }
    }
  };
  const togglePanel = () => {
    if (!state || toggle?.disabled) return;
    if (batches().length || editing || !root.hidden) {
      root.hidden = !root.hidden;
      syncToggle();
    } else picker(false);
  };
  const closePicker = () => {
    if (!batches().length && !editing) root.hidden = true;
    syncToggle(); toggle?.focus(); render(controller.read() || state);
  };
  const escape = (event) => {
    if (event.key === "Escape" && !root.querySelector("dialog[open]")) {
      event.preventDefault(); event.stopPropagation(); root.hidden = true; syncToggle(); toggle?.focus();
    }
  };
  // close does not bubble, so capture also handles dialogs replaced during a state refresh.
  root.addEventListener("close", closePicker, true);
  root.addEventListener("keydown", escape);
  toggle?.addEventListener("click", togglePanel);
  syncToggle();
  root.addEventListener("click", click); root.addEventListener("input", input);
  const released = () => queueMicrotask(() => { if (!destroyed && state) render(state); });
  root.addEventListener("focusout", released);
  let unsubscribe = controller.subscribe(render), timer = setInterval(countdown, 1000);
  let suspended = false;
  timer.unref?.();
  return { suspend() { if (suspended) return; suspended = true; unsubscribe(); clearInterval(timer); },
    resume() { if (!suspended || destroyed) return; suspended = false; unsubscribe = controller.subscribe(render); timer = setInterval(countdown, 1000); controller.refresh().catch(() => {}); },
    refresh: () => controller.refresh(), async open() { await controller.refresh(); if (!destroyed && root.hidden) togglePanel(); }, destroy() { destroyed = true; root.removeEventListener("focusout", released); unsubscribe(); clearInterval(timer); root.removeEventListener("close", closePicker, true); root.removeEventListener("keydown", escape); toggle?.removeEventListener("click", togglePanel); root.hidden = true; root.removeEventListener("click", click); root.removeEventListener("input", input); root.innerHTML = ""; } };
}
