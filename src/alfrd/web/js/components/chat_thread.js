import { mountActivity } from "./chat_activity.js";
import { mountMailbox } from "./chat_mailbox.js";

// A request's one activity line in the transcript (transcript.js): the run summary with its
// attempt rows, then the request's mailbox cards. Mail is read for requests that are open
// (active, waiting on you, or the newest) and for any whose line the reader expands.
export function mountRequestLine(node, o) {
  let group = null, state = null, mailbox = null, asked = false;
  node.className = "chat-request-line";
  const slot = document.createElement("section"); slot.className = "chat-activity"; node.append(slot);
  const activity = mountActivity(slot, o);
  function mail() {
    if (mailbox || state?.mailbox_version !== 1 || !(group?.open || asked)) return;
    const box = document.createElement("section"); node.append(box);
    mailbox = mountMailbox(box, { ...o, teamBox: activity.teamMailboxHost() });
  }
  slot.addEventListener("toggle", (e) => { if (e.target.open && !asked) { asked = true; mail(); mailbox?.kick(); } }, true);
  return {
    node,
    update(next, nextState) { group = next; state = nextState; activity.kick(); mail(); mailbox?.kick(); },
    kick() { activity.kick(); mailbox?.kick(); },
    stop() { activity.stop(); mailbox?.stop(); },
  };
}
