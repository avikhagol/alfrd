// Browser preferences: the one reader and writer of this browser's Studio preferences, the
// browser-scope `ui.*` flags of the server's ui_flags registry. They sit under one localStorage
// key. The keys of earlier builds are copied in once, then removed: alfrd-studio:prefs (pageSize,
// live), alfrd-studio:ui:chat.enabled and alfrd-studio:ui:notifications. No DOM or Flask here,
// so Node tests can load this file alone.

export const STORAGE_KEY = "alfrd-studio:prefs.v2";
export const LEGACY_KEYS = Object.freeze({
  prefs: "alfrd-studio:prefs",
  chat: "alfrd-studio:ui:chat.enabled",
  notifications: "alfrd-studio:ui:notifications",
});
const ROWS_PER_PAGE = [10, 25, 50, 100];

export const DEFAULTS = Object.freeze({
  "ui.live_updates": true,
  "ui.rows_per_page": 25,
  "ui.experimental_chat": false,
  "ui.browser_notifications": false,
});

const VALID = {
  "ui.live_updates": (v) => typeof v === "boolean",
  "ui.rows_per_page": (v) => ROWS_PER_PAGE.includes(v),
  "ui.experimental_chat": (v) => typeof v === "boolean",
  "ui.browser_notifications": (v) => typeof v === "boolean",
};

function read(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? undefined : JSON.parse(raw);
  } catch {
    return undefined;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function drop(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    /* storage optional */
  }
}

/** The values the earlier keys hold, in the flag names; only those this build understands. */
export function legacyValues() {
  const out = {};
  const old = read(LEGACY_KEYS.prefs);
  if (old && typeof old === "object") {
    if (VALID["ui.rows_per_page"](old.pageSize)) out["ui.rows_per_page"] = old.pageSize;
    if (VALID["ui.live_updates"](old.live)) out["ui.live_updates"] = old.live;
  }
  const chat = read(LEGACY_KEYS.chat);
  if (VALID["ui.experimental_chat"](chat)) out["ui.experimental_chat"] = chat;
  const notes = read(LEGACY_KEYS.notifications);
  if (notes && VALID["ui.browser_notifications"](notes.browser)) out["ui.browser_notifications"] = notes.browser;
  return out;
}

/** The stored values, valid ones only. First use copies the old keys in and removes them. */
function stored() {
  const saved = read(STORAGE_KEY);
  if (saved && saved.version === 2 && saved.values && typeof saved.values === "object") return pick(saved.values);
  const values = legacyValues();
  // remove the old keys only once the copy is saved: a full storage keeps them for the next try
  if (write(STORAGE_KEY, { version: 2, values })) Object.values(LEGACY_KEYS).forEach(drop);
  return values;
}

function pick(values) {
  const out = {};
  for (const key of Object.keys(DEFAULTS)) if (VALID[key](values[key])) out[key] = values[key];
  return out;
}

/** Every browser preference, defaults filled in. */
export function readPrefs() {
  return { ...DEFAULTS, ...stored() };
}

/** Saves the given flags (`{"ui.rows_per_page": 50}`) and returns every preference. A bad key or value throws. */
export function writePrefs(patch) {
  const next = stored();
  for (const [key, value] of Object.entries(patch || {})) {
    if (!(key in DEFAULTS)) throw new TypeError(`unknown browser preference ${key}`);
    if (!VALID[key](value)) throw new TypeError(`${key} has an invalid value`);
    next[key] = value;
  }
  write(STORAGE_KEY, { version: 2, values: next });
  return readPrefs();
}
