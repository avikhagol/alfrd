import { $ } from "../utils/dom.js";
import { threadGroups } from "../data/chat.js";
import { renderMessageMarkdown } from "./handoff_markdown.js";
import { mountRequestLine } from "./chat_thread.js";

// One continuous transcript for the Chat page and the dock: each request is its user message,
// one activity line, then the answer. Nodes are keyed, so tail scroll, selections, focus and
// open disclosures survive polling; a reader above the end keeps their place.

/** FNV-1a, enough to tell two versions of one message apart. */
export function textHash(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  return (h >>> 0).toString(16);
}

/** Sanitized markdown per message id and text hash; oldest entries go first. */
export function markdownCache(limit = 500) {
  const map = new Map(), key = (id, text) => `${id}|${textHash(String(text))}`;
  return {
    get(id, text) { const k = key(id, text), v = map.get(k); if (v != null) { map.delete(k); map.set(k, v); } return v; },
    set(id, text, html) { const k = key(id, text); map.delete(k); map.set(k, html); while (map.size > limit) map.delete(map.keys().next().value); },
    get size() { return map.size; },
  };
}
const markdown = markdownCache();

/** Flat, keyed order: first message, the request's activity line, the remaining messages. */
export function transcriptItems(groups) {
  const items = [];
  for (const group of groups) {
    const start = items.length, rid = group.request ? group.id : "";
    const message = (m) => ({ key: `m:${m.id}`, kind: "message", message: m, group, request: rid });
    if (group.messages[0]) items.push(message(group.messages[0]));
    if (group.request) items.push({ key: `run:${group.id}`, kind: "activity", group, request: rid });
    for (const m of group.messages.slice(1)) items.push(message(m));
    if (items[start]) items[start].first = true;
  }
  return items;
}

/** Index of the first node whose bottom edge is below `top` (nodes are in visual order). */
export function anchorIndex(nodes, top) {
  let lo = 0, hi = nodes.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (nodes[mid].getBoundingClientRect().bottom > top) hi = mid; else lo = mid + 1; }
  return lo < nodes.length ? lo : -1;
}

export function mountTranscript(box, o) {
  let scope = "", messages = new Map(), lines = new Map(), flow, status, phase, newer, empty, currentState, stick = true;
  const render = o.renderMarkdown || renderMessageMarkdown, mountLine = o.mountLine || mountRequestLine;
  const atEnd = () => box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  const toEnd = () => { box.scrollTop = box.scrollHeight; stick = true; newer.hidden = true; };
  const stopAll = () => { for (const l of lines.values()) l.stop(); };
  function reset() {
    stopAll(); messages = new Map(); lines = new Map(); box.replaceChildren(); stick = true;
    status = document.createElement("p"); status.className = "muted small chat-flow-status"; status.setAttribute("role", "status");
    phase = document.createElement("span"); phase.className = "sr-only"; phase.setAttribute("aria-live", "polite");
    empty = document.createElement("p"); empty.className = "muted small"; empty.textContent = "No messages yet. Ask the team, or start with @agent.";
    flow = document.createElement("div"); flow.className = "chat-flow";
    newer = document.createElement("button"); newer.type = "button"; newer.className = "btn sm chat-new-activity"; newer.textContent = "New activity"; newer.hidden = true;
    newer.onclick = toEnd;
    box.append(status, phase, empty, flow, newer);
  }
  box.addEventListener("scroll", () => { stick = atEnd(); if (stick && newer) newer.hidden = true; }, { passive: true });
  function prose(node, m) {
    const el = $(".chat-text", node);
    if (!el || m.role === "user") return;
    const text = el.textContent, hit = markdown.get(m.id, text);
    if (hit != null) { el.innerHTML = hit; el.classList.add("chat-markdown"); return; }
    Promise.resolve(render(el, text)).then(() => {
      if (!el.classList.contains("chat-markdown")) return;
      markdown.set(m.id, text, el.innerHTML);
      if (stick && el.isConnected) box.scrollTop = box.scrollHeight;
    }, () => {});
  }
  function paint(item) {
    const m = item.message, html = o.messageHtml(m);
    let row = messages.get(item.key);
    if (!row) {
      const node = document.createElement("div"); node.className = "chat-message-slot"; node.dataset.key = item.key;
      row = { node, html: "" }; messages.set(item.key, row);
    }
    const node = row.node, selection = window.getSelection?.();
    if (row.html === html || node.querySelector("textarea:focus,input:focus") || (String(selection || "") && node.contains(selection?.anchorNode))) return false;
    const focused = node.contains(document.activeElement) ? { ...document.activeElement.dataset } : null;
    const disclosures = [...node.querySelectorAll("details")].map((d) => d.open);
    node.innerHTML = html; row.html = html;
    [...node.querySelectorAll("details")].forEach((d, i) => { d.open = disclosures[i] || false; });
    if (focused) [...node.querySelectorAll("button")].find((b) => Object.entries(focused).every(([k, v]) => b.dataset[k] === v))?.focus();
    prose(node, m);
    return true;
  }
  const api = {
    update(state, notes = []) {
      currentState = state;
      const next = JSON.stringify([o.project(), o.sid()]);
      if (next !== scope || !flow?.isConnected) { scope = next; reset(); }
      const following = stick || atEnd(), kids = [...flow.children];
      const i = following ? -1 : anchorIndex(kids, box.getBoundingClientRect().top);
      const anchor = kids[i], before = anchor?.getBoundingClientRect().top;
      const groups = threadGroups({ ...state, messages: [...(state?.messages || []), ...notes] }), items = transcriptItems(groups);
      empty.hidden = Boolean(items.length);
      status.textContent = o.error?.() ? "Updates paused — reconnecting" : "";
      const phases = groups.filter((g) => g.request).map((g) => `${g.request.text?.slice(0, 80) || "Request"}: ${g.request.status.replaceAll("_", " ")}`).join(". ");
      if (phase.textContent !== phases) phase.textContent = phases;
      const keys = new Set(items.map((x) => x.key));
      for (const [key, row] of messages) if (!keys.has(key)) { row.node.remove(); messages.delete(key); }
      for (const [key, line] of lines) if (!keys.has(key)) { line.stop(); line.node.remove(); lines.delete(key); }
      let changed = false;
      const nodes = items.map((item) => {
        let node;
        if (item.kind === "message") { changed = paint(item) || changed; node = messages.get(item.key).node; }
        else {
          let line = lines.get(item.key);
          if (!line) {
            line = mountLine(document.createElement("div"), { ...o, request: () => item.group.id, state: () => currentState, busy: () => line.group?.active });
            line.node.dataset.key = item.key; lines.set(item.key, line); changed = true;
          }
          line.group = item.group; line.update(item.group, state); node = line.node;
        }
        node.dataset.request = item.request; node.classList.toggle("chat-flow-start", Boolean(item.first));
        return node;
      });
      nodes.forEach((node, n) => { if (flow.children[n] !== node) flow.insertBefore(node, flow.children[n] || null); });
      if (following) toEnd();
      else {
        if (anchor?.isConnected) box.scrollTop += anchor.getBoundingClientRect().top - before;
        if (changed) newer.hidden = false;
      }
    },
    kick() { for (const l of lines.values()) l.kick(); },
    reset() { scope = ""; reset(); },
    stop: stopAll,
  };
  return api;
}
