import test from "node:test";
import assert from "node:assert/strict";
import { currentSteps, stepLabel } from "../../src/alfrd/web/js/data/chat_steps.js";
import { graphModel } from "../../src/alfrd/web/js/data/chat.js";
import { stepsHtml, revealMail } from "../../src/alfrd/web/js/components/chat_steps.js";
const step = (kind = "execute", task_row = "a", status = "running", attempt = 1) => ({ id: `${kind}-${task_row}-${attempt}`, request_id: "r", kind, task_row, status, attempt, waiting_on: [] });

test("M7 uses only live M5 steps, retires retries and preserves parallel work", () => {
  const steps = [step("plan", "p", "done"), step("delegate", "a", "done"), step("execute", "a", "waiting"),
    step("execute", "a", "running", 2), step("execute", "b", "waiting"), step("repair", "fix"), step("execute", "c", "queued"),
    { ...step("summary", "sum"), request_id: "other" }];
  assert.deepEqual(currentSteps({ id: "r", steps }).map((s) => s.task_row), ["a", "b", "fix"]);
  assert.deepEqual(currentSteps({ id: "r", steps }, "a").map((s) => s.attempt), [2]);
  steps[3].status = "done";
  assert.deepEqual(currentSteps({ id: "r", steps }, "a"), [], "terminal retry cannot resurrect an old wait");
  assert.deepEqual(currentSteps({ id: "r" }), [], "legacy records invent no step");
  for (const kind of ["plan", "execute", "repair", "replan", "summary"]) assert.match(stepLabel(step(kind)), /running/);
  assert.match(stepLabel(step("execute", "a", "waiting", 2)), /attempt 2.*waiting/);
});

test("M7 graph projections match request AND task row, including orchestrator rows", () => {
  const graph = { nodes: [{ id: "a", request: "r" }, { id: "p", request: "r", kind: "plan" }, { id: "b", request: "other" }, { id: "legacy" }] };
  const requests = [{ id: "r", steps: [step(), step("plan", "p")] }, { id: "other", steps: [{ ...step("execute", "b", "waiting"), request_id: "other" }] }];
  const m = graphModel(graph, {}, null, requests);
  assert.deepEqual(m.nodes.map((n) => n.steps.map((s) => s.task_row)), [["a"], ["p"], ["b"], []]);
  assert.equal(graphModel(graph, {}, "r", requests).nodes.length, 2);
  assert.deepEqual(graphModel(graph).nodes[0].steps, []);
});

test("M7 waiting links retain exact escaped IDs; no IDs means no fabricated link", () => {
  const waiting = { ...step("execute", "a", "waiting"), waiting_on: ['m"<1', "peer", "peer"] };
  const html = stepsHtml([waiting, step()], "r");
  assert.match(html, /data-mail-jump="m&quot;&lt;1"/);
  assert.equal((html.match(/data-mail-jump=/g) || []).length, 2);
  assert.match(html, /data-mail-request="r"/);
  assert.doesNotMatch(stepsHtml([step("execute", "a", "waiting")], "r"), /<button/);
  assert.doesNotMatch(stepsHtml([step()], "r"), /<button/);
});

test("M7 reveal opens closed ancestors and full content once, without changing answers", () => {
  const details = { tagName: "DETAILS", open: false }, parent = { parentElement: details }, attrs = {};
  const button = { setAttribute: (k, v) => attrs[k] = v, focus: (o) => { attrs.focus = o; } }, content = { hidden: true };
  const card = { parentElement: parent, querySelector: (q) => q === "[data-mail-full]" ? content : button,
    scrollIntoView: (o) => { attrs.scroll = o; } };
  revealMail(card);
  assert.equal(details.open, true); assert.equal(content.hidden, false);
  assert.equal(attrs["aria-expanded"], "true"); assert.deepEqual(attrs.focus, { preventScroll: true });
  assert.deepEqual(attrs.scroll, { block: "center" });
});
