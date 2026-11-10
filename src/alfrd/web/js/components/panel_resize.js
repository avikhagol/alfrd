// Shared panel resizing. Preferred widths survive viewport clamping.
import { storage } from '../utils/dom.js';

export function panelWidth(value, min, max, fallback) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(min, Math.min(max, Math.round(value))) : fallback;
}

export function mountPanelResize({ panel, handle, key, width, min, max, defaultWidth, side = 'left', label, controls = panel.id,
  available = () => max, enabled = () => true, apply, save = (w) => storage.set(key, w) }) {
  let preferred = panelWidth(width ?? storage.get(key, defaultWidth), min, max, defaultWidth), effective = preferred, drag = null;
  const listeners = [];
  let suspended = false;
  const on = (node, name, fn) => { node.addEventListener(name, fn); listeners.push(() => node.removeEventListener(name, fn)); };
  handle.classList.add('panel-resize'); handle.classList.toggle('panel-resize-right', side === 'right');
  for (const [k, v] of Object.entries({ role: 'separator', 'aria-orientation': 'vertical', 'aria-label': label, 'aria-controls': controls, 'aria-valuemin': min, 'aria-valuemax': max })) handle.setAttribute(k, String(v));
  handle.title = 'Left/Right: move 20 pixels (Shift: 80). Home/End: minimum/maximum. Enter or double-click: reset. Escape: cancel drag.';
  function sync() {
    if (suspended) return;
    const active = enabled();
    if (!active && drag) finish(true);
    handle.hidden = !active; handle.tabIndex = active ? 0 : -1;
    effective = Math.min(preferred, Math.max(min, available()));
    apply(effective);
    handle.setAttribute('aria-valuenow', String(effective)); handle.setAttribute('aria-valuetext', `${effective} pixels`);
  }
  const set = (w, persist = false) => { preferred = panelWidth(w, min, max, defaultWidth); sync(); if (persist) save(preferred); };
  function finish(cancel) {
    if (!drag) return;
    const old = drag; drag = null;
    if (cancel) set(old.preferred); else save(preferred);
    if (handle.hasPointerCapture?.(old.id)) handle.releasePointerCapture(old.id);
  }
  on(handle, 'pointerdown', (e) => {
    if (drag || !enabled() || e.isPrimary === false || (e.button != null && e.button !== 0)) return;
    e.preventDefault();
    drag = { id: e.pointerId, x: e.clientX, width: effective, preferred };
    handle.setPointerCapture?.(e.pointerId);
  });
  on(handle, 'pointermove', (e) => { if (drag && drag.id === e.pointerId) set(drag.width + (e.clientX - drag.x) * (side === 'left' ? 1 : -1)); });
  on(handle, 'pointerup', (e) => { if (drag?.id === e.pointerId) finish(false); });
  on(handle, 'pointercancel', () => finish(true)); on(handle, 'lostpointercapture', () => finish(true));
  on(handle, 'keydown', (e) => {
    if (e.key === 'Escape' && drag) { e.preventDefault(); finish(true); return; }
    if (!enabled()) return;
    const step = (e.shiftKey ? 80 : 20) * (side === 'left' ? 1 : -1);
    const next = { ArrowLeft: effective - step, ArrowRight: effective + step, Home: min, End: max, Enter: defaultWidth }[e.key];
    if (next == null) return;
    e.preventDefault(); set(next, true);
  });
  on(window, 'keydown', (e) => { if (e.key === 'Escape' && drag) { e.preventDefault(); finish(true); } });
  on(handle, 'dblclick', () => { if (enabled()) set(defaultWidth, true); });
  on(window, 'resize', sync);
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(sync) : null;
  if (panel.parentElement) observer?.observe(panel.parentElement);
  sync();
  return { suspend() { finish(true); suspended = true; observer?.disconnect(); },
    resume() { suspended = false; if (panel.parentElement) observer?.observe(panel.parentElement); sync(); },
    sync, preferred: () => preferred, destroy() { finish(true); observer?.disconnect(); listeners.splice(0).forEach((off) => off()); } };
}
