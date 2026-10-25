// Inline decisions and the single Details disclosure (chat_thread.js, chat_mailbox.js,
// chat_activity.js): a pending approval is on screen without opening anything, and one
// Details per request holds the activity log, task outputs and raw protocol.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.className = ""; this.dataset = {}; this.listeners = {}; }
  append(...nodes) { for (const n of nodes) { n.parentNode = this; this.children.push(n); } }
  addEventListener(type, f) { (this.listeners[type] ||= []).push(f); }
  closest(tag) { for (let x = this; x; x = x.parentNode) if (x.tagName === tag.toUpperCase()) return x; return null; }
}
globalThis.document = { hidden: false, activeElement: null, addEventListener() {}, removeEventListener() {}, createElement: (t) => new El(t) };
globalThis.window = { getSelection: () => "", addEventListener() {} };

const { mountRequestLine } = await import("../../src/alfrd/web/js/components/chat_thread.js");
const { mailCardHtml, decisionsFor } = await import("../../src/alfrd/web/js/components/chat_mailbox.js");
const { sanitizeHtml } = await import("../../src/alfrd/web/js/components/chat_activity.js");

const approval = { id: "m1", kind: "permission_request", state: "pending", version: 3, subject: "Run tests", body: "Needs <b>shell</b>",
  sender: { kind: "agent", agent_id: "codex" }, recipient: { kind: "user" },
  payload: { description: "Run pytest", target: "tests/", action_hash: "h1", arguments: { cmd: "pytest" } } };

function line(group) {
  const calls = [], host = new El("section"), top = new El("div");
  top.append(host);
  const activity = { kick() {}, stop() {}, decisionsHost: () => host, teamMailboxHost: () => "team", rawHost: () => "raw" };
  const l = mountRequestLine(top, { mountActivity: () => activity, mountMailbox: (box, o) => { calls.push(o); return { kick() {}, stop() {}, jump(id) { calls.push(id); } }; } });
  return { l, calls, host, top, group };
}

test("a pending permission request is visible without opening any disclosure", () => {
  const { l, calls, host } = line();
  l.update({ id: "r1", open: true }, { mailbox_version: 1 });
  assert.equal(calls.length, 1, "the mailbox is mounted for an open request");
  assert.equal(host.closest("details"), null, "the decisions host is outside every details element");
  assert.equal(host.children[0].parentNode.closest("details"), null);
  assert.deepEqual([calls[0].teamBox, calls[0].rawBox], ["team", "raw"], "team messages and raw protocol go into Details");
  const html = mailCardHtml(approval, (id) => id);
  assert.match(html, /Approval needed/);
  assert.match(html, /Run pytest/);
  assert.match(html, /<button type="submit" class="btn sm" value="approve">Approve once<\/button>/);
  assert.match(html, /value="deny">Deny/);
  assert.doesNotMatch(html, /<details|<summary/, "the card itself holds no disclosure");
  assert.doesNotMatch(html, /pytest"|h1/, "raw arguments and the action hash are not in the card");
  assert.deepEqual(decisionsFor([approval, { ...approval, id: "m2", kind: "reply" }, { ...approval, id: "m3", recipient: { kind: "task" } }]).map((m) => m.id), ["m1"]);
});

test("older requests read mail only once Details is opened", () => {
  const { l, calls, top } = line();
  l.update({ id: "r0", open: false }, { mailbox_version: 1 });
  assert.equal(calls.length, 0);
  const summary = new El("summary"), details = new El("details"); details.open = true; details.append(summary); top.append(details);
  top.listeners.toggle[0]({ target: details });
  assert.equal(calls.length, 1);
});

test("exactly one Details disclosure per request; the permission hash and CSRF path are untouched", async () => {
  const dir = new URL("../../src/alfrd/web/js/components/", import.meta.url);
  const src = Object.fromEntries(await Promise.all(["chat_activity", "chat_mailbox", "chat_thread"].map(async (f) => [f, await readFile(new URL(`${f}.js`, dir), "utf8")])));
  const opens = Object.values(src).join("\n").match(/<details[\s>]/g) || [];
  assert.equal(opens.length, 1, "one <details> template across activity, mailbox and thread");
  assert.match(src.chat_activity, /<details class="chat-details"><summary>Details<\/summary>[^`]*data-team-mailbox[^`]*data-raw-protocol/, "log, outputs, team messages and raw protocol live inside it");
  assert.match(src.chat_activity, /chat-run-rows/);
  assert.match(src.chat_mailbox, /action_hash: approval \? original\.payload\.action_hash : null/);
  assert.match(src.chat_mailbox, /server\.mutate\(/, "replies keep the CSRF-protected mutate path");
  assert.match(src.chat_mailbox, /idempotency_key/);
});

test("Details content is sanitized with DOMPurify", () => {
  const seen = [];
  const stub = { isSupported: true, sanitize: (html, cfg) => { seen.push(cfg); return html.replace(/<script[\s\S]*?<\/script>/g, ""); } };
  assert.equal(sanitizeHtml("<p>hi</p><script>x()</script>", stub), "<p>hi</p>");
  assert.deepEqual(seen[0].FORBID_TAGS, ["style"]);
  assert.ok(seen[0].FORBID_ATTR.includes("style"));
  assert.equal(sanitizeHtml("<img src=x onerror=alert(1)>", { isSupported: false }), "&lt;img src=x onerror=alert(1)&gt;", "no usable DOMPurify: shown as text, never as markup");
  const card = mailCardHtml({ ...approval, subject: "<script>x()</script>", body: "<img src=x onerror=1>" }, (id) => id);
  assert.doesNotMatch(card, /<script|<img/, "message text is escaped");
});


test("M7 waiting navigation mounts a closed request and routes its exact message for page or dock", () => {
  for (const version of [1, 2]) {
    const { l, calls, top } = line();
    l.update({ id: "r0", open: false }, { mailbox_version: version });
    l.jump("peer-id");
    assert.equal(calls.length, 2); assert.equal(calls[1], "peer-id");
    let stopped = false;
    top.listeners.click[0]({ target: { closest: () => ({ dataset: { mailJump: "user-id" } }) }, stopPropagation() { stopped = true; } });
    assert.equal(calls.at(-1), "user-id"); assert.equal(stopped, true); assert.equal(calls.length, 3);
  }
});
