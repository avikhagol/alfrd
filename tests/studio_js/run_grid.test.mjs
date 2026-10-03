// Overview run grids for projects without targets (pure: js/data/run_grid.js).
import test from "node:test";
import assert from "node:assert/strict";

const G = await import("../../src/alfrd/web/js/data/run_grid.js");

// Two iterations × two agent steps, named so nothing can be hard-coded.
const loopStatus = () => ({
  plan: { id: "run-b", status: "running", loop: { iterations: 2 } },
  plans: [{ id: "run-b", status: "running" }, { id: "run-a", status: "finished" }],
  loop: { iteration: 2, iterations: 2, phase: "awaiting_review", unit: "u4" },
  table: {
    steps: ["i001-alpha-turn", "i001-beta-turn", "i002-alpha-turn", "i002-beta-turn"],
    rows: [{ key: "task", target: "task", cells: { "i001-alpha-turn": "done", "i001-beta-turn": "done", "i002-alpha-turn": "running", "i002-beta-turn": "todo" } }],
  },
  units: [
    { id: "u1", row: "task", steps: ["i001-alpha-turn"], iteration: 1, status: "failed", error: "boom" },
    { id: "u2", row: "task", steps: ["i001-alpha-turn"], iteration: 1, status: "done", agent: "alpha", model: "m-1", log: "a.log", started: "2026-01-01T00:00:00Z", finished: "2026-01-01T00:01:05Z" },
    { id: "u3", row: "task", steps: ["i001-beta-turn"], iteration: 1, status: "done", agent: "beta" },
    { id: "u4", row: "task", steps: ["i002-alpha-turn"], iteration: 2, status: "running", agent: "alpha" },
  ],
});

test("loop grid: iteration rows and step columns come from the run data", () => {
  const g = G.buildRunGrid(loopStatus(), { label: (base) => ({ "alpha-turn": "Alpha turn" }[base] || base) });
  assert.equal(g.kind, "loop");
  assert.deepEqual(g.columns, [{ id: "alpha-turn", label: "Alpha turn" }, { id: "beta-turn", label: "beta-turn" }]);
  const run = g.groups[0];
  assert.equal(run.run, "run-b");
  assert.deepEqual(run.rows.map((r) => [r.label, r.iteration]), [["Iteration 1", 1], ["Iteration 2", 2]]);
  assert.equal(run.rows[0].cells["alpha-turn"].status, "completed");
  assert.equal(run.rows[1].cells["alpha-turn"].status, "awaiting_review", "the waiting turn shows its phase");
  assert.equal(run.rows[1].cells["beta-turn"].status, "pending", "configured but unstarted");
});

test("retries: latest attempt status, attempt count and badge", () => {
  const g = G.buildRunGrid(loopStatus());
  const cell = g.groups[0].rows[0].cells["alpha-turn"];
  assert.equal(cell.attempts, 2);
  assert.equal(cell.status, "completed");
  assert.equal(cell.agent, "alpha"); assert.equal(cell.model, "m-1"); assert.equal(cell.log, "a.log");
  assert.equal(G.elapsed(cell), "1m 5s");
  const html = G.renderRunGrid("p", "Proj", g, g);
  assert.match(html, /×2<\/span>/);
  assert.match(html, /aria-label="Project Proj, run run-b, iteration 1, step alpha-turn, status Completed, 2 attempts"/);
});

test("multiple runs: other runs show overall status without step detail", () => {
  const g = G.buildRunGrid(loopStatus());
  assert.deepEqual(g.groups.map((x) => [x.run, x.status, x.rows.length]), [["run-b", "running", 2], ["run-a", "finished", 0]]);
  assert.equal(g.groups[1].note, "Step details unavailable.");
  const html = G.renderRunGrid("p", "Proj", g, g, G.FILTERS_DEFAULT, { runLabel: (s) => ({ finished: "Done" }[s] || s) });
  assert.match(html, /Run <span class="mono">run-a<\/span> <span>Done<\/span>/);
  assert.match(html, /Step details unavailable\./);
});

test("pending vs not applicable, and distinct statuses", () => {
  const status = {
    plan: { id: "r1", status: "failed" },
    table: { steps: ["fetch", "build", "ship"], rows: [
      { key: "x", target: "x", cells: { fetch: "done", build: "failed", ship: "todo" } },
      { key: "y", target: "y", cells: { fetch: "blocked", build: "skip" } },
      { key: "z", target: "z", cells: { fetch: "interrupted", build: "cancelled", ship: "todo" } },
    ] },
    queue: [{ row: "z", step: "ship" }],
    units: [],
  };
  const g = G.buildRunGrid(status);
  assert.equal(g.kind, "generic");
  assert.deepEqual(g.columns.map((c) => c.id), ["fetch", "build", "ship"]);
  const [x, y, z] = g.groups[0].rows;
  assert.equal(x.cells.ship.status, "pending");
  assert.equal(y.cells.build, undefined, "skip with no attempts is not in that row");
  assert.equal(y.cells.ship, undefined);
  assert.equal(z.cells.ship.status, "queued");
  assert.deepEqual([x.cells.build.status, y.cells.fetch.status, z.cells.fetch.status, z.cells.build.status], ["failed", "blocked", "interrupted", "cancelled"]);
  const html = G.renderRunGrid("p", "P", g, g);
  assert.match(html, /Not applicable/);
  assert.match(html, /<th scope="col"/); assert.match(html, /<th scope="row"/);
  const labels = Object.values(G.GRID_STATUS).map((s) => s.label);
  assert.equal(new Set(labels).size, labels.length);
  for (const k of ["completed", "running", "failed", "blocked", "interrupted", "queued", "pending", "skipped", "cancelled", "awaiting_response", "awaiting_review", "na"]) assert.ok(G.GRID_STATUS[k], k);
});

test("unknown project type: run × step grid; rows without step detail", () => {
  const g = G.buildRunGrid({ plan: { id: "r9", status: "running" }, table: { steps: ["s1"], rows: [{ key: "k", cells: { s1: "running" } }] } });
  assert.equal(g.kind, "generic");
  assert.equal(g.groups[0].rows[0].label, "k");
  assert.equal(g.groups[0].rows[0].cells.s1.status, "running");
  const bare = G.buildRunGrid({ plan: { id: "r10", status: "finished" } });
  assert.equal(bare.groups[0].note, "Step details unavailable.");
});

test("run with no turns started: every configured cell is Pending", () => {
  const s = loopStatus();
  s.units = []; s.loop = null;
  Object.keys(s.table.rows[0].cells).forEach((k) => { s.table.rows[0].cells[k] = "todo"; });
  const g = G.buildRunGrid(s);
  const all = g.groups[0].rows.flatMap((r) => Object.values(r.cells).map((c) => c.status));
  assert.deepEqual([...new Set(all)], ["pending"]);
  assert.equal(all.length, 4);
});

test("filters: search, status, run and the no-match empty state", () => {
  const g = G.buildRunGrid(loopStatus());
  assert.equal(G.filterRunGrid(g, { status: "awaiting_review" }).groups[0].rows.length, 1);
  assert.deepEqual(G.filterRunGrid(g, { run: "run-a" }).groups.map((x) => x.run), ["run-a"]);
  assert.deepEqual(G.filterRunGrid(g, { search: "Awaiting REVIEW" }).groups[0].rows.map((r) => r.iteration), [2]);
  assert.equal(G.filterRunGrid(g, { search: "beta" }).groups[0].rows.length, 2, "step labels are searchable");
  const none = G.filterRunGrid(g, { search: "nothing-like-this" });
  assert.equal(none.empty, "nomatch");
  const html = G.renderRunGrid("p", "P", g, none, { search: "nothing-like-this" });
  assert.match(html, /No runs match these filters\./);
  assert.match(html, /data-rg-reset="p"[^>]*>Reset filters/);
  assert.ok(G.filtersActive({ status: "failed" })); assert.ok(!G.filtersActive({}));
});

test("empty states", () => {
  assert.equal(G.buildRunGrid({ plan: null, plans: [] }).empty, "none");
  const none = G.emptyState("none");
  assert.match(none, /No runs yet\. Start a run from Workflow\./); assert.match(none, /href="#\/workflow">Open workflow/);
  assert.match(G.emptyState("offline"), /Connect with <code>alfrd serve<\/code> to load run details\./);
  const err = G.emptyState("error", { project: "p", name: "Loop A", error: "HTTP 500" });
  assert.match(err, /Could not load runs for Loop A: HTTP 500/); assert.match(err, /data-rg-retry="p">Retry/);
});

test("cell detail: run, iteration, step, agent/model, elapsed, attempts, error and log", () => {
  const g = G.buildRunGrid(loopStatus());
  const found = G.findCell(g, "run-b", "task:1", "alpha-turn");
  const html = G.cellDetail("Proj", found, "<button>View log</button>");
  for (const s of ["Proj", "run-b", "Iteration", "alpha · m-1", "1m 5s", "Attempts", "View log"]) assert.ok(html.includes(s), s);
  const na = G.findCell(g, "run-b", "task:2", "missing");
  assert.equal(na, null);
});

test("historical run load uses that run's handoffs and detail", async () => {
  const calls = [];
  const api = {
    async planStatus(project, id) {
      calls.push(["status", project, id]);
      const status = loopStatus();
      status.plan.id = id; status.loop = null;
      return status;
    },
    async handoffs(project, id) {
      calls.push(["handoffs", project, id]);
      return { handoffs: [{ id: "u4", phase: "awaiting_response", model: "review-model" }] };
    },
  };
  const loaded = await G.loadRunStatus(api, "other-project", "run-a");
  assert.deepEqual(calls, [["status", "other-project", "run-a"], ["handoffs", "other-project", "run-a"]]);
  const found = G.findCell(G.buildRunGrid(loaded.status, { handoffs: loaded.handoffs }), "run-a", "task:2", "alpha-turn");
  assert.equal(found.cell.status, "awaiting_response");
  assert.equal(found.cell.model, "review-model");
});

test("handoff fetch errors remain retryable failures rather than incorrect waiting statuses", async () => {
  const api = { planStatus: async () => loopStatus(), handoffs: async () => { throw new Error("handoffs unavailable"); } };
  await assert.rejects(G.loadRunStatus(api, "p"), /handoffs unavailable/);
});
