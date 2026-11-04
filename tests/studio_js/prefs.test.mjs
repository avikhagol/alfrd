// data/prefs.js: the browser preferences have one reader and writer; the keys of earlier builds migrate once.
import test from "node:test";
import assert from "node:assert/strict";

class FakeStorage {
  constructor() { this.items = new Map(); this.failWrites = false; }
  getItem(k) { return this.items.has(k) ? this.items.get(k) : null; }
  setItem(k, v) { if (this.failWrites) throw new Error("QuotaExceededError"); this.items.set(k, String(v)); }
  removeItem(k) { this.items.delete(k); }
}
const storage = new FakeStorage();
globalThis.localStorage = storage;

const { readPrefs, writePrefs, legacyValues, STORAGE_KEY, LEGACY_KEYS, DEFAULTS } = await import("../../src/alfrd/web/js/data/prefs.js");

const reset = () => { storage.items.clear(); storage.failWrites = false; };
const put = (key, value) => storage.setItem(key, JSON.stringify(value));
const stored = () => JSON.parse(storage.getItem(STORAGE_KEY));

test("old keys migrate once and are removed", () => {
  reset();
  put(LEGACY_KEYS.prefs, { pageSize: 50, live: false, extra: "kept by nobody" });
  put(LEGACY_KEYS.chat, true);
  put(LEGACY_KEYS.notifications, { browser: true });
  assert.deepEqual(readPrefs(), {
    "ui.live_updates": false, "ui.rows_per_page": 50, "ui.experimental_chat": true, "ui.browser_notifications": true,
  });
  for (const key of Object.values(LEGACY_KEYS)) assert.equal(storage.getItem(key), null, `${key} removed`);
  assert.equal(stored().version, 2);
});

test("a migrated store wins over old keys written later (an old tab)", () => {
  reset();
  put(LEGACY_KEYS.prefs, { pageSize: 50, live: true });
  readPrefs();
  put(LEGACY_KEYS.prefs, { pageSize: 10, live: false });
  assert.equal(readPrefs()["ui.rows_per_page"], 50);
  assert.equal(readPrefs()["ui.live_updates"], true);
});

test("nothing stored gives the defaults and an empty store", () => {
  reset();
  assert.deepEqual(readPrefs(), { ...DEFAULTS });
  assert.deepEqual(stored(), { version: 2, values: {} });
});

test("invalid old values are ignored", () => {
  reset();
  put(LEGACY_KEYS.prefs, { pageSize: 12, live: "no" });
  put(LEGACY_KEYS.chat, "yes");
  put(LEGACY_KEYS.notifications, { browser: 1 });
  assert.deepEqual(legacyValues(), {});
  assert.deepEqual(readPrefs(), { ...DEFAULTS });
});

test("writes validate, merge and persist", () => {
  reset();
  assert.equal(writePrefs({ "ui.rows_per_page": 100 })["ui.rows_per_page"], 100);
  assert.equal(writePrefs({ "ui.live_updates": false })["ui.rows_per_page"], 100, "other keys kept");
  assert.equal(readPrefs()["ui.live_updates"], false);
  assert.throws(() => writePrefs({ "ui.rows_per_page": 12 }), TypeError);
  assert.throws(() => writePrefs({ "ui.live_updates": "yes" }), TypeError);
  assert.throws(() => writePrefs({ "ui.theme": "dark" }), /unknown browser preference/);
  assert.equal(readPrefs()["ui.rows_per_page"], 100, "a refused write changes nothing");
});

test("a full store keeps the old keys for the next try", () => {
  reset();
  put(LEGACY_KEYS.prefs, { pageSize: 50, live: true });
  storage.failWrites = true;
  assert.equal(readPrefs()["ui.rows_per_page"], 50, "the old values still apply");
  assert.notEqual(storage.getItem(LEGACY_KEYS.prefs), null, "old key kept");
  storage.failWrites = false;
  assert.equal(readPrefs()["ui.rows_per_page"], 50);
  assert.equal(storage.getItem(LEGACY_KEYS.prefs), null, "removed once the copy is saved");
});

test("unreadable storage falls back to the defaults", () => {
  reset();
  const getItem = storage.getItem;
  storage.getItem = () => { throw new Error("SecurityError"); };
  try {
    assert.deepEqual(readPrefs(), { ...DEFAULTS });
  } finally {
    storage.getItem = getItem;
  }
});
