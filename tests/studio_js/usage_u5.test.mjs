import test from "node:test";
import assert from "node:assert/strict";
import { countText, quietCounts, usageUnits, usageCountsHtml } from "../../src/alfrd/web/js/components/usage_counts.js";

test("coverage never turns missing, partial-zero or invalid counts into zero", () => {
  for (const count of [null, {}, {value: null, coverage: "complete"}, {value: 0, coverage: "partial"}, {value: 8, coverage: "unavailable"}, {value: -1, coverage: "complete"}, {value: 2, coverage: "bogus"}]) assert.equal(countText(count), "Not reported");
  assert.equal(countText({value: 0, coverage: "complete"}), "0");
  assert.equal(countText({value: 3, coverage: "partial"}), "At least 3");
  assert.equal(quietCounts({counts: {tool_calls: {value: 12, coverage: "complete"}, web_search: {value: 3, coverage: "partial"}, file_edit: {value: 5, coverage: "complete"}}}), "12 tool calls · At least 3 web searches · 5 edits");
  assert.equal(quietCounts(null), "");
});

test("attempt ids include historical retries and every orchestrator kind once, scoped by request", () => {
  const tasks = ["task", "plan", "repair", "replan", "summary"].map((kind, i) => ({request: "r", kind, units: [`u${i}`], logs: [{unit: `u${i}`}, {unit: `retry${i}` }]}));
  tasks.push({request: "other", units: ["excluded"]});
  assert.deepEqual(usageUnits({tasks}, "r"), ["u0", "retry0", "u1", "retry1", "u2", "retry2", "u3", "retry3", "u4", "retry4"]);
  assert.deepEqual(usageUnits({}, "r"), []);
});

test("Usage shows exact request total and per-attempt coverage, escapes data and marks incomplete detail", () => {
  const telemetry = {attempts: 3, counts: {tool_calls: {value: 12, coverage: "partial"}}};
  const items = [{request: "r", unit: "u", title: '<img src=x>', kind: "repair", attempt: 2, status: "failed", telemetry: {counts: {tool_calls: {value: 3, coverage: "partial", reason: '<tail>'}}}}, {request: "other", unit: "x", title: "excluded"}];
  const html = usageCountsHtml({id: "r", telemetry}, null, items, ["u", "missing"], {at: 1, stale: true});
  assert.match(html, /included in tool calls/); assert.match(html, /At least 12 tool calls/);
  assert.match(html, /At least 3 tool calls/); assert.match(html, /partial: &lt;tail&gt;/);
  assert.match(html, /repair · attempt 2 · failed/); assert.match(html, /1\/2 known attempts loaded; 3 attempts in request total/);
  assert.match(html, /figures may be stale/); assert.doesNotMatch(html, /<img|excluded/);
  assert.match(usageCountsHtml({id: "r"}, null, [], [], {unsupported: true}), /Attempt detail unavailable/);
});

test("shared activity loads 32-id batches, keeps aggregates and reports failed refreshes without replacing data", async () => {
  globalThis.document = {hidden: false, addEventListener() {}, removeEventListener() {}};
  const timers = new Map(); let id = 0;
  const realSet = globalThis.setTimeout, realClear = globalThis.clearTimeout;
  globalThis.setTimeout = (fn) => {timers.set(++id, fn); return id;};
  globalThis.clearTimeout = (key) => timers.delete(key);
  const {server} = await import("../../src/alfrd/web/js/data/server.js");
  const {subscribeActivity} = await import("../../src/alfrd/web/js/data/chat_live.js");
  const original = server.request, queries = [], units = Array.from({length: 35}, (_, i) => `u${i}`);
  let fail = false, snapshot;
  server.request = async (url) => {
    const q = new URL(url, "http://local").searchParams; queries.push(q);
    if (fail) throw Error("offline");
    return {ok: true, json: async () => ({items: q.has("units") ? q.get("units").split(",").map((unit) => ({unit, request: "r", status: "failed"})) : [], telemetry: {requests: {r: {attempts: 35, counts: {tool_calls: {value: 9, coverage: "partial"}}}}}})};
  };
  let sub;
  const tick = async () => {const [key, fn] = timers.entries().next().value; timers.delete(key); await fn();};
  try {
    sub = subscribeActivity("p", "u5", (s) => {snapshot = s;}, {visible: () => true, busy: () => false, units: () => units.map((u) => [u, 40])});
    await tick();
    assert.equal(queries.length, 3); assert.equal(queries[1].get("units").split(",").length, 32); assert.equal(queries[2].get("units").split(",").length, 3);
    assert.equal(snapshot.items.length, 35); assert.equal(snapshot.telemetry.requests.r.attempts, 35);
    assert.equal(snapshot.stale, false);
    fail = true; sub.kick(); await tick();
    assert.equal(snapshot.stale, true); assert.equal(snapshot.items.length, 35);
    fail = false; sub.kick(); await tick(); assert.equal(snapshot.stale, false);
  } finally {sub?.stop(); server.request = original; globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear;}
});
