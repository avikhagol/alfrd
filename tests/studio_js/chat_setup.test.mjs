// Chat round helpers (data/chat.js): orchestrator nodes and planned edges in the graph, slash commands,
// /help text, live activity labels, /setup role assignment, model hints, availability badges and the
// env/fallback round trip of the chat: block (no browser DOM).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const C = await import("../../src/alfrd/web/js/data/chat.js");
const { parseYaml, dumpYaml } = await import("../../src/alfrd/web/js/utils/yaml_parser.js");

const graph = {
  nodes: [
    { id: "r0001-plan", kind: "plan", request: "r0001", owner: "claude", status: "done" },
    { id: "t1", kind: "task", request: "r0001", title: "Research", owner: "agy", status: "done" },
    { id: "t2", kind: "task", request: "r0001", title: "Build", owner: "codex", status: "running" },
    { id: "r0001-sum", kind: "summary", request: "r0001", owner: "claude", status: "queued" },
  ],
  edges: [
    { from: "r0001-plan", to: "t1", kind: "planned" }, { from: "r0001-plan", to: "t2", kind: "planned" },
    { from: "t1", to: "t2", kind: "depends_on" },
  ],
};

test("orchestrator rows are graph nodes with a badge; planned edges are idle and drawn above their tasks", () => {
  const m = C.graphModel(graph, { claude: { label: "Claude" } });
  const plan = m.nodes.find((n) => n.id === "r0001-plan");
  assert.equal(plan.orch, true);
  assert.equal(plan.badge, "Orchestrator");
  assert.equal(plan.title, "Plan r0001");
  assert.equal(plan.hint, "Planned 2 tasks");
  const t2 = m.nodes.find((n) => n.id === "t2");
  assert.equal(t2.orch, undefined);
  assert.equal(t2.hint, "Waits for t1", "planned edges are not dependencies");
  assert.deepEqual(m.edges.filter((e) => e.kind === "planned").map((e) => e.tone), ["idle", "idle"]);
  const L = C.layoutGraph(m.nodes, m.edges, { w: 100, h: 50, gapX: 10, gapY: 10 });
  assert.equal(L.pos["r0001-plan"].y, 0);
  assert.equal(L.pos.t1.y, 60);
  assert.equal(L.pos.t2.y, 120);
  assert.equal(L.pos["r0001-sum"].y, 180, "the summary call goes below its request's tasks");
  assert.deepEqual(L.order, ["r0001-plan", "t1", "t2", "r0001-sum"]);
});

test("old graphs without kinds still map as before", () => {
  const m = C.graphModel({ nodes: [{ id: "a", owner: "x", status: "done" }], edges: [] });
  assert.equal(m.nodes[0].orch, undefined);
  assert.equal(m.nodes[0].hint, "No dependencies");
});

test("slash commands: parse, unknown, leading space sends text, per-place list", () => {
  assert.deepEqual(C.parseSlash("/setup"), { cmd: "setup", args: "" });
  assert.deepEqual(C.parseSlash("/HELP  me"), { cmd: "help", args: "me" });
  assert.equal(C.parseSlash("hello /setup"), null);
  assert.equal(C.parseSlash(" /etc/hosts please"), null, "a leading space sends it as text");
  assert.match(C.parseSlash("/frob").error, /Unknown command \/frob .*\/setup, \/help, \/new, \/graph/);
  assert.match(C.parseSlash("/graph", "dock").error, /Unknown command/, "no graph in the dock");
  assert.deepEqual(C.slashOptions("", "dock").map((c) => c.cmd), ["setup", "help", "new"]);
  assert.deepEqual(C.slashOptions("g").map((c) => c.cmd), ["graph"]);
  assert.deepEqual(C.slashAt("/se", 3), { start: 0, end: 3, query: "se" });
  assert.equal(C.slashAt("/setup now", 9), null, "past the command word");
  assert.equal(C.slashAt("x/se", 4), null);
});

test("/help lists commands and agents with roles, models and fallbacks", () => {
  const text = C.helpText({ orchestrator: "claude", orchestrator_model: "sonnet", agents: {
    claude: { label: "Claude", model: "opus", roles: ["orchestration"] }, local: { label: "Local", model: "qwen3", roles: ["research"], fallback: "claude" } } }, "dock");
  assert.match(text, /\/setup — /);
  assert.doesNotMatch(text, /\/graph/);
  assert.match(text, /@claude — Claude · orchestrator · model opus · orchestration/);
  assert.match(text, /@local — Local · model qwen3 · research · falls back to @claude/);
  assert.match(text, /Orchestrator: @claude \(plans sonnet, escalation agent model\)/);
  assert.match(C.helpText(null), /Commands/);
});

test("activity labels and when to poll", () => {
  assert.equal(C.activityLabel({ kind: "plan", request: "r0002" }), "Planning request r0002");
  assert.equal(C.activityLabel({ kind: "repair", request: "r0002" }), "Repair r0002");
  assert.equal(C.activityLabel({ kind: "summary", request: "r0003" }), "Summary r0003");
  assert.equal(C.activityLabel({ kind: "task", title: "Write tests", row: "t1" }), "Write tests");
  assert.equal(C.hasActivity({ tasks: [{ status: "done" }], requests: [{ status: "done" }] }), false);
  assert.equal(C.hasActivity({ tasks: [{ status: "running" }] }), true);
  assert.equal(C.hasActivity({ requests: [{ status: "planning" }] }), true);
  assert.equal(C.hasActivity({ requests: [{ status: "needs_decision" }] }), false, "waiting on the user: nothing runs");
  assert.equal(C.hasActivity(null), false);
});

test("setup roles: words from agents and suggestions, moving a role to one agent", () => {
  const agents = { a: { roles: ["orchestration", "research"] }, b: { roles: ["design"] } };
  assert.deepEqual(C.roleWords(agents, ["research", "image generation"]), ["research", "design", "image generation"]);
  assert.equal(C.roleOwner(agents, "design"), "b");
  C.assignRole(agents, "research", "b");
  assert.deepEqual(agents.a.roles, ["orchestration"]);
  assert.deepEqual(agents.b.roles, ["design", "research"]);
  C.assignRole(agents, "design", "");
  assert.deepEqual(agents.b.roles, ["research"]);
  assert.equal(C.roleOwner(agents, "design"), "");
});

test("backends, model hints and availability badges from the probe", () => {
  const probe = { agents: { claude: { available: true, path: "/bin/claude" }, oss: { available: true }, hf: { available: true }, gone: { available: false, detail: "codex not on PATH" } },
    ollama: { url: "http://localhost:11434", reachable: false, models: ["qwen3-coder", "gpt-oss:20b"], error: "refused" }, huggingface: { token: false } };
  const oss = { entrypoint: "codex", cmd: ["codex", "exec", "--oss"] };
  const cl = { entrypoint: "claude", env: { ANTHROPIC_BASE_URL: "http://localhost:11434" } };
  assert.equal(C.backendOf(oss), "ollama");
  assert.equal(C.backendOf(cl), "ollama");
  assert.equal(C.backendOf({ entrypoint: "claude" }), "claude");
  assert.equal(C.backendOf({ cmd: ["python", "-m", "alfrd.llm_chat"] }), "llm");
  assert.equal(C.backendOf({ entrypoint: "llm-chat", env: { ALFRD_LLM_BASE_URL: "http://localhost:11434/v1" } }), "llm");
  assert.equal(C.backendOf({ entrypoint: "codex-oss" }), "ollama");
  assert.equal(C.backendOf({ entrypoint: "codex-hf" }), "hf");
  assert.deepEqual(C.modelHints(oss, probe), ["qwen3-coder", "gpt-oss:20b"]);
  assert.deepEqual(C.modelHints({ entrypoint: "claude" }, probe), ["opus", "sonnet", "haiku"]);
  assert.deepEqual(C.modelHints({ entrypoint: "codex" }, null), ["gpt-5-codex", "gpt-5"]);
  assert.deepEqual(C.modelHints({ entrypoint: "codex" }, { codex: { models: ["gpt-5.6-luna", "gpt-5"] } }), ["gpt-5.6-luna", "gpt-5", "gpt-5-codex"]);
  assert.deepEqual(C.probeBadge("claude", { entrypoint: "claude" }, probe), { tone: "ok", label: "Available", detail: "/bin/claude" });
  assert.equal(C.probeBadge("oss", oss, probe).label, "Ollama not reachable");
  assert.equal(C.probeBadge("hf", { cmd: ["codex", "-c", "model_provider=huggingface"] }, probe).label, "HF_TOKEN missing");
  assert.equal(C.probeBadge("gone", { entrypoint: "codex" }, probe).label, "Not found");
  assert.equal(C.probeBadge("claude", {}, null), null);
  assert.equal(C.freeKey({ ollama: {}, "ollama-2": {} }, "ollama"), "ollama-3");
});

test("env and fallback round-trip through the chat: block; a bad fallback is a problem", () => {
  const s = C.mergeChat({ agents: { local: { label: "Local", entrypoint: "claude", model: "qwen3-coder", env: { ANTHROPIC_BASE_URL: "http://localhost:11434" }, fallback: "claude", roles: ["research"] } } });
  const block = C.chatBlock(s);
  assert.deepEqual(block.agents.local.env, { ANTHROPIC_BASE_URL: "http://localhost:11434" });
  assert.equal(block.agents.local.fallback, "claude");
  const again = C.mergeChat(parseYaml(dumpYaml({ chat: block })).chat);
  assert.deepEqual(again.agents.local, s.agents.local);
  assert.deepEqual(C.chatProblems(s), []);
  s.agents.local.fallback = "local";
  assert.match(C.chatProblems(s).join(" "), /@local: the fallback must be another agent/);
  s.agents.local.fallback = "claude"; delete s.agents.claude; s.orchestrator = "sonnet";
  assert.equal(C.chatBlock(s).agents.local.fallback, undefined, "a removed fallback agent is dropped");
  s.agents.local.env = {};
  assert.equal(C.chatBlock(s).agents.local.env, undefined);
});

test("setup summary names the orchestrator, role owners and fallbacks", () => {
  const s = { orchestrator: "claude", orchestrator_model: "sonnet", agents: { claude: { roles: ["orchestration"] }, agy: { roles: ["research"], fallback: "claude" } } };
  assert.equal(C.setupSummary(s), "Setup saved: orchestrator @claude (sonnet), research → @agy, @agy falls back to @claude");
});
