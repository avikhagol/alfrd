// Keep what the reader opened across re-renders (Metadata). Loaded with import()
// alongside panels.js / metadata_avica.js, so it never counts toward startup.

/**
 * Replace `el`'s HTML without losing what the reader opened. Native <details> with a
 * stable `data-detail-key` keep their open *and* closed state in `states`
 * (`${scope}|${key}` -> Boolean); unvisited ones keep the HTML default. Scroll offsets
 * of `scroller` and of code/table viewports inside keyed details survive when the scope
 * is unchanged. An identical HTML string leaves the DOM (focus, caret, scroll) alone.
 * `prepare(el)` runs on the new DOM before scroll offsets are restored.
 */
export function swapHtml(el, html, scope, states, { scroller = null, prepare = null } = {}) {
  if (el._shownFirst !== el.firstChild) delete el.dataset.detailScope; // replaced by someone else
  const same = el.dataset.detailScope === scope;
  if (same && el._shownHtml === html) return false;
  const inner = [];
  if (el.dataset.detailScope) {
    el.querySelectorAll("details[data-detail-key]").forEach((d) => { states[`${el.dataset.detailScope}|${d.dataset.detailKey}`] = d.open; });
    if (same) {
      el.querySelectorAll("details[data-detail-key] :is(pre, .grid-scroll)").forEach((v) => {
        if (v.scrollTop || v.scrollLeft) inner.push([viewportKey(v), v.scrollTop, v.scrollLeft]);
      });
    }
  }
  const top = scroller?.scrollTop;
  const left = scroller?.scrollLeft;
  const focused = same ? summaryKey(document.activeElement, el) : null;
  el.innerHTML = html;
  el._shownHtml = html;
  el._shownFirst = el.firstChild;
  el.dataset.detailScope = scope;
  el.querySelectorAll("details[data-detail-key]").forEach((d) => {
    const v = states[`${scope}|${d.dataset.detailKey}`];
    if (typeof v === "boolean" && d.open !== v) d.open = v;
  });
  prepare?.(el);
  if (!same) return true;
  if (inner.length) {
    const found = new Map([...el.querySelectorAll("details[data-detail-key] :is(pre, .grid-scroll)")].map((v) => [viewportKey(v), v]));
    inner.forEach(([k, t, l]) => { const v = found.get(k); if (v) { v.scrollTop = t; v.scrollLeft = l; } });
  }
  if (scroller && top != null) { scroller.scrollTop = top; scroller.scrollLeft = left; }
  // The focused summary went with the old DOM: focus its replacement, unless focus has moved elsewhere.
  const now = document.activeElement;
  if (focused && (!now || now === document.body || !now.isConnected)) {
    [...el.querySelectorAll("details > summary")].find((s) => summaryKey(s, el) === focused)?.focus({ preventScroll: true });
  }
  return true;
}

/** Identity of a disclosure summary inside `root` (keyed file, panel card or section card), else null. */
function summaryKey(node, root) {
  const d = node?.tagName === "SUMMARY" && root.contains(node) ? node.parentElement : null;
  if (!d || d.tagName !== "DETAILS") return null;
  const { detailKey, pn, sec } = d.dataset;
  return detailKey != null ? `k|${detailKey}` : pn != null ? `pn|${pn}` : sec != null ? `sec|${sec}` : null;
}

/**
 * Metadata's #md-main: section cards keep ui.open / ui.closed (synced from the DOM
 * first, in case a toggle event is still queued, and re-applied because the HTML may
 * have been drawn before the last toggle); file disclosures keep ui.details.
 */
export function showKept(main, html, scope, ui, scroller) {
  const pn = (d) => d.dataset.pn;
  const sec = (d) => d.dataset.sec;
  if (main.dataset.detailScope === scope && main._shownFirst === main.firstChild) { // never another project's cards
    main.querySelectorAll("details[data-pn]").forEach((d) => { d.open ? ui.closed.delete(pn(d)) : ui.closed.add(pn(d)); });
    main.querySelectorAll("details[data-sec]").forEach((d) => { d.open ? ui.open.add(sec(d)) : ui.open.delete(sec(d)); });
  }
  return swapHtml(main, html, scope, ui.details, {
    scroller,
    prepare: () => {
      main.querySelectorAll("details[data-pn]").forEach((d) => { d.open = !ui.closed.has(pn(d)); });
      main.querySelectorAll("details[data-sec]").forEach((d) => { d.open = ui.open.has(sec(d)); });
    },
  });
}

function viewportKey(v) {
  const d = v.closest("details[data-detail-key]");
  return `${d.dataset.detailKey}#${[...d.querySelectorAll(":is(pre, .grid-scroll)")].indexOf(v)}`;
}

/** Record a user toggle of a keyed <details> (listen to `toggle` with capture: it does not bubble). */
export function noteDetailToggle(event, states, limit = 500) {
  const d = event.target;
  if (!(d instanceof HTMLDetailsElement) || !d.dataset.detailKey || !d.isConnected) return false;
  const scope = d.closest("[data-detail-scope]")?.dataset.detailScope;
  if (!scope) return false;
  const k = `${scope}|${d.dataset.detailKey}`;
  delete states[k]; // re-insert: the oldest keys go first once over the limit
  states[k] = d.open;
  const keys = Object.keys(states);
  keys.slice(0, Math.max(0, keys.length - limit)).forEach((x) => delete states[x]);
  return true;
}
