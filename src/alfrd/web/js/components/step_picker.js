// Step picker: which steps a new plan (Run dialog) or a new row (Add target) marks todo.
// Master "All steps" (tri-state) + Invert, stage groups with their own tri-state box,
// shift-click / Shift+arrow ranges, a From … To … range. State: data/step_select.js.
// Loaded with import() the first time a dialog needs it.

import { $, $$, esc, loadCss, storage } from "../utils/dom.js";
import * as S from "../data/step_select.js";

/** Stage groups for step ids from a project's defs ({stages, steps: {id: {stage}}}). */
export function groupsFor(defs, order) {
  return S.groupSteps(order, (id) => defs?.steps?.[id]?.stage || null, defs?.stages || []);
}

const setBox = (box, state) => {
  box.checked = state === "all";
  box.indeterminate = state === "mixed";
  box.setAttribute("aria-checked", state === "mixed" ? "mixed" : String(state === "all"));
};

/**
 * Mount into `host`. opts: {steps, defs, selected, remember (localStorage key), onChange(ids)}.
 * Returns {selected() → ids in workflow order, el}.
 */
export function mountStepPicker(host, { steps, defs = null, selected = null, remember = null, onChange = null } = {}) {
  loadCss("css/lazy.css");
  const saved = remember ? storage.get(remember, null) : null;
  const initial = selected ?? (Array.isArray(saved) && saved.some((s) => steps.includes(s)) ? saved : null);
  const sel = S.createSelection(steps, { groups: groupsFor(defs, steps), selected: initial });
  const grouped = sel.groups.length > 1;
  const opt = (id) => `<option value="${esc(id)}">${esc(id)}</option>`;
  host.innerHTML = `<div class="sp" role="group" aria-label="Steps">
    <div class="sp-h row gap wrap">
      <label class="check"><input type="checkbox" data-sp-all> <b>All steps</b></label>
      <span class="muted small" data-sp-count aria-live="polite"></span>
      <button type="button" class="link-btn small" data-sp-invert>Invert</button>
      <span class="grow"></span>
      <label class="small muted">From <select class="input sm" data-sp-from><option value="">first</option>${steps.map(opt).join("")}</select></label>
      <label class="small muted">to <select class="input sm" data-sp-to><option value="">last</option>${steps.map(opt).join("")}</select></label>
    </div>
    ${sel.groups.map((g, gi) => `<div class="sp-g">${grouped ? `<div class="sp-gh"><label class="check"><input type="checkbox" data-sp-stage="${esc(g.id)}"> ${esc(g.title)}</label> <span class="muted small" data-sp-gc="${esc(g.id)}"></span><button type="button" class="link-btn small" data-sp-fold="${gi}" aria-expanded="true" aria-controls="sp-l${gi}" title="Collapse or expand">▾</button></div>` : ""}
      <div class="run-steps" id="sp-l${gi}">${g.steps.map((id) => `<label class="check"><input type="checkbox" data-step="${esc(id)}"> <span class="mono">${esc(id)}</span></label>`).join("")}</div></div>`).join("")}
  </div>`;
  const root = $(".sp", host);
  const boxes = $$("input[data-step]", root);
  const sync = (fire = true, keepRange = false) => {
    if (!keepRange) { $("[data-sp-from]", root).value = ""; $("[data-sp-to]", root).value = ""; }
    boxes.forEach((b) => { b.checked = sel.on.has(b.dataset.step); });
    setBox($("[data-sp-all]", root), S.masterState(sel));
    $$("[data-sp-stage]", root).forEach((b) => setBox(b, S.stageState(sel, b.dataset.spStage)));
    $$("[data-sp-gc]", root).forEach((el) => {
      const g = sel.groups.find((x) => x.id === el.dataset.spGc);
      el.textContent = `${g.steps.filter((s) => sel.on.has(s)).length}/${g.steps.length}`;
    });
    $("[data-sp-count]", root).textContent = S.countText(sel);
    if (fire) {
      if (remember) storage.set(remember, S.selected(sel));
      onChange?.(S.selected(sel));
    }
  };
  // No preventDefault on these clicks: the box has already toggled, sync() sets every box from `sel`.
  $("[data-sp-all]", root).addEventListener("click", () => { S.toggleAll(sel); sync(); });
  $("[data-sp-invert]", root).addEventListener("click", () => { S.invert(sel); sync(); });
  $$("[data-sp-stage]", root).forEach((b) => b.addEventListener("click", () => { S.toggleStage(sel, b.dataset.spStage); sync(); }));
  $$("[data-sp-fold]", root).forEach((b) => b.addEventListener("click", () => {
    const list = $(`#sp-l${b.dataset.spFold}`, root);
    list.hidden = !list.hidden;
    b.setAttribute("aria-expanded", String(!list.hidden));
    b.textContent = list.hidden ? "▸" : "▾";
  }));
  const range = () => { S.setRange(sel, $("[data-sp-from]", root).value, $("[data-sp-to]", root).value); sync(true, true); };
  $("[data-sp-from]", root).addEventListener("change", range);
  $("[data-sp-to]", root).addEventListener("change", range);
  // Boxes are in workflow order (groups follow it), so DOM order = range order.
  const byStep = new Map(boxes.map((b) => [b.dataset.step, b]));
  const ordered = steps.map((id) => byStep.get(id)).filter(Boolean);
  let extending = false;
  ordered.forEach((b, i) => {
    // Tabbing onto a box starts a new range there; a click sets it in toggle().
    b.addEventListener("pointerdown", () => { extending = true; });
    b.addEventListener("focus", () => { if (!extending) sel.anchor = b.dataset.step; extending = false; });
    b.addEventListener("click", (e) => { extending = false; S.toggle(sel, b.dataset.step, { shift: e.shiftKey }); sync(); });
    b.addEventListener("keydown", (e) => {
      const dir = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
      if (!dir) return;
      e.preventDefault();
      const next = ordered[Math.min(ordered.length - 1, Math.max(0, i + dir))];
      extending = e.shiftKey;
      if (e.shiftKey) {
        S.extendTo(sel, next.dataset.step);
        sync();
      }
      next.focus();
      extending = false;
    });
  });
  sync(false);
  onChange?.(S.selected(sel));
  return { el: root, selected: () => S.selected(sel) };
}
