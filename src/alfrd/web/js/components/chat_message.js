import { esc } from "../utils/dom.js";
import { presentMessage } from "../data/chat.js";
import { messageIdentity, messageTime } from "../data/chat_identity.js";

export function messageHtml(message, { agentName, orchestrator, agents, compact = false } = {}) {
  const m = presentMessage(message), who = messageIdentity(m, { agentName, orchestrator, agents }), time = messageTime(m.at);
  return `<article class="chat-msg chat-${esc(m.role)} chat-identity-${who.slot}${compact ? " chat-compact" : ""}${m.kind ? ` chat-${esc(m.kind)}` : ""}" data-msg="${esc(m.id)}" data-agent="${esc(who.key)}" aria-label="${esc(who.name)}">
    <header><span class="chat-avatar" aria-hidden="true">${esc(who.initials)}</span><b>${esc(who.name)}</b><span class="chat-agent-key">@${esc(who.key)}</span>${who.role ? `<span class="chat-agent-role">${esc(who.role)}</span>` : ""}${time ? `<time datetime="${time.datetime}" title="${esc(time.full)}" aria-label="${esc(time.full)}">${esc(time.short)}</time>` : ""}</header>
    <div class="chat-text">${esc(m.text)}</div>${m.technical ? `<details data-message-detail="technical"><summary>Technical details</summary><pre class="chat-pre" tabindex="0" aria-label="Raw protocol">${esc(m.technical)}</pre></details>` : ""}
    ${m.artifacts?.length ? `<ul class="chat-arts" aria-label="Artifacts">${m.artifacts.map((ref) => `<li><span class="mono">${esc(String(ref).split("@")[0])}</span> <button type="button" class="btn sm" data-artifact="${esc(ref)}">View</button></li>`).join("")}</ul>` : ""}</article>`;
}

// Enhance only sanitized viewer output, after caching it. Copy source excludes UI labels.
export function enhanceCode(target) {
  for (const pre of target.querySelectorAll("pre")) {
    if (pre.parentNode.classList.contains("chat-code")) continue;
    const code = pre.querySelector("code"), language = /(?:language|lang)-([\w+-]+)/.exec(code?.className || "")?.[1];
    const group = document.createElement("div"); group.className = "chat-code";
    const head = document.createElement("div"); head.className = "chat-code-head";
    const label = document.createElement("span"); label.textContent = language || "Code";
    const button = document.createElement("button"); button.type = "button"; button.className = "btn sm"; button.dataset.copyCode = ""; button.textContent = "Copy code";
    const status = document.createElement("span"); status.className = "chat-copy-status"; status.setAttribute("role", "status");
    pre.tabIndex = 0; pre.setAttribute("role", "region"); pre.setAttribute("aria-label", `${language || "Source"} code`);
    pre.before(group); head.append(label, button); group.append(head, pre, status);
  }
}

// A disabled Markdown plugin still leaves fenced code readable and copyable.
// Everything else remains escaped source; rich Markdown uses the sanitized viewer.
export function fencedCodeHtml(text) {
  let end = 0, html = "", found = false;
  for (const m of text.matchAll(/^```([\w+-]*)[^\n]*\n([\s\S]*?)^```[ \t]*$/gm)) {
    html += esc(text.slice(end, m.index)) + `<pre><code${m[1] ? ` class="language-${esc(m[1])}"` : ""}>${esc(m[2])}</code></pre>`;
    end = m.index + m[0].length; found = true;
  }
  return found ? html + esc(text.slice(end)) : null;
}

export async function copyCode(button, clipboard = globalThis.navigator?.clipboard) {
  const group = button.closest(".chat-code"), status = group.querySelector(".chat-copy-status");
  try { await clipboard.writeText(group.querySelector("pre").textContent); status.textContent = "Copied"; }
  catch { status.textContent = "Couldn't copy. Select the code and copy it."; }
}
