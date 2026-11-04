import { esc, icon } from "../utils/dom.js";
import { actions } from "./task_actions.js";

const unit = (t) => t.current_unit ?? t.units?.at(-1) ?? t.logs?.at(-1)?.unit ?? null;
const stamp = (t) => JSON.stringify([t?.context, t?.status, t && unit(t)]);

// The snapshot belongs to one opening. Async work from an older opening is ignored.
export function createTaskMenu(el, opts) {
  if (!el) return null;
  let current = null, generation = 0, busy = false, drawing = false, anchor = null;
  const icons = { log: "log", prompt: "file", reply: "chat", files: "folder", retry: "reset", cancel: "stop", copy_id: "copy" };
  const outside = (e) => { if (!el.contains(e.target) && !anchor?.contains?.(e.target)) close(false); };
  const position = () => {
    if (!el.style || !anchor || !globalThis.window) return;
    const r = anchor.getBoundingClientRect?.() || anchor;
    const edge = 12, gap = 6, w = el.offsetWidth, h = el.offsetHeight;
    const width = window.innerWidth, height = window.innerHeight;
    el.style.left = `${Math.max(edge, Math.min(r.left, width - w - edge))}px`;
    const below = r.bottom + gap;
    el.style.top = `${below + h > height - edge && r.top - gap - h >= edge ? r.top - gap - h : Math.max(edge, Math.min(below, height - h - edge))}px`;
  };
  const close = (focus = true) => {
    const row = current?.row;
    current = null; generation++; busy = false; el.hidden = true;
    anchor?.setAttribute?.("aria-expanded", "false");
    document.removeEventListener?.("pointerdown", outside, true);
    globalThis.window?.removeEventListener("resize", position);
    if (row) opts.closed?.();
    if (focus && row) { if (anchor?.focus && anchor.isConnected) anchor.focus(); else opts.focus(row); }
  };
  const list = () => actions(current).filter((a) => a.enabled &&
    (!["retry", "cancel"].includes(a.id) || (current.mutation_ready !== false && !current.orch && (!current.kind || current.kind === "task"))));
  const draw = (message = "") => {
    const active = el.contains(document.activeElement) ? document.activeElement?.dataset?.action : null;
    drawing = true;
    el.hidden = false;
    el.innerHTML = `<p class="task-menu-status" role="status">${esc(message || current.row)}</p>` + list().map((a, i, all) => `${i && (a.id === "copy_id" || (["retry", "cancel"].includes(a.id) && !["retry", "cancel"].includes(all[i - 1].id))) ? '<hr role="separator">' : ""}<button type="button" role="menuitem" tabindex="-1" class="${a.id === "cancel" ? "danger" : ""}" data-action="${a.id}" aria-disabled="${busy}">${icon(icons[a.id])}<span>${esc(a.label)}</span></button>`).join("");
    position();
    if (active) el.querySelector(`[data-action="${active}"]`)?.focus();
    drawing = false;
  };
  async function open(row, message = "", at = null) {
    if (at) anchor = at;
    anchor?.setAttribute?.("aria-expanded", "true");
    document.addEventListener?.("pointerdown", outside, true);
    globalThis.window?.addEventListener("resize", position);
    const t = opts.target(row);
    if (!t) { close(); return; }
    const token = ++generation;
    current = { ...t, row, rev: opts.revision(), current_unit: unit(t) };
    current.mutation_ready = false; busy = false; draw(message || "Loading task actions…");
    el.querySelector('[role="menuitem"]')?.focus();
    try {
      const preview = current.orch || (current.kind && current.kind !== "task") ? null : await opts.preview(row);
      if (token !== generation) return;
      if (preview) current = { ...current, ...preview, status: preview.current_status ?? current.status,
        current_unit: preview.current_unit ?? current.current_unit, retry_preview: preview, mutation_ready: preview.mutation_ready !== false };
      busy = false; draw(message || preview?.preview_error);
      if (!el.contains(document.activeElement)) el.querySelector('[role="menuitem"]')?.focus();
    } catch (e) {
      if (token !== generation) return;
      // A failed mutation preview must not disable read actions.
      busy = false; draw(e.message);
    }
  }
  async function activate(id) {
    if (!current || busy) return;
    const action = list().find((a) => a.id === id);
    if (!action?.enabled) return;
    const target = current, token = generation;
    const latest = opts.target(target.row);
    if (!latest || latest.context !== target.context) { close(); return; }
    if (["retry", "cancel"].includes(id) && (stamp(opts.target(target.row)) !== stamp(target) || opts.revision() !== target.rev)) {
      await opts.refresh();
      if (token === generation) await open(target.row, "Task changed. Review the refreshed actions.");
      return;
    }
    if (action.confirmation && !globalThis.confirm(action.confirmation.message)) return;
    busy = true; draw();
    try {
      await opts.run(id, target);
      if (token === generation) close();
    } catch (e) {
      if (token !== generation) return;
      if (e.status === 409) {
        await opts.refresh();
        if (token === generation) await open(target.row, "Task changed. Review the refreshed actions.");
      } else { busy = false; draw(e.message); opts.error(e.message); }
    }
  }
  el.addEventListener("click", (e) => { e.stopPropagation(); const id = e.target.closest("[data-action]")?.dataset.action; if (id) activate(id).catch((err) => opts.error(err.message)); });
  el.addEventListener("keydown", (e) => {
    e.stopPropagation();
    const buttons = [...el.querySelectorAll('[role="menuitem"]')], i = buttons.indexOf(document.activeElement);
    if (e.key === "Escape" || e.key === "Tab") { if (e.key === "Escape") e.preventDefault(); close(); }
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault(); buttons[e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : (i + (e.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length]?.focus();
    }
  });
  el.addEventListener("focusout", (e) => { if (!drawing && !el.contains(e.relatedTarget)) close(false); });
  return { open, close, activate, isOpen: () => Boolean(current), validate() { if (current && (!opts.target(current.row) || opts.target(current.row).context !== current.context)) close(); } };
}
