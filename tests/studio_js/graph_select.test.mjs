import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
globalThis.CSS = { escape: (s) => s };
let frames = [];
globalThis.requestAnimationFrame = (fn) => frames.push(fn);
const GRAPH = "../../src/alfrd/web/js/components/graph_view.js";
const { mountGraph } = await import(GRAPH);
const model = (ids) => ({ rev: 1, nodes: ids.map((id) => ({ id, request: id, title: id, st: "queued", label: "Queued", tone: "muted" })), edges: [] });

function mounted(opts = {}) {
  frames = [];
  const element = () => {
    const listeners = new Map();
    return { style: {}, innerHTML: "", textContent: "", classList: { add() {}, remove() {} },
      addEventListener(type, fn) { listeners.set(type, fn); },
      fire(type, event = {}) { listeners.get(type)?.({ button: 0, pointerId: 1, pointerType: "mouse", clientX: 0, clientY: 0,
        target: { closest: () => null }, preventDefault() {}, stopPropagation() {}, ...event }); },
      getBoundingClientRect: () => ({ width: 1000, height: 600, left: 0, top: 0 }),
      querySelector: () => null, querySelectorAll: () => [], contains: () => false, focus() {} };
  };
  const elements = new Map([".gv-canvas", ".world", "[data-gv-live]", ".gv-empty", ".gv-zoom"].map((s) => [s, element()]));
  const host = element(); host.querySelector = (s) => elements.get(s);
  const graph = mountGraph(host, opts);
  return { graph, host, canvas: elements.get(".gv-canvas"), world: elements.get(".world"), live: elements.get("[data-gv-live]"),
    tick: () => { const f = frames; frames = []; f.forEach((fn) => fn()); } };
}
const focusNode = (canvas, key) => canvas.fire("focusin", { target: { closest: () => ({ dataset: { key } }) } });

test("G6 legend lists all six states with visible text, read-only graphs only", () => {
  const { host } = mounted();
  for (const label of ["Done", "Running", "Failed", "Blocked", "Queued", "Cancelled"]) {
    assert.match(host.innerHTML, new RegExp(`class="status-label">${label}<`));
  }
  assert.match(host.innerHTML, /<summary>Status legend and keyboard help<\/summary>/);
  assert.match(host.innerHTML, /Escape clears selection/);
  assert.doesNotMatch(mounted({ editable: true }).host.innerHTML, /Status legend/);
});

test("G6 arrow panning never selects, at 20%, 100% and 200% zoom", () => {
  for (const deltaY of [1, 0, -1]) {
    const selected = [];
    const { graph, canvas, world, tick } = mounted({ onSelect: (id) => selected.push(id) });
    graph.update(model(["a", "b"])); tick();
    if (deltaY) for (let i = 0; i < 20; i++) canvas.fire("wheel", { ctrlKey: true, deltaY, clientX: 0, clientY: 0 });
    const nums = () => world.style.transform.match(/-?[\d.]+(?=px)/g).map(Number);
    const near = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 1e-6);
    const before = nums();
    const key = (k) => canvas.fire("keydown", { key: k });
    key("ArrowLeft");
    assert.ok(!near(nums(), before));
    key("ArrowRight"); assert.ok(near(nums(), before));
    key("ArrowUp"); key("ArrowDown"); assert.ok(near(nums(), before));
    assert.deepEqual(selected, []);
  }
});

test("G6 keyboard: focus selects, arrows clamp at the ends, Space opens, Escape clears and announces", () => {
  const selected = [], opened = [];
  const { graph, canvas, live, tick } = mounted({ onSelect: (id) => selected.push(id), onOpen: (id) => opened.push(id) });
  graph.update(model(["a", "b", "c"])); tick();
  const key = (k) => canvas.fire("keydown", { key: k });
  focusNode(canvas, "a");
  key("ArrowLeft"); key("ArrowUp");
  assert.equal(selected.at(-1), "a");
  key("ArrowRight"); key("ArrowDown"); key("ArrowRight"); key("ArrowRight");
  assert.equal(selected.at(-1), "c");
  key(" "); assert.deepEqual(opened, ["c"]);
  key("Escape");
  assert.equal(selected.at(-1), null);
  assert.match(live.textContent, /Selection cleared/);
});

test("G6 defect: Escape without a selection propagates so the full-screen modal can close", () => {
  const { graph, canvas, tick } = mounted();
  graph.update(model(["a"])); tick();
  let stopped = 0, prevented = 0;
  const esc = () => canvas.fire("keydown", { key: "Escape", stopPropagation() { stopped++; }, preventDefault() { prevented++; } });
  esc();
  assert.equal(stopped, 0); assert.equal(prevented, 0);
  focusNode(canvas, "a");
  esc();
  assert.equal(stopped, 1); assert.equal(prevented, 1);
  esc();
  assert.equal(stopped, 1);
});

test("G6 editable drafts leave Escape alone", () => {
  const { graph, canvas, tick } = mounted({ editable: true });
  graph.update(model(["a", "b"])); tick();
  let stopped = 0;
  canvas.fire("keydown", { key: "Escape", stopPropagation() { stopped++; } });
  assert.equal(stopped, 0);
});

test("G7 inverse-coordinate math round-trips at 20%, 100% and 200% zoom (existing worldAt only)", () => {
  const src = readFileSync(new URL(GRAPH, import.meta.url), "utf8");
  const line = src.split("\n").find((l) => l.includes("const worldAt ="));
  assert.ok(line);
  const worldAt = new Function("canvas", "view", `${line.trim()}\nreturn worldAt;`);
  const canvas = { getBoundingClientRect: () => ({ left: 37, top: 91 }) };
  for (const zoom of [0.2, 1, 2]) {
    const view = { x: -123, y: 57, zoom };
    const at = worldAt(canvas, view);
    for (const [wx, wy] of [[40, 80], [580, 360], [200, 200]]) {
      const p = at(wx * zoom + view.x + 37, wy * zoom + view.y + 91);
      assert.ok(Math.abs(p.x - wx) < 1e-9 && Math.abs(p.y - wy) < 1e-9, `zoom ${zoom}`);
    }
  }
});

// Deferred at the lazy payload cap (g4/g5/g7 reports): documented, not passing.
test("G5 Shift+F10 opens the task context menu on the focused node", { skip: "G5 deferred at lazy payload cap" }, () => {});
test("G7 rectangle selection selects nodes at 20%, 100% and 200% zoom", { skip: "G7 deferred at lazy payload cap" }, () => {});
test("G4 request scope selector is shared by Graph, Files and Goals", { skip: "G4 deferred at lazy payload cap" }, () => {});
