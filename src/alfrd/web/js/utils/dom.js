// Small DOM, formatting and icon helpers shared by every Studio view.
// No framework: views render template strings and use event delegation.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Escape any value for safe interpolation into HTML text or attributes. */
export function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (c) => ESC[c]);
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

/** Delegate `type` events on `root` to descendants matching `selector`. */
export function on(root, type, selector, handler) {
  root.addEventListener(type, (event) => {
    const target = event.target.closest(selector);
    if (target && root.contains(target)) handler(event, target);
  });
}

/** Format seconds as `00h 42m 10s`. */
export function hms(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return `${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${String(r).padStart(2, "0")}s`;
}

/** Compact duration for grid cells: `0m41s`, `1h05m`. */
export function short(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  const s = Math.max(0, Math.round(seconds));
  if (s >= 3600) return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}

/** Long elapsed format: `495d 15:32:54`. */
export function elapsed(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const rest = s % 86400;
  const clock = [Math.floor(rest / 3600), Math.floor((rest % 3600) / 60), rest % 60]
    .map((v) => String(v).padStart(2, "0"))
    .join(":");
  return d ? `${d}d ${clock}` : clock;
}

export function bytes(n) {
  if (!Number.isFinite(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toISOString().replace("T", " ").slice(0, 19);
}

/** Add a stylesheet once (css/lazy.css for features loaded on first use). */
export function loadCss(href) {
  if (document.querySelector(`link[data-lazy="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.dataset.lazy = href;
  document.head.appendChild(link);
}

/** Trigger a client-side download of text content. */
export function download(filename, text, type = "text/plain") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** localStorage wrapper that never throws (private windows, blocked storage). */
export const storage = {
  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem(`alfrd-studio:${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`alfrd-studio:${key}`, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  },
  remove(key) {
    try {
      localStorage.removeItem(`alfrd-studio:${key}`);
    } catch {
      /* ignore */
    }
  },
  clear() {
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith("alfrd-studio:"))
        .forEach((k) => localStorage.removeItem(k));
    } catch {
      /* ignore */
    }
  },
  size() {
    try {
      return Object.keys(localStorage)
        .filter((k) => k.startsWith("alfrd-studio:"))
        .reduce((n, k) => n + k.length + (localStorage.getItem(k) || "").length, 0);
    } catch {
      return 0;
    }
  },
};

/**
 * Remembered per-view UI state (filters, zoom, folded panels...). Reset from
 * Settings → "Reset view state", which removes every `ui:*` key.
 */
export function loadUi(key, defaults) {
  return { ...defaults, ...(storage.get(`ui:${key}`, {}) || {}) };
}
const uiTimers = {};
export function saveUi(key, obj, fields) {
  clearTimeout(uiTimers[key]);
  uiTimers[key] = setTimeout(() => {
    const out = {};
    fields.forEach((f) => {
      const v = obj[f];
      out[f] = v instanceof Set ? [...v] : v;
    });
    storage.set(`ui:${key}`, out);
  }, 150);
}
export function resetUi() {
  try {
    Object.keys(localStorage).filter((k) => k.startsWith("alfrd-studio:ui:")).forEach((k) => localStorage.removeItem(k));
  } catch { /* ignore */ }
}

/**
 * Run `fn` (a re-render inside `root`) keeping the offsets of `[data-scroll-key]`
 * viewports and focus on the same `[data-focus-key]` / scroll region: a poll must not
 * send a sideways-scrolled grid back to column one or drop the focused action.
 */
export function keepScroll(root, fn) {
  if (!root?.querySelectorAll) { fn(); return; }
  const at = (k) => root.querySelector(`[data-focus-key="${CSS.escape(k)}"],[data-scroll-key="${CSS.escape(k)}"]`);
  const offsets = [...root.querySelectorAll("[data-scroll-key]")].map((v) => [v.dataset.scrollKey, v.scrollTop, v.scrollLeft]);
  const a = document.activeElement;
  const focus = root.contains(a) && (a.dataset.focusKey || a.dataset.scrollKey);
  const own = [root.scrollTop, root.scrollLeft];
  fn();
  [root.scrollTop, root.scrollLeft] = own;
  offsets.forEach(([k, t, l]) => { const v = root.querySelector(`[data-scroll-key="${CSS.escape(k)}"]`); if (v) { v.scrollTop = t; v.scrollLeft = l; } });
  if (focus && !root.contains(document.activeElement)) at(focus)?.focus({ preventScroll: true });
}

// Inline SVG icons (stroke based, 24px viewBox). Kept tiny on purpose.
const P = {
  overview: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
  workflow: '<rect x="3" y="3" width="7" height="6" rx="1"/><rect x="14" y="15" width="7" height="6" rx="1"/><rect x="14" y="3" width="7" height="6" rx="1"/><path d="M10 6h4M17.5 9v6"/>',
  metadata: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
  results: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M8 17v-5M12 17V8M16 17v-3"/>',
  project: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
  download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  caret: '<path d="M9 6l6 6-6 6"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
  xCircle: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  alert: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4M12 17.5v.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17v.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  hourglass: '<path d="M7 3h10M7 21h10M8 3c0 5 8 5 8 9s-8 4-8 9M16 3c0 5-8 5-8 9s8 4 8 9"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
  reset: '<path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/>',
  map: '<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
  graph: '<rect x="3" y="3" width="7" height="6" rx="1"/><rect x="14" y="15" width="7" height="6" rx="1"/><path d="M6.5 9v5a2 2 0 0 0 2 2H14"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.5M3 12h.5M3 18h.5"/>',
  validate: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 12l3 3 5-6"/>',
  sync: '<path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 12a8 8 0 0 1-14 5.3L4 15"/><path d="M20 4v5h-5M4 20v-5h5"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  arrows: '<path d="M4 8h14l-3-3M20 16H6l3 3"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  power: '<path d="M12 3v8"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/>',
  expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>',
  logs: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 10h8M8 14h8M8 18h5"/>',
  log: '<path d="M4 5h16M4 10h12M4 15h16M4 20h9"/>', // every log affordance; `terminal` only for a real terminal
  save: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
  edit: '<path d="M12 20h9M16 3l5 5-12 12H4v-5zM14 5l5 5"/>',
  convert: '<path d="M4 7h11l-3-3M20 17H9l3 3"/>',
  shift: '<path d="M20 12a8 8 0 1 1-3-6.2"/><path d="M20 4v5h-5"/>',
  average: '<path d="M4 8h16M4 16h16M12 4v4M12 16v4"/>',
  braces: '<path d="M8 4c-2 0-3 1-3 3v2c0 1.5-1 3-2 3 1 0 2 1.5 2 3v2c0 2 1 3 3 3M16 4c2 0 3 1 3 3v2c0 1.5 1 3 2 3-1 0-2 1.5-2 3v2c0 2-1 3-3 3"/>',
  snr: '<path d="M3 17l5-6 4 4 7-9"/><circle cx="17" cy="16" r="3"/><path d="M19.5 18.5L22 21"/>',
  fill: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 12h10M13 9l3 3-3 3"/>',
  split: '<path d="M12 20v-7M12 13L6 5M12 13l6-8M4 5h4M16 5h4"/>',
  pipeline: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 12h10M12 8v8"/>',
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  columns: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
  filter: '<path d="M4 5h16M7 12h10M10 19h4"/>',
  more: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.5M7 16.5h.5"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  fold: '<path d="M9 6l6 6-6 6"/>',
  unfold: '<path d="M15 6l-6 6 6 6"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
};

export function icon(name, cls = "") {
  const body = P[name] || P.info;
  return `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

export const LOGO = `<img class="logo" src="assets/favicon.svg" alt="" width="36" height="36">`;

// Project and target links.
export function parseRoute(hash) {
  const [path, query = ""] = hash.replace(/^#\//, "").split("?");
  const params = new URLSearchParams(query);
  return { view: path, project: params.get("p") || params.get("project"), target: params.get("t"), plan: params.get("plan"), unit: params.get("unit") };
}

export function routeHash(view, project, target) {
  const params = new URLSearchParams();
  if (project) params.set("p", project);
  if (target) params.set("t", target);
  return `#/${view}${params.size ? `?${params}` : ""}`;
}

export function chooseTarget(targets, ...preferred) {
  return preferred.find((id) => targets.some((t) => t.id === id)) || targets[0]?.id || null;
}
