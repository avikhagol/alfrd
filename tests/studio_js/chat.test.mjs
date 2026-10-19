// Project chat helpers (data/chat.js): @-mentions and autocomplete, task-graph mapping and layout,
// the decision form, task actions, progress/budget and the chat: settings block (no browser DOM).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const C = await import("../../src/alfrd/web/js/data/chat.js");
const { parseYaml, dumpYaml } = await import("../../src/alfrd/web/js/utils/yaml_parser.js");

const agents = {
  claude: { label: "Claude (Opus)", roles: ["orchestration"] },
  sonnet: { label: "Claude (Sonnet)", roles: ["test planning", "verification"] },
  codex: { label: "GPT (Codex)", roles: ["design", "development"] },
};

test("a leading @agent is a direct message; unknown agents and later mentions are not", () => {
  assert.deepEqual(C.parseMention("@codex add a health endpoint", agents), { agent: "codex", body: "add a health endpoint" });
  assert.deepEqual(C.parseMention("  @Sonnet\nplan tests", agents), { agent: "sonnet", body: "plan tests" });
  assert.match(C.parseMention("@nobody hi", agents).error, /Unknown agent @nobody .*@claude, @sonnet, @codex/);
  assert.equal(C.parseMention("please ask @codex", agents), null);
  assert.equal(C.parseMention("email@codex", agents), null);
});

test("mention autocomplete: the @word at the start under the caret, ranked options, replacement", () => {
  assert.deepEqual(C.mentionAt("@co", 3), { start: 0, end: 3, query: "co" });
  assert.deepEqual(C.mentionAt("  @", 3), { start: 2, end: 3, query: "" });
  assert.equal(C.mentionAt("@codex hello", 10), null, "caret past the mention");
  assert.equal(C.mentionAt("hi @co", 6), null, "only a leading mention goes to an agent");
  assert.deepEqual(C.agentOptions(agents, "").map((o) => o.key), ["claude", "codex", "sonnet"]);
  assert.deepEqual(C.agentOptions(agents, "c", "claude").map((o) => [o.key, o.orchestrator]), [["claude", true], ["codex", false], ["sonnet", false]], "keys first, then labels (Claude (Sonnet))");
  assert.deepEqual(C.agentOptions(agents, "verif").map((o) => o.key), ["sonnet"], "role words match too");
  assert.deepEqual(C.agentOptions(agents, "gpt").map((o) => o.key), ["codex"], "labels match");
  assert.equal(C.agentOptions(agents, "zz").length, 0);
  assert.equal(C.agentOptions(agents, "sonnet")[0].roles, "test planning, verification");
  const at = C.mentionAt("@so  write tests", 3);
  assert.deepEqual(C.applyMention("@so  write tests", at, "sonnet"), { text: "@sonnet write tests", caret: 8 });
});

test("graph mapping: canvas status classes, owners, dependency hints, edge tones, request filter", () => {
  const graph = {
    nodes: [
      { id: "r0001.a", request: "r0001", title: "Impl", owner: "codex", status: "done" },
      { id: "r0001.b", request: "r0001", title: "Test", owner: "sonnet", status: "running" },
      { id: "r0001.c", request: "r0001", title: "Doc", owner: "codex", status: "waiting" },
      { id: "r0002.t1", request: "r0002", title: "Hi", owner: "codex", status: "failed" },
    ],
    edges: [{ from: "r0001.a", to: "r0001.b", kind: "depends_on" }, { from: "r0001.b", to: "r0001.c", kind: "depends_on" },
      { from: "r0001.a", to: "r0001.c", kind: "delegated" }, { from: "r0001.a", to: "missing", kind: "depends_on" }],
  };
  const m = C.graphModel(graph, agents);
  assert.deepEqual(m.nodes.map((n) => [n.id, n.st, n.tone]), [["r0001.a", "completed", "ok"], ["r0001.b", "running", "run"], ["r0001.c", "queued", "muted"], ["r0002.t1", "failed", "fail"]]);
  assert.equal(m.nodes[1].sub, "@sonnet · Claude (Sonnet)");
  assert.equal(m.nodes[2].hint, "Waits for r0001.b");
  assert.equal(m.nodes[0].hint, "No dependencies");
  assert.deepEqual(m.edges.map((e) => e.tone), ["run", "idle", "idle"], "edges to unknown nodes are dropped; delegation is drawn idle");
  assert.equal(C.graphModel(graph, agents, "r0002").nodes.length, 1);
  assert.equal(C.statusInfo("needs_decision").st, "warning");
  assert.equal(C.statusInfo("stopped").label, "Stopped (budget)");
  const L = C.layoutGraph(m.nodes, m.edges, { w: 100, h: 50, gapX: 10, gapY: 20 });
  assert.equal(L.pos["r0001.a"].y, 0);
  assert.equal(L.pos["r0001.b"].y, 70);
  assert.equal(L.pos["r0001.c"].y, 140);
  assert.deepEqual(L.order.slice(0, 2), ["r0001.a", "r0002.t1"]);
  const loop = C.layoutGraph([{ id: "x" }, { id: "y" }], [{ from: "x", to: "y" }, { from: "y", to: "x" }]);
  assert.ok(loop.pos.x && loop.pos.y, "a cycle never hangs the layout");
});

test("decision form: an option, a written answer, or both; invalid choices are refused", () => {
  const withOptions = { id: "d", options: ["postgres", "sqlite"] };
  assert.deepEqual(C.decisionPayload(withOptions, { choice: "sqlite" }), { choice: "sqlite" });
  assert.deepEqual(C.decisionPayload(withOptions, { choice: "sqlite", answer: " in memory " }), { choice: "sqlite", answer: "in memory" });
  assert.deepEqual(C.decisionPayload(withOptions, { answer: "mysql" }), { answer: "mysql" });
  assert.match(C.decisionPayload(withOptions, {}).error, /Pick an option/);
  assert.match(C.decisionPayload(withOptions, { choice: "oracle" }).error, /not one of the options/);
  assert.match(C.decisionPayload({ id: "q" }, { answer: "  " }).error, /Write an answer/);
  assert.match(C.decisionPayload({ id: "q" }, { answer: "x".repeat(8001) }).error, /8000/);
});

test("cancel and retry are offered only where the server accepts them", () => {
  for (const s of ["queued", "waiting", "running", "retrying", "needs_decision"]) assert.ok(C.canCancel({ status: s }) && !C.canRetry({ status: s }), s);
  for (const s of ["failed", "blocked", "cancelled", "stopped"]) assert.ok(!C.canCancel({ status: s }) && C.canRetry({ status: s }), s);
  assert.ok(!C.canCancel({ status: "done" }) && !C.canRetry({ status: "done" }));
});

test("progress follows the newest active request and measures cost and time against its bounds", () => {
  const state = {
    requests: [
      { id: "r0001", status: "done", created_ts: 1000, finished: "1970-01-01T00:20:00", bounds: { max_cost_usd: 5, max_seconds: 100 }, usage: { total_cost_usd: 1 } },
      { id: "r0002", status: "running", mode: "orchestrated", created_ts: 1000, bounds: { max_cost_usd: 2, max_seconds: 100 },
        usage: { total_cost_usd: 1.7, input_tokens: 100, output_tokens: 20 }, orchestrator_calls: 1 },
    ],
    tasks: [{ request: "r0002", kind: "task", status: "done" }, { request: "r0002", kind: "task", status: "running" },
      { request: "r0002", kind: "plan", status: "done" }, { request: "r0002", kind: "task", status: "blocked" }],
  };
  const req = C.activeRequest(state);
  assert.equal(req.id, "r0002");
  const p = C.requestProgress(state, req, 1050 * 1000);
  assert.deepEqual([p.total, p.done, p.running, p.failed, p.seconds, p.tokens, p.calls], [3, 1, 1, 1, 50, 120, 1]);
  assert.equal(p.tone, "warn", "85% of the cost bound");
  assert.equal(C.requestProgress(state, req, 1200 * 1000).tone, "fail", "over the time bound");
  assert.equal(C.activeRequest({ requests: [state.requests[0]] }).id, "r0001");
  assert.equal(C.activeRequest({}), null);
  assert.equal(C.money(0.0123), "$0.012");
  assert.equal(C.money(null), "—");
  assert.equal(C.duration(3725), "1h 2m");
});

test("chat settings: presets merge, the written block round-trips, problems mirror the server", () => {
  const s = C.mergeChat({ orchestrator: "sonnet", agents: { agy: null, sonnet: { persona: "tester" }, qa: { label: "QA", entrypoint: "claude", roles: "testing" } } });
  assert.deepEqual(Object.keys(s.agents), ["claude", "sonnet", "codex", "qa"]);
  assert.deepEqual(s.agents.qa.roles, ["testing"]);
  const block = C.chatBlock(s);
  assert.equal(block.agents.agy, null, "a removed preset is written as null");
  const back = C.mergeChat(parseYaml(dumpYaml({ chat: block })).chat);
  assert.deepEqual(back, s);
  assert.deepEqual(C.chatProblems(s, ["tester"]), []);
  assert.match(C.chatProblems(s, []).join(" "), /persona “tester”/);
  const bad = C.mergeChat({ orchestrator: "ghost", bounds: { max_retries: 9, max_cost_usd: 0 } });
  const problems = C.chatProblems(bad).join(" ");
  assert.match(problems, /orchestrator must be one of/);
  assert.match(problems, /max_retries/);
  assert.match(problems, /max_cost_usd/);
  assert.match(C.chatProblems({ ...s, agents: { "Bad Key": {} }, orchestrator: "Bad Key" }).join(" "), /Agent key/);
});

test("the transcript shows each specialist's result once, in time order, besides the messages", () => {
  const state = {
    messages: [{ id: "m1", role: "user", text: "go", at: "2026-10-18T10:00:00" }, { id: "m2", role: "assistant", agent: "claude", text: "plan", at: "2026-10-18T10:00:05" },
      { id: "m3", role: "assistant", agent: "claude", text: "summary", at: "2026-10-18T10:02:00" },
      { id: "m4", role: "assistant", agent: "codex", task: "r0002.t1", text: "direct", at: "2026-10-18T10:03:00" }],
    tasks: [{ row: "r0001.a", kind: "task", owner: "codex", request: "r0001", result: { status: "done", summary: "did a", artifacts: ["x@1"], at: "2026-10-18T10:01:00" } },
      { row: "r0001.b", kind: "task", owner: "sonnet", request: "r0001", result: { status: "failed", summary: "broke", at: "2026-10-18T10:01:30" } },
      { row: "r0001.plan", kind: "plan", owner: "claude", result: { status: "done", summary: "1 task" } },
      { row: "r0002.t1", kind: "task", owner: "codex", result: { status: "done", summary: "direct", at: "2026-10-18T10:03:00" } },
      { row: "r0001.c", kind: "task", owner: "codex", result: null }],
  };
  const t = C.timeline(state);
  assert.deepEqual(t.map((m) => m.id), ["m1", "m2", "task:r0001.a", "task:r0001.b", "m3", "m4"]);
  assert.deepEqual([t[2].kind, t[3].kind, t[2].agent, t[2].artifacts], ["result", "error", "codex", ["x@1"]]);
  assert.deepEqual(C.timeline({}), []);
});

test("token report: new input, cache read, cache write, output and cost; warnings; promotable request", () => {
  const u = { input_tokens: 1200, input_uncached_tokens: 1200, cache_read_input_tokens: 45000, cache_creation_input_tokens: 3000, output_tokens: 900, total_cost_usd: 0.042 };
  assert.deepEqual(C.tokenReport(u), { fresh: 1200, read: 45000, write: 3000, out: 900, cost: 0.042 });
  assert.equal(C.tokenLine(u), "new 1.2k · cache read 45k · cache write 3.0k · out 900 · $0.042");
  assert.equal(C.tokenLine({ input_tokens: 10, output_tokens: 5, total_cost_usd: null }), "new 10 · out 5", "unknown parts are left out");
  assert.equal(C.tokenLine(null), "");
  const state = { requests: [{ id: "r0001", tasks: ["r0001.a"] }, { id: "r0002", tasks: [] }],
    tasks: [{ row: "r0001.a", request: "r0001", cost_warning: { bound: 0.5, value: 0.7 } }, { row: "r0001.b", request: "r0001" }] };
  assert.deepEqual(C.costWarnings(state, "r0001").map((t) => t.row), ["r0001.a"]);
  assert.deepEqual(C.costWarnings(state, "r0002"), []);
  assert.equal(C.promotable(state).id, "r0001", "the newest request that has tasks");
  assert.equal(C.promotable({ requests: [{ id: "r1", tasks: [] }] }), null);
});

test("chat settings: orchestrator / escalation models, final summary off, task cost warning, lean presets", () => {
  const s = C.mergeChat({});
  assert.deepEqual([s.orchestrator_model, s.escalation_model, s.final_summary, s.warn_task_cost_usd], ["sonnet", "opus", false, null]);
  assert.equal(s.agents.claude.lean, true);
  const block = C.chatBlock({ ...s, orchestrator_model: "", escalation_model: " opus ", warn_task_cost_usd: "0.75" });
  assert.deepEqual([block.orchestrator_model, block.escalation_model, block.warn_task_cost_usd], [null, "opus", 0.75]);
  assert.equal(block.agents.claude.lean, true);
  assert.equal(C.chatBlock({ ...s, agents: { ...s.agents, qa: { label: "QA", lean: false } } }).agents.qa.lean, undefined, "off is not written for new agents");
  assert.match(C.chatProblems({ ...s, warn_task_cost_usd: -1 }).join(" "), /Task cost warning/);
  assert.deepEqual(C.chatProblems({ ...s, warn_task_cost_usd: "" }), []);
});


test("public messages separate protocol results and preserve authored JSON and prose", () => {
  const envelope = { protocol: "alfrd.orchestration/1", kind: "result", result: { status: "done", summary: "Updated the chat.", artifacts: ["chat.css@abc"] } };
  const raw = JSON.stringify(envelope);
  const m = C.presentMessage({ role: "assistant", text: "Review complete.\n\n```json\n" + raw + "\n```" });
  assert.equal(m.text, "Review complete.\n\nUpdated the chat.");
  assert.deepEqual(m.artifacts, ["chat.css@abc"]);
  assert.ok(m.technical.includes(raw));
  assert.equal(C.presentMessage({ role: "user", text: raw }).text, raw);
  assert.equal(C.presentMessage({ role: "assistant", text: '{"example":true}' }).text, '{"example":true}');
  assert.match(C.presentMessage({ role: "assistant", text: '{"protocol":"alfrd.orchestration/1",' }).text, /could not be read/);
  const example = 'Use this example:\n```json\n{"hello":"world"}\n```';
  assert.equal(C.presentMessage({ role: "assistant", text: example }).text, example);
});

test("history groups overlapping requests by identity; active and unanswered work stays open", () => {
  const state = { requests: [{ id: "r1", text: "First request", status: "done" }, { id: "r2", text: "Second", status: "running" }, { id: "r3", text: "Third", status: "done" }],
    messages: [{ id: "a", request: "r1", role: "user", text: "first", at: "1" }, { id: "b", request: "r2", role: "user", text: "second", at: "2" }, { id: "c", request: "r1", role: "assistant", text: "answer", at: "3" }, { id: "old", role: "system", text: "legacy" }], tasks: [] };
  const groups = C.threadGroups(state);
  assert.deepEqual(groups[0].messages.map((m) => m.id), ["a", "c"]);
  assert.equal(groups[0].open, false);
  assert.equal(groups[1].open, true);
  assert.equal(groups.find((g) => g.id === "r3").open, true);
  assert.ok(groups.some((g) => g.id === "message:old"));
  state.requests[0].mailbox = { pending_user: 1 };
  assert.equal(C.threadGroups(state)[0].open, true);
  assert.match(C.requestSummary(state, state.requests[0]), /First request.*—/);
});

test("activity keeps parallel same-agent tasks and retry attempts distinct, unknown cost stays unknown", () => {
  const state = { tasks: [{ row: "a", request: "r", owner: "codex", status: "done", task: { title: "Build" }, logs: [{ unit: "u1", status: "failed" }, { unit: "u2", status: "done", total_cost_usd: 0 }] },
    { row: "b", request: "r", owner: "codex", status: "waiting", task: { title: "Test" } }] };
  const rows = C.activityRows(state, [{ row: "a", request: "r", unit: "u2", status: "running", display_tail: ["last line"] }], "r");
  assert.equal(new Set(rows.map(C.activityKey)).size, 3);
  assert.deepEqual(rows.map((r) => r.status), ["failed", "done", "waiting"]);
  assert.equal(rows[1].cost_usd, 0);
  assert.equal(rows[1].display_tail[0], "last line");
  assert.equal(C.money(rows[2].cost_usd), "—");
  assert.equal(C.activityRows(state, [], "other").length, 0);
});
