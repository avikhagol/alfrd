import { mountActivity } from "./chat_activity.js";
import { mountMailbox } from "./chat_mailbox.js";

// A request's activity line in the transcript (transcript.js). Pending decisions (approvals and
// questions from the mailbox) render inline under the line; Details, the request's one
// disclosure, holds the attempt rows with their output, team messages and raw protocol.
// Mail is read for requests that are open (active, waiting on you, or the newest) and for any
// whose Details the reader opens.
export function mountRequestLine(node, o) {
  let group = null, state = null, mailbox = null, asked = false;
  const activity = (o.mountActivity || mountActivity)(node, o), mount = o.mountMailbox || mountMailbox;
  node.className = "chat-request-line";
  function mail() {
    if (mailbox || state?.mailbox_version !== 1 || !(group?.open || asked)) return;
    const host = activity.decisionsHost();
    if (!host) return;
    const box = document.createElement("section"); host.append(box);
    mailbox = mount(box, { ...o, teamBox: activity.teamMailboxHost(), rawBox: activity.rawHost() });
  }
  node.addEventListener("toggle", (e) => { if (e.target.open && !asked) { asked = true; mail(); mailbox?.kick(); } }, true);
  return {
    node,
    update(next, nextState) { group = next; state = nextState; activity.kick(); mail(); mailbox?.kick(); },
    kick() { activity.kick(); mailbox?.kick(); },
    stop() { activity.stop(); mailbox?.stop(); },
  };
}
