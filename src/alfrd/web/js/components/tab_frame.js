// Studio settings frame: a tab rail and one visible panel. Arrows move focus; Enter/Space selects.
import { icon } from "../utils/dom.js";

export const tabFrame = (p, label, tabs, panels, cls = "") => `<div class="set-body"><div class="set-tabs" role="tablist" aria-label="${label}" aria-orientation="vertical">${tabs.map((t) => `<button type="button" role="tab" id="${p}-tab-${t.id}" aria-controls="${p}-panel-${t.id}" data-tab="${t.id}" ${t.hidden ? "hidden" : ""}>${icon(t.icon)}<span class="grow">${t.label}</span>${t.dot ? '<i class="dot warn" aria-hidden="true"></i>' : ""}</button>`).join("")}</div>
    <div class="modal-b set-panels ${p}-panels ${cls}">${tabs.map((t) => `<section class="set-panel" role="tabpanel" id="${p}-panel-${t.id}" aria-labelledby="${p}-tab-${t.id}" hidden>${panels[t.id]}</section>`).join("")}</div></div>`;

export function tabRail(root, p, onShow = () => {}) {
  const buttons = [...root.querySelectorAll("[role=tab]")], rail = root.querySelector(".set-tabs");
  const show = (name) => {
    buttons.forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1; });
    root.querySelectorAll(`.${p}-panels > .set-panel`).forEach((panel) => { panel.hidden = panel.id !== `${p}-panel-${name}`; });
    onShow(name);
  };
  rail.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) show(b.dataset.tab); });
  rail.addEventListener("keydown", (e) => {
    const shown = buttons.filter((b) => !b.hidden), i = shown.indexOf(document.activeElement);
    const to = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: shown.length - 1 }[e.key];
    if (i < 0 || to === undefined) return;
    e.preventDefault();
    shown[(to + shown.length) % shown.length].focus();
  });
  return show;
}
