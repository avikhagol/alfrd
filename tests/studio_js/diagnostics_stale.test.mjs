import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

// Only refresh() + loadRuns() from diagnostics.js; the DOM, server and draw() are stubs.
const full = readFileSync(new URL("../../src/alfrd/web/js/components/diagnostics.js", import.meta.url), "utf8");
const slice = (start, end) => full.slice(full.indexOf(start), full.indexOf(end));
const source = slice("export function refresh", "const filtered").replace(/export /g, "");

function setup() {
  const pending = [], drawn = [];
  const body = { hidden: false, innerHTML: "" };
  const card = { body };
  const st = { project: "p", name: "diag", runs: [], filter: { target: "A" }, run: null, other: null };
  const cards = new Map([[card, st]]);
  const server = { collections: (project, q) => new Promise((resolve, reject) => pending.push({ q, resolve, reject })) };
  const sandbox = { cards, server, esc: String, icon: () => "", $: (s, el) => el.body, $$: () => [card],
    filtered: (s) => s.runs, draw: (c, ctx, s) => drawn.push(s.runs.map((r) => r.id).join(",")) };
  const api = runInNewContext(`${source}; ({ refresh, loadRuns })`, sandbox);
  let target = "A";
  const ctx = { target: () => ({ name: target }) };
  return { api, ctx, card, st, body, pending, drawn, select: (name) => { target = name; api.refresh({}, ctx); } };
}

test("collections: a slow reply for the previous target is dropped after a quick switch", async () => {
  const t = setup();
  t.api.loadRuns(t.card, t.ctx, t.st);
  t.select("B");
  assert.equal(t.pending.length, 2);
  assert.equal(t.pending[1].q.target, "B");
  t.pending[1].resolve({ runs: [{ id: "b1" }] });
  await new Promise((r) => setTimeout(r));
  t.pending[0].resolve({ runs: [{ id: "a1" }] });
  await new Promise((r) => setTimeout(r));
  assert.deepEqual(t.drawn, ["b1"]);
  assert.equal(t.st.run, "b1");
});

test("collections: A → B → A keeps only the latest request, and a stale error is ignored", async () => {
  const t = setup();
  t.api.loadRuns(t.card, t.ctx, t.st);
  t.select("B");
  t.select("A");
  assert.deepEqual(t.pending.map((p) => p.q.target), ["A", "B", "A"]);
  t.pending[0].resolve({ runs: [{ id: "a-old" }] });
  t.pending[1].reject(new Error("boom"));
  await new Promise((r) => setTimeout(r));
  assert.deepEqual(t.drawn, []);
  assert.doesNotMatch(t.body.innerHTML, /boom/);
  t.pending[2].resolve({ runs: [{ id: "a-new" }] });
  await new Promise((r) => setTimeout(r));
  assert.deepEqual(t.drawn, ["a-new"]);
});
