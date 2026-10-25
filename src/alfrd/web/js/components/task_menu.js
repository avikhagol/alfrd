import { esc } from "../utils/dom.js";
import { actions } from "./task_actions.js";

const unit = (t) => t.current_unit ?? t.units?.at(-1) ?? t.logs?.at(-1)?.unit ?? null;
const stamp = (t) => JSON.stringify([t?.context, t?.status, t && unit(t)]);

// The snapshot belongs to one opening. Async work from an older opening is ignored.
export function createTaskMenu(el, opts) {
  if (!el) return null;
  let current = null, generation = 0, busy = false;
  const close = (focus = true) => {
    const row = current?.row;
    current = null; generation++; busy = false; el.hidden = true;
    if (focus && row) opts.focus(row);
  };
  const list = () => actions(current).filter((a) => !current.orch && (!current.kind || current.kind === "task") || !["retry", "cancel"].includes(a.id));
  const draw = (message = "") => {
    el.hidden = false;
    el.innerHTML = `<p role="status">${esc(message || current.row)}</p>` + list().map((a) => `<button type="button" role="menuitem" data-action="${a.id}" aria-disabled="${busy || !a.enabled}" title="${esc(a.reason || "")}">${esc(a.label)}${a.reason ? ` — ${esc(a.reason)}` : ""}</button>`).join("");
  };
  async function open(row, message = "") {
    const t = opts.target(row);
    if (!t) { close(); return; }
    const token = ++generation;
    current = { ...t, row, rev: opts.revision(), current_unit: unit(t) };
    busy = true; draw(message || "Loading task actions…");
    el.querySelector('[role="menuitem"]')?.focus();
    try {
      const preview = await opts.preview(row);
      if (token !== generation) return;
      if (preview) current = { ...current, status: preview.current_status, current_unit: preview.current_unit, retry_preview: preview };
      busy = false; draw(message);
      el.querySelector('[role="menuitem"]')?.focus();
    } catch (e) {
      if (token !== generation) return;
      // Without a fresh preview mutations are unavailable, including Cancel.
      busy = true; draw(e.message);
    }
  }
  async function activate(id) {
    if (!current || busy) return;
    const action = list().find((a) => a.id === id);
    if (!action?.enabled) return;
    const target = current, token = generation;
    if (stamp(opts.target(target.row)) !== stamp(target) || opts.revision() !== target.rev) {
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
  el.addEventListener("focusout", (e) => { if (!el.contains(e.relatedTarget)) close(false); });
  return { open, close, activate, validate() { if (current && !opts.target(current.row)) close(); } };
}
