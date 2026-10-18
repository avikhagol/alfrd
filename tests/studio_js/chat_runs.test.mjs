// Chat sessions' runs in Workflow: run picker groups, Chat mode gate, never the active workflow run.
import test from "node:test";
import assert from "node:assert/strict";
globalThis.document = { hidden: false, addEventListener() {} };
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const { runPicker, activePlan, loadPlan, forgetPlan } = await import("../../src/alfrd/web/js/components/plans.js");

const status = {
  plan: { id: "wf1", status: "finished" },
  plans: [{ id: "wf1", status: "finished" }],
  chat_plans: [{ id: "chat-s1", status: "running", phase: "running", run_kind: "chat", title: "Fix <login>", chat: { session: "s1" } }],
};

test("run picker: workflow runs, then a Chat sessions group only with Chat mode on", () => {
  const off = runPicker({ chatOn: () => false }, status);
  assert.doesNotMatch(off, /optgroup|chat-s1/);
  const on = runPicker({ chatOn: () => true }, status);
  assert.match(on, /<optgroup label="Workflow runs"><option value="wf1" selected>Run wf1 · Done<\/option><\/optgroup>/);
  assert.match(on, /<optgroup label="Chat sessions"><option value="chat-s1" >Chat · Fix &lt;login&gt; · Running<\/option><\/optgroup>/);
  assert.ok(on.indexOf("wf1") < on.indexOf("chat-s1"));
  // A chat run shown: it is the selected option; with no workflow run the group is left out.
  const picked = runPicker({ chatOn: () => true }, { ...status, plans: [], plan: { id: "chat-s1", status: "running", chat: { session: "s1" } } });
  assert.doesNotMatch(picked, /Workflow runs/); assert.match(picked, /value="chat-s1" selected/);
  assert.match(runPicker({ chatOn: () => true }, { ...status, plan: null, plans: [] }), /Choose a run…/);
});

test("a running chat run is never the active workflow run", async () => {
  const project = "chat-runs", original = server.planStatus;
  const ctx = { state: { mode: "server", trees: { [project]: { provider: "server" } } }, update() {}, log() {}, toast() {} };
  server.planStatus = async () => ({ plan: { id: "chat-s1", status: "running", chat: { session: "s1" } }, plans: [], chat_plans: status.chat_plans });
  try {
    await loadPlan(ctx, project, { id: "chat-s1" });
    assert.equal(activePlan(project), null);
  } finally { server.planStatus = original; forgetPlan(project); }
});
