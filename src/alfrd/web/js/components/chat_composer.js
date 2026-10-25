// The chat composer (Chat page and chat dock): a textarea where Enter sends, Shift+Enter adds a line,
// `@` at the start completes agent keys and `/` at the start lists the slash commands (listbox, arrows,
// Enter/Tab picks, Esc closes). Commands (/setup, /help, /new, /graph) run here and are never sent.
import { $, esc, icon } from "../utils/dom.js";
import { agentOptions, applyMention, mentionAt, parseSlash, slashAt, slashOptions } from "../data/chat.js";
import { attachmentTrayHtml, mountAttachmentTray, bindAttachmentPaste } from "./attach_tray.js";

/** The form's HTML; ids start with `id` (e.g. "chat" → #chat-input, #chat-mentions, #chat-send …). */
export function composerHtml(id, { rows = 3, hint = true, attachments = false } = {}) {
  return `<form class="chat-composer" id="${id}-form" autocomplete="off">
    <div class="chat-input-wrap">
      <label class="sr-only" for="${id}-input">Message</label>
      <textarea id="${id}-input" class="input" rows="${rows}" placeholder="Ask the orchestrator, @agent for one agent, /help for commands" role="combobox"
        aria-autocomplete="list" aria-expanded="false" aria-controls="${id}-mentions" aria-describedby="${id}-hint ${id}-send-error${attachments ? ` ${id}-image-warning` : ""}"></textarea>
      <ul id="${id}-mentions" class="chat-mentions" role="listbox" aria-label="Agents and commands" hidden></ul>
    </div>
    ${attachments ? attachmentTrayHtml(id) + `<p id="${id}-image-warning" class="warn-t small" role="status"></p>` : ""}
    <div class="row gap"><p id="${id}-hint" class="muted small grow">${hint ? "Enter sends · Shift+Enter adds a line · <code>@agent</code> at the start goes straight to that agent (no orchestrator call) · <code>/</code> commands: <code>/setup</code>, <code>/help</code>." : "Enter sends · <code>@agent</code> → one agent · <code>/</code> commands"}</p>
      <button type="submit" class="btn primary sm" id="${id}-send">${icon("play")} Send</button></div>
    <p id="${id}-send-error" class="fail-t small" role="alert"></p>
  </form>`;
}

/** Wire the composer inside `root`. opts: agents() → {key: preset}, orchestrator() → key, send(), onDraft(text),
 *  where ("chat" | "dock": which slash commands exist), onCommand(cmd, args), onError(message). */
export function bindComposer(root, id, { agents, orchestrator, send, onDraft = () => {}, where = "chat", onCommand = () => {}, onError = () => {} }) {
  let menu = { at: null, items: [], active: 0, slash: false };
  const input = $(`#${id}-input`, root), list = $(`#${id}-mentions`, root);
  const update = () => {
    const caret = input.selectionStart === input.selectionEnd ? input.selectionStart : -1;
    const sl = caret >= 0 ? slashAt(input.value, caret) : null;
    const at = sl || (caret >= 0 ? mentionAt(input.value, caret) : null);
    const items = sl ? slashOptions(sl.query, where).map((c) => ({ key: c.cmd, html: `<b class="mono">/${esc(c.cmd)}</b> <span class="muted small">${esc(c.hint)}</span>` }))
      : at ? agentOptions(agents(), at.query, orchestrator()).map((o) => ({ key: o.key, html: `<b class="mono">@${esc(o.key)}</b> <span>${esc(o.label)}</span>${o.orchestrator ? ' <span class="badge tone-muted"><span>orchestrator</span></span>' : ""}<span class="muted small">${esc(o.roles)}</span>` })) : [];
    menu = { at, items, slash: Boolean(sl), active: Math.min(menu.active, Math.max(0, items.length - 1)) };
    list.hidden = !items.length;
    input.setAttribute("aria-expanded", String(Boolean(items.length)));
    list.innerHTML = items.map((o, i) => `<li id="${id}-opt-${i}" role="option" aria-selected="${i === menu.active}" data-agent="${esc(o.key)}" class="${i === menu.active ? "on" : ""}">${o.html}</li>`).join("");
    if (items.length) input.setAttribute("aria-activedescendant", `${id}-opt-${menu.active}`); else input.removeAttribute("aria-activedescendant");
  };
  const close = () => { menu = { at: null, items: [], active: 0 }; list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); };
  const run = (cmd, args = "") => { input.value = ""; onDraft(""); close(); onCommand(cmd, args); };
  const pick = (key, complete = false) => {
    if (menu.slash && !complete) { run(key); return; }
    const next = menu.slash ? { text: `/${key} `, caret: key.length + 2 } : applyMention(input.value, menu.at, key);
    input.value = next.text; input.setSelectionRange(next.caret, next.caret);
    onDraft(input.value); close(); input.focus();
  };
  const submit = () => {
    const sl = parseSlash(input.value, where);
    if (!sl) send();
    else if (sl.error) onError(sl.error);
    else run(sl.cmd, sl.args);
  };
  list.addEventListener("mousedown", (e) => e.preventDefault()); // keep the caret in the textarea
  list.addEventListener("click", (e) => { const li = e.target.closest("[data-agent]"); if (li) pick(li.dataset.agent); });
  $(`#${id}-form`, root).addEventListener("submit", (e) => { e.preventDefault(); e.stopPropagation(); submit(); });
  input.addEventListener("input", () => { onDraft(input.value); update(); });
  input.addEventListener("click", update);
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) close(); }, 150));
  input.addEventListener("keydown", (e) => {
    if (menu.items.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); menu.active = (menu.active + (e.key === "ArrowDown" ? 1 : -1) + menu.items.length) % menu.items.length; update(); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(menu.items[menu.active].key, e.key === "Tab"); return; }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); submit(); }
  });
  input.addEventListener("keyup", (e) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) update(); });
  return { input, close };
}

/** Optional A4 mount point; callers opt in by rendering composerHtml(..., {attachments:true}). */
export function mountComposerAttachments(root, id, options = {}) {
  const input = $(`#${id}-input`, root);
  const tray = mountAttachmentTray(root, { id, sendButton: $(`#${id}-send`, root), ...options });
  const unbind = input ? bindAttachmentPaste(input, tray) : () => {};
  const textarea = input;
  const resize = () => { if (!textarea) return; textarea.style.height = "auto"; textarea.style.height = `${Math.min(textarea.scrollHeight, Math.round((globalThis.innerHeight || 800) * 0.4))}px`; };
  textarea?.addEventListener("input", resize); resize();
  return { ...tray, destroy: () => { unbind(); tray.destroy(); textarea?.removeEventListener("input", resize); } };
}
