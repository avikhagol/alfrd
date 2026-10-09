import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../../examples/plugins/alfrd-gsheet/alfrd_gsheet/web/index.js", import.meta.url), "utf8")
  .replace(/^import .*;\n/gm, "").replace(/export /g, "");
function harness() {
  const events = {}, calls = [], toasts = [];
  const sandbox = { Date, document: { addEventListener: (kind, fn) => { events[kind] = fn; } } };
  runInNewContext(`${source}; globalThis.exports = {activate, render};`, sandbox);
  const api = { sanitize: html => html, registerPanel: (kind, draw) => { api.draw = draw; api.kind = kind; },
    registerCommand: command => { api.command = command; }, project: () => "my project",
    fetchJSON: async (...args) => { calls.push(args); return { level: "warn", text: "2 problems: unknown column RAM" }; },
    toast: (...args) => toasts.push(args) };
  sandbox.exports.activate(api);
  return { api, events, calls, toasts };
}
const entry = i => ({ step: `step-${i}`, at: "2026-10-09T12:00:00Z", cells_written: 2, conflicts: 0, result: "ok" });
const model = { state: "syncing", label: "Syncing", rows: Array.from({ length: 10 }, (_, i) => entry(i)), more_rows: [entry(10)], total: 11,
  sheet: { id: "a".repeat(20), worksheet: "Targets", url: `https://docs.google.com/spreadsheets/d/${"a".repeat(20)}/edit#gid=42` } };

test("panel has semantic table, text/icon chips, safe sheet link and bounded initial rows", () => {
  const { api } = harness();
  const html = api.draw(model);
  assert.equal(api.kind, "gsheet_sync");
  assert.match(html, /<caption>Last sync per step<\/caption>/);
  assert.equal((html.match(/scope="col"/g) || []).length, 5);
  assert.equal((html.match(/scope="row"/g) || []).length, 11);
  assert.equal((html.match(/data-gsheet-more hidden/g) || []).length, 1);
  assert.match(html, /aria-expanded="false" aria-controls="gsheet-rows-/);
  assert.match(html, /target="_blank" rel="noopener noreferrer" aria-label="Open the Google Sheet in a new tab"/);
  assert.match(html, /aria-hidden="true">✓<\/span> Syncing/);
  assert.match(html, /title="2026-10-09T12:00:00Z"/);
});

test("untrusted text is escaped and non-Google links are omitted", () => {
  const { api } = harness();
  const html = api.draw({ ...model, label: '<img src=x onerror="alert(1)">', sheet: { ...model.sheet, url: "javascript:alert(1)" },
    rows: [{ ...entry(0), step: "<script>evil</script>", result: "error", error: '<b>"bad"</b>' }], more_rows: [],
    command: "alfrd gsheet init <project> --spreadsheet <id>" });
  assert.ok(!html.includes("<script>") && !html.includes("<img") && !html.includes("javascript:"));
  assert.ok(html.includes("&lt;script&gt;") && html.includes("&quot;bad&quot;"));
  assert.match(html, /<code .*tabindex="0"/);
});

test("Show all toggles scoped rows, label and aria-expanded in both directions", () => {
  const { events } = harness();
  const rows = [{ hidden: true }, { hidden: true }];
  const attrs = { "aria-expanded": "false" };
  const button = { dataset: { total: "12" }, getAttribute: k => attrs[k], setAttribute: (k, v) => { attrs[k] = v; },
    closest: () => ({ querySelectorAll: () => rows }) };
  const event = { target: { closest: () => button } };
  events.click(event);
  assert.ok(rows.every(row => !row.hidden)); assert.equal(attrs["aria-expanded"], "true"); assert.equal(button.textContent, "Show fewer");
  events.click(event);
  assert.ok(rows.every(row => row.hidden)); assert.equal(attrs["aria-expanded"], "false"); assert.equal(button.textContent, "Show all (12)");
});

test("palette uses the project at execution, POSTs only explicitly, and toasts safe failures", async () => {
  const { api, calls, toasts } = harness();
  assert.equal(api.command.label, "Google Sheet: validate mapping");
  assert.equal(calls.length, 0);
  await api.command.run();
  assert.equal(calls[0][0], "/studio/projects/my%20project/plugins/gsheet/check");
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(toasts[0], ["2 problems: unknown column RAM", "warn"]);
  api.project = () => null;
  await api.command.run(); assert.equal(calls.length, 1); assert.equal(toasts.at(-1)[1], "warn");
  api.project = () => "other";
  api.fetchJSON = async () => { throw new Error("SECRET HTTP response"); };
  await api.command.run(); assert.equal(toasts.at(-1)[1], "fail"); assert.ok(!toasts.at(-1)[0].includes("SECRET"));
});

test("host project API respects project switches and the All projects target", () => {
  const hostSource = readFileSync(new URL("../../src/alfrd/web/js/components/plugin_api.js", import.meta.url), "utf8")
    .replace(/^import .*;\n/gm, "").replace(/export /g, "");
  const sandbox = { registerViewer() {}, registerPanel() {}, registerProjectSection() {} };
  runInNewContext(`${hostSource}; globalThis.makeApi = api;`, sandbox);
  const ctx = { state: { selectedProject: "first" }, target: () => ({ project: "target-project" }) };
  const api = sandbox.makeApi(ctx, {});
  assert.equal(api.project(), "first"); ctx.state.selectedProject = "second"; assert.equal(api.project(), "second");
  ctx.state.selectedProject = "all"; assert.equal(api.project(), "target-project");
  ctx.target = () => null; assert.equal(api.project(), null);
});
