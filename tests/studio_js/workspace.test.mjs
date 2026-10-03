// Project workspaces: per-project UI state, late-response isolation, removal (run with `node --test`).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document ||= { hidden: false, addEventListener() {}, querySelectorAll: () => [] };
const ws = await import("../../src/alfrd/web/js/data/workspace.js");
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const { loadPlan, planOf, activeJobs, forgetPlan } = await import("../../src/alfrd/web/js/components/plans.js");
const { renderHome } = await import("../../src/alfrd/web/js/components/overview.js");

test("scoped state follows the active project and restores on return (P1 → P2 → P1)", () => {
  const ui = ws.scoped("test-view", () => ({ search: "", selected: null, checked: new Set() }));
  ws.setActive("p1");
  ui.search = "beam"; ui.selected = "step-a"; ui.checked.add("row-1");
  ws.setActive("p2");
  assert.equal(ui.search, "", "P2 starts clean: no P1 filter leaks in");
  assert.equal(ui.selected, null);
  assert.equal(ui.checked.size, 0);
  ui.search = "other";
  ws.setActive("p1");
  assert.equal(ui.search, "beam");
  assert.equal(ui.selected, "step-a");
  assert.ok(ui.checked.has("row-1"));
  assert.equal(ws.stateOf("p2", "test-view").search, "other");
});

test("duplicate display names stay separate because workspaces are keyed by identifier", () => {
  const ui = ws.scoped("dup-view", () => ({ draft: null }));
  ws.setActive("host/a/demo"); ui.draft = "A";
  ws.setActive("host/b/demo"); ui.draft = "B";
  assert.equal(ws.stateOf("host/a/demo", "dup-view").draft, "A");
  assert.equal(ws.stateOf("host/b/demo", "dup-view").draft, "B");
});

test("All projects is its own workspace", () => {
  const ui = ws.scoped("all-view", () => ({ search: "" }));
  ws.setActive("p1"); ui.search = "p1 only";
  ws.setActive(null);
  assert.equal(ws.activeKey(), "all");
  assert.equal(ui.search, "");
});

test("a late response sees that the user switched away", () => {
  ws.setActive("p1");
  const job = ws.owner();
  assert.equal(job.still(), true);
  ws.setActive("p2");
  assert.equal(job.still(), false, "a P1 response must not paint over P2");
  assert.equal(job.project, "p1");
});

test("unsaved edits are tracked per project and removal drops only that workspace", () => {
  ws.markDirty("p1", "settings", true);
  assert.equal(ws.isDirty("p1"), true);
  assert.equal(ws.isDirty("p2"), false);
  ws.markDirty("p1", "settings", false);
  assert.equal(ws.isDirty("p1"), false);
  const ui = ws.scoped("drop-view", () => ({ v: 0 }));
  ws.setActive("gone"); ui.v = 1;
  ws.setActive("kept"); ui.v = 2;
  ws.dropWorkspace("gone");
  assert.equal(ws.stateOf("gone", "drop-view").v, 0, "recreated fresh");
  assert.equal(ws.stateOf("kept", "drop-view").v, 2);
});

function context(selected) {
  return { state: { mode: "server", selectedProject: selected, selectedTarget: null, view: "workflow",
    trees: { p1: { provider: "server" }, p2: { provider: "server" } }, workflow: { steps: [] }, targets: [], notes: {}, avica: {} },
    target: () => null, steps: () => [], projectName: (p) => p, projects: () => [{ id: "p1" }, { id: "p2" }],
    activeProject: () => (selected === "all" ? null : selected), update() {}, log() {}, showError() {}, clearError() {} };
}

test("a failed P1 run that resolves while P2 is open only raises a P1-keyed error", async () => {
  const ctx = context("p2");
  const errors = [];
  ctx.showError = (message, retry, view, key) => errors.push({ message, view, key });
  const old = server.planStatus;
  let release;
  server.planStatus = (project) => new Promise((resolve) => { release = () => resolve({ plan: { id: `${project}-run`, status: "failed" } }); });
  try {
    const pending = loadPlan(ctx, "p1");
    release();
    await pending;
  } finally { server.planStatus = old; }
  assert.equal(planOf("p1").plan.id, "p1-run");
  assert.equal(planOf("p2"), null, "nothing written into P2's cache");
  assert.deepEqual(errors.map((e) => e.key), ["plan:p1"], "the banner belongs to P1, not the active P2");
  forgetPlan("p1");
});

test("runs in several projects stay active for the Jobs tray; removal stops only that project's polling", async () => {
  const ctx = context("p2");
  const old = server.planStatus;
  server.planStatus = async (project) => ({ plan: { id: `${project}-r`, status: "running" }, units: [] });
  try {
    await loadPlan(ctx, "p1", { quiet: true });
    await loadPlan(ctx, "p2", { quiet: true });
  } finally { server.planStatus = old; }
  assert.deepEqual(activeJobs().map((j) => j.project).sort(), ["p1", "p2"]);
  forgetPlan("p1");
  assert.deepEqual(activeJobs().map((j) => j.project), ["p2"]);
  forgetPlan("p2");
});

test("Overview in All projects never borrows a project's setup card", () => {
  const ctx = context("all"), box = {};
  renderHome({ querySelector: () => box }, ctx);
  assert.equal(box.innerHTML, "");
});
