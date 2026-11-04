import test from "node:test";
import assert from "node:assert/strict";
import { actions } from "../../src/alfrd/web/js/components/task_actions.js";

const byId = (target) => Object.fromEntries(actions(target).map((action) => [action.id, action]));

test("returns enabled actions for a running task with available content", () => {
  const got = byId({
    row: "r0001.a", status: "running", log: "run.log", prompt: "prompt.md", reply: "reply.md",
    files: ["result.txt"],
  });
  for (const id of ["log", "prompt", "reply", "files", "cancel", "copy_id"]) {
    assert.equal(got[id].enabled, true, id);
  }
  assert.equal(got.retry.enabled, false);
  assert.match(got.retry.reason, /running/);
});

test("disabled actions carry a reason", () => {
  const got = byId({ row: "r0001.done", status: "done" });
  for (const action of Object.values(got)) {
    if (!action.enabled) assert.equal(typeof action.reason, "string");
  }
  assert.match(got.log.reason, /No log/);
  assert.match(got.retry.reason, /cannot be retried|done/);
  assert.equal(got.cancel.enabled, false);
});

test("retry confirmation lists the rows returned by retry-preview", () => {
  const got = byId({
    row: "r0001.a", status: "failed",
    retry_preview: { eligible: true, affected_rows: ["r0001.a", "r0001.b"] },
  });
  assert.equal(got.retry.enabled, true);
  assert.deepEqual(got.retry.confirmation.affected_rows, ["r0001.a", "r0001.b"]);
  assert.match(got.retry.confirmation.message, /r0001\.a, r0001\.b/);
});
