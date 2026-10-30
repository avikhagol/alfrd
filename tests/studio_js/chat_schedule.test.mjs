import test from "node:test";
import assert from "node:assert/strict";
import { schedulePayload, timeCandidates, scheduleCountdown, scheduleSummary, scheduleController } from "../../src/alfrd/web/js/data/chat_schedule.js";
import { sessionDraft } from "../../src/alfrd/web/js/data/chat_draft.js";
import { imageWarning } from "../../src/alfrd/web/js/components/composer_draft.js";
import { server } from "../../src/alfrd/web/js/data/server.js";

globalThis.sessionStorage = { getItem() { return null; }, setItem() {} };
const state = (schedule = null, revision = 0) => ({ schedule, schedule_revision: revision, server_now: "2026-10-28T10:00:00Z", schedule_service: { available: true } });

test("IANA wall times: normal offset, DST gap and both fold choices", () => {
  assert.equal(schedulePayload("2099-01-01T12:00", "Europe/Athens").run_at, "2099-01-01T10:00:00.000Z");
  assert.equal(timeCandidates("2026-03-29T03:30", "Europe/Athens").length, 0);
  assert.throws(() => schedulePayload("2026-03-29T03:30", "Europe/Athens"), /does not exist/);
  assert.equal(timeCandidates("2026-10-25T03:30", "Europe/Athens").length, 2);
  assert.throws(() => schedulePayload("2026-10-25T03:30", "Europe/Athens"), /occurs twice/);
  assert.equal(schedulePayload("2026-10-25T03:30", "Europe/Athens", 0).run_at, "2026-10-25T00:30:00.000Z");
  assert.equal(schedulePayload("2026-10-25T03:30", "Europe/Athens", 1).run_at, "2026-10-25T01:30:00.000Z");
});

test("countdown never fires requests and uses authoritative waiting state", () => {
  const batch = { status: "scheduled", run_at: "2026-10-28T11:00:00Z", timezone: "UTC", items: [{}, { removed_at: "now" }] };
  assert.match(scheduleSummary(batch), /1 item/);
  assert.equal(scheduleCountdown(batch, Date.parse("2026-10-28T10:59:59Z")), "In 0h 0m 1s");
  assert.equal(scheduleCountdown(batch, Date.parse(batch.run_at)), "Due; waiting for server");
  assert.match(scheduleCountdown({ ...batch, hold_reason: "active_requests", waiting_on_requests: ["r0001"] }, Date.parse(batch.run_at)), /r0001/);
});

test("draft retries preserve the original schedule target across deadline and reload", async () => {
  const d = sessionDraft("schedule", "draft"); d.edit("one", []);
  const first = d.beginSend({ schedule_id: "sch_one", expected_schedule_revision: 2 }); d.finish();
  assert.deepEqual(d.beginSend({}), first); d.finish();
  d.rebaseSchedule(3, "sch_one"); const retry = d.beginSend({}); d.finish();
  assert.equal(retry.schedule_id, first.schedule_id); assert.equal(retry.client_message_id, first.client_message_id); assert.equal(retry.expected_schedule_revision, 3);
  d.edit("new draft while sending", []);
  d.accepted(200, first); assert.equal(d.read().text, "new draft while sending");
});

test("schedule image warning follows orchestrator even with a leading mention", () => {
  const settings = { orchestrator: "claude" }, probe = { agents: { codex: { images: true }, claude: { images: false } } };
  assert.match(imageWarning("@codex do this", settings, probe, true), /@claude/);
  assert.equal(imageWarning("@codex do this", settings, probe), "");
});

test("shared controller retries the same operation and original URL after lost run response", async () => {
  const c = scheduleController("test", "lost");
  assert.equal(c, scheduleController("test", "lost"));
  c.accept(state({ id: "sch_original" }, 2));
  const calls = [];
  server.session = { csrf_token: "csrf" };
  server.request = async (url, init) => {
    calls.push([url, JSON.parse(init.body)]);
    if (calls.length === 1) throw new Error("response lost");
    return new Response(JSON.stringify(state(null, 4)), { status: 202 });
  };
  await assert.rejects(c.mutate("run"), /response lost/);
  c.accept(state(null, 4));
  await c.mutate("run");
  assert.deepEqual(calls[0], calls[1]);
  assert.match(calls[1][0], /sch_original\/run$/);
});
