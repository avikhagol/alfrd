// Fit names into a fixed width by cutting the middle: the start and the
// distinguishing end (a folder, a suffix) stay visible. The full text belongs in
// a tooltip and in the open list.

const ELLIPSIS = "…";

/** ``text`` shortened in the middle so ``measure(result) <= width``; unchanged when it fits. */
export function middleTruncate(text, width, measure) {
  const value = String(text ?? "");
  if (!value || measure(value) <= width) return value;
  const chars = [...value];
  let lo = 0, hi = chars.length - 1, best = ELLIPSIS;
  while (lo <= hi) {
    const keep = (lo + hi) >> 1;
    const head = Math.ceil(keep / 2), tail = keep - head;
    const candidate = chars.slice(0, head).join("") + ELLIPSIS + (tail ? chars.slice(-tail).join("") : "");
    if (measure(candidate) <= width) { best = candidate; lo = keep + 1; } else hi = keep - 1;
  }
  return best;
}

let canvas = null;
/** A text-width function for an element's computed font (canvas measureText). */
export function measurer(el) {
  const style = getComputedStyle(el);
  canvas ||= document.createElement("canvas");
  const g = canvas.getContext("2d");
  g.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  return (s) => g.measureText(s).width;
}

/** Set ``el``'s text to ``text``, shortened in the middle when it overflows the width it gets (or ``width``); the full text goes in ``title``. */
export function fitMiddle(el, text, width) {
  el.textContent = text;
  const limit = width ?? el.clientWidth; // 0 while hidden: keep the full text
  if (limit > 0 && el.scrollWidth > el.clientWidth) el.textContent = middleTruncate(text, limit, measurer(el));
  el.title = el.textContent === text ? "" : text;
  return el.textContent;
}

/** Picker labels: a project's name, plus its folder when two projects share the name. */
export function projectLabels(list) {
  const count = {};
  list.forEach((p) => { count[p.label] = (count[p.label] || 0) + 1; });
  return Object.fromEntries(list.map((p) => [p.id, count[p.label] > 1 && p.root ? `${p.label} (${p.root.split(/[\\/]/).filter(Boolean).pop()})` : p.label]));
}
