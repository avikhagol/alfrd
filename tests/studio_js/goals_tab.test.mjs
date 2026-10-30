import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { goalsHtml, createGoalsTab } from "../../src/alfrd/web/js/components/goals_tab.js";

const state = (rev = 1) => ({ rev, requests: [{ id: "r1", text: "First <request>\nsecond line" }, { id: "r2", text: "Later request" }], tasks: [
  { row: "r1.a", request: "r1", kind: "task", status: "done", task: { title: "Build <thing>" } },
  { row: "r1.b", request: "r1", kind: "task", status: "running", task: { title: "No criteria" } },
  { row: "r2.a", request: "r2", kind: "task", status: "failed", task: { title: "Other task" } },
  { row: "r1.plan", request: "r1", kind: "plan", status: "done" },
] });
const overview = (rev = 1) => ({ rev, goal: { text: "First <request>\nsecond line", source: "request:r1" }, criteria: [
  { text: "Escape <script>", task_rows: ["r1.a"], status: "unverified" },
  { text: "Evidence checked", task_rows: ["r1.a"], status: "verified" },
  { text: "Evidence failed", task_rows: ["r1.a"], status: "failed" },
  { text: "Unknown verdict", task_rows: ["r2.a"], status: "done" },
] });
const tick = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const box = () => ({ innerHTML: "", onclick: null });
const retry = (host) => host.onclick({ target: { closest: () => ({}) } });

test("request, every task and missing criteria; task completion never verifies a criterion", () => {
  const html = goalsHtml(overview(), state());
  assert.match(html, /First &lt;request&gt;\nsecond line/);
  assert.match(html, /class="chat-text"/);
  assert.match(html, /Build &lt;thing&gt;/);
  assert.match(html, /Task status: done/);
  assert.match(html, /Escape &lt;script&gt;.*Criterion: unverified/);
  assert.match(html, /Evidence checked.*Criterion: verified/);
  assert.match(html, /Evidence failed.*Criterion: failed/);
  assert.match(html, /Unknown verdict.*Criterion: unverified/);
  assert.match(html, /No criteria<\/h3>.*Task status: running<\/p><p>Not specified/);
  assert.doesNotMatch(html, /r1.plan|<script>/);
});

test("shared request scope filters both request text and task criteria", () => {
  const html = goalsHtml(overview(), state(), "r2");
  assert.match(html, /Later request|Other task/);
  assert.doesNotMatch(html, /First|Build|Escape|Evidence|No criteria/);
  assert.match(goalsHtml(overview(), state(), "missing"), /state-empty/);
});

test("empty and legacy sessions keep recorded request text without inventing criteria", () => {
  assert.match(goalsHtml({}, {}), /No goals yet/);
  assert.match(goalsHtml({ goal: { text: "Legacy\nrequest" } }, {}), /Legacy\nrequest/);
  assert.match(goalsHtml({}, { requests: [{ id: "old", text: "Recorded request" }] }), /Recorded request/);
  assert.match(goalsHtml({}, { tasks: [{ kind: "task", row: "old" }] }), /Unknown<\/p><p>Not specified/);
});

test("loader uses authenticated getter, encodes path, retains markup on unchanged polls and refreshes revisions", async () => {
  const pending = [], urls = [], host = box();
  const api = createGoalsTab({ get: (url) => { urls.push(url); const d = deferred(); pending.push(d); return d.promise; } });
  const args = { host, state: state(), project: "a/b", sid: "s ?", request: "r1" };
  api.update(args);
  assert.match(host.innerHTML, /state-loading/);
  assert.equal(urls[0], "/api/studio/projects/a%2Fb/chat/sessions/s%20%3F/overview");
  pending[0].resolve(overview()); await tick();
  const html = host.innerHTML, click = host.onclick;
  api.update({ ...args, state: state() });
  assert.equal(urls.length, 1); assert.equal(host.onclick, click); assert.equal(host.innerHTML, html);
  api.update({ ...args, state: state(2) });
  assert.equal(host.innerHTML, html, "revision refresh retains content until the new overview arrives");
  pending[1].resolve(overview(2)); await tick();
  assert.match(host.innerHTML, /Task status: done/);
  api.stop();
});

test("mismatched revisions show retry and never mix task statuses with criteria", async () => {
  const host = box(); let count = 0, refreshes = 0;
  const api = createGoalsTab({ get: async () => overview(++count === 1 ? 2 : 1), refresh: () => refreshes++ });
  api.update({ host, state: state(), project: "p", sid: "s" }); await tick();
  assert.match(host.innerHTML, /Session changed/); assert.match(host.innerHTML, /data-state-retry/);
  assert.doesNotMatch(host.innerHTML, /Task status/);
  retry(host); await tick();
  assert.equal(refreshes, 1); assert.match(host.innerHTML, /Task status: done/);
  api.stop();
});

test("scope, session, tab and disposal invalidate stale successes and errors", async () => {
  for (const change of ["scope", "session", "tab", "stop", "host"]) {
    const pending = [], host = box(), other = box();
    const api = createGoalsTab({ get: () => { const d = deferred(); pending.push(d); return d.promise; } });
    const args = { host, state: state(), project: "p", sid: "s", request: "r1" };
    api.update(args);
    if (change === "stop") api.stop();
    else api.update({ ...args, ...(change === "scope" ? { request: "r2" } : change === "session" ? { sid: "new" } : { host: change === "tab" ? null : other }) });
    const before = host.innerHTML;
    pending[0].resolve(overview()); await tick();
    assert.equal(host.innerHTML, before, change);
    if (pending[1]) { pending[1].reject(new Error("<stale>")); await tick(); }
    api.stop();
  }
});

test("errors escape text, timeouts expose retry and late responses cannot replace recovery", async () => {
  const host = box(), late = deferred(); let count = 0;
  const api = createGoalsTab({ timeoutMs: 5, get: () => ++count === 1 ? late.promise : Promise.resolve(overview()) });
  api.update({ host, state: state(), project: "p", sid: "s" });
  await new Promise((resolve) => setTimeout(resolve, 15));
  assert.match(host.innerHTML, /timed out/); assert.match(host.innerHTML, /data-state-retry/);
  retry(host); await tick(); const recovered = host.innerHTML;
  late.resolve(overview(9)); await tick(); assert.equal(host.innerHTML, recovered);
  api.stop();
  const failing = createGoalsTab({ get: async () => { throw new Error("<bad>"); } });
  failing.update({ host, state: state(), project: "p", sid: "s" }); await tick();
  assert.match(host.innerHTML, /&lt;bad&gt;/); assert.match(host.innerHTML, /state-error/); failing.stop();
});

test("no session and pending snapshot use F2 states without fetching; integration stops on leave", async () => {
  const host = box(); let calls = 0;
  const api = createGoalsTab({ get: () => calls++ });
  api.update({ host, project: "p" }); assert.match(host.innerHTML, /state-empty/);
  api.update({ host, project: "p", sid: "s" }); assert.match(host.innerHTML, /Loading session/);
  assert.equal(calls, 0); api.stop();
  const source = await readFile(new URL("../../src/alfrd/web/js/components/chat_view.js", import.meta.url), "utf8");
  assert.match(source, /drawGoals\(\{ host: inspTab === "goals"/);
  assert.match(source.slice(source.indexOf("function leave() {\n"), source.indexOf("function render(el, ctx) {\n  if (destroyed)")), /goals\?\.stop\(\)/);
});
