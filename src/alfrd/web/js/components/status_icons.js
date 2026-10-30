import { esc, icon } from "../utils/dom.js";

// Six run states, each with a glyph and a text label. Colour is decoration only: the label carries
// the meaning, so the set reads the same in greyscale and to colour-blind users.
// Pure markup helpers: no DOM access at import, so graph and tests can use them directly.
export const STATUS_ICONS = {
  done: { icon: "check", label: "Done" },
  running: { icon: "sync", label: "Running" },
  failed: { icon: "close", label: "Failed" },
  blocked: { icon: "alert", label: "Blocked" },
  queued: { icon: "hourglass", label: "Queued" },
  cancelled: { icon: "stop", label: "Cancelled" },
};

export const STATUS_ICON_STATES = Object.keys(STATUS_ICONS);

// Icon and label markup for one state. Unknown states fall back to a neutral info glyph and the raw name.
export function statusIcon(state, label) {
  const known = STATUS_ICON_STATES.includes(state);
  const meta = known ? STATUS_ICONS[state] : { icon: "info", label: String(state || "Unknown") };
  return `<span class="status-icon" data-status="${known ? state : "unknown"}">${icon(meta.icon)}<span class="status-label">${esc(label || meta.label)}</span></span>`;
}
