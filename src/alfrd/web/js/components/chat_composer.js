// The chat composer (Chat page and chat dock): a textarea where Enter sends, Shift+Enter adds a line,
// `@` at the start completes agent keys and `/` at the start lists the slash commands (listbox, arrows,
// Enter/Tab inserts the pick, Esc closes). Commands (/setup, /help, /new, /graph) run here and are never sent.
import { $, esc, icon } from "../utils/dom.js";
import { agentOptions, applyMention, mentionAt, parseMention, parseSlash, slashAt, slashOptions } from "../data/chat.js";
import { attachmentButtonHtml, attachmentTrayHtml, mountAttachmentTray, bindAttachmentPaste } from "./attach_tray.js";

/** Grow `textarea` to its content, capped at min(240px, 40dvh); beyond that it scrolls internally. */
export function autogrow(textarea) {
  if (!textarea?.style) return;
  // Placeholder wrapping must never make an empty composer taller than its one row.
  if (!textarea.value) { textarea.style.height = ""; textarea.style.overflowY = "hidden"; return; }
  const view = globalThis.visualViewport?.height || globalThis.innerHeight || 800;
  const cap = Math.round(Math.min(240, view * 0.4));
  textarea.style.height = "auto";
  const full = textarea.scrollHeight || 0;
  textarea.style.height = `${Math.min(full, cap)}px`;
  textarea.style.overflowY = full > cap ? "auto" : "hidden";
}

/** The form's HTML; ids start with `id` (e.g. "chat" → #chat-input, #chat-mentions, #chat-send, #chat-stop …). */
export function composerHtml(id, { attachments = false } = {}) {
  return `<form class="chat-composer" id="${id}-form" autocomplete="off">
    <div class="chat-composer-row">
    <div class="chat-input-wrap">
      <label class="sr-only" for="${id}-input">Message</label>
      <textarea id="${id}-input" class="input" rows="1" placeholder="To orchestrate, type a message; @agent for direct, /help for commands" title="Enter sends · Shift+Enter adds a line" role="combobox"
        aria-autocomplete="list" aria-expanded="false" aria-controls="${id}-mentions" aria-describedby="${id}-send-reason ${id}-send-error${attachments ? ` ${id}-image-warning` : ""}"></textarea>
      <ul id="${id}-mentions" class="chat-mentions" role="listbox" aria-label="Agents and commands" hidden></ul>
    </div>
    <button type="button" class="icon-btn chat-schedule-toggle" id="${id}-schedule-toggle" title="Schedule messages" aria-label="Schedule messages" aria-pressed="false" aria-expanded="false" aria-controls="${id}-schedule"><span aria-hidden="true">◷</span></button>
    ${attachments ? attachmentButtonHtml(id) : ""}
    <button type="button" class="btn sm chat-stop" id="${id}-stop" hidden>${icon("stop")} <span data-stop-label>Stop request</span></button>
    <button type="submit" class="btn primary sm chat-send" id="${id}-send">${icon("play")} <span data-send-label>Send</span></button>
    </div>
    <div class="chat-schedule" id="${id}-schedule" hidden></div>
    ${attachments ? attachmentTrayHtml(id) + `<p id="${id}-image-warning" class="chat-composer-note warn-t small" role="status"></p>` : ""}
    <p id="${id}-send-reason" class="chat-composer-note muted small" role="status"></p>
    <div class="chat-error-row"><p id="${id}-send-error" class="chat-composer-note fail-t small" role="alert"></p><button type="button" class="btn sm" id="${id}-retry" hidden>Retry</button></div>
  </form>`;
}

/** Wire the composer inside `root`. opts: agents() → {key: preset}, orchestrator() → key, send(), onDraft(text),
 *  where ("chat" | "dock": which slash commands exist), onCommand(cmd, args), onError(message), and optionally
 *  stopState() → {visible, disabled, label, reason} with onStop() (the host owns cancellation).
 *  Returns {input, close, refresh}; refresh() re-reads height, target, Stop and Retry state. */
export function bindComposer(root, id, { agents, orchestrator, send, onDraft = () => {}, where = "chat", onCommand = () => {}, onError = () => {}, stopState = null, onStop = () => {} }) {
  let menu = { at: null, items: [], active: 0, slash: false };
  const input = $(`#${id}-input`, root), list = $(`#${id}-mentions`, root), form = $(`#${id}-form`, root);
  const stop = $(`#${id}-stop`, root), retry = $(`#${id}-retry`, root);
  const composing = (e) => e.isComposing || e.keyCode === 229;
  const route = () => {
    const text = input.value;
    if (form.dataset?.scheduled === "true" && text[0] !== "/") return "Queued for one orchestrator plan; @mentions are preferences";
    if (text[0] === "/") return "Command: runs here, not sent";
    const m = parseMention(text.trim(), agents());
    if (m?.error) return m.error;
    if (m) return `To @${m.agent} directly`;
    const o = orchestrator();
    return o ? `To orchestrator @${o}` : "";
  };
  const refresh = () => {
    autogrow(input);
    input.placeholder = `${route() || "To orchestrate"}; @agent for direct, /help for commands`;
    if (stop) {
      const s = stopState?.() || {};
      stop.hidden = !s.visible;
      stop.disabled = !!s.disabled; stop.setAttribute("aria-disabled", String(!!s.disabled));
      stop.title = s.reason || "";
      const label = $("[data-stop-label]", stop); if (label) label.textContent = s.label || "Stop request";
    }
    if (retry) { const err = $(`#${id}-send-error`, root)?.textContent || ""; retry.hidden = !/^(Couldn't send|Not sent)/.test(err); }
  };
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
    refresh();
  };
  const close = () => { menu = { at: null, items: [], active: 0 }; list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); };
  const run = (cmd, args = "") => { input.value = ""; onDraft(""); close(); refresh(); onCommand(cmd, args); };
  // Picking a hint (Enter, Tab or pointer) only inserts it; a following Enter executes a slash command.
  const pick = (key) => {
    const next = menu.slash ? { text: `/${key} `, caret: key.length + 2 } : applyMention(input.value, menu.at, key);
    input.value = next.text; input.setSelectionRange(next.caret, next.caret);
    onDraft(input.value); close(); refresh(); input.focus();
  };
  const submit = () => {
    const sl = parseSlash(input.value, where);
    if (sl?.error) { onError(sl.error); return; }
    if (sl) { run(sl.cmd, sl.args); return; }
    const m = parseMention(input.value.trim(), agents());
    if (m?.error && form.dataset?.scheduled !== "true") { onError(m.error); return; }
    if ($(`#${id}-send`, root)?.disabled) return; // empty, uploading or sending: the keyboard cannot bypass the button
    send();
  };
  list.addEventListener("mousedown", (e) => e.preventDefault()); // keep the caret in the textarea
  list.addEventListener("click", (e) => { const li = e.target.closest("[data-agent]"); if (li) pick(li.dataset.agent); });
  form.addEventListener("submit", (e) => { e.preventDefault(); e.stopPropagation(); submit(); });
  stop?.addEventListener("click", () => { if (!stop.disabled) onStop(); });
  retry?.addEventListener("click", () => submit());
  input.addEventListener("input", () => { onDraft(input.value); update(); });
  input.addEventListener("click", update);
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) close(); }, 150));
  input.addEventListener("keydown", (e) => {
    if (composing(e)) return; // IME: never send, pick or dismiss
    if (e.key === "Enter" && e.shiftKey) return; // newline, even with hints visible
    if (menu.items.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); menu.active = (menu.active + (e.key === "ArrowDown" ? 1 : -1) + menu.items.length) % menu.items.length; update(); return; }
      if ((e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.altKey) || e.key === "Tab") { e.preventDefault(); pick(menu.items[menu.active].key); return; }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
    }
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); submit(); }
  });
  input.addEventListener("keyup", (e) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) update(); });
  if (typeof ResizeObserver === "function") { let width = -1; new ResizeObserver(([e]) => { const w = Math.round(e.contentRect.width); if (w !== width) { width = w; autogrow(input); } }).observe(form); }
  refresh();
  return { input, close, refresh };
}

/** Optional A4 mount point; callers opt in by rendering composerHtml(..., {attachments:true}). */
export function mountComposerAttachments(root, id, options = {}) {
  const input = $(`#${id}-input`, root);
  const tray = mountAttachmentTray(root, { id, sendButton: $(`#${id}-send`, root), ...options });
  const unbind = input ? bindAttachmentPaste(input, tray) : () => {};
  autogrow(input);
  return { ...tray, destroy: () => { unbind(); tray.destroy(); } };
}
