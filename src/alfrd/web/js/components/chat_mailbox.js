import { $, esc } from "../utils/dom.js";
import { server } from "../data/server.js";
import { subscribeMailbox } from "../data/chat_live.js";
import { sanitizeHtml } from "./chat_activity.js";

const safe = (html) => sanitizeHtml(html);
export const pendingMail = (m) => m.recipient?.kind === "user" && ["question", "permission_request"].includes(m.kind)
  && m.state === "pending" && (!m.expires_at || Date.parse(m.expires_at) > Date.now());
export const compareMail = (a, b) => Number(pendingMail(b) && b.kind === "permission_request") - Number(pendingMail(a) && a.kind === "permission_request")
  || ["urgent", "high", "normal", "low"].indexOf(a.priority || "normal") - ["urgent", "high", "normal", "low"].indexOf(b.priority || "normal") || a.seq - b.seq;
export const resolvedLabel = (own) => own ? "Answer recorded" : "Answered elsewhere";
function reorder(list, nodes) {
  const focused = document.activeElement, selection = focused?.selectionStart == null ? null : [focused.selectionStart, focused.selectionEnd];
  nodes.forEach((el, i) => { if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null); });
  if (focused && list.contains(focused) && document.activeElement !== focused) { focused.focus({ preventScroll: true }); if (selection) focused.setSelectionRange(...selection); }
}
let mailboxViews = 0;
const expandedMail = (m, view) => `<button type="button" class="btn sm" data-mail-expand aria-expanded="false" aria-controls="mail-full-${view}-${esc(m.id)}">Full message</button><div id="mail-full-${view}-${esc(m.id)}" data-mail-full hidden>${safe(`<p>${esc(m.subject)}</p>${m.description ? `<p class="chat-text">${esc(m.description)}</p>` : ""}<p class="chat-text">${esc(m.body)}</p>`)}</div>`;

/** A card's markup, text parts sanitized. The form skeleton is static: DOMPurify's `name` rule would break its fields. */
export function mailCardHtml(m, agentName, view = "card") {
  const approval = m.kind === "permission_request", p = m.payload || {};
  return `<header>${safe(`<b>${esc(approval ? "Approval needed" : m.kind === "question" ? "Question" : m.subject)} · ${esc(agentName(m.sender.agent_id || m.sender.kind))}</b>`)}</header>
          ${safe(`<p>${esc(m.summary || m.subject)}</p><p class="muted small">${esc(m.code || m.kind)} · ${esc(m.priority || "normal")}</p>`)}
          ${expandedMail(m, view)}
          ${approval ? safe(`<dl class="chat-dl small"><dt>Action</dt><dd>${esc(p.description || p.action)}</dd><dt>Target</dt><dd>${esc(p.target)}</dd><dt>Scope</dt><dd>Once, for this attempt</dd></dl>`) : ""}
          ${m.expires_at ? safe(`<p class="muted small">Expires ${esc(m.expires_at)}</p>`) : ""}
          <form><fieldset><legend class="sr-only">Your answer</legend>${!approval ? (p.options || []).map((v) => `<label class="row gap small"><input type="radio" name="choice" value="${esc(v.id)}">${safe(esc(v.label))}</label>`).join("") : ""}
          ${approval || p.allow_text ? `<label class="field small"><span>${approval ? "Explanation (optional)" : "Answer"}</span><textarea class="input" name="answer" rows="2" maxlength="16384"></textarea></label>` : ""}
          <div class="row gap wrap">${approval ? `<button type="submit" class="btn sm" value="approve">Approve once</button><button type="submit" class="btn sm" value="deny">Deny</button>` : `<button type="submit" class="btn sm primary" value="answer">Send answer</button>`}</div></fieldset><p class="fail-t small" role="alert" data-error></p></form>
          <p class="muted small" data-resolution role="status"></p>`;
}

/** Messages that need the user: shown inline, outside the Details disclosure. */
export const decisionsFor = (messages) => messages.filter((m) => m.recipient?.kind === "user" && m.kind !== "reply").sort(compareMail);

/** One editable form per mailbox id; polling never replaces an unsent answer. */
export function mountMailbox(box, o) {
  const cards = new Map(), teamCards = new Map();
  const view = ++mailboxViews;
  const replyKey = () => `reply-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`}`;
  let sub, team, list, status;
  box.className = "chat-mailbox";
  box.innerHTML = `<p class="muted small" role="status"></p><div data-mail-cards></div><section class="chat-team"><p class="small"><b data-team-title>Team messages</b></p><div></div></section>`;
  status = $("p", box); list = $("[data-mail-cards]", box); team = $(".chat-team", box);
  const expand = (e) => {
    const button = e.target.closest("[data-mail-expand]");
    if (!button) return;
    const content = button.nextElementSibling;
    content.hidden = !content.hidden; button.setAttribute("aria-expanded", String(!content.hidden));
  };
  list.addEventListener("click", expand); team.addEventListener("click", expand);
  if (o.teamBox) o.teamBox.append(team);
  const rawBox = o.rawBox;
  const url = `/studio/projects/${encodeURIComponent(o.project())}/chat/sessions/${encodeURIComponent(o.sid())}/requests/${encodeURIComponent(o.request())}/mailbox/messages`;
  function draw(snapshot) {
    status.textContent = snapshot.unsupported ? "Agent messages are unavailable on this server. Existing blockers remain in Decisions." : snapshot.stale ? "Messages paused — reconnecting" : "";
    const user = decisionsFor(snapshot.messages);
    const peers = snapshot.messages.filter((m) => m.recipient?.kind !== "user" && (m.kind !== "reply" || m.sender?.kind !== "user"));
    team.hidden = !peers.length;
    $("[data-team-title]", team).textContent = `Team messages · ${peers.length}`;
    reorder($("div", team), [...peers].sort(compareMail).map((m) => {
      if (!teamCards.has(m.id)) {
        const el = document.createElement("article"); el.className = "small";
        el.innerHTML = `${safe(`<p><b>${esc(m.sender.agent_id || m.sender.kind)} → ${esc(m.recipient.task_id || m.recipient.kind)}</b> · ${esc(m.summary || m.subject)}</p>`)}${expandedMail(m, view)}`;
        teamCards.set(m.id, el);
      }
      return teamCards.get(m.id);
    }));
    for (const m of user) {
      let card = cards.get(m.id);
      if (!card) {
        const el = document.createElement("article"); el.className = "chat-mail-card"; el.dataset.mailId = m.id;
        const approval = m.kind === "permission_request";
        el.innerHTML = mailCardHtml(m, o.agentName, view);
        if (approval && m.payload.arguments) {
          const raw = document.createElement("div"); raw.className = "chat-raw"; raw.dataset.rawFor = m.id;
          raw.innerHTML = safe(`<p class="small muted">Raw protocol · ${esc(m.subject)}</p><pre class="log chat-pre">${esc(JSON.stringify(m.payload.arguments, null, 2))}</pre>`);
          (rawBox || el).append(raw);
        }
        list.append(el); card = { el, message: m }; cards.set(m.id, card);
        $("form", el).hidden = m.kind === "info";
        $("form", el).onsubmit = async (event) => {
          event.preventDefault(); event.stopPropagation();
          if (card.sending || card.message.state !== "pending" || (card.message.expires_at && Date.parse(card.message.expires_at) <= Date.now())) return;
          const original = card.message, form = event.currentTarget, error = $("[data-error]", el);
          const outcome = event.submitter?.value, answer = $("textarea", form)?.value.trim() || null;
          const choice = $("input:checked", form)?.value || null;
          if (!(approval ? ["approve", "deny"] : ["answer"]).includes(outcome)) return;
          if (outcome === "answer" && !answer && !choice) { error.textContent = "Choose an option or write an answer."; return; }
          const payload = { expected_version: original.version, outcome, answer, choice_id: choice,
            action_hash: approval ? original.payload.action_hash : null };
          const signature = JSON.stringify(payload);
          if (card.signature !== signature) { card.signature = signature; card.key = replyKey(); }
          card.sending = true; $("fieldset", form).disabled = true; error.textContent = "";
          try {
            const result = await server.mutate(`${url}/${encodeURIComponent(m.id)}/reply`, { ...payload, idempotency_key: card.key });
            card.message = { ...original, state: "resolved" };
            card.reply = result.message || { payload };
            $("[data-resolution]", el).textContent = `${outcome === "approve" ? "Approved once" : outcome === "deny" ? "Denied" : "Answer recorded"}; agent will read it at its next checkpoint.`;
            sub?.kick(); o.onReply?.(); window.dispatchEvent(new Event("alfrd-mail-replied"));
          } catch (e) {
            error.textContent = e.message;
            $("fieldset", form).disabled = e.status === 409;
            if (e.status === 409 && /answered elsewhere/i.test(e.message)) {
              card.message = { ...original, state: "resolved" }; card.elsewhere = true;
              $("[data-resolution]", el).textContent = resolvedLabel(false);
            }
            sub?.kick();
          }
          finally { card.sending = false; }
        };
      }
      // A poll started before our successful reply must not re-enable the form.
      const current = (card.reply || card.elsewhere) && m.state === "pending" ? { ...m, state: "resolved" } : m;
      card.message = current;
      const expired = m.expires_at && Date.parse(m.expires_at) <= Date.now();
      const pending = pendingMail(current);
      $("fieldset", card.el).disabled = !pending || Boolean(card.sending);
      if (!pending && m.kind !== "info") {
        const reply = snapshot.messages.find((r) => r.reply_to === m.id) || card.reply;
        $("[data-resolution]", card.el).textContent = current.state === "resolved" ? `${resolvedLabel(card.reply)}${reply ? `: ${reply.payload.outcome === "answer" ? reply.payload.answer || reply.payload.choice_id : reply.payload.outcome === "approve" ? "Approved once" : "Denied"}` : ""}; agent will read it at its next checkpoint.` : expired ? "Expired" : current.state;
      }
    }
    // Move existing cards, never replace their form, disclosure or unsent draft.
    reorder(list, user.map((m) => cards.get(m.id).el));
  }
  const api = {
    kick() {
      if (!sub) sub = subscribeMailbox(o.project(), o.sid(), o.request(), draw, {
        visible: () => box.isConnected && !box.closest("[hidden]"), busy: o.busy,
      }); else sub.kick();
    },
    stop() { sub?.stop(); sub = null; },
  };
  api.kick(); return api;
}
