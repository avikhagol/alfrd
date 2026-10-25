import test from "node:test";
import assert from "node:assert/strict";
import { createGraphUpdates } from "../../src/alfrd/web/js/components/graph_groups.js";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
let frames = [];
globalThis.requestAnimationFrame = (fn) => frames.push(fn);
const { mountGraph } = await import("../../src/alfrd/web/js/components/graph_view.js");
const model = (rev, ids = ["a"]) => ({ ...(rev === undefined ? {} : { rev }),
  nodes: ids.map((id) => ({ id, request: id, title: id, st: "queued", label: "Queued", tone: "muted" })), edges: [] });
function mounted(groups = true) {
  frames = [];
  const element = () => {
    const listeners = new Map();
    return { style: {}, innerHTML: "", textContent: "", classList: { add() {}, remove() {} },
      addEventListener(type, fn) { listeners.set(type, fn); },
      fire(type, event = {}) { listeners.get(type)?.({ button: 0, pointerId: 1, pointerType: "mouse",
        clientX: 0, clientY: 0, target: { closest: () => null }, preventDefault() {}, ...event }); },
      getBoundingClientRect: () => ({ width: 1000, height: 600, left: 0, top: 0 }),
      querySelector: () => null, querySelectorAll: () => [], contains: () => false, focus() {} };
  };
  const elements = new Map([".gv-canvas", ".world", "[data-gv-live]", ".gv-empty", ".gv-zoom"].map((s) => [s, element()]));
  const host = element(); host.querySelector = (s) => elements.get(s);
  const graph = mountGraph(host, { groups });
  return { graph, canvas: elements.get(".gv-canvas"), world: elements.get(".world"),
    tick: () => { const pending = frames; frames = []; pending.forEach((fn) => fn()); } };
}

test("pan and zoom survive status, node, lane and empty graph re-renders", () => {
  for (const groups of [false, true]) {
    const { graph, canvas, world, tick } = mounted(groups);
    graph.update(model(1)); tick();
    canvas.fire("wheel", { deltaX: 35, deltaY: 50 });
    canvas.fire("wheel", { ctrlKey: true, deltaY: -1, clientX: 300, clientY: 200 });
    const transform = world.style.transform;
    const status = model(1);
    status.nodes[0].st = "running";
    graph.update(status); tick();
    assert.match(world.innerHTML, /st-running/);
    assert.equal(world.style.transform, transform);
    for (const [rev, ids] of [[2, ["a", "b"]], [3, ["b"]], [4, []], [5, ["c"]]]) {
      graph.update(model(rev, ids)); tick();
      assert.equal(world.style.transform, transform);
    }
    graph.fit();
    assert.notEqual(world.style.transform, transform);
  }
});

test("navigation before the initial animation frame cancels automatic fit", () => {
  const { graph, canvas, world, tick } = mounted();
  graph.update(model(1));
  canvas.fire("wheel", { deltaX: 20, deltaY: 40 });
  const transform = world.style.transform;
  tick(); assert.equal(world.style.transform, transform);
});

test("empty initial graph waits for the first populated graph to fit", () => {
  const { graph, world, tick } = mounted();
  graph.update(model(1, [])); tick();
  assert.equal(world.style.transform, "translate(0px, 0px) scale(1)");
  graph.update(model(2)); tick();
  assert.notEqual(world.style.transform, "translate(0px, 0px) scale(1)");
});

test("stale revisions and unchanged responses cannot overwrite the mounted graph", () => {
  const { graph, world } = mounted();
  graph.update(model(5)); const html = world.innerHTML;
  graph.update(model(3, ["stale"]));
  graph.update(model(undefined, ["unversioned"]));
  graph.update({ rev: 6, unchanged: true });
  assert.equal(world.innerHTML, html);
  graph.update(model(6, ["new"])); assert.match(world.innerHTML, /data-key="new"/);
  const other = mounted(); other.graph.update(model(1, ["independent"]));
  assert.match(other.world.innerHTML, /independent/);
});

test("hover coalesces updates, rejects out-of-order pending revisions and leaves no stale lanes", () => {
  const { graph, canvas, world } = mounted();
  graph.update(model(1)); const html = world.innerHTML;
  canvas.fire("pointerenter");
  graph.update(model(3, ["discarded"])); graph.update(model(5, ["latest"]));
  graph.update(model(4, ["stale"]));
  assert.equal(world.innerHTML, html);
  canvas.fire("pointerleave");
  assert.match(world.innerHTML, /data-key="latest"/);
  assert.doesNotMatch(world.innerHTML, /discarded|stale/);
});

for (const finish of ["pointerup", "pointercancel", "lostpointercapture"]) {
  test(`pan defers until ${finish}, even after leaving the canvas`, () => {
    const { graph, canvas, world, tick } = mounted();
    graph.update(model(1)); tick(); const html = world.innerHTML;
    canvas.fire("pointerenter"); canvas.fire("pointerdown");
    canvas.fire("pointermove", { clientX: 80, clientY: 50 });
    const transform = world.style.transform;
    graph.update(model(2, ["latest"])); canvas.fire("pointerleave");
    assert.equal(world.innerHTML, html);
    canvas.fire(finish);
    assert.match(world.innerHTML, /data-key="latest"/);
    assert.equal(world.style.transform, transform);
  });
}

test("node drag waits for both pointer release and hover exit", () => {
  const { graph, canvas, world } = mounted();
  graph.update(model(1)); const html = world.innerHTML;
  canvas.fire("pointerenter");
  canvas.fire("pointerdown", { target: { closest: (s) => s === ".node" ? { dataset: { key: "a" } } : null } });
  canvas.fire("pointermove", { clientX: 10 });
  graph.update(model(2, ["latest"]));
  canvas.fire("pointerup"); assert.equal(world.innerHTML, html);
  canvas.fire("pointerleave"); assert.match(world.innerHTML, /data-key="latest"/);
});

test("delivery gate supports unversioned drafts, equal-revision selection and latest-only flush", () => {
  let busy = false; const applied = [];
  const gate = createGraphUpdates((m, selected) => applied.push([m.rev, selected]), () => busy);
  gate.update(model(), "draft"); gate.update(model(2), "a"); gate.update(model(2), "b");
  busy = true; gate.update(model(4), "queued"); gate.update(model(3), "stale");
  gate.flush(); assert.deepEqual(applied, [[undefined, "draft"], [2, "a"], [2, "b"]]);
  busy = false; gate.flush(); gate.flush();
  assert.deepEqual(applied.at(-1), [4, "queued"]); assert.equal(applied.length, 4);
});
