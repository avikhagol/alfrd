import { $, storage } from "../utils/dom.js";
import { threadGroups } from "../data/chat.js";
import { mountActivity } from "./chat_activity.js";
import { renderMessageMarkdown } from "./handoff_markdown.js";
import { mountMailbox } from "./chat_mailbox.js";

// Stable request/message nodes preserve tail scroll, selections and disclosure focus.
export function mountThread(box, o) {
  let scope = "", groups = new Map(), controls, newer, currentState, empty;
  const pref = (id) => `ui:chat.history:${JSON.stringify([o.project(), o.sid(), id])}`;
  const protectedTree = (el) => el.contains(document.activeElement) || (String(window.getSelection?.() || "") && el.contains(window.getSelection()?.anchorNode))
    || [...el.querySelectorAll("textarea,input:not([type=radio])")].some((e) => e.value);
  function setOpen(row, open, save = false) {
    if (!open && (row.group.active || row.group.pending || protectedTree(row.el))) return;
    if (row.el.open !== open) { row.automatic = !save; row.el.open = open; }
    if (save) storage.set(pref(row.group.id), open);
  }
  function reset() {
    for (const r of groups.values()) { r.activity?.stop(); r.mailbox?.stop(); }
    groups = new Map(); box.replaceChildren();
    controls = document.createElement("div"); controls.className = "chat-history-tools row gap wrap";
    controls.innerHTML = `<button type="button" class="btn sm link" data-collapse>Collapse earlier turns</button><button type="button" class="btn sm link" data-expand>Expand earlier turns</button><button type="button" class="btn sm" data-new hidden>New activity</button><span class="muted small" data-thread-status role="status"></span><span class="sr-only" data-thread-phase aria-live="polite"></span>`;
    box.append(controls); newer = $("[data-new]", controls);
    $("[data-collapse]", controls).onclick = () => { for (const r of groups.values()) if (!r.group.open) setOpen(r, false, true); };
    $("[data-expand]", controls).onclick = () => { for (const r of groups.values()) setOpen(r, true, true); };
    newer.onclick = () => { box.scrollTop = box.scrollHeight; newer.hidden = true; };
    empty = document.createElement("p"); empty.className = "muted small"; empty.textContent = "No messages yet. Ask the team, or start with @agent."; box.append(empty);
  }
  box.addEventListener("scroll", () => { if (newer && box.scrollHeight - box.scrollTop - box.clientHeight < 48) newer.hidden = true; }, { passive: true });
  const api = {
    update(state, notes = []) {
      currentState = state;
      const next = JSON.stringify([o.project(), o.sid()]);
      if (next !== scope || !controls?.isConnected) { scope = next; reset(); }
      const following = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
      const anchor = [...groups.values()].find((r) => r.el.getBoundingClientRect().bottom > box.getBoundingClientRect().top);
      const before = anchor?.el.getBoundingClientRect().top;
      const data = threadGroups({ ...state, messages: [...(state?.messages || []), ...notes] });
      empty.hidden = Boolean(data.length);
      $("[data-thread-status]", controls).textContent = o.error?.() ? "Updates paused — reconnecting" : "";
      const phases = data.filter((g) => g.request).map((g) => `${g.request.text?.slice(0, 80) || "Request"}: ${g.request.status.replaceAll("_", " ")}`).join(". ");
      const phase = $("[data-thread-phase]", controls); if (phase.textContent !== phases) phase.textContent = phases;
      const ids = new Set(data.map((g) => g.id));
      for (const [id, r] of groups) if (!ids.has(id)) { r.activity?.stop(); r.mailbox?.stop(); r.el.remove(); groups.delete(id); }
      let changed = false;
      for (const group of data) {
        let row = groups.get(group.id);
        if (!row) {
          const el = document.createElement(group.request ? "details" : "section"); el.className = "chat-turn";
          if (group.request) { const summary = document.createElement("summary"); summary.className = "chat-turn-summary"; el.append(summary); }
          const content = document.createElement("div"); content.className = "chat-turn-content"; el.append(content); box.append(el);
          row = { el, content, group, messages: new Map(), automatic: false }; groups.set(group.id, row); changed = true;
          if (group.request) {
            const initialOpen = group.active || group.pending || storage.get(pref(group.id), group.open);
            row.automatic = Boolean(initialOpen); el.open = initialOpen;
            $("summary", el).addEventListener("click", (e) => { if (row.group.active || row.group.pending) e.preventDefault(); });
            el.addEventListener("toggle", () => {
              if (!el.open && (row.group.active || row.group.pending)) { row.automatic = true; el.open = true; return; }
              if (!row.automatic) storage.set(pref(group.id), el.open);
              row.automatic = false;
              if (el.open) { api.update(currentState, o.notes?.() || []); row.activity?.kick(); }
            });
          }
        }
        row.group = group;
        if (group.request) {
          $("summary", row.el).textContent = group.summary;
          const desired = group.active || group.pending || storage.get(pref(group.id), group.open);
          setOpen(row, desired);
          row.el.classList.toggle("chat-turn-current", group.open);
          if (!row.el.open) { row.activity?.stop(); row.mailbox?.stop(); continue; }
        }
        for (const m of group.messages) {
          const html = o.messageHtml(m);
          let message = row.messages.get(m.id);
          if (!message) {
            const node = document.createElement("div"); node.className = "chat-message-slot"; row.content.append(node);
            message = { node, html: "" }; row.messages.set(m.id, message);
          }
          const editing = message.node.querySelector("textarea:focus,input:focus") || (String(window.getSelection?.() || "") && message.node.contains(window.getSelection()?.anchorNode));
          if (message.html !== html && !editing) {
            const focused = message.node.contains(document.activeElement) ? { ...document.activeElement.dataset } : null;
            const disclosures = [...message.node.querySelectorAll("details")].map((d) => d.open);
            message.node.innerHTML = html; message.html = html; changed = true;
            [...message.node.querySelectorAll("details")].forEach((d, i) => { d.open = disclosures[i] || false; });
            if (focused) [...message.node.querySelectorAll("button")].find((b) => Object.entries(focused).every(([k, v]) => b.dataset[k] === v))?.focus();
            const prose = $(".chat-text", message.node); if (prose && m.role !== "user") renderMessageMarkdown(prose, prose.textContent);
          }
        }
        if (group.request) {
          if (!row.activity) {
            const slot = document.createElement("section"); slot.className = "chat-activity";
            const first = row.messages.get(group.messages[0]?.id)?.node;
            if (first) first.after(slot); else row.content.append(slot);
            row.activity = mountActivity(slot, { ...o, request: () => group.id, state: () => currentState, busy: () => row.group.active });
            row.activitySlot = slot;
          }
          row.activity.kick();
          if (state.mailbox_version === 1) {
            if (!row.mailbox) {
              const mail = document.createElement("section"); row.content.append(mail);
              row.mailbox = mountMailbox(mail, { ...o, request: () => group.id, busy: () => row.group.active, teamBox: row.activity.teamMailboxHost() });
              row.mailboxSlot = mail;
            }
            row.mailbox.kick();
          }
        }
        const messageNodes = group.messages.map((m) => row.messages.get(m.id).node);
        const ordered = [messageNodes[0], row.activitySlot, row.mailboxSlot, ...messageNodes.slice(1)].filter(Boolean);
        ordered.forEach((node, i) => { if (row.content.children[i] !== node) row.content.insertBefore(node, row.content.children[i] || null); });
      }
      controls.hidden = !data.some((g) => g.request) && !o.error?.();
      if (following) box.scrollTop = box.scrollHeight;
      else {
        if (anchor?.el.isConnected) box.scrollTop += anchor.el.getBoundingClientRect().top - before;
        if (changed) newer.hidden = false;
      }
    },
    kick() { for (const r of groups.values()) if (r.el.open) { r.activity?.kick(); r.mailbox?.kick(); } },
    reset() { scope = ""; reset(); },
    stop() { for (const r of groups.values()) { r.activity?.stop(); r.mailbox?.stop(); } },
  };
  return api;
}
