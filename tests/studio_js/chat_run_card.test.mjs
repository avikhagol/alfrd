import test from "node:test";
import assert from "node:assert/strict";
import { runSummary } from "../../src/alfrd/web/js/data/chat.js";
const fixture = (status = "running") => ({ requests: [{ id: "r0001", text: "  Build   a game\nMore detail", status, mailbox: { pending_user: 0 } }], tasks: [
  { request: "r0001", kind: "plan", status: "done" },
  { request: "r0001", kind: "task", status: "done" },
  { request: "r0001", kind: "task", status: "running" },
  { request: "other", kind: "task", status: "done" },
], decisions: [] });
test("Run: empty, planning, direct task, running and terminal counts", () => {
  assert.equal(runSummary(), null); assert.equal(runSummary({ requests: [] }), null);
  const s = fixture();
  assert.deepEqual([runSummary(s).title, runSummary(s).status, runSummary(s).done, runSummary(s).total], ["Build a game", "Running", 1, 2]);
  for (const [status, word] of [["planning", "Planning…"], ["summarizing", "Summarizing"], ["done", "Done"], ["failed", "Failed"], ["cancelled", "Cancelled"], ["stopped", "Stopped"], ["new_state", "New state"]]) {
    s.requests[0].status = status;
    assert.equal(runSummary(s).status, word); assert.equal(runSummary(s).done, 1);
  }
  s.tasks = []; s.requests[0].status = "planning";
  assert.deepEqual([runSummary(s).done, runSummary(s).total], [0, 0]);
  s.requests[0].status = "failed"; assert.equal(runSummary(s).status, "Planning failed");
  s.tasks.push({ request: "r0001", kind: "task", status: "done" }); s.requests[0].mode = "direct";
  assert.equal(runSummary(s).total, 1);
});
test("Run: per-request user attention deduplicates linked decisions and keeps terminal status", () => {
  const s = fixture(); s.tasks[2].status = "blocked";
  assert.equal(runSummary(s).status, "Blocked"); assert.equal(runSummary(s).needsUser, 0);
  s.requests[0].mailbox.pending_user = 1;
  s.decisions = [{ request: "r0001", status: "pending", needs: "user", mailbox_message_id: "m" },
    { request: "r0001", status: "pending", needs: "user" }, { request: "other", status: "pending", needs: "user" },
    { request: "r0001", status: "resolved", needs: "user" }, { request: "r0001", status: "pending", needs: "orchestrator" }];
  assert.equal(runSummary(s).needsUser, 2); assert.equal(runSummary(s).status, "Needs you");
  for (const status of ["done", "failed", "cancelled", "stopped"]) { s.requests[0].status = status; assert.notEqual(runSummary(s).status, "Needs you"); }
  delete s.requests[0].mailbox; assert.equal(runSummary(s).needsUser, 1);
  s.decisions = []; s.tasks = []; s.requests[0].status = "needs_decision";
  assert.equal(runSummary(s).status, "Waiting for orchestrator");
});
test("Run: active selection and title fallbacks are independent of inspector scope", () => {
  const s = fixture(); s.requests.push({ id: "r0002", status: "done", text: "Other" });
  assert.equal(runSummary(s).id, "r0001");
  s.requests[0].text = ""; s.messages = [{ role: "user", request: "r0001", text: "<script> title" }];
  assert.equal(runSummary(s).title, "<script> title");
  s.messages = []; assert.equal(runSummary(s).title, "Request 1");
  s.requests[0].text = "x".repeat(120); assert.equal(runSummary(s).title.length, 80); assert.equal(runSummary(s).fullTitle.length, 120);
  s.requests[0].status = "done"; assert.equal(runSummary(s).id, "r0002");
});


test("Run uses the server's display summary for legacy scheduled requests", () => {
  const s = fixture();
  s.requests[0].text = "Scheduled batch sch_secret (3 pending items)";
  s.requests[0].title = "Stitch theme, Files tab, Graph menu";
  assert.equal(runSummary(s).fullTitle, "Stitch theme, Files tab, Graph menu");
  assert.doesNotMatch(runSummary(s).title, /sch_|pending/);
});
