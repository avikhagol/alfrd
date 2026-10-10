import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const app = readFileSync(new URL("../../src/alfrd/web/js/app.js", import.meta.url), "utf8");

test("Re-scan requests and invalidates only the active project", async () => {
  const calls = [];
  const state = { selectedTarget: "p/B", avica: { p: {}, q: { keep: true } }, serverWorkflows: [{ project: "q" }], workflowProject: "p" };
  const workflowCache = new Map([["p", {}], ["q", { keep: true }]]);
  const record = (name, project) => calls.push([name, project]);
  const context = {
    state, workflowCache, projectLoader: null, loadJob: () => null,
    ctx: { projectName: (p) => p, log() {}, toast() {}, scopedTargets: () => [{ id: "p/B" }], target: () => ({ project: "p" }) },
    server: {
      async loadProjectRuntime({ name }) { record("runtime", name); return { messages: [], workflows: [{ project: name }], targets: [] }; },
      async projectScan(p) { record("scan", p); return { root: "/p" }; },
      clearFitsCache: (p) => record("fits", p),
    },
    clearWorkdirCache: (p) => record("workdir", p),
    async applyServerScan(p) { record("apply", p); return { targets: [], logFiles: [] }; },
    syncWorkflow() {}, plansAvailable: () => true,
    loadPlan: (_, p) => record("plan", p), applyAvicaParams() {}, scheduleRender() {}, live: { sync() {} },
  };
  const source = app.slice(app.indexOf("async function rescanServerProject("), app.indexOf("async function rescan()"));
  await runInNewContext(`${source}; rescanServerProject("p")`, context);
  assert.deepEqual(calls.map(([name]) => name), ["runtime", "scan", "workdir", "fits", "apply", "plan"]);
  assert.ok(calls.every(([, p]) => p === "p"));
  assert.deepEqual(state.avica.q, { keep: true });
  assert.deepEqual(workflowCache.get("q"), { keep: true });
  assert.equal(state.selectedTarget, "p/B");
});

test("replacing a modal removes the old Escape handler", () => {
  const listeners = new Set();
  const host = { hidden: true, innerHTML: "", contains: () => false };
  let activeRoot;
  const document = {
    activeElement: null,
    addEventListener: (_, fn) => listeners.add(fn),
    removeEventListener: (_, fn) => listeners.delete(fn),
  };
  const source = app.slice(app.indexOf("let clearModalKeys;"), app.indexOf("ctx.modal = modal;"));
  const modal = runInNewContext(`${source}; modal`, {
    document, Event,
    $: (selector) => selector === "#modal-host" ? host : activeRoot,
  });
  activeRoot = { dispatchEvent: () => true, querySelector: () => null };
  modal("first");
  activeRoot = { dispatchEvent: () => false, querySelector: () => null };
  modal("warning");
  assert.equal(listeners.size, 1);
  [...listeners][0]({ key: "Escape" });
  assert.equal(host.hidden, false, "warning's beforeclose can return to step one");
});

test("retained Re-scan publishes source failure and ignores an invalidated runtime response", async () => {
  const source = app.slice(app.indexOf("async function rescanServerProject("), app.indexOf("async function rescan()"));
  const make = () => {
    let resolve;
    const gate = new Promise((r) => resolve = r), calls = [];
    const job = { scan: { status: "ready", value: { root: "/old" } }, runtime: { status: "ready" } };
    const projectLoader = { generation: 1, isCurrent: (g) => g === projectLoader.generation };
    const context = { projectLoader, loadJob: () => job, state: { serverWorkflows: [{project: "q"}] },
      ctx: { projectName: (p) => p, log() {}, toast() {} }, scheduleRender() {},
      server: { loadProjectRuntime: () => gate, projectScan: async () => { calls.push("scan"); throw new Error("disk failure"); } } };
    return { context, job, calls, resolve };
  };
  const a = make();
  const run = runInNewContext(`${source}; rescanServerProject("p")`, a.context);
  assert.equal(a.job.retained, true);
  assert.equal(a.job.runtime.status, "loading");
  a.resolve({ok: true, messages: [], workflows: [{project: "p"}], targets: []}); await run;
  assert.equal(a.job.runtime.status, "ready");
  assert.equal(a.job.scan.status, "failed");
  assert.equal(a.job.scan.error.message, "disk failure");
  assert.equal(a.job.running, false);
  assert.equal(a.job.scan.value.root, "/old");
  const b = make();
  const stale = runInNewContext(`${source}; rescanServerProject("p")`, b.context);
  b.context.projectLoader.generation++;
  b.resolve({ok: true, messages: [], workflows: [{project: "p"}], targets: []}); await stale;
  assert.deepEqual(b.calls, []);
  assert.deepEqual(b.context.state.serverWorkflows, [{project: "q"}]);
});
