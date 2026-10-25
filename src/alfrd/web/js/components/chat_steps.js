import { esc } from "../utils/dom.js";
import { stepLabel } from "../data/chat_steps.js";

export function stepsHtml(steps, request) {
  return steps.map((s) => `<span>${esc(stepLabel(s))}</span>${s.status === "waiting" ? [...new Set(s.waiting_on || [])].map((id) => `<button type="button" class="btn sm link" data-mail-jump="${esc(id)}" data-mail-request="${esc(request)}" aria-label="${esc(stepLabel(s))}: open message ${esc(id)}">Message ${esc(id)}</button>`).join("") : ""}`).join(" · ");
}

// Open only on deliberate navigation; later polls never steal focus.
export function revealMail(card) {
  for (let p = card.parentElement; p; p = p.parentElement) if (p.tagName === "DETAILS") p.open = true;
  const button = card.querySelector("[data-mail-expand]"), content = card.querySelector("[data-mail-full]");
  if (content) { content.hidden = false; button?.setAttribute("aria-expanded", "true"); }
  card.scrollIntoView({ block: "center" });
  (card.querySelector("button,textarea") || card).focus({ preventScroll: true });
}
