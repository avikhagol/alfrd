// Audit every workflow-only menu callback without starting real jobs or editing user files.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
const source = readFileSync(new URL("../../src/alfrd/web/js/components/canvas_ops.js", import.meta.url), "utf8");
function fixture() {
  const calls = [], menus = [];
  const record = name => (...args) => calls.push([name, ...args]);
  const status = { plan: { id: "p1", status: "running", loop: false }, units: [{ id: "u1", row: "row", steps: ["a"], status: "running", log: "run.log" }] };
  const cell = { value: "running" };
  const api = { canEdit: () => true, steps: () => [{ key: "a", depends: ["before"] }, { key: "after", depends: ["a"] }],
    plans: () => true, project: () => "p", overlay: () => ({ row: { key: "row", target: "T", cells: { a: cell.value } } }),
    select: record("select"), focus: record("focus"), fit: record("fit"), fullscreen: record("fullscreen"), compact: record("compact"), ui: {} };
  const ctx = { menu: (_anchor, items) => menus.push(items) };
  const sandbox = { server: { session: { mutations_enabled: true } }, document: { fullscreenEnabled: true, fullscreenElement: null, body: { classList: { contains: () => false } } },
    keyLabel: x => x, planOf: () => status, later: fn => fn(), configure: record("configure"), startLink: record("connect"), disconnect: record("disconnect"),
    addStepAt: record("add"), skip: record("skip"), remove: record("delete"), planAct: record("plan"), schedule: record("schedule"),
    setCell: record("cell"), stopUnit: record("stop"), openLog: record("log") };
  runInNewContext(source.slice(source.indexOf("function stepItems("), source.indexOf("async function setCell(")) + "\nthis.menuItems = { stepItems, canvasItems, workflowRunItems, stepRunItems };", sandbox);
  return { ...sandbox.menuItems, api, ctx, status, cell, calls, menus };
}

test("workflow step edit items each dispatch to their intended operation", () => {
  const f = fixture(), s = { key: "a", depends: ["before"] };
  const items = f.stepItems(f.ctx, f.api, s, {});
  for (const [label, expected] of [["Configure a", "configure"], ["Connect to a step that waits for it…", "connect"],
    ["Add a step after it", "add"], ["Skip it in the workflow", "skip"], ["Delete step…", "delete"]]) {
    const before = f.calls.length;
    items.find(it => it.label === label).run();
    assert.ok(f.calls.slice(before).some(call => call[0] === expected), label);
  }
  items.find(it => it.label === "Disconnect…").run();
  assert.equal(f.menus[0].length, 2);
  for (const item of f.menus[0]) { item.run(); assert.equal(f.calls.at(-1)[0], "disconnect"); }
  assert.equal(items.find(it => it.label === "Delete step…").danger, true);
  const skipped = f.stepItems(f.ctx, f.api, { ...s, skipped: true }, {});
  assert.equal(skipped.some(it => it.label?.startsWith("Connect")), false);
  skipped.find(it => it.label === "Run it again (unskip)").run();
  assert.equal(f.calls.at(-1).at(-1), false);
});

test("workflow background and run controls each dispatch; state-specific entries are absent", () => {
  const f = fixture();
  const items = f.canvasItems(f.ctx, f.api, { x: 400, y: 200 });
  for (const [label, expected] of [["Add step here", "add"], ["Fit to view", "fit"], ["Fullscreen", "fullscreen"],
    ["Compact mode", "compact"], ["Run workflow…", "plan"], ["Schedule a run…", "schedule"], ["Pause run p1", "plan"], ["Cancel run p1…", "plan"]]) {
    items.find(it => it.label === label).run(); assert.equal(f.calls.at(-1)[0], expected, label);
  }
  f.status.plan.start_at = 'later';
  f.workflowRunItems(f.ctx, f.api).find(it => it.label === 'Start run p1 now').run();
  assert.equal(f.calls.at(-1)[3], 'start-now');
  delete f.status.plan.start_at; f.status.plan.status = 'paused';
  f.workflowRunItems(f.ctx, f.api).find(it => it.label === 'Resume run p1').run();
  assert.equal(f.calls.at(-1)[3], 'resume');
  f.status.plan.status = 'done';
  assert.equal(f.workflowRunItems(f.ctx, f.api).some(it => it.label?.includes('Cancel')), false);
  f.api.canEdit = () => false;
  assert.equal(f.canvasItems(f.ctx, f.api, {}).some(it => it.label?.includes('Add step')), false);
});

test("workflow target-cell actions dispatch run, stop, retry, skip and log only in matching states", () => {
  const f = fixture(), s = { key: 'a' };
  let items = f.stepRunItems(f.ctx, f.api, s);
  for (const [label, expected] of [['Run a…', 'plan'], ['Stop a for T…', 'stop'], ['Open its log', 'log']]) {
    items.find(it => it.label === label).run(); assert.equal(f.calls.at(-1)[0], expected);
  }
  assert.equal(items.find(it => it.label === 'Stop a for T…').danger, true);
  f.cell.value = 'failed';
  items = f.stepRunItems(f.ctx, f.api, s);
  assert.equal(items.some(it => it.label?.startsWith('Stop')), false);
  items.find(it => it.label === 'Retry a for T').run(); assert.equal(f.calls.at(-1).at(-1), 'todo');
  f.cell.value = 'todo';
  items = f.stepRunItems(f.ctx, f.api, s);
  items.find(it => it.label === 'Skip a for T in this run').run(); assert.equal(f.calls.at(-1).at(-1), 'skip');
  assert.equal(items.some(it => it.label?.startsWith('Retry')), false);
  assert.equal(f.stepRunItems(f.ctx, f.api, { ...s, skipped: true }).length, 0);
});

test("edge menu dispatches disconnect and selecting either endpoint", () => {
  const calls = [], api = { canEdit: () => true, select: key => calls.push(['select', key]) };
  const sandbox = { anchorAt: () => ({}), disconnect: (_ctx, _api, from, to) => calls.push(['disconnect', from, to]) };
  const start = source.indexOf('export function edgeMenu('), end = source.indexOf('// --- Configure', start);
  runInNewContext(source.slice(start, end).replace('export ', ''), sandbox);
  let items;
  const ctx = { menu: (_anchor, next) => { items = next; } };
  sandbox.edgeMenu(ctx, api, 'a', 'b', 0, 0);
  for (const item of items) item.run();
  assert.deepEqual(calls, [['disconnect', 'a', 'b'], ['select', 'a'], ['select', 'b']]);
  api.canEdit = () => false;
  sandbox.edgeMenu(ctx, api, 'a', 'b', 0, 0);
  assert.equal(items.length, 2);
});
