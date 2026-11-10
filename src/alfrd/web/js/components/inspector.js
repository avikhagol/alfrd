// Chat inspector shell: hidden until asked for. One Inspect toggle at every width; its state is a
// browser preference. Docked beside the conversation above 1280 px, an overlay at or below it.
// Alt+Shift+I toggles, Escape closes and focus goes back to where it was. A background update
// (events, polling, a task changing) never opens it. No DOM lookups at import: Node tests load this alone.
import { icon } from "../utils/dom.js";
import { activeRequest } from "../data/chat.js";
import { empty } from "./state_view.js";
import { DEFAULTS, readPrefs, writePrefs } from "../data/prefs.js";

export const OPEN_CLASS = "chat-inspect-open";
export const DOCK_MIN = 1281; // docked above 1280 px
export const PREF_KEY = "ui.chat_inspector";
const FALLBACK_KEY = "alfrd-studio:chat.inspector";

export const TABS = Object.freeze([
  { id: "graph", label: "Graph", title: "No task graph yet", next: "Tasks appear here when the orchestrator plans a request." },
  { id: "files", label: "Files", title: "File browsing is unavailable", next: "Use artifact links in messages." },
  { id: "goals", label: "Goals", title: "Goal browsing is unavailable", next: "Review the recorded request in messages." },
]);

/** "docked" above 1280 px, otherwise "overlay". */
export const layout = (width) => (width >= DOCK_MIN ? "docked" : "overlay");

/** A tab's F2 state; deferred tabs explain the available alternative. */
export function placeholderHtml(id) {
  const tab = TABS.find((t) => t.id === id) || TABS[0];
  return empty(tab.title, tab.next);
}

export function tabsHtml(active = TABS[0].id) {
  const on = TABS.some((t) => t.id === active) ? active : TABS[0].id;
  return `<div class="row gap"><div class="chat-insp-tabs seg" role="tablist" aria-label="Inspector">${TABS.map((t) =>
    `<button type="button" role="tab" id="chat-insp-tab-${t.id}" data-insp-tab="${t.id}" aria-selected="${t.id === on}" aria-controls="chat-insp-panel" tabindex="${t.id === on ? 0 : -1}">${t.label}</button>`).join("")}</div>${on === "files" ? `<span class="grow"></span><button type="button" class="icon-btn sm" data-chat="close-files" aria-label="Close Files" title="Close Files (Esc)">${icon("close")}</button>` : ""}</div>
    <div id="chat-insp-panel" role="tabpanel" aria-labelledby="chat-insp-tab-${on}">${placeholderHtml(on)}</div>`;
}

/** Files is temporary: closing it returns to the last non-Files inspector view. */
export function createInspectorTabs() {
  let active = "graph", previous = "graph";
  return {
    active: () => active,
    select(id) {
      if (!TABS.some((tab) => tab.id === id)) return;
      if (id === "files" && active !== "files") previous = active;
      active = id;
    },
    closeFiles() {
      if (active !== "files") return false;
      active = previous;
      return true;
    },
  };
}

/**
 * The saved open/closed flag. It goes through data/prefs.js once that registry knows PREF_KEY;
 * until then (prefs.js rejects unknown keys) a separate localStorage key holds it. Default: closed.
 */
export function prefsStore({ storage = globalThis.localStorage } = {}) {
  const registered = PREF_KEY in DEFAULTS;
  return {
    get() {
      if (registered) return readPrefs()[PREF_KEY] === true;
      try { return storage?.getItem(FALLBACK_KEY) === "1"; } catch { return false; }
    },
    set(open) {
      if (registered) { writePrefs({ [PREF_KEY]: Boolean(open) }); return; }
      try { storage?.setItem(FALLBACK_KEY, open ? "1" : "0"); } catch { /* storage optional */ }
    },
  };
}

export const isToggleKey = (e) => e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && String(e.key).toLowerCase() === "i";

/**
 * Controller. `chat` has classList, `button` setAttribute, `panel` contains(); `focus(el)` and `active()`
 * default to the DOM. `store` is prefsStore(). Nothing here opens the inspector except open()/toggle()
 * from a user action and the saved preference at start.
 */
export function createInspector({ chat, button, panel, store = prefsStore(), width = () => globalThis.innerWidth || 0, active = () => globalThis.document?.activeElement || null, focus = (el) => el?.focus?.() }) {
  let opener = null;
  const sync = (open) => {
    chat.classList.toggle(OPEN_CLASS, open);
    button?.setAttribute("aria-expanded", String(open));
    if (chat.dataset) chat.dataset.inspect = layout(width());
  };
  const api = {
    isOpen: () => chat.classList.contains(OPEN_CLASS),
    layout: () => layout(width()),
    /** The saved flag applies at mount; focus stays where it is. */
    restore() { sync(api.layout() === "docked" && Boolean(store.get())); return api.isOpen(); },
    open() {
      if (api.isOpen()) return;
      opener = active();
      sync(true); store.set(true);
      if (api.layout() === "overlay") focus(panel);
    },
    close({ restoreFocus = true } = {}) {
      if (!api.isOpen()) return;
      sync(false); store.set(false);
      if (restoreFocus) focus(opener?.isConnected === false ? button : opener || button);
      opener = null;
    },
    toggle() { if (api.isOpen()) api.close(); else api.open(); },
    /** Poll, event stream and session refresh call this: it changes nothing. */
    backgroundUpdate() { return api.isOpen(); },
    /** Returns true when the key was handled. */
    onKeydown(e) {
      if (isToggleKey(e)) { e.preventDefault?.(); api.toggle(); return true; }
      if (e.key === "Escape" && !e.defaultPrevented && api.isOpen() && (panel?.contains?.(e.target) || api.layout() === "overlay")) {
        e.preventDefault?.(); api.close(); return true;
      }
      return false;
    },
  };
  return api;
}

/** Current follows activity; explicit choices persist. */
export function createRequestScope() {
  let choice = "latest";
  return {
    select(value) { choice = value; },
    label(state) {
      const id = this.request(state);
      return choice === "all" ? "All requests" : choice === "latest" ? `Latest request · ${id || "None yet"}` : id || "No requests yet";
    },
    items(state, onSelect) {
      return [["latest", "Latest request"], ["all", "All requests"],
        ...(state?.requests || []).slice().reverse().map((r) => [r.id, `${r.id} · ${String(r.title || r.text || "Request").replace(/\s+/g, " ").slice(0, 90)}`])]
        .map(([value, label]) => ({ label, hint: value === choice ? "Selected" : "", run: () => { this.select(value); onSelect(value); } }));
    },
    isAll() { return choice === "all"; },
    request(state) { return choice === "all" ? null : choice === "latest" ? state?.requests?.at(-1)?.id || null : choice === "current" ? activeRequest(state)?.id || null : choice; },
  };
}
