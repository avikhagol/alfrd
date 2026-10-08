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
    state, workflowCache,
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
  const host = { hidden: true, innerHTML: "" };
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
