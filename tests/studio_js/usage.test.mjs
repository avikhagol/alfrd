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
