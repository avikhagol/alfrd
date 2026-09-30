// Step selection state for the step picker (pure; tests in tests/studio_js/step_select.test.mjs).
//
// sel = { order: [ids in workflow order], groups: [{id, title, steps: [ids]}], on: Set, anchor }
// `selected(sel)` is always in workflow order, whatever order steps were clicked in.

/** Group steps by their stage, groups in workflow order (first step of each), titles from `stages`. */
export function groupSteps(order, stageOf = () => null, stages = []) {
  const titles = new Map((stages || []).map((s) => [s.id, s.title || s.id]));
  const map = new Map();
  order.forEach((id) => {
    const g = stageOf(id) || "";
    if (!map.has(g)) map.set(g, { id: g, title: g ? titles.get(g) || g : "Other", steps: [] });
    map.get(g).steps.push(id);
  });
  return [...map.values()];
}

export function createSelection(order, { groups = null, selected = null } = {}) {
  const ids = [...order];
  const on = new Set(selected == null ? ids : [...selected].filter((s) => ids.includes(s)));
  return { order: ids, groups: groups || [{ id: "", title: "Steps", steps: ids }], on, anchor: null };
}

export const selected = (sel) => sel.order.filter((id) => sel.on.has(id));

const stateOf = (ids, on) => {
  const n = ids.filter((id) => on.has(id)).length;
  return n === 0 ? "none" : n === ids.length ? "all" : "mixed";
};

/** "all" | "none" | "mixed" for everything, or for one stage. */
export const masterState = (sel) => stateOf(sel.order, sel.on);
export const stageState = (sel, stage) => stateOf(sel.groups.find((g) => g.id === stage)?.steps || [], sel.on);

/** none / some → all; all → none. */
export function toggleAll(sel) {
  const to = masterState(sel) !== "all";
  sel.on = new Set(to ? sel.order : []);
  return sel;
}

export function toggleStage(sel, stage) {
  const ids = sel.groups.find((g) => g.id === stage)?.steps || [];
  const to = stateOf(ids, sel.on) !== "all";
  ids.forEach((id) => (to ? sel.on.add(id) : sel.on.delete(id)));
  return sel;
}

export function invert(sel) {
  sel.on = new Set(sel.order.filter((id) => !sel.on.has(id)));
  return sel;
}

/** Click on a step; with `shift`, the range from the last clicked step gets that step's new value. */
export function toggle(sel, id, { shift = false } = {}) {
  if (!sel.order.includes(id)) return sel;
  const to = !sel.on.has(id);
  if (shift && sel.anchor && sel.anchor !== id) {
    const [a, b] = [sel.order.indexOf(sel.anchor), sel.order.indexOf(id)].sort((x, y) => x - y);
    sel.order.slice(a, b + 1).forEach((s) => (to ? sel.on.add(s) : sel.on.delete(s)));
  } else {
    to ? sel.on.add(id) : sel.on.delete(id);
  }
  sel.anchor = id;
  return sel;
}

/** Shift+arrow: `id` takes the anchor's value (extends the range the keyboard way). */
export function extendTo(sel, id) {
  const value = sel.anchor ? sel.on.has(sel.anchor) : true;
  const [a, b] = [sel.order.indexOf(sel.anchor ?? id), sel.order.indexOf(id)].sort((x, y) => x - y);
  sel.order.slice(a, b + 1).forEach((s) => (value ? sel.on.add(s) : sel.on.delete(s)));
  return sel;
}

/** Exactly the contiguous range from…to (like `alfrd plan new --from/--to`); either end may be empty. */
export function setRange(sel, from, to) {
  const lo = from ? sel.order.indexOf(from) : 0;
  const hi = to ? sel.order.indexOf(to) : sel.order.length - 1;
  const [a, b] = [Math.max(0, lo), hi < 0 ? sel.order.length - 1 : hi].sort((x, y) => x - y);
  sel.on = new Set(sel.order.slice(a, b + 1));
  return sel;
}

export const countText = (sel) => `${sel.on.size} of ${sel.order.length} selected`;
