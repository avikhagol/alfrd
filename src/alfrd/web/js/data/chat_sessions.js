import { needsMe } from "./inbox.js";

// Search only the list snapshot. Never fetch messages or change the selection.
export function filterSessions(sessions = [], query = "", needsOnly = false) {
  const needle = String(query).trim().toLocaleLowerCase();
  return (needsOnly ? needsMe(sessions) : sessions).filter((s) =>
    !needle || [s.title, s.id, s.last_message?.text].some((v) => String(v || "").toLocaleLowerCase().includes(needle)));
}

// One modal sheet at a time. Inert only siblings along the panel's ancestor path,
// preserving pre-existing inert flags (including the Studio shell's own overlays).
let releaseSheet = null;
export function chatSheet(panel, close, { focus = true } = {}) {
  if (releaseSheet) { const previous = releaseSheet; previous({ restore: false }); previous.close?.(); }
  const opener = document.activeElement, inert = [], role = panel.getAttribute("role");
  for (let node = panel; node.parentElement; node = node.parentElement) {
    for (const sibling of node.parentElement.children) if (sibling !== node && !sibling.inert) { sibling.inert = true; inert.push(sibling); }
  }
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true");
  const controls = () => [...panel.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')].filter((n) => !n.disabled && n.tabIndex >= 0 && n.getClientRects().length);
  const key = (e) => {
    if (e.defaultPrevented || (e.key === "Escape" && e.target.closest?.("[data-rename]"))) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    if (e.key === "Tab") {
      const items = controls(), first = items[0] || panel, last = items.at(-1) || panel;
      if (e.shiftKey && (document.activeElement === first || document.activeElement === panel || !panel.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
    }
  };
  panel.addEventListener("keydown", key);
  if (focus) (controls()[0] || panel).focus();
  const release = ({ restore = true } = {}) => {
    if (releaseSheet !== release) return;
    panel.removeEventListener("keydown", key); inert.forEach((n) => { n.inert = false; });
    panel.removeAttribute("aria-modal");
    if (role) panel.setAttribute("role", role); else panel.removeAttribute("role");
    releaseSheet = null;
    if (restore && opener?.isConnected) opener.focus();
  };
  release.close = close;
  releaseSheet = release;
  return release;
}

// The host supplies empty-session suggestions; picking one only edits the shared draft.
export function conversationStarter(box) {
  const empty = box.querySelector(".chat-flow")?.previousElementSibling;
  if (!empty || empty.hidden || empty.dataset.starter) return;
  empty.dataset.starter = "true";
  empty.innerHTML = `What would you like the agents to do? <button type="button" class="btn sm" data-chat-prompt="Explain this project">Explain this project</button> <button type="button" class="btn sm" data-chat-prompt="Plan a change">Plan a change</button>`;
}
