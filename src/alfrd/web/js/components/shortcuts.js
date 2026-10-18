// Keyboard shortcut reference (? or the palette's "Keyboard shortcuts"), written with
// this platform's modifiers: ⌘ ⌥ ⇧ on macOS, Ctrl Alt Shift elsewhere.

import { esc, icon, keyLabel, loadCss } from "../utils/dom.js";

loadCss("css/lazy.css");

/** [group, [[keys, what], …]]; "Ctrl+" means Cmd on macOS (both work). */
export const SHORTCUTS = [
  ["Anywhere (not while typing in a field)", [
    ["?", "This list"],
    ["Ctrl+K", "Command palette: go to a view, target or file, run an action"],
    ["Ctrl+Alt+N", "New target (server projects)"],
    ["Alt+Shift+S", "Collapse or expand the sidebar (wide windows)"],
    ["Alt+Shift+C", "Compact workflow mode: only the canvas, with run controls"],
    ["Alt+Shift+A", "Open or close the chat dock beside the current view (server projects; also from its message field)"],
    ["Esc", "Close a dialog or menu"],
  ]],
  ["Workflow canvas (click it or Tab to it)", [
    ["Arrow keys", "Select the previous or next step"],
    ["Enter", "Select the step (while editing: focus its actions)"],
    ["Shift+F10 / Menu", "Menu of the selected step (same as right-click)"],
    ["N", "Add a step after the selected one"],
    ["E", "Configure the selected step"],
    ["C", "Connect: arrows pick the step that waits, Enter connects, Esc cancels"],
    ["Delete", "Delete the selected step (asks first)"],
    ["+ / −", "Zoom in / out"],
    ["0", "Fit to view"],
    ["F", "Fullscreen on / off"],
  ]],
  ["Menus and lists", [
    ["↑ ↓ Home End", "Move between items"],
    ["Enter / Space", "Choose"],
    ["Tab", "Leave the menu"],
  ]],
  ["Settings · alfrd.yaml", [["Ctrl+S", "Save alfrd.yaml (in the editor)"]]],
];

export function shortcutRows(mac) {
  return SHORTCUTS.map(([group, rows]) => [group, rows.map(([keys, what]) => [keyLabel(keys, mac), what])]);
}

export function openShortcuts(ctx) {
  ctx.modal(`<header class="modal-h"><h2 id="keys-title">${icon("info")} Keyboard shortcuts</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b keys-ref">${shortcutRows().map(([group, rows]) => `<h3>${esc(group)}</h3>
      <dl>${rows.map(([keys, what]) => `<div><dt>${keys.split(" / ").map((k) => `<kbd>${esc(k)}</kbd>`).join(" or ")}</dt><dd>${esc(what)}</dd></div>`).join("")}</dl>`).join("")}
      <p class="muted small">Browser shortcuts (reload, back, find, tabs) are left alone. Right-click actions on the canvas all have a key or menu equivalent.</p></div>`,
  (root) => root.setAttribute("aria-labelledby", "keys-title"), "keys-modal");
}
