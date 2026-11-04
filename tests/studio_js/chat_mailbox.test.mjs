import test from "node:test";
import assert from "node:assert/strict";
import { mergeMailbox, subscribeActivity, subscribeMailbox } from "../../src/alfrd/web/js/data/chat_live.js";
import { server } from "../../src/alfrd/web/js/data/server.js";

test("mailbox cursor replay deduplicates messages and terminal events", () => {
  const question = { id: "q", seq: 1, state: "pending", version: 1, kind: "question" };
  const event = { event_seq: 1, type: "resolved", message_id: "q", data: { reply_id: "a" } };
  const page = { messages: [question, { id: "a", seq: 2, kind: "reply", reply_to: "q" }], events: [event] };
  const first = mergeMailbox([], page);
  assert.deepEqual(first.map((m) => m.id), ["q", "a"]);
  assert.equal(first[0].state, "resolved");
  assert.deepEqual(mergeMailbox(first, { events: [event] }), first);
  assert.deepEqual(mergeMailbox(first, page), first, "replayed pending messages cannot reopen resolved cards");
  assert.equal(mergeMailbox(first, { events: [{ ...event, type: "expired", event_seq: 2 }] })[0].state, "expired");
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
test("activity shares polling, batches expanded tails, finalizes output and retains stale snapshots", async () => {
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
  const original = server.request, calls = [];
  let fail = false, done = false, latest;
  server.request = async (url) => {
    calls.push(url);
    if (fail) throw new Error("offline");
    const params = new URL(url, "http://test").searchParams;
    return { ok: true, status: 200, json: async () => ({ items: params.has("units") ? params.get("units").split(",").map((unit) => ({ unit, row: unit, status: done ? "done" : "running", display_tail: [done ? "final" : "live"] })) : done ? [] : [{ unit: "u0", row: "u0", status: "running", display_tail: ["live"] }] }) };
  };
  const units = Array.from({ length: 35 }, (_, i) => [`u${i}`, 100]);
  const a = subscribeActivity("p", "s", (s) => { latest = s; }, { busy: () => true, units: () => units });
  const b = subscribeActivity("p", "s", () => {}, { busy: () => true });
  try {
    await sleep(180);
    assert.equal(calls.filter((url) => !url.includes("units=")).length, 1);
    assert.equal(calls.filter((url) => url.includes("units=")).length, 2);
    assert.equal(latest.items.length, 35);
    done = true; a.kick(); await sleep(180);
    assert.equal(latest.items.find((r) => r.unit === "u0").display_tail[0], "final");
    fail = true; a.kick(); await sleep(180);
    assert.equal(latest.stale, true);
    assert.equal(latest.items.length, 35);
  } finally { a.stop(); b.stop(); server.request = original; }
});

test("pending mail polls without running units, drains cursor pages and refreshes after visibility return", async () => {
  const handlers = new Map();
  globalThis.document = { hidden: false, addEventListener(n, f) { handlers.set(n, f); }, removeEventListener(n) { handlers.delete(n); } };
  const original = server.request, calls = [];
  let snapshot;
  server.request = async (url) => {
    calls.push(url);
    const after = new URL(url, "http://test").searchParams.get("after");
    return { status: 200, ok: true, json: async () => url.endsWith("pending") ? { messages: [{ id: "q", seq: 1, state: "pending", kind: "question", recipient: { kind: "user" } }] }
      : !after ? { messages: [{ id: "q", seq: 1 }], next_cursor: "one", has_more: true }
      : { messages: [{ id: "info", seq: 2 }], next_cursor: "two", has_more: false } };
  };
  const subscription = subscribeMailbox("p", "s", "r", (s) => { snapshot = s; }, { busy: () => false });
  try {
    await sleep(200);
    assert.equal(calls.length, 3);
    assert.equal(snapshot.messages.length, 2);
    assert.equal(snapshot.messages[0].state, "pending");
    await sleep(2100);
    assert.ok(calls.length >= 4, "unanswered mail uses the active cadence");
    document.hidden = true; subscription.kick(); await sleep(180); const hiddenCalls = calls.length;
    document.hidden = false; handlers.get("visibilitychange")(); await sleep(180);
    assert.equal(calls.length, hiddenCalls + 1);
  } finally { subscription.stop(); server.request = original; }
});

test("terminal mailbox state survives stale pages and newer cancellation wins", () => {
  const resolved = { id: "p", seq: 1, kind: "permission_request", state: "resolved", version: 2 };
  const cancelled = mergeMailbox([resolved], { events: [{ event_seq: 3, type: "cancelled", message_id: "p" }] });
  const stale = mergeMailbox(cancelled, { messages: [{ ...resolved, state: "pending", version: 1 }] });
  assert.equal(stale[0].state, "cancelled");
  assert.equal(stale[0].version, 2);
  assert.equal(stale[0].resolution.event_seq, 3);
});
