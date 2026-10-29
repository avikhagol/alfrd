import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import * as chat from "../../src/alfrd/web/js/data/chat.js";
import { createRequestScope } from "../../src/alfrd/web/js/components/inspector.js";

const now = 200_000;
const state = () => ({ requests: [
  { id: "r1", status: "done", created_ts: 100, finished: "1970-01-01T00:02:00Z", usage: { input_tokens: 100, input_uncached_tokens: 60, cache_read_input_tokens: 40, output_tokens: 20, total_cost_usd: .25 } },
  { id: "r2", status: "running", created_ts: 150, usage: { input_tokens: 200, output_tokens: 30, total_cost_usd: .5 } },
  { id: "r3", status: "cancelled", created_ts: 120, finished: "1970-01-01T00:02:10Z" },
] });

test("session totals sum each request including overlapping wall time and live elapsed time", () => {
  const s = state(), t = chat.requestTotals(s, now);
  assert.deepEqual([t.count, t.seconds, t.ongoing], [3, 80, 1]);
  assert.deepEqual(t.usage, { input_tokens: 300, input_uncached_tokens: 260, cache_read_input_tokens: 40, cache_creation_input_tokens: null, output_tokens: 50, total_cost_usd: .75 });
  assert.equal(t.seconds, s.requests.reduce((sum, r) => sum + chat.requestProgress(s, r, now).seconds, 0));
  assert.equal(t.usage.total_cost_usd, s.requests.reduce((sum, r) => sum + chat.requestProgress(s, r, now).cost, 0));
  assert.equal(t.usage.input_tokens + t.usage.output_tokens, s.requests.reduce((sum, r) => sum + chat.requestProgress(s, r, now).tokens, 0));
  assert.equal(chat.requestTotals(s, now + 1000).seconds, 81);
  s.requests[1].finished = "1970-01-01T00:03:20Z";
  s.requests[1].status = "done";
  assert.equal(chat.requestTotals(s, now + 10000).seconds, 80);
  assert.equal(chat.requestTotals(s, now + 10000).ongoing, 0);
});

test("empty and missing usage stays unknown; malformed timing and nonfinite usage are skipped", () => {
  assert.deepEqual(chat.requestTotals().count, 0);
  const s = { requests: [
    { status: "done", created_ts: 100 },
    { status: "running", created: "bad", usage: { input_tokens: NaN, output_tokens: Infinity, total_cost_usd: null } },
    { status: "done", created_ts: 100, finished: "bad" },
    { status: "done", created_ts: 100, finished: "1970-01-01T00:01:30Z", usage: { input_tokens: 0, output_tokens: 0, total_cost_usd: 0 } },
  ] };
  const t = chat.requestTotals(s, now);
  assert.deepEqual([t.count, t.seconds, t.ongoing], [4, 0, 1]);
  assert.deepEqual([t.usage.input_tokens, t.usage.output_tokens, t.usage.total_cost_usd], [0, 0, 0]);
  assert.equal(chat.requestTotals({ requests: [{}] }).usage.total_cost_usd, null);
  assert.doesNotMatch(chat.tokenLine(t.usage), /NaN|undefined|Infinity/);
});

test("scope changes render totals or the selected request and keep unknown totals readable", async () => {
  const source = await readFile(new URL("../../src/alfrd/web/js/components/chat_view.js", import.meta.url), "utf8");
  const start = source.indexOf("function drawProgress(ctx)");
  const body = source.slice(start, source.indexOf("\n}\n", start) + 2);
  const S = { data: state() }, scope = createRequestScope(), box = { dataset: {}, innerHTML: "" };
  const deps = { ...chat, S, scope, host: {}, $: () => box, server: { session: {} }, esc: String, icon: () => "", meter: (label, value, max, ratio, shown) => `${label}: ${shown}` };
  const draw = new Function(...Object.keys(deps), `${body}; return drawProgress;`)(...Object.values(deps));
  const realNow = Date.now;
  Date.now = () => now;
  try {
    scope.select("all"); draw();
    assert.match(box.innerHTML, /All requests.*3 requests.*1 ongoing/s);
    assert.match(box.innerHTML, /in 300 · out 50/);
    assert.match(box.innerHTML, /Total cost: \$0.750/);
    assert.match(box.innerHTML, /Total run time: 1m 20s \(ongoing\)/);
    scope.select("r1"); draw();
    assert.match(box.innerHTML, /Request r1/);
    assert.match(box.innerHTML, /Time: 20s/);
    assert.doesNotMatch(box.innerHTML, /Total run time/);
    scope.select("current"); draw();
    assert.match(box.innerHTML, /Request r2/);
    assert.match(box.innerHTML, /50s \(ongoing\)/);
    scope.select("all"); S.data = { requests: [{}] }; draw();
    assert.match(box.innerHTML, /1 request/);
    assert.match(box.innerHTML, /in — · out —/);
    assert.match(box.innerHTML, /Total cost: —/);
    assert.doesNotMatch(box.innerHTML, /NaN|undefined|Infinity/);
  } finally { Date.now = realNow; }
});
