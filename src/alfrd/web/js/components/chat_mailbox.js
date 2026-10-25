import { $, esc } from "../utils/dom.js";
import { server } from "../data/server.js";
import { subscribeMailbox } from "../data/chat_live.js";
import { sanitizeHtml } from "./chat_activity.js";

const safe = (html) => sanitizeHtml(html);

/** A card's markup, text parts sanitized. The form skeleton is static: DOMPurify's `name` rule would break its fields. */
export function mailCardHtml(m, agentName) {
  const approval = m.kind === "permission_request", p = m.payload || {};
  return `<header>${safe(`<b>${esc(approval ? "Approval needed" : m.kind === "question" ? "Question" : m.subject)} · ${esc(agentName(m.sender.agent_id || m.sender.kind))}</b>`)}</header>
          ${safe(`<p>${esc(m.subject)}</p><p class="chat-text">${esc(m.body)}</p>`)}
          ${approval ? safe(`<dl class="chat-dl small"><dt>Action</dt><dd>${esc(p.description || p.action)}</dd><dt>Target</dt><dd>${esc(p.target)}</dd><dt>Scope</dt><dd>Once, for this attempt</dd></dl>`) : ""}
          ${m.expires_at ? safe(`<p class="muted small">Expires ${esc(m.expires_at)}</p>`) : ""}
          <form><fieldset><legend class="sr-only">Your answer</legend>${!approval ? (p.options || []).map((v) => `<label class="row gap small"><input type="radio" name="choice" value="${esc(v.id)}">${safe(esc(v.label))}</label>`).join("") : ""}
          ${approval || p.allow_text ? `<label class="field small"><span>${approval ? "Explanation (optional)" : "Answer"}</span><textarea class="input" name="answer" rows="2" maxlength="16384"></textarea></label>` : ""}
          <div class="row gap wrap">${approval ? `<button type="submit" class="btn sm" value="approve">Approve once</button><button type="submit" class="btn sm" value="deny">Deny</button>` : `<button type="submit" class="btn sm primary" value="answer">Send answer</button>`}</div></fieldset><p class="fail-t small" role="alert" data-error></p></form>
          <p class="muted small" data-resolution role="status"></p>`;
}

/** Messages that need the user: shown inline, outside the Details disclosure. */
export const decisionsFor = (messages) => messages.filter((m) => m.recipient?.kind === "user" && m.kind !== "reply");

/** One editable form per mailbox id; polling never replaces an unsent answer. */
export function mountMailbox(box, o) {
  const cards = new Map();
  const replyKey = () => `reply-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`}`;
  let sub, team, list, status;
  box.className = "chat-mailbox";
  box.innerHTML = `<p class="muted small" role="status"></p><div data-mail-cards></div><section class="chat-team"><p class="small"><b data-team-title>Team messages</b></p><div></div></section>`;
  status = $("p", box); list = $("[data-mail-cards]", box); team = $(".chat-team", box);
  if (o.teamBox) o.teamBox.append(team);
  const rawBox = o.rawBox;
  const url = `/studio/projects/${encodeURIComponent(o.project())}/chat/sessions/${encodeURIComponent(o.sid())}/requests/${encodeURIComponent(o.request())}/mailbox/messages`;
  function draw(snapshot) {
    status.textContent = snapshot.unsupported ? "Agent messages are unavailable on this server. Existing blockers remain in Decisions." : snapshot.stale ? "Messages paused — reconnecting" : "";
    const user = decisionsFor(snapshot.messages);
    const peers = snapshot.messages.filter((m) => m.recipient?.kind !== "user" && (m.kind !== "reply" || m.sender?.kind !== "user"));
    team.hidden = !peers.length;
    $("[data-team-title]", team).textContent = `Team messages · ${peers.length}`;
    const peerHTML = safe(peers.map((m) => `<p class="small"><b>${esc(m.sender.agent_id || m.sender.kind)} → ${esc(m.recipient.task_id || m.recipient.kind)}</b> · ${esc(m.subject)}<br>${esc(m.body)}</p>`).join(""));
    if (team.dataset.html !== peerHTML) { team.dataset.html = peerHTML; $("div", team).innerHTML = peerHTML; }
    for (const m of user) {
      let card = cards.get(m.id);
      if (!card) {
        const el = document.createElement("article"); el.className = "chat-mail-card"; el.dataset.mailId = m.id;
        const approval = m.kind === "permission_request";
        el.innerHTML = mailCardHtml(m, o.agentName);
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
            sub?.kick(); o.onReply?.();
          } catch (e) { error.textContent = e.message; $("fieldset", form).disabled = false; sub?.kick(); }
          finally { card.sending = false; }
        };
      }
      // A poll started before our successful reply must not re-enable the form.
      const current = card.reply && m.state === "pending" ? { ...m, state: "resolved" } : m;
      card.message = current;
      const expired = m.expires_at && Date.parse(m.expires_at) <= Date.now();
      const pending = current.state === "pending" && !expired;
      $("fieldset", card.el).disabled = !pending || Boolean(card.sending);
      if (!pending && m.kind !== "info") {
        const reply = snapshot.messages.find((r) => r.reply_to === m.id) || card.reply;
        $("[data-resolution]", card.el).textContent = current.state === "resolved" ? `Answer recorded${reply ? `: ${reply.payload.outcome === "answer" ? reply.payload.answer || reply.payload.choice_id : reply.payload.outcome === "approve" ? "Approved once" : "Denied"}` : ""}; agent will read it at its next checkpoint.` : expired ? "Expired" : current.state;
      }
    }
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
