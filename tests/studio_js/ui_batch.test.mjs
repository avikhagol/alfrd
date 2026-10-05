// October 2026 UI batch: run history export, history rows, exact-run loading, keyed scroll.
import test from "node:test";
import assert from "node:assert/strict";

globalThis.window ||= globalThis;
globalThis.document ||= { addEventListener() {}, hidden: false, activeElement: null };
globalThis.location ||= { origin: "http://localhost", protocol: "http:" };

const h = await import("../../src/alfrd/web/js/data/run_history.js");
const rg = await import("../../src/alfrd/web/js/data/run_grid.js");
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const { loadPlan, planOf } = await import("../../src/alfrd/web/js/components/plans.js");
const { keepScroll } = await import("../../src/alfrd/web/js/utils/dom.js");

const plan = (id, status, extra = {}) => ({ plan: { id, status, created: "2026-10-05T10:00:00", steps: ["i1-a", "i1-b", "i2-a", "i2-b"], loop: { iterations: 2 }, ...extra },
  table: { rows: [{ key: "r", cells: { "i1-a": "done", "i1-b": "done", "i2-a": "running", "i2-b": "todo" } }] } });

test("run summary: active run has no finish or runtime; ended run uses saved counts", () => {
  const live = h.runSummary("p", "P", plan("r2", "running"), [{ started: "2026-10-05T10:01:00" }]);
  assert.equal(live.finished_at, null);
  assert.equal(live.runtime_seconds, null);
  assert.equal(live.completed_turns, 2);
  assert.equal(live.total_turns, 4);
  assert.equal(live.run_label, "Run r2");
  const done = h.runSummary("p", "P", plan("r1", "failed", { counts: { done: 3, failed: 1 }, runner: { stopped: "2026-10-05T10:11:00" } }),
    [{ started: "2026-10-05T10:05:00" }, { started: "2026-10-05T10:01:00" }]);
  assert.equal(done.started_at, null, "no timezone recorded: omitted");
  assert.equal(done.ran, true);
  assert.equal(done.runtime_seconds, 600, "earliest handoff start to the runner's stop");
  assert.equal(done.completed_turns, 3);
  const unstarted = h.runSummary("p", "P", { ...plan("r0", "running"), table: { error: "bad csv", rows: [] } }, []);
  assert.equal(unstarted.ran, false);
  assert.equal(unstarted.completed_turns, null, "an unreadable table is unknown, not zero");
});

test("timestamps: offsets become UTC; no recorded timezone or invalid is omitted", () => {
  assert.equal(h.isoTime("2026-10-05T12:00:00+02:00"), "2026-10-05T10:00:00.000Z");
  assert.equal(h.isoTime("2026-10-05T12:00:00+0200"), "2026-10-05T10:00:00.000Z");
  assert.equal(h.isoTime("2026-10-05T10:00:00Z"), "2026-10-05T10:00:00.000Z");
  assert.equal(h.isoTime("2026-10-05T12:00:00"), null, "server-local: the browser's zone is not the server's");
  assert.equal(h.isoTime("not a time+02:00"), null);
  assert.equal(h.isoTime(""), null);
  assert.equal(h.isoTime(null), null);
});

test("export omits timezone-less timestamps as empty CSV cells and JSON null, but keeps runtime", () => {
  const run = h.runSummary("p", "P", plan("r1", "finished", { counts: { done: 4 }, runner: { stopped: "2026-10-05T10:11:00" } }), [{ started: "2026-10-05T10:01:00" }]);
  assert.equal(run.created_at, null);
  assert.equal(run.started_at, null);
  assert.equal(run.finished_at, null);
  assert.equal(run.runtime_seconds, 600, "a difference of two server-local stamps needs no zone");
  assert.equal(run.ran, true, "a run with turns still has results");
  const csv = h.historyCsv([run]).trim().split("\n")[1].split(",");
  assert.deepEqual(csv.slice(5, 9), ["", "", "", "600"]);
  const json = JSON.parse(h.historyJson([run], ["p"]));
  assert.equal(json.runs[0].created_at, null);
  assert.equal(json.runs[0].finished_at, null);
  assert.equal("ran" in json.runs[0], false, "only schema v1 fields are exported");
  const offset = h.runSummary("p", "P", plan("r2", "finished", { created: "2026-10-05T12:00:00+02:00", counts: {}, runner: { stopped: "2026-10-05T12:30:00+02:00" } }), [{ started: "2026-10-05T12:10:00+02:00" }]);
  assert.deepEqual([offset.created_at, offset.started_at, offset.finished_at], ["2026-10-05T10:00:00.000Z", "2026-10-05T10:10:00.000Z", "2026-10-05T10:30:00.000Z"]);
});

test("history window: mixed loop and non-loop plans give only loop runs, in server order", async () => {
  // The server lists its newest 20 plans; non-loop plans among them leave fewer loop runs.
  const refs = Array.from({ length: 20 }, (_, i) => ({ id: `p${String(20 - i).padStart(2, "0")}` }));
  const isLoop = (id) => Number(id.slice(1)) % 3 !== 0;
  const api = {
    planStatus: async (p, id) => ({ plan: { id: id || refs[0].id, status: "finished", counts: {}, ...(isLoop(id || refs[0].id) ? { loop: { iterations: 1 } } : {}) }, plans: refs }),
    handoffs: async () => ({ handoffs: [] }),
  };
  const runs = await h.collectHistory(api, [{ id: "a", name: "A" }]);
  assert.deepEqual(runs.map((r) => r.run_id), refs.map((r) => r.id).filter(isLoop));
  assert.equal(runs.length, 14);
});

test("history sheet and export menu disclose the 20-plan window", async () => {
  const html = rg.renderRunHistory("p", "P", { groups: [] }, { groups: [] }, [{ id: "r1", status: "finished" }], () => null, {}, {});
  assert.match(html, /Loop runs among the newest 20 plans per project\./);
  const src = await import("node:fs").then((fs) => fs.readFileSync(new URL("../../src/alfrd/web/js/components/overview.js", import.meta.url), "utf8"));
  assert.match(src, /Loop runs among the newest 20 plans per project\. Timestamps without a recorded timezone are omitted\./);
  assert.doesNotMatch(src, /All runs in the selected project scope/);
});

test("collectHistory reads every loop run of the project, skips non-loop plans, and fails whole", async () => {
  const api = {
    planStatus: async (p, id) => (id === "old" ? { plan: { id: "old", status: "finished" }, plans: [] } : { ...plan(id || "new", "finished", { counts: { done: 4 } }), plans: [{ id: "new" }, { id: "mid" }, { id: "old" }] }),
    handoffs: async () => ({ handoffs: [] }),
  };
  const runs = await h.collectHistory(api, [{ id: "p", name: "P" }]);
  assert.deepEqual(runs.map((r) => r.run_id), ["new", "mid"]);
  await assert.rejects(h.collectHistory({ ...api, handoffs: async () => { throw new Error("boom"); } }, [{ id: "p", name: "P" }]), /boom/);
});

test("CSV escapes quotes and newlines and neutralises formulas; JSON keeps numbers and nulls", () => {
  const run = { project_id: "p", project_name: '=HYPERLINK("x")', run_id: "r", run_label: "Run r", status: "finished", created_at: "a\nb",
    started_at: null, finished_at: null, runtime_seconds: 12, iterations: 2, completed_turns: 4, total_turns: 4 };
  const csv = h.historyCsv([run]);
  assert.equal(csv.split("\n")[0], h.HISTORY_FIELDS.join(","));
  assert.match(csv, /"'=HYPERLINK\(""x""\)"/);
  assert.match(csv, /"a\nb"/);
  const json = JSON.parse(h.historyJson([run], ["p"], new Date("2026-10-05T00:00:00Z")));
  assert.equal(json.schema_version, 1);
  assert.deepEqual(json.scope, { project_ids: ["p"] });
  assert.equal(json.runs[0].runtime_seconds, 12);
  assert.equal(json.runs[0].started_at, null);
  assert.equal(json.runs[0].project_name, '=HYPERLINK("x")', "JSON keeps the original text");
  assert.equal(h.historyFilename("My Project/../x", "csv", new Date("2026-10-05T00:00:00Z")), "alfrd-run-history-my-project-x-20261005.csv");
});

test("history rows: filters, exact-run actions, two-line label, no results yet", () => {
  const runs = [{ id: "r3", status: "running" }, { id: "r2", status: "failed" }, { id: "r1", status: "finished" }];
  assert.deepEqual(rg.filterHistory(runs, { status: "failed" }).map((r) => r.id), ["r2"]);
  assert.deepEqual(rg.filterHistory(runs, { search: "r1" }).map((r) => r.id), ["r1"]);
  const sums = { r3: { ran: false, completed_turns: 0, total_turns: 4 }, r2: { ran: true, started_at: null, completed_turns: 1, total_turns: 4, runtime_seconds: 65 } };
  const html = rg.renderRunHistory("p", "P", { groups: [] }, { groups: [] }, runs, (id) => sums[id] || null, {}, { runLabel: (s) => s });
  assert.match(html, /data-rh-open="p" data-rh-run="r2"/);
  assert.match(html, /aria-label="View results for run r2"/);
  assert.match(html, /class="rh-label"/);
  assert.match(html, /data-rh-run="r3"[^>]*aria-label="View results for run r3" disabled/);
  assert.match(html, /No results yet/);
  assert.match(html, /1m 05s/);
  assert.match(rg.renderRunHistory("p", "P", { empty: "none", groups: [] }, {}, [], () => null), /No runs yet\.<\/b> Edit task\.md/);
  assert.match(rg.renderRunHistory("p", "P", { groups: [] }, {}, runs, () => null, { status: "cancelled" }), /No runs match these filters/);
});

test("Open run loads that run even while another read is in flight", async () => {
  const ctx = { state: { mode: "server", trees: { q: { provider: "server" } } }, update() {}, log() {}, showError() {}, clearError() {} };
  const old = server.planStatus;
  const asked = [];
  let release;
  server.planStatus = (p, id) => { asked.push(id); return id === "old" || id === null ? new Promise((r) => { release = () => r({ plan: { id: "newest", status: "finished" } }); }) : Promise.resolve({ plan: { id, status: "finished" } }); };
  try {
    const first = loadPlan(ctx, "q");
    const second = loadPlan(ctx, "q", { id: "old-run" });
    assert.equal(planOf("q"), null, "the newest run is never shown in its place");
    release();
    await first; await second;
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(planOf("q").plan.id, "old-run");
    assert.deepEqual(asked, [null, "old-run"]);
  } finally { server.planStatus = old; }
});

test("keepScroll restores keyed offsets and focus after a re-render", () => {
  globalThis.CSS ||= { escape: (s) => s };
  const make = (key, focusKey) => ({ dataset: { scrollKey: key, focusKey }, scrollTop: 0, scrollLeft: 0, focus() { document.activeElement = this; } });
  let views = [make("rg|p|r1")];
  const root = { scrollTop: 5, scrollLeft: 0, contains: (el) => views.includes(el),
    querySelectorAll: () => views, querySelector: (sel) => views.find((v) => sel.includes(v.dataset.scrollKey)) || null };
  views[0].scrollLeft = 900;
  document.activeElement = views[0];
  keepScroll(root, () => { views = [make("rg|p|r1")]; root.scrollTop = 0; });
  assert.equal(views[0].scrollLeft, 900);
  assert.equal(root.scrollTop, 5);
  assert.equal(document.activeElement, views[0]);
});
