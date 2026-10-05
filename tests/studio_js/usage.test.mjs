// Resource usage view helpers (components/usage_view.js).
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const U = await import(path.join(path.resolve(here, "../../src/alfrd/web/js"), "components/usage_view.js"));

test("usage: jsonl parse, sparkline, per-step table", () => {
  const rows = U.parseUsage('{"t":0,"cores":0,"mem":10}\nnot json\n{"t":5,"cores":1.5,"mem":40}\n');
  assert.equal(rows.length, 2);
  const svg = U.spark(rows, "mem");
  assert.match(svg, /<polyline[^>]+points="1\.0,41\.0 319\.0,2\.0"/);
  assert.match(U.spark(rows.slice(0, 1), "mem"), /not enough samples/);
  const table = U.usageTable([
    { steps: ["a"], status: "done", usage: { wall_s: 10, avg_cores: 1, peak_mem: 100, cpu_s: 10 } },
    { steps: ["a"], status: "failed", usage: { wall_s: 30, avg_cores: 3, peak_mem: 300, cpu_s: 90 } },
    { steps: ["b"], status: "running", usage: { wall_s: 1 } },
    { steps: ["a"], status: "done" },
  ]);
  assert.deepEqual(table, [{ step: "a", n: 2, wall: 20, cores: 2, mem: 200, peak: 300, cpu: 100 }]);
});

test("agent usage preserves unknown metrics and totals reported turns", () => {
  const units = [
    { id: "claude-turn", agent: "claude", agent_usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120 }, total_cost_usd: 0.01 },
    { id: "codex-turn", agent: "codex", agent_usage: null, total_cost_usd: null },
    { id: "another-turn", agent: "claude", agent_usage: { input_tokens: 200, output_tokens: 30, total_tokens: 230 }, total_cost_usd: 0.02 },
  ];
  const total = U.agentTotals(units);
  assert.equal(total.input_tokens, 300);
  assert.equal(total.total_tokens, 350);
  assert.equal(total.cache_read_input_tokens, null);
  assert.equal(total.total_cost_usd, 0.03);
  const html = U.agentUsageHtml(units);
  assert.match(html, /codex-turn<\/td><td class="tabular">—/);
  assert.match(html, /Plan total \(reported\)/);
  assert.match(html, /\$0.0300/);
  assert.equal(U.agentTotals([]).total_tokens, null);
  assert.match(U.agentUsageHtml(units.slice(0, 1), { total_tokens: 999, total_cost_usd: 1 }), /999/);
});
