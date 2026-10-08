import test from "node:test";
import assert from "node:assert/strict";
globalThis.document = { hidden: false, addEventListener() {} };
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const { loadPlan, forgetPlan, phaseLabel, runSwitcher } = await import("../../src/alfrd/web/js/components/plans.js");

test("phase labels include local time and dates only outside today", () => {
  const now = new Date(2026, 9, 8, 10);
  assert.equal(phaseLabel({ phase: "scheduled", start_at: new Date(2026, 9, 8, 22, 45).toISOString() }, now), "Scheduled 22:45");
  assert.equal(phaseLabel({ phase: "scheduled", start_at: new Date(2026, 9, 9, 2).toISOString() }, now), "Scheduled Oct 9 02:00");
  assert.equal(phaseLabel({ phase: "review" }), "Review pending");
});
test("switcher needs multiple active runs, orders working first and marks current", () => {
  const working = { id: "old", target: "t-task", status: "running", phase: "manual", working: true, turn: 2, turns: 4 };
  assert.equal(runSwitcher({ plans: [working] }), "");
  const html = runSwitcher({ plan: working, plans: [{ id: "new", status: "running", phase: "scheduled" }, working, { id: "done", status: "finished" }] });
  assert.match(html, /2 active runs/); assert.match(html, /aria-current="true"/);
  assert.match(html, /turn 2\/4/); assert.ok(html.indexOf('pick-id="old"') < html.indexOf('pick-id="new"'));
  assert.doesNotMatch(html, /pick-id="done"/);
});
test("unpinned follows default; explicit active pin releases only on completion with other work", async () => {
  const project = "pin-test", asked = [], toasts = [];
  const ctx = { state: { mode: "server", trees: { [project]: { provider: "server" } } }, update() {}, log() {}, toast: (...a) => toasts.push(a) };
  let status = "running";
  const original = server.planStatus;
  server.planStatus = async (_, id) => { asked.push(id); return { plan: { id: id || "default", status: id ? status : "running" }, plans: [{ id: "default", working: true, status: "running" }] }; };
  try {
    await loadPlan(ctx, project); await loadPlan(ctx, project); assert.deepEqual(asked, [null, null]);
    await loadPlan(ctx, project, { id: "picked" }); await loadPlan(ctx, project); assert.equal(asked.at(-1), "picked");
    status = "finished"; await loadPlan(ctx, project); assert.equal(asked.at(-1), null); assert.equal(toasts.length, 1);
    await loadPlan(ctx, project, { id: "history" }); await loadPlan(ctx, project); assert.equal(asked.at(-1), "history");
    status = "running"; await loadPlan(ctx, project, { id: "active-again" });
    status = "finished"; await loadPlan(ctx, project, { id: "other-history" });
    assert.equal(asked.at(-1), "other-history"); assert.equal(toasts.length, 1);
  } finally { server.planStatus = original; forgetPlan(project); }
});
