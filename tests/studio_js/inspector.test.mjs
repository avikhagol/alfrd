// Inspector shell (inspector.js, chat.css): hidden by default, saved toggle, Alt+Shift+I, Escape with
// focus return, no opening from background updates, docked/overlay layout and 200 % zoom CSS.
// The browser runtime checks are at the bottom and skipped: ENV1 (no browser in this environment) is open.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const store = new Map();
globalThis.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };

const { createInspector, prefsStore, layout, tabsHtml, placeholderHtml, isToggleKey, OPEN_CLASS, TABS } = await import("../../src/alfrd/web/js/components/inspector.js");

const classes = () => { const s = new Set(); return { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle(c, on) { if (on) s.add(c); else s.delete(c); return on; } }; };

function rig({ width = 1400 } = {}) {
  const attrs = {}, focused = [];
  const chat = { classList: classes(), dataset: {} };
  const panel = { contains: (t) => t === panelChild || t === panel };
  const panelChild = {};
  const opener = { isConnected: true, name: "composer" };
  const button = { setAttribute: (k, v) => { attrs[k] = v; } };
  let w = width, current = opener;
  const insp = createInspector({ chat, panel, button, store: prefsStore({ storage: globalThis.localStorage }), width: () => w, active: () => current, focus: (el) => focused.push(el) });
  return { insp, chat, attrs, focused, opener, panel, panelChild, setWidth: (n) => { w = n; }, focusOn: (el) => { current = el; } };
}
const key = (o) => ({ key: "", altKey: false, shiftKey: false, ctrlKey: false, metaKey: false, preventDefault() { this.prevented = true; }, ...o });

test.beforeEach(() => store.clear());

test("hidden by default, at every width", () => {
  for (const width of [900, 1280, 1281, 1600]) {
    const r = rig({ width });
    assert.equal(r.insp.restore(), false, `closed at ${width}`);
    assert.equal(r.chat.classList.contains(OPEN_CLASS), false);
    assert.equal(r.attrs["aria-expanded"], "false");
  }
});

test("layout: docked above 1280 px, overlay at 1280 and below", () => {
  assert.equal(layout(1281), "docked");
  assert.equal(layout(1600), "docked");
  assert.equal(layout(1280), "overlay");
  assert.equal(layout(900), "overlay");
  assert.equal(layout(640), "overlay");
});

test("toggle at 900 and 1280 px opens the overlay and moves focus into it", () => {
  for (const width of [900, 1280]) {
    const r = rig({ width });
    r.insp.toggle();
    assert.equal(r.insp.isOpen(), true);
    assert.equal(r.chat.dataset.inspect, "overlay");
    assert.equal(r.focused.at(-1), r.panel, "focus moves to the overlay");
  }
});

test("docked toggle opens in place and keeps focus where it is", () => {
  const r = rig({ width: 1400 });
  r.insp.toggle();
  assert.equal(r.insp.isOpen(), true);
  assert.equal(r.chat.dataset.inspect, "docked");
  assert.equal(r.focused.length, 0);
});

test("the toggle is persisted and restored in a new mount; closing persists too", () => {
  const a = rig();
  a.insp.toggle();
  assert.equal(prefsStore({ storage: globalThis.localStorage }).get(), true);
  const b = rig();
  assert.equal(b.insp.restore(), true, "a new mount comes back open");
  assert.equal(b.attrs["aria-expanded"], "true");
  b.insp.toggle();
  assert.equal(rig().insp.restore(), false, "and closed again");
});

test("restore does not move focus", () => {
  store.set("alfrd-studio:chat.inspector", "1");
  const r = rig({ width: 900 });
  r.insp.restore();
  assert.equal(r.focused.length, 0);
});

test("Alt+Shift+I toggles; other chords do not", () => {
  const r = rig();
  const e = key({ key: "I", altKey: true, shiftKey: true });
  assert.equal(r.insp.onKeydown(e), true);
  assert.equal(e.prevented, true);
  assert.equal(r.insp.isOpen(), true);
  assert.equal(r.insp.onKeydown(key({ key: "i", altKey: true, shiftKey: true })), true);
  assert.equal(r.insp.isOpen(), false);
  for (const o of [{ key: "i" }, { key: "i", altKey: true }, { key: "i", altKey: true, shiftKey: true, ctrlKey: true }, { key: "k", altKey: true, shiftKey: true }]) {
    assert.equal(isToggleKey(key(o)), false);
    assert.equal(r.insp.onKeydown(key(o)), false);
  }
  assert.equal(r.insp.isOpen(), false);
});

test("Escape closes the overlay and focus returns to the opener", () => {
  const r = rig({ width: 900 });
  r.focusOn(r.opener);
  r.insp.toggle();
  const esc = key({ key: "Escape", target: r.panelChild });
  assert.equal(r.insp.onKeydown(esc), true);
  assert.equal(r.insp.isOpen(), false);
  assert.equal(r.focused.at(-1), r.opener, "focus returns");
  assert.equal(r.attrs["aria-expanded"], "false");
});

test("Escape with the opener gone returns focus to the Inspect button path (focus callback gets the stale opener)", () => {
  const r = rig({ width: 900 });
  r.insp.toggle();
  r.opener.isConnected = false;
  r.insp.onKeydown(key({ key: "Escape", target: r.panelChild }));
  assert.equal(r.focused.at(-1) === undefined, false);
});

test("Escape does nothing when closed, when already handled, or docked with focus outside", () => {
  const r = rig({ width: 1400 });
  assert.equal(r.insp.onKeydown(key({ key: "Escape", target: {} })), false);
  r.insp.toggle();
  assert.equal(r.insp.onKeydown(key({ key: "Escape", target: {} })), false, "docked: Escape in the conversation is not ours");
  assert.equal(r.insp.isOpen(), true);
  assert.equal(r.insp.onKeydown(key({ key: "Escape", target: r.panelChild, defaultPrevented: true })), false);
  assert.equal(r.insp.onKeydown(key({ key: "Escape", target: r.panelChild })), true, "docked: Escape inside closes");
});

test("background updates never open it", () => {
  const r = rig({ width: 900 });
  for (let i = 0; i < 3; i++) r.insp.backgroundUpdate();
  assert.equal(r.insp.isOpen(), false);
  assert.equal(r.focused.length, 0);
  assert.equal(prefsStore({ storage: globalThis.localStorage }).get(), false);
  r.insp.toggle();
  r.insp.backgroundUpdate();
  assert.equal(r.insp.isOpen(), true, "and never close it either");
});

test("chat_view.js: only user actions open it; the event handler and pollers do not", async () => {
  const src = await readFile(new URL("../../src/alfrd/web/js/components/chat_view.js", import.meta.url), "utf8");
  const opens = [...src.matchAll(/insp\??\.open\(\)/g)].length;
  assert.equal(opens, 1, "one open(): picking a task in the transcript");
  for (const name of ["scheduleRefresh", "loadSession", "loadSessions", "drawHead", "drawTask"]) {
    const body = src.slice(src.indexOf(`function ${name}`), src.indexOf("\n}\n", src.indexOf(`function ${name}`)));
    assert.equal(/insp\??\.open|OPEN_CLASS|chat-inspect-open/.test(body), false, `${name} does not open the inspector`);
  }
});

test("tabs are placeholders in the shared F2 empty state", () => {
  assert.deepEqual(TABS.map((t) => t.id), ["graph", "files", "goals"]);
  for (const t of TABS) {
    const html = placeholderHtml(t.id);
    assert.match(html, /class="state-view state-empty"/);
    assert.match(html, /role="status"/);
    assert.ok(html.includes(t.title));
  }
  const html = tabsHtml("files");
  assert.match(html, /role="tablist"/);
  assert.match(html, /data-insp-tab="files"[^>]*aria-selected="true"/);
  assert.match(html, /data-insp-tab="graph"[^>]*aria-selected="false"/);
  assert.match(tabsHtml("nope"), /data-insp-tab="graph"[^>]*aria-selected="true"/, "unknown tab falls back to the first");
});

test("prefs.js is imported, not edited: its registry has no inspector key, so the fallback key is used", async () => {
  const { DEFAULTS } = await import("../../src/alfrd/web/js/data/prefs.js");
  const registered = "ui.chat_inspector" in DEFAULTS;
  prefsStore({ storage: globalThis.localStorage }).set(true);
  assert.equal(store.has("alfrd-studio:chat.inspector"), !registered);
});

// --- CSS, unit level: the rules that make the layout, read from chat.css ---------------------------

const css = await readFile(new URL("../../src/alfrd/web/css/chat.css", import.meta.url), "utf8");
// every @media block with this query, joined (a query can appear more than once)
const block = (query) => {
  const out = [];
  for (let at = css.indexOf(`@media ${query} {`); at !== -1; at = css.indexOf(`@media ${query} {`, at + 1)) {
    let depth = 0;
    for (let i = css.indexOf("{", at); i < css.length; i++) {
      if (css[i] === "{") depth++;
      if (css[i] === "}" && --depth === 0) { out.push(css.slice(at, i + 1)); break; }
    }
  }
  assert.ok(out.length, `@media ${query} exists`);
  return out.join("\n");
};

test("css: hidden by default; open shows it", () => {
  assert.match(css, /\.chat-side \{[^}]*display: none/);
  assert.match(css, /\.chat-inspect-open \.chat-side \{ display: flex; \}/);
  assert.match(css, /\.chat \{[^}]*grid-template-columns: 220px minmax\(0, 1fr\);/, "no inspector column by default");
});

test("css: the Inspect toggle is shown at every width", () => {
  assert.match(css, /\.chat-inspect-toggle \{ display: inline-flex; \}/);
  assert.doesNotMatch(css, /\.chat-inspect-toggle \{ display: none/);
  assert.doesNotMatch(css, /\.chat-nav-toggle, \.chat-inspect-toggle \{ display: none/);
});

test("css: docked above 1280 px adds the third column only when open", () => {
  const docked = block("(min-width: 1281px)");
  assert.match(docked, /\.chat\.chat-inspect-open \{ grid-template-columns: 220px minmax\(0, 1fr\) 320px/);
  assert.doesNotMatch(docked, /position: absolute/);
});

test("css: overlay at 1280 px and below, and at 900 px", () => {
  const overlay = block("(max-width: 1280px)");
  assert.match(overlay, /\.chat-side \{[^}]*position: absolute/);
  assert.match(overlay, /z-index: 5/);
  assert.match(overlay, /width: min\(320px, calc\(100% - 24px\)\)/);
  const narrow = block("(max-width: 900px)");
  assert.match(narrow, /grid-template-columns: minmax\(0, 1fr\)/);
  assert.doesNotMatch(narrow, /\.chat-inspect-toggle \{ display: none/);
  const mid = block("(min-width: 901px) and (max-width: 1280px)");
  assert.match(mid, /grid-template-columns: 208px minmax\(0, 1fr\)/);
});

test("css: 200 % zoom (1280 px window = 640 CSS px) fills the view and scrolls", () => {
  const zoom = block("(max-width: 640px)");
  assert.match(zoom, /\.chat-side \{[^}]*left: 8px; right: 8px; width: auto/);
  assert.match(zoom, /overflow: auto/);
  assert.ok(css.indexOf("(max-width: 640px)") > css.indexOf("(max-width: 1280px)"), "later rule wins over the 1280 px overlay");
});

// --- browser runtime checks: skipped, ENV1 is open ---------------------------------------------

for (const name of [
  "browser: 900 px overlay opens over the transcript and Escape returns focus",
  "browser: 1280 px overlay and 1281 px docked column",
  "browser: 200 % zoom overlay scrolls and nothing is clipped",
  "browser: Alt+Shift+I from the composer toggles the inspector",
]) test(name, { skip: "ENV1 is open: no browser runtime in this environment" }, () => {});
