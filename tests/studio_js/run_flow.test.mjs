import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {} };
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const { loadPlan, activePlan, openRunDialog, renderSchedule, planAct, RUN_MODE, RUN_FAILURE, CELL, RUN_STATUS } = await import("../../src/alfrd/web/js/components/plans.js");
const { STEP_STATUS } = await import("../../src/alfrd/web/js/data/model.js");
const { renderHome } = await import("../../src/alfrd/web/js/components/overview.js");
const { showRun } = await import("../../src/alfrd/web/js/components/canvas.js");
const { showLogs, render: renderLogs } = await import("../../src/alfrd/web/js/components/logs.js");

function context(project) {
  return { state: { mode: "server", selectedProject: project, selectedTarget: null,
    trees: { [project]: { provider: "server" } }, workflow: { steps: [] }, notes: {}, avica: {} },
    target: () => null, steps: () => [], projectName: (p) => p, projects: () => [],
    update() {}, log() {}, showError() {}, setFooterRight() {}, navigate() {} };
}

test("unchanged run responses skip updates while polling remains armed", async () => {
  const ctx = context("poll-test"); let updates = 0, timer;
  ctx.update = () => updates++;
  const oldStatus = server.planStatus, oldSet = globalThis.setTimeout, oldClear = globalThis.clearTimeout;
  let status = { plan: { id: "run-1", status: "running" }, units: [] };
  server.planStatus = async () => structuredClone(status);
  globalThis.setTimeout = (callback) => { timer = callback; return 1; };
  globalThis.clearTimeout = () => {};
  try {
    await loadPlan(ctx, "poll-test"); assert.equal(updates, 1);
    await timer(); await Promise.resolve(); await Promise.resolve();
    await loadPlan(ctx, "poll-test"); assert.equal(updates, 1); assert.equal(typeof timer, "function");
    status.plan.status = "finished";
    await loadPlan(ctx, "poll-test"); assert.equal(updates, 2);
  } finally { server.planStatus = oldStatus; globalThis.setTimeout = oldSet; globalThis.clearTimeout = oldClear; }
});

test("a finished run offers results and logs for that run", async () => {
  const ctx = context("finished-test"), box = {};
  const old = server.planStatus;
  server.planStatus = async () => ({ plan: { id: "run-done", status: "finished" }, table: { steps: [], rows: [] }, plans: [] });
  try { await loadPlan(ctx, "finished-test"); } finally { server.planStatus = old; }
  renderSchedule(box, ctx, "finished-test");
  assert.match(box.innerHTML, /href="#\/results">View results/);
  assert.match(box.innerHTML, /data-plan="logs"><svg [^>]*>.*<\/svg> View logs/);
  let filter;
  ctx.openLogs = (opts) => filter = opts;
  await planAct(ctx, "finished-test", "logs");
  assert.deepEqual(filter, { group: "plan:run-done" });
});

test("opening a run switches project and clears an unrelated target", () => {
  const ctx = context("old"); ctx.state.selectedTarget = "old-target";
  ctx.target = () => ({ project: "old" }); let view;
  ctx.navigate = (value) => view = value;
  showRun(ctx, "new");
  assert.equal(view, "workflow"); assert.equal(ctx.state.selectedProject, "new"); assert.equal(ctx.state.selectedTarget, null);
});

test("run logs ignore a selected target and exclude other runs", () => {
  const ctx = context("logs-test"); let view;
  ctx.navigate = (value) => view = value;
  ctx.state.trees["logs-test"].provider = "browser";
  ctx.state.trees["logs-test"].logs = [];
  ctx.state.trees["logs-test"].logFiles = [
    { project: "logs-test", rel: "a.log", name: "a.log", groups: ["plan:one"] },
    { project: "logs-test", rel: "b.log", name: "b.log", groups: ["plan:two"] },
  ];
  const parts = { "#lg-head": {}, "#lg-body": {} };
  showLogs(ctx, { group: "plan:one" });
  renderLogs({ querySelector: (key) => parts[key] }, ctx);
  assert.equal(view, "logs"); assert.match(parts["#lg-body"].innerHTML, /a.log/); assert.doesNotMatch(parts["#lg-body"].innerHTML, /b.log/);
});

test("run choices have plain labels while preserving API enum keys", () => {
  assert.equal(RUN_MODE.target, "One target at a time");
  assert.equal(RUN_FAILURE.stop_plan, "Stop the whole run");
});


test("step and cell statuses have distinct labels", () => {
  for (const statuses of [STEP_STATUS, CELL]) {
    const labels = Object.values(statuses).map((status) => status.label);
    assert.equal(new Set(labels).size, labels.length);
  }
});

test("background failures stay quiet and viewing a failed run offers a real retry that clears on recovery", async () => {
  const ctx = context("recovery-test");
  let banner = null, cleared = 0, action;
  ctx.toast = () => {};
  ctx.showError = (message, retry, view, key) => { banner = { message, retry, view, key }; };
  ctx.clearError = (view, key) => { assert.equal(view, banner.view); assert.equal(key, banner.key); banner = null; cleared++; };
  const oldStatus = server.planStatus, oldAction = server.planAction;
  let status = "failed";
  server.planStatus = async () => ({ plan: { id: "recover", status } });
  server.planAction = async (...args) => { action = args; status = "finished"; };
  try {
    ctx.state.view = "overview";
    await loadPlan(ctx, "recovery-test", { quiet: true });
    assert.equal(banner, null);
    ctx.state.view = "workflow";
    await loadPlan(ctx, "recovery-test");
    assert.match(banner.message, /retry failed steps/);
    await banner.retry();
    assert.deepEqual(action, ["recovery-test", "recover", "resume", { retry_failed: true }]);
    assert.equal(banner, null); assert.equal(cleared, 1);
    status = "failed";
    await loadPlan(ctx, "recovery-test");
    assert.ok(banner, "a subsequent failure is visible again");
  } finally { server.planStatus = oldStatus; server.planAction = oldAction; }
});

test("Overview uses the shared label for every run status", async () => {
  const old = server.planStatus;
  try {
    for (const [status, label] of Object.entries(RUN_STATUS)) {
      const project = `home-${status}`, ctx = context(project), box = {};
      ctx.state.targets = []; ctx.state.view = "overview";
      server.planStatus = async () => ({ plan: { id: "home-run", status } });
      await loadPlan(ctx, project);
      renderHome({ querySelector: () => box }, ctx);
      assert.ok(box.innerHTML.includes(`<span>${label}</span>`), `${status}: ${label}`);
    }
  } finally { server.planStatus = old; }
});


test("active runs are scoped to the requested task across run history", async () => {
  const ctx = context("task-scope"), old = server.planStatus;
  server.planStatus = async () => ({
    plan: { id: "idle", status: "finished", targets: ["fix-login"] },
    plans: [{ id: "busy", status: "running", targets: ["task"] }],
    table: { rows: [{ target: "fix-login" }] },
  });
  try {
    await loadPlan(ctx, "task-scope");
    assert.equal(activePlan("task-scope", "task").plan.id, "busy");
    assert.equal(activePlan("task-scope", "fix-login"), null);
  } finally { server.planStatus = old; }
});


test("Run fetches execution settings for the selected task before rendering", async () => {
  const ctx = context("iterations-test"), old = server.executionInfo;
  let requested, html;
  ctx.toast = () => {};
  ctx.modal = (value) => { html = value; };
  server.executionInfo = async (project, target) => {
    requested = { project, target };
    return { configured: false, error: "test response" };
  };
  try {
    await openRunDialog(ctx, "iterations-test", { target: "fix-login" });
    assert.deepEqual(requested, { project: "iterations-test", target: "fix-login" });
    assert.match(html, /test response/);
  } finally { server.executionInfo = old; }
});
