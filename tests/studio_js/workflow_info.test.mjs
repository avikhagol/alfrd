import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {} };
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const { loadPlan, forgetPlan, runTargets } = await import("../../src/alfrd/web/js/components/plans.js");
const { infoHead, scopeRun } = await import("../../src/alfrd/web/js/components/canvas.js");

const targets = [
  { id: "a:T1", project: "a", name: "T1", columns: { RA: "12h", CORRELATOR: "DiFX" } },
  { id: "a:T2", project: "a", name: "T2", columns: {} },
  { id: "b:T9", project: "b", name: "T9", columns: {} },
];
function context(project, selected = null) {
  const state = { mode: "server", selectedProject: project, selectedTarget: selected, targets,
    trees: { a: { provider: "server" }, b: { provider: "server" } } };
  return { state, target: (id = state.selectedTarget) => targets.find((t) => t.id === id) || null,
    projectName: (p) => p.toUpperCase(), update() {}, log() {}, showError() {}, clearError() {}, toast() {} };
}
async function withStatus(project, status, fn) {
  const old = server.planStatus;
  server.planStatus = async () => structuredClone(status);
  try { await loadPlan(context(project), project, { quiet: true }); return fn(); } finally { server.planStatus = old; forgetPlan(project); }
}

test("run target wins over a different header selection", () => withStatus("a", {
  plan: { id: "r1", status: "running", targets: ["T2"] }, plans: [{ id: "r1", status: "running", phase: "running", working: true }],
}, () => {
  const h = infoHead(context("a", "a:T1"), "a");
  assert.equal(h.title, "A / Target: T2");
  assert.equal(h.run.label, "Running");
  assert.match(h.line, /Current run r1 · Header target T1 is not in this run/);
}));

test("scheduled runs label their start; facts come from the run target", () => withStatus("a", {
  plan: { id: "r2", status: "running", start_at: "2099-01-01T08:30:00", targets: ["T1"] }, plans: [{ id: "r2", status: "running", phase: "scheduled" }],
}, () => {
  const h = infoHead(context("a", null), "a");
  assert.equal(h.title, "A / Target: T1");
  assert.match(h.run.label, /^Scheduled /);
  assert.equal(h.correlator, "DiFX");
  assert.match(h.line, /^RA: 12h · Current run r2$/);
}));

test("finished run falls back to the last run and uses its table rows", () => withStatus("a", {
  plan: { id: "r3", status: "finished" }, table: { rows: [{ target: "T1" }, { target: "T2" }] }, plans: [],
}, () => {
  const h = infoHead(context("a", null), "a");
  assert.equal(h.title, "A / Targets (2): T1, T2");
  assert.equal(h.run.label, "Done");
  assert.match(h.line, /^Last run r3$/);
}));

test("a header target in the shown run scopes the title to it", () => withStatus("a", {
  plan: { id: "r3", status: "finished" }, table: { rows: [{ target: "T1" }, { target: "T2" }] }, plans: [],
}, () => {
  const h = infoHead(context("a", "a:T1"), "a");
  assert.equal(h.title, "A / Target: T1");
  assert.equal(h.correlator, "DiFX");
  assert.equal(h.run.label, "Done");
  assert.equal(h.line, "RA: 12h · Last run r3 (with 1 other target)");
}));

test("a header target with no run says so instead of showing another target's run", () => withStatus("a", {
  plan: { id: "r4", status: "finished", targets: ["T2"] }, plans: [{ id: "r4", status: "finished", targets: ["T2"] }],
}, () => {
  const h = infoHead(context("a", "a:T1"), "a");
  assert.equal(h.title, "A / Target: T1");
  assert.equal(h.run, undefined);
  assert.match(h.line, /No runs for T1 yet\. Start one with Run/);
}));

async function scoped(status, selected) {
  const asked = [], old = server.planStatus, ctx = context("a", selected);
  server.planStatus = async (p, id) => { asked.push(id); return structuredClone(id ? { ...status, plan: { id, status: "finished", targets: ["T1"] }, selected: "id" } : status); };
  try {
    await loadPlan(ctx, "a", { quiet: true });
    scopeRun(ctx, "a"); scopeRun(ctx, "a"); // once per header target
    await new Promise((r) => setTimeout(r, 0));
    return asked;
  } finally { server.planStatus = old; forgetPlan("a"); scopeRun(context("a"), "a"); }
}

test("selecting a target loads its newest run, an active one first, once", async () => {
  const plans = [{ id: "new", status: "finished", targets: ["T2"] }, { id: "mid", status: "finished", targets: ["T1"] },
    { id: "act", status: "paused", targets: ["T1", "T2"] }];
  assert.deepEqual(await scoped({ plan: { id: "new", status: "finished", targets: ["T2"] }, plans }, "a:T1"), [null, "act"]);
  assert.deepEqual(await scoped({ plan: { id: "new", status: "finished", targets: ["T2"] }, plans: plans.slice(0, 2) }, "a:T1"), [null, "mid"]);
});

test("a run loaded for the header target is the last run, not a hand-picked one", async () => {
  const ctx = context("a", "a:T1"), old = server.planStatus;
  const plans = [{ id: "new", status: "finished", targets: ["T2"] }, { id: "mid", status: "finished", targets: ["T1"] }];
  server.planStatus = async (p, id) => structuredClone(id ? { plan: { ...plans[1] }, plans, selected: "id" } : { plan: { ...plans[0] }, plans, selected: "default" });
  try {
    await loadPlan(ctx, "a", { quiet: true });
    scopeRun(ctx, "a");
    await new Promise((r) => setTimeout(r, 0));
    const h = infoHead(ctx, "a");
    assert.equal(h.title, "A / Target: T1");
    assert.equal(h.line, "RA: 12h · Last run mid");
  } finally { server.planStatus = old; forgetPlan("a"); scopeRun(context("a"), "a"); }
});

test("the shown run is kept when it has the target; no target or no run loads nothing", async () => {
  const plans = [{ id: "r", status: "finished", targets: ["T1"] }];
  assert.deepEqual(await scoped({ plan: plans[0], plans }, "a:T1"), [null]);
  assert.deepEqual(await scoped({ plan: plans[0], plans }, null), [null]);
  assert.deepEqual(await scoped({ plan: plans[0], plans }, "a:T2"), [null]);
});

test("a run picked from history is labelled as selected", () => withStatus("a", {
  plan: { id: "old", status: "failed", target: "T2" }, selected: "id", plans: [],
}, () => assert.match(infoHead(context("a"), "a").line, /^Selected run old$/)));

test("many targets are clipped with the full list in the tooltip", () => {
  const r = runTargets({ plan: { id: "x", status: "running", targets: "A, B,C,D,E" } });
  assert.deepEqual(r.targets, ["A", "B", "C", "D", "E"]);
  return withStatus("a", { plan: { id: "x", status: "running", targets: ["A", "B", "C", "D", "E"] }, plans: [] }, () => {
    const h = infoHead(context("a"), "a");
    assert.equal(h.title, "A / Targets (5): A, B, C +2 more"); assert.equal(h.full, "A, B, C, D, E");
  });
});

test("project switch: each project shows its own run, never another project's target", async () => {
  await withStatus("b", { plan: { id: "rb", status: "running", targets: ["T9"] }, plans: [] }, () => {
    assert.equal(infoHead(context("b", "a:T1"), "b").title, "B / Target: T9");
    assert.doesNotMatch(infoHead(context("b", "a:T1"), "b").line, /Header target/);
  });
  // Project a has no cached status: never substitute the header selection.
  const h = infoHead(context("a", "a:T1"), "a");
  assert.equal(h.title, "A / Loading runs…"); assert.doesNotMatch(h.title + h.line, /T1/);
});

test("no runs yet and offline mode", async () => {
  await withStatus("a", { plan: null, plans: [] }, () => assert.match(infoHead(context("a", "a:T1"), "a").line, /Start one with Run/));
  const ctx = context("a", "a:T1"); ctx.state.mode = "static";
  assert.equal(infoHead(ctx, "a").title, "A / Target: T1");
});
