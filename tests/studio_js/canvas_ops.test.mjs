// Canvas operations: connect / disconnect / cycles / delete on workflow drafts, the Configure
// form, keyboard connect, history routing and global shortcuts (no browser DOM).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const { parseYaml } = await import("../../src/alfrd/web/js/utils/yaml_parser.js");
const { manifestToWorkflows } = await import("../../src/alfrd/web/js/data/model.js");
const {
  connectSteps, connectProblem, dependencyMap, disconnectSteps, findCycle, removeStep, setDependencies, workflowText, editorSteps,
} = await import("../../src/alfrd/web/js/data/workflow_edit.js");
const { setRoute, isEditable, keyLabel, parseRoute, routeHash } = await import("../../src/alfrd/web/js/utils/dom.js");
const ops = await import("../../src/alfrd/web/js/components/canvas_ops.js");
const { shortcutRows } = await import("../../src/alfrd/web/js/components/shortcuts.js");

const wf = (steps) => ({ name: "p", workflows: [{ name: "main", steps }] });
const steps = (data) => manifestToWorkflows(data, "alfrd.yaml", { aliases: false }).workflows[0].steps.map((s) => [s.key, s.depends]);

test("connect adds an explicit dependency and keeps the implicit chain", () => {
  let data = wf(["a", "b", { id: "c", depends_on: [] }]);
  assert.deepEqual(dependencyMap(data), { a: [], b: ["a"], c: [] });
  data = connectSteps(data, "a", "c");
  assert.deepEqual(data.workflows[0].steps[2], { id: "c", depends_on: ["a"] });
  data = connectSteps(data, "b", "c");
  assert.deepEqual(steps(data), [["a", []], ["b", ["a"]], ["c", ["a", "b"]]]);
  // "c" waited on "b" implicitly (previous step); connecting a → c keeps that wait explicitly.
  assert.deepEqual(connectSteps(wf(["a", "b", "c"]), "a", "c").workflows[0].steps[2], { id: "c", depends_on: ["b", "a"] });
});

test("cycles are refused with the path, on connect and on explicit dependencies", () => {
  const data = wf(["a", "b", "c"]); // a → b → c by order
  assert.match(connectProblem(data, "c", "a"), /dependency cycle: a → b → c → a|cycle/);
  assert.throws(() => connectSteps(data, "c", "a"), /This would create a dependency cycle: .*a.*→.*a/);
  assert.throws(() => connectSteps(data, "a", "a"), /cannot wait on itself/);
  assert.throws(() => connectSteps(data, "a", "b"), /already waits/);
  assert.throws(() => setDependencies(data, "a", ["zz"]), /Unknown step "zz"/);
  assert.throws(() => setDependencies(data, "a", ["c"]), /cycle/);
  assert.deepEqual(findCycle({ a: ["c"], b: ["a"], c: ["b"] }), ["a", "b", "c", "a"]);
  assert.equal(findCycle({ a: [], b: ["a"] }), null);
  // The model reports a cycle that came from the file (Save stays disabled).
  const errors = manifestToWorkflows(wf([{ id: "a", depends_on: ["b"] }, { id: "b", depends_on: ["a"] }]), "alfrd.yaml", { aliases: false }).errors;
  assert.ok(errors.some((e) => /dependency cycle between a, b/.test(e)), errors.join("\n"));
});

test("disconnect leaves an explicit list; the last one makes the step start first", () => {
  let data = connectSteps(wf(["a", "b", { id: "c", depends_on: ["a"] }]), "b", "c");
  data = disconnectSteps(data, "a", "c");
  assert.deepEqual(dependencyMap(data).c, ["b"]);
  data = disconnectSteps(data, "a", "b"); // implicit previous-step edge
  assert.deepEqual(data.workflows[0].steps[1], { id: "b", depends_on: [] });
  assert.throws(() => disconnectSteps(data, "a", "b"), /does not wait on a/);
  // A step-name `after` is replaced by the explicit list; a delay stays.
  data = disconnectSteps(wf(["a", { id: "b", after: "a" }, { id: "c", after: "+30m", depends_on: ["a", "b"] }]), "a", "c");
  assert.deepEqual(data.workflows[0].steps.slice(1), [{ id: "b", after: "a" }, { id: "c", after: "+30m", depends_on: ["b"] }]);
  assert.deepEqual(connectSteps(wf(["a", "x", { id: "b", after: "a" }]), "x", "b").workflows[0].steps[2], { id: "b", depends_on: ["a", "x"] });
});

test("delete removes the step and its edges; the text keeps other sections", () => {
  const text = "# keep\nname: p\nworkflows:\n  - name: main\n    steps:\n      - a\n      - b\n      - {id: c, depends_on: [a, b]}\n";
  let data = parseYaml(text);
  data = removeStep(data, "b");
  assert.deepEqual(steps(data), [["a", []], ["c", ["a"]]]);
  const out = workflowText(text, data);
  assert.match(out, /^# keep/);
  assert.deepEqual(steps(parseYaml(out)), [["a", []], ["c", ["a"]]]);
});

test("Configure form: command, deps, inputs/outputs, delay; refuses a cycle and a bad delay", () => {
  const data = wf(["a", "b", { id: "c", cmd: ["echo", "c"] }]);
  const d = { data, template: null };
  const row = editorSteps(data).find((r) => r.id === "a");
  const base = { label: "", id: "a", runs: "cmd", cmd: "python run.py '{target} x'", stage: "", inputs: "in/{target}.fits\n\n", outputs: "out.ms", timeout: "60", delay: "+5m", status_from: "", skip: false };
  const { next, id } = ops.applyForm(d, { row, v: base, deps: [], depsTouched: false });
  assert.equal(id, "a");
  assert.deepEqual(next.workflows[0].steps[0], { id: "a", cmd: ["python", "run.py", "{target} x"], after: "+5m", timeout: 60, inputs: ["in/{target}.fits"], outputs: ["out.ms"] });
  assert.throws(() => ops.applyForm(d, { row, v: { ...base, deps: [] }, deps: ["c"], depsTouched: true }), /cycle/);
  assert.throws(() => ops.applyForm(d, { row, v: { ...base, delay: "soon" }, deps: [], depsTouched: false }), /Start delay/);
  assert.throws(() => ops.applyForm(d, { row, v: { ...base, cmd: "" }, deps: [], depsTouched: false }), /Give the step a command/);
});

function fakeApi(stepsList) {
  return {
    ui: { selected: "c" }, el: { querySelector: () => null, querySelectorAll: () => [] },
    steps: () => stepsList, project: () => "p", canEdit: () => true, plans: () => false, announce(t) { this.said = t; },
    focus() {}, select(k) { this.ui.selected = k; }, editor: async () => { throw new Error("editor must not load for a refused link"); },
  };
}

test("drag/keyboard connect: a cycle is refused with a message and nothing is edited", async () => {
  const api = fakeApi([{ key: "a", depends: [] }, { key: "b", depends: ["a"] }, { key: "c", depends: ["b"] }]);
  assert.equal(ops.linkProblem(api, "a", "c"), null);
  assert.match(ops.linkProblem(api, "c", "a"), /dependency cycle: a → b → c → a/);
  const toasts = [];
  const ctx = { toast: (m, tone) => toasts.push([m, tone]), menu() {}, update() {} };
  assert.equal(await ops.connect(ctx, api, "c", "a"), false);
  assert.match(toasts[0][0], /^Not connected: This would create a dependency cycle/);
  assert.equal(toasts[0][1], "fail");
  // Keyboard: C on the selected step "c", arrows to "a", Enter → refused (cycle).
  const key = (k) => ({ key: k, preventDefault() {} });
  global.CSS = { escape: (s) => s };
  assert.equal(ops.key(ctx, api, key("c")), true);
  assert.equal(ops.connecting(), true);
  assert.match(api.said, /Connecting from c\..* a: This would create a dependency cycle/); // first candidate: a
  ops.key(ctx, api, key("ArrowRight"));
  assert.match(api.said, /^b: This would create a dependency cycle: b → c → b/);
  ops.key(ctx, api, key("ArrowLeft"));
  assert.match(api.said, /^a: /);
  ops.key(ctx, api, key("Enter"));
  assert.equal(ops.connecting(), false);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(toasts.length, 2);
  // Escape cancels connect mode.
  ops.key(ctx, api, key("c"));
  ops.key(ctx, api, key("Escape"));
  assert.equal(ops.connecting(), false);
  assert.equal(api.said, "Connecting cancelled");
  assert.equal(ops.key(ctx, api, key("x")), false);
});

test("history: section and target changes push entries; normalization replaces", () => {
  const calls = [];
  globalThis.location = { hash: "#/overview?p=a" };
  globalThis.history = { pushState: (_s, _t, u) => { calls.push(["push", u]); location.hash = u; }, replaceState: (_s, _t, u) => { calls.push(["replace", u]); location.hash = u; } };
  assert.equal(setRoute(routeHash("workflow", "a", "a/T1")), true);
  assert.equal(setRoute(routeHash("workflow", "a", "a/T1")), false); // same URL: no entry
  setRoute(routeHash("workflow", "a", "a/T2"));
  setRoute(routeHash("workflow", "b", null), { replace: true });
  assert.deepEqual(calls.map((c) => c[0]), ["push", "push", "replace"]);
  assert.deepEqual(parseRoute(calls[1][1]), { view: "workflow", project: "a", target: "a/T2", plan: null, unit: null });
});

const el = (tag, attrs = {}) => ({
  tagName: tag.toUpperCase(), isContentEditable: Boolean(attrs.editable),
  closest(sel) { return sel.split(/,(?![^(]*\))/).some((s) => matches(this, tag, attrs, s.trim())) ? this : null; },
});
function matches(_n, tag, attrs, sel) {
  const m = sel.match(/^([a-z]+|\[contenteditable\])(.*)$/);
  if (!m) return false;
  if (m[1] === "[contenteditable]") return "contenteditable" in attrs && attrs.contenteditable !== "false";
  if (m[1] !== tag) return false;
  const not = [...m[2].matchAll(/\[type=(\w+)\]/g)].map((x) => x[1]);
  return !not.includes(attrs.type);
}

test("editable fields: text inputs, textareas, selects and contenteditable, not checkboxes or buttons", () => {
  assert.equal(isEditable(el("input", { type: "text" })), true);
  assert.equal(isEditable(el("textarea")), true);
  assert.equal(isEditable(el("select")), true);
  assert.equal(isEditable(el("div", { editable: true })), true);
  assert.equal(isEditable(el("input", { type: "checkbox" })), false);
  assert.equal(isEditable(el("button")), false);
  assert.equal(isEditable(null), false);
});

test("global shortcuts (?, Ctrl/Cmd+K, Alt+Shift+C, Alt+Shift+A) are ignored while typing in a field", () => {
  const app = readFileSync(new URL("../../src/alfrd/web/js/app.js", import.meta.url), "utf8");
  const start = app.indexOf("// Global keys never act while typing");
  const source = app.slice(start, app.indexOf("function toggleCompact()", start));
  const handlers = [], opened = [];
  const sandbox = {
    document: { addEventListener: (t, fn) => handlers.push([t, fn]) }, isEditable, $: () => ({ hidden: true }),
    ctx: { openPalette: () => opened.push("palette"), openShortcuts: () => opened.push("shortcuts") },
    canCreateTarget: () => true, newTarget: () => opened.push("target"), toggleCompact: () => opened.push("compact"),
    dockAvailable: () => true, setChatDock: () => opened.push("dock"),
  };
  runInNewContext(source, sandbox);
  const [type, onKey] = handlers[0];
  assert.equal(type, "keydown");
  const press = (target, k) => onKey({ target, preventDefault() {}, isComposing: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, code: "", ...k });
  const input = el("input", { type: "text" }), body = el("body");
  press(input, { key: "?" });
  press(input, { key: "n" });
  press(input, { key: "k", ctrlKey: true });
  press(el("textarea"), { key: "k", metaKey: true });
  press(input, { key: "C", altKey: true, shiftKey: true, code: "KeyC" });
  press(input, { key: "A", altKey: true, shiftKey: true, code: "KeyA" });
  assert.deepEqual(opened, []);
  press(body, { key: "?" });
  press(body, { key: "k", metaKey: true });
  press(body, { key: "N", ctrlKey: true, altKey: true, code: "KeyN" });
  press(body, { key: "C", altKey: true, shiftKey: true, code: "KeyC" });
  press(body, { key: "k", ctrlKey: true, isComposing: true });
  press(body, { key: "A", altKey: true, shiftKey: true, code: "KeyA" });
  assert.deepEqual(opened, ["shortcuts", "palette", "target", "compact", "dock"]);
});

test("shortcut reference uses the platform's modifiers", () => {
  assert.equal(keyLabel("Ctrl+K", true), "⌘K");
  assert.equal(keyLabel("Ctrl+Alt+N", true), "⌘⌥N");
  assert.equal(keyLabel("Alt+Shift+C", false), "Alt+Shift+C");
  const mac = shortcutRows(true).flatMap(([, rows]) => rows.map(([k]) => k));
  const pc = shortcutRows(false).flatMap(([, rows]) => rows.map(([k]) => k));
  assert.ok(mac.includes("⌘K") && mac.includes("⌥⇧C") && !mac.some((k) => k.includes("Ctrl")));
  assert.ok(pc.includes("Ctrl+K") && pc.includes("Shift+F10 / Menu"));
});
