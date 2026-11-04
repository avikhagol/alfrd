// New project wizard helpers and the Settings → Entrypoints card (run with `node --test`).
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/alfrd/web/js");
globalThis.document ||= { hidden: false, addEventListener: () => {}, querySelectorAll: () => [] };
const W = await import(path.join(web, "components/create_wizard.js"));

const OPTIONS = [
  { id: "claude", label: "Claude Code", help: "Runs claude.", group: "common", available: true, status: "found at /usr/bin/claude", default: true, shown: true, hidden_reason: null, loop: true, cmd: ["claude", "-p"] },
  { id: "codex", label: "Codex", help: "Runs codex.", group: "common", available: false, status: "not installed — you can still add it", default: true, shown: true, hidden_reason: null, loop: true, cmd: ["codex", "exec"] },
  { id: "agy", label: "Antigravity (agy)", help: "Runs agy.", group: "common", available: false, status: "agy is not on PATH", default: false, shown: false, hidden_reason: "agy is not on PATH", loop: true, cmd: ["agy"] },
  { id: "llm-chat", label: "Text-only LLM chat", help: "No tools.", group: "advanced", available: true, status: "built in", default: false, shown: true, hidden_reason: null, loop: false, cmd: null },
  { id: "custom", label: "Custom command", help: "Your own.", group: "custom", available: true, status: "", default: false, shown: true, hidden_reason: null, loop: true, cmd: null },
];

test("steps: Basic skips Agents; an agent loop has four steps", () => {
  assert.deepEqual(W.stepsFor("basic").map((s) => s.id), ["folder", "commands", "review"]);
  assert.deepEqual(W.stepsFor("agent-loop").map((s) => s.title), ["Folder", "Agent commands", "Agents", "Review & create"]);
});

test("option rows: checkboxes with help, hidden options explained, advanced collapsed", () => {
  const html = W.optionRows(OPTIONS, new Set(["claude"]));
  assert.match(html, /<input type="checkbox" name="agent" value="claude" aria-describedby="cw-help-claude" checked>/);
  assert.match(html, /value="codex" aria-describedby="cw-help-codex" >/);
  assert.match(html, /not installed — you can still add it/);
  assert.doesNotMatch(html, /value="agy"/, "a hidden option has no checkbox");
  assert.match(html, /Why isn't Antigravity \(agy\) listed\?/);
  assert.match(html, /<details class="cw-adv" ><summary>Advanced integrations/);
  assert.ok(html.indexOf('value="custom"') > html.indexOf("Advanced integrations"), "custom command comes last");
});

test("turn order only holds ticked loop agents", () => {
  const agents = W.loopAgents(OPTIONS, new Set(["claude", "codex", "llm-chat", "custom"]), ["mine"]);
  assert.deepEqual(agents, ["claude", "codex", "mine"], "text-only chat is not a loop agent; custom comes last");
  assert.deepEqual(W.sequenceFrom(agents, ["codex", "claude", "codex"]), ["codex", "claude", "codex"]);
  assert.deepEqual(W.sequenceFrom(["claude"], ["codex", "claude", "codex"]), ["claude"], "unticking removes an agent's turns");
  assert.deepEqual(W.sequenceFrom(["claude", "codex"], ["agy"]), ["claude", "codex"], "nothing left: every ticked agent once");
  assert.deepEqual(W.sequenceFrom(["claude", "codex", "mine"], ["codex", "claude"], ["claude", "codex"]), ["codex", "claude", "mine"], "a newly ticked agent gets a turn");
});

test("step problems name the field and the fix", () => {
  const base = { path: "/tmp/p", name: "", template: "agent-loop", task: "Do it", iterations: 4, maxTurns: 200, agents: ["claude"], sequence: ["claude"], custom: null, taken: ["claude", "custom"] };
  assert.deepEqual(W.stepProblems("folder", base), []);
  assert.match(W.stepProblems("folder", { ...base, path: " " })[0], /^Folder: is empty\./);
  assert.match(W.stepProblems("commands", { ...base, agents: [] })[0], /^Agent commands: an agent loop needs/);
  assert.deepEqual(W.stepProblems("commands", { ...base, template: "basic", agents: [] }), [], "a basic project may start without commands");
  assert.match(W.stepProblems("commands", { ...base, custom: { name: "Bad", cmd: "x" } })[0], /^Custom command name/);
  assert.match(W.stepProblems("commands", { ...base, custom: { name: "claude", cmd: "x" } })[0], /already used/);
  assert.match(W.stepProblems("agents", { ...base, task: "" })[0], /^Initial task/);
  assert.match(W.stepProblems("agents", { ...base, iterations: 0 })[0], /^Iterations: must be 1–200/);
  assert.match(W.stepProblems("review", { ...base, sequence: ["claude", "codex"] })[0], /^Turn order: codex is not a ticked/);
});

test("payload and review list the exact commands and warnings", () => {
  const state = { path: " /tmp/p ", name: "p", template: "agent-loop", manifest: ".alfrd/alfrd.yaml", task: "Do it", iterations: 2,
    selected: ["claude", "codex", "custom"], sequence: ["claude", "codex"], custom: { name: "mine", cmd: " ./run.sh ", model_option: "" } };
  const payload = W.createPayload(state);
  assert.deepEqual(payload, { path: "/tmp/p", template: "agent-loop", manifest: ".alfrd/alfrd.yaml", agents: ["claude", "codex"], name: "p",
    custom: [{ name: "mine", cmd: "./run.sh" }], task: "Do it", iterations: 2, sequence: ["claude", "codex"] });
  assert.equal(W.createPayload({ ...state, template: "basic" }).sequence, undefined);
  const html = W.reviewSummary(payload, OPTIONS, { entries: [{ name: "mine", cmd: ["{project_dir}/run.sh"] }], warnings: ["custom command mine: x is not on PATH here."] });
  assert.match(html, /<pre class="code small">claude -p<\/pre>/);
  assert.match(html, /\{project_dir\}\/run\.sh/);
  assert.match(html, /lvl-warn">custom command mine/);
  assert.match(html, /Codex: not installed — you can still add it\. Install it before the first run\./);
  const spaced = W.reviewSummary({ ...payload, agents: [] }, OPTIONS, { entries: [{ name: "mine", cmd: ["{project_dir}/agent tool", "--flag"] }], warnings: [] });
  assert.match(spaced, /<pre class="code small">&quot;\{project_dir\}\/agent tool&quot; --flag<\/pre>/, "an argument with a space is shown quoted");
});

test("Settings → Entrypoints card: names, programs, found/missing and warnings", async () => {
  globalThis.localStorage ||= { getItem: () => null, setItem() {}, removeItem() {}, key: () => null, length: 0 };
  globalThis.location ||= { origin: "http://x", hash: "", search: "" };
  const { entrypointRows } = await import(path.join(web, "components/alfrd_config.js"));
  const data = { entrypoint: [{ name: "claude", cmd: ["claude", "-p"] }, { name: "agy", cmd: ["/mnt/x/.alfrd/bin/agy.py"] }, { name: "new", cmd: ["x"] }] };
  const probe = { entrypoints: [{ name: "claude", program: "claude", cmd: ["claude", "-p"], available: true, detail: "found" },
    { name: "agy", program: "/mnt/x/.alfrd/bin/agy.py", cmd: ["/mnt/x/.alfrd/bin/agy.py"], available: false, detail: "does not exist" }],
  warnings: ["entrypoint agy: /mnt/x/.alfrd/bin/agy.py is inside another project's .alfrd folder."] };
  const html = entrypointRows(data, probe);
  assert.match(html, /<td class="mono">claude<\/td><td class="mono small">claude<\/td><td><span class="badge tone-ok"/);
  assert.match(html, /tone-warn" title="does not exist">missing/);
  assert.match(html, /not checked/, "an entry not in the saved file is not checked yet");
  assert.match(html, /lvl-warn">entrypoint agy/);
  assert.match(entrypointRows({ entrypoint: [] }), /No agent commands yet/);
  // an existing name whose draft command was edited: the saved file's result is not shown beside it
  const edited = entrypointRows({ entrypoint: [{ name: "claude", cmd: ["/does/not/exist"] }, { name: "agy", cmd: ["/mnt/x/.alfrd/bin/agy.py", "--x"] }] }, probe);
  assert.doesNotMatch(edited, /found|missing/);
  assert.equal(edited.match(/not checked/g).length, 2);
  assert.match(entrypointRows({ entrypoint: [{ name: "c", cmd: ["claude"] }] }, { entrypoints: [{ name: "c", program: "claude", available: true, detail: "found" }] }),
    /tone-ok/, "a probe row without cmd falls back to the program");
});

test("Agent chat dialog: an agent's found/missing only while it runs the saved command", async () => {
  globalThis.CSS ||= { escape: (s) => s };
  const { probeFor } = await import(path.join(web, "components/chat_settings.js"));
  const probe = { agents: { mine: { available: true, detail: "found", entrypoint: "mine", cmd: ["{project_dir}/bin/agent.sh"] },
    claude: { available: false, detail: "claude is not on PATH", entrypoint: "claude", cmd: null } } };
  const entries = [{ name: "mine", cmd: ["{project_dir}/bin/agent.sh"] }];
  assert.equal(probeFor(probe, "mine", { entrypoint: "mine" }, entries)?.detail, "found");
  assert.equal(probeFor(probe, "mine", { entrypoint: "claude" }, entries), null, "entrypoint changed in the dialog");
  assert.equal(probeFor(probe, "mine", { entrypoint: "mine" }, [{ name: "mine", cmd: ["/does/not/exist"] }]), null, "draft command edited");
  assert.equal(probeFor(probe, "mine", { cmd: ["{project_dir}/bin/other.sh"] }, entries), null, "its own cmd differs");
  assert.equal(probeFor(probe, "claude", {}, entries)?.available, false, "a built-in: no draft entry, no cmd");
  assert.equal(probeFor(probe, "claude", {}, [{ name: "claude", cmd: ["claude", "-p"] }]), null, "built-in now overridden in the draft");
  assert.equal(probeFor(null, "mine", {}, entries), null);
});
