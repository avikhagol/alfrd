import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createInbox, pendingCount, needsMe, countText } from "../../src/alfrd/web/js/data/inbox.js";
import { mergeMailbox } from "../../src/alfrd/web/js/data/chat_live.js";
import { activeSession, paintSessionList } from "../../src/alfrd/web/js/data/chat_sessions.js";
import { server } from "../../src/alfrd/web/js/data/server.js";

// Small DOM surface; exercise the installed mailbox handlers and actual subscription.
class El {
  constructor() { this.dataset = {}; this.children = []; this.selectors = new Map(); this.listeners = {}; this.textContent = ""; this.value = ""; }
  querySelector(s) {
    if (s === "input:checked") return null;
    if (!this.selectors.has(s)) this.selectors.set(s, new El());
    return this.selectors.get(s);
  }
  append(el) { this.children.push(el); }
  insertBefore(el, before) { this.children = this.children.filter((x) => x !== el); const i = this.children.indexOf(before); this.children.splice(i < 0 ? this.children.length : i, 0, el); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  setAttribute(k, v) { this[k] = v; }
  closest() { return null; }
  contains(el) { return this.children.includes(el) || [...this.selectors.values()].some((x) => x === el || x.contains(el)); }
  focus() { document.activeElement = this; }
  setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; }
  get isConnected() { return true; }
}
globalThis.document = { hidden: false, activeElement: null, createElement: () => new El(), addEventListener() {}, removeEventListener() {} };
globalThis.window = { dispatchEvent() {} };
const { mailCardHtml, mountMailbox, decisionsFor, pendingMail } = await import("../../src/alfrd/web/js/components/chat_mailbox.js");
const sleep = (ms = 160) => new Promise((r) => setTimeout(r, ms));
const q = (id, seq, extra = {}) => ({ id, seq, state: "pending", version: 1, kind: "question", priority: "normal", subject: "Choose", summary: "Short", description: "Human context", body: "Full body", sender: { kind: "agent", agent_id: "codex" }, recipient: { kind: "user" }, payload: { allow_text: true }, ...extra });

test("session totals ignore malformed counts while Needs me retains unknown sessions", () => {
  const rows = [{ id: "a", pending_user: 2 }, { id: "b", pending_user: 0 }, { id: "c", pending_user: 3 }];
  assert.equal(pendingCount(rows), 5);
  assert.equal(pendingCount([]), 0);
  assert.equal(pendingCount([...rows, { id: "old" }]), 5);
  const invalid = [null, {}, { pending_user: null }, ...[-1, 1.5, "2", NaN, Infinity].map((pending_user) => ({ pending_user }))];
  assert.equal(pendingCount([...rows, ...invalid]), 5);
  assert.equal(pendingCount(invalid), 0);
  for (const value of [undefined, {}, "sessions"]) assert.equal(pendingCount(value), null);
  assert.equal(pendingCount(null), null);
  for (const value of [null, undefined, -1, 1.5, "2", NaN]) assert.equal(countText(value), "");
  assert.equal(countText(0), "0 pending");
  assert.equal(countText(3), "3 pending");
  assert.deepEqual(needsMe([...rows, { id: "old" }]).map((s) => s.id), ["a", "c", "old"]);
});

test("permission first then priority and seq, without mutating chronological cursor merge", () => {
  const rows = [q("low", 1, { priority: "low" }), q("urgent", 2, { priority: "urgent" }),
    q("permission", 3, { kind: "permission_request", priority: "low" }), q("urgent-2", 4, { priority: "urgent" }),
    q("info", 5, { kind: "info", priority: "low" }), q("expired", 6, { expires_at: "2000-01-01" })];
  assert.equal(rows.filter(pendingMail).length, 4, "info and expiry are never pending");
  assert.deepEqual(decisionsFor(rows).map((m) => m.id), ["permission", "urgent", "urgent-2", "expired", "low", "info"]);
  assert.deepEqual(mergeMailbox([], { messages: rows }).map((m) => m.seq), [1, 2, 3, 4, 5, 6]);
  assert.equal(rows[0].id, "low");
});

test("shell summaries refresh outside Chat, reject late project responses and report failure honestly", async () => {
  let resolveOld, failure = false; const published = [], urls = [];
  const inbox = createInbox({ visible: () => true, publish: (...v) => published.push(v), request: async (url) => {
    urls.push(url);
    if (url.includes("old")) return new Promise((r) => { resolveOld = r; });
    if (failure) throw new Error("offline");
    return { ok: true, json: async () => ({ sessions: [{ id: "s", pending_user: 2 }] }) };
  } });
  try {
    inbox.select("old"); inbox.select("new / project");
    resolveOld({ ok: true, json: async () => ({ sessions: [{ pending_user: 99 }] }) });
    await sleep();
    assert.equal(published.at(-1)[2], 2);
    assert.ok(!published.some(([p, , n]) => p === "old" && n === 99));
    assert.match(urls.at(-1), /new%20%2F%20project\/chat\/sessions$/);
    failure = true; inbox.kick(); await sleep();
    assert.deepEqual(published.at(-1), ["new / project", null, null]);
  } finally { inbox.stop(); }
});

test("compact summary expands human description/body and leaves answer outside expansion", () => {
  const html = mailCardHtml(q("m", 1), (x) => x);
  assert.match(html, /Short/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /data-mail-full hidden/);
  assert.match(html, /Human context/);
  assert.match(html, /Full body/);
  assert.ok(html.indexOf("</div>") < html.indexOf("<form>"));
});

test("two mounted forms preserve drafts while polling and show answered elsewhere to the losing tab", async () => {
  const originalRequest = server.request, originalMutate = server.mutate;
  const message = q("q", 1); let resolved = false, permissionArrived = false; const payloads = [];
  server.request = async () => ({ ok: true, status: 200, json: async () => ({ messages: [
    { ...message, state: resolved ? "resolved" : "pending", version: resolved ? 2 : 1 },
    ...(permissionArrived ? [q("p", 2, { kind: "permission_request", payload: { action_hash: "hash" } })] : [])], next_cursor: "unchanged" }) });
  server.mutate = async (_url, payload) => {
    payloads.push(payload);
    if (resolved) { const e = new Error("Answered elsewhere"); e.status = 409; throw e; }
    resolved = true; return { message: { payload } };
  };
  const a = new El(), b = new El(), c = new El(), options = { project: () => "p", sid: () => "s", request: () => "r", agentName: (x) => x };
  const one = mountMailbox(a, options), two = mountMailbox(b, options), three = mountMailbox(c, options);
  try {
    await sleep();
    const card = (host) => host.querySelector("[data-mail-cards]").children[0];
    const ac = card(a), bc = card(b), af = ac.querySelector("form"), bf = bc.querySelector("form");
    const draft = af.querySelector("textarea"); draft.value = "My draft"; draft.selectionStart = 1; draft.selectionEnd = 3; draft.focus();
    const full = ac.querySelector("[data-mail-full]"); full.hidden = true;
    const button = new El(); button.nextElementSibling = full;
    a.querySelector("[data-mail-cards]").listeners.click({ target: { closest: () => button } });
    assert.equal(button["aria-expanded"], "true"); assert.equal(full.hidden, false);
    permissionArrived = true;
    one.kick(); await sleep();
    assert.equal(card(a).dataset.mailId, "p");
    assert.equal(a.querySelector("[data-mail-cards]").children[1], ac);
    assert.equal(af.querySelector("textarea").value, "My draft"); assert.equal(document.activeElement, draft);
    assert.equal(full.hidden, false); assert.deepEqual([draft.selectionStart, draft.selectionEnd], [1, 3]);
    bf.querySelector("textarea").value = "Other tab";
    const submit = (form) => ({ preventDefault() {}, stopPropagation() {}, currentTarget: form, submitter: { value: "answer" } });
    await af.onsubmit(submit(af)); await bf.onsubmit(submit(bf));
    assert.match(bc.querySelector("[data-resolution]").textContent, /Answered elsewhere/);
    assert.equal(bf.querySelector("fieldset").disabled, true);
    assert.equal(payloads[0].expected_version, 1); assert.equal(payloads[0].action_hash, null);
    assert.notEqual(payloads[0].idempotency_key, payloads[1].idempotency_key);
    one.kick(); await sleep();
    assert.match(ac.querySelector("[data-resolution]").textContent, /Answer recorded/);
    assert.match(bc.querySelector("[data-resolution]").textContent, /Answered elsewhere/);
    assert.equal(bf.querySelector("textarea").value, "Other tab");
    const untouched = c.querySelector("[data-mail-cards]").children.find((el) => el.dataset.mailId === "q");
    assert.match(untouched.querySelector("[data-resolution]").textContent, /Answered elsewhere/);
    assert.equal(untouched.querySelector("fieldset").disabled, true);
    assert.notEqual(ac.innerHTML.match(/aria-controls="([^"]+)/)[1], bc.innerHTML.match(/aria-controls="([^"]+)/)[1]);
  } finally { one.stop(); two.stop(); three.stop(); server.request = originalRequest; server.mutate = originalMutate; document.activeElement = null; }
});


test("Chat navigation hides unknown and zero badges and labels positive counts", async () => {
  const src = await readFile(new URL("../../src/alfrd/web/js/data/chat_projects.js", import.meta.url), "utf8");
  const body = src.slice(src.indexOf("  function paint() {"), src.indexOf("  const events"));
  const badge = { parentElement: {}, setAttribute(k, v) { this[k] = v; } };
  const paint = new Function("pendingCount", "countText", "$", "chatProjects", "enabled", "ctx", "routeHash", "project", `${body}; paint();`);
  for (const count of [null, 0, 3, null]) {
    paint(() => count, countText, () => badge, { snapshot: () => ({ sessions: [] }) }, true, { state: {} }, () => "#chat", "p");
    const label = count > 0 ? "Chat · 3 pending" : "Chat";
    assert.equal(badge.hidden, !(count > 0));
    assert.equal(badge.textContent, count > 0 ? count : "");
    assert.equal(badge["aria-label"], label);
    assert.equal(badge.parentElement.title, label);
  }
});


test("session rows contain only escaped title and a plan-running dot", async () => {
  const src = await readFile(new URL("../../src/alfrd/web/js/components/chat_view.js", import.meta.url), "utf8");
  const row = src.slice(src.indexOf('    : `<li class="chat-session-item"><a'), src.indexOf('// --- session list:'));
  assert.match(row, /s.plan_status === "running"/);
  assert.match(row, /aria-label="Running"/);
  assert.doesNotMatch(row, /activeSession|countText|last_message|<time|data-chat="rename"|data-chat="delete"/);
});
