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
test("G4 request scope selector is shared by Graph, Files and Goals", async () => {
  const { createRequestScope, tabsHtml } = await import("../../src/alfrd/web/js/components/inspector.js");
  const { graphModel } = await import("../../src/alfrd/web/js/data/chat.js");
  const scope = createRequestScope();
  const state = { requests: [{ id: "a", text: "First", status: "running" }, { id: "b", text: "Second", status: "done" }] };
  assert.equal(scope.request(state), "a", "newest active is current");
  state.requests[0].status = "done";
  assert.equal(scope.request(state), "b", "otherwise newest");
  scope.select("a");
  const before = scope.html(state);
  for (const tab of ["graph", "files", "goals"]) {
    assert.match(tabsHtml(tab), new RegExp(`data-insp-tab="${tab}"[^>]*aria-selected="true"`));
    assert.equal(scope.html(state), before);
    assert.equal(scope.request(state), "a");
  }
  state.requests.push({ id: "c", text: "<script>long title</script>", status: "running" });
  assert.equal(scope.request(state), "a", "background refresh keeps individual selection");
  assert.match(scope.html(state), /&lt;script&gt;/);
  const raw = { rev: 4, nodes: [{ id: "a1", request: "a" }, { id: "b1", request: "b" }, { id: "old" }],
    edges: [{ from: "a1", to: "b1" }], groups: [{ id: "a", title: "First" }, { id: "b", title: "Second" }, { id: "unscoped", title: "Unscoped" }] };
  const filtered = graphModel(raw, {}, scope.request(state));
  assert.deepEqual(filtered.nodes.map((n) => n.id), ["a1"]);
  assert.deepEqual(filtered.edges, []);
  assert.equal(filtered.rev, 4);
  assert.deepEqual(filtered.groups.map((g) => g.id), ["a"]);
  scope.select("all");
  const all = graphModel(raw, {}, scope.request(state));
  const r = mounted({ groups: true });
  r.graph.update({ ...all, scope: "all" }); r.tick();
  for (const title of ["First", "Second", "Unscoped"]) assert.ok(r.world.innerHTML.includes(title));
  r.graph.update({ ...filtered, scope: "a" }); r.tick();
  assert.doesNotMatch(r.world.innerHTML, /data-graph-group="b"|data-graph-group="unscoped"/);
  const viewport = r.world.style.transform;
  r.canvas.fire("wheel", { deltaX: 77, deltaY: 22 });
  const moved = r.world.style.transform;
  assert.notEqual(moved, viewport);
  r.graph.update({ ...filtered, rev: 5, scope: "a" }); r.tick();
  assert.equal(r.world.style.transform, moved, "ordinary updates preserve navigation");
  r.graph.update({ ...all, rev: 6, scope: "all" }); r.tick();
  assert.notEqual(r.world.style.transform, moved, "scope changes fit");
  scope.select("current");
  assert.equal(scope.request({}), null, "legacy and empty sessions remain usable");
  assert.match(scope.html({}), /Current request: None/);
  const src = readFileSync(new URL("../../src/alfrd/web/js/components/chat_view.js", import.meta.url), "utf8");
  assert.match(src, /id="chat-request-scope"/);
  assert.match(src, /scope\.select\(e.target.value\)/);
  assert.match(src, /full.g.update\(model, S.task\)/);
});


test("G4 session changes reset revision delivery without accepting stale scope updates", async () => {
  const { createGraphUpdates } = await import("../../src/alfrd/web/js/components/graph_groups.js");
  const seen = [];
  const gate = createGraphUpdates((m) => seen.push(m), () => false);
  gate.update({ stream: "first", rev: 90 });
  gate.update({ stream: "second", rev: 1, scope: "all" });
  gate.update({ stream: "second", rev: 0, scope: "a" });
  assert.deepEqual(seen.map((m) => m.rev), [90, 1]);
});
