import { mountActivity } from "./chat_activity.js";
import { mountMailbox } from "./chat_mailbox.js";

// A request's activity line in the transcript (transcript.js). Pending decisions (approvals and
// questions from the mailbox) render inline under the line. The request's Details
// holds attempt output and raw protocol; team messages have their own disclosure.
// Mail is read for requests that are open (active, waiting on you, or the newest) and for any
// whose Details the reader opens.
export function mountRequestLine(node, o) {
  let group = null, state = null, mailbox = null, asked = false;
  const activity = (o.mountActivity || mountActivity)(node, o), mount = o.mountMailbox || mountMailbox;
  node.className = "chat-request-line";
  function mail() {
    if (mailbox || ![1, 2].includes(state?.mailbox_version) || !(group?.open || asked)) return;
    const host = activity.decisionsHost();
    if (!host) return;
    const box = document.createElement("section"); host.append(box);
    mailbox = mount(box, { ...o, teamBox: activity.teamMailboxHost(), rawBox: activity.rawHost() });
  }
  node.addEventListener("toggle", (e) => { if (e.target.open && !asked) { asked = true; mail(); mailbox?.kick(); } }, true);
  function jump(id) { asked = true; mail(); mailbox?.jump(id); }
  node.addEventListener("click", (e) => {
    const button = e.target.closest("[data-mail-jump]");
    if (button) { e.stopPropagation(); o.onMailJump?.(); jump(button.dataset.mailJump); }
  });
  return {
    node, jump,
    update(next, nextState) { group = next; state = nextState; activity.kick(); mail(); mailbox?.kick(); },
    kick() { activity.kick(); mailbox?.kick(); },
    suspend() { activity.suspend(); mailbox?.suspend(); },
    resume() { activity.resume(); mailbox?.resume(); },
    stop() { activity.stop(); mailbox?.stop(); },
  };
}
