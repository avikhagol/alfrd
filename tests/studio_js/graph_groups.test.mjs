import test from "node:test";
import assert from "node:assert/strict";
import { createGraphGroups, renderGraphGroups } from "../../src/alfrd/web/js/components/graph_groups.js";
import { layoutGraph } from "../../src/alfrd/web/js/data/chat.js";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
globalThis.requestAnimationFrame = () => {}; // Leave fit pending: inspect canvas coordinates.
const { mountGraph } = await import("../../src/alfrd/web/js/components/graph_view.js");

function host() {
  const elements = new Map();
  const el = () => ({ style: {}, innerHTML: "", textContent: "", addEventListener() {},
    getBoundingClientRect: () => ({ width: 1000, height: 600 }), querySelector: () => null });
  for (const selector of [".gv-canvas", ".world", "[data-gv-live]", ".gv-empty", ".gv-zoom"]) elements.set(selector, el());
  return { innerHTML: "", addEventListener() {}, contains: () => false,
    querySelector: (selector) => elements.get(selector), querySelectorAll: () => [], elements };
}
const node = (id, request) => ({ id, request, title: id, st: "queued", label: "Queued", tone: "muted" });
const model = {
  nodes: [node("a", "r1"), node("b", "r1"), node("c", "r2")],
  edges: [{ from: "a", to: "b" }],
  groups: [{ id: "r1", title: "First request", nodes: ["a", "b"] }, { id: "r2", title: "Second request", nodes: ["c"] }],
};

test("project canvas unchanged without opts.groups", () => {
  const root = host();
  const graph = mountGraph(root, { editable: true });
  graph.update(model);
  const world = root.elements.get(".world"), expected = layoutGraph(model.nodes, model.edges);
  assert.equal(world.style.width, `${expected.width}px`);
  assert.equal(world.style.height, `${expected.height}px`);
  assert.doesNotMatch(world.innerHTML, /data-graph-group/);
  for (const p of Object.values(expected.pos)) assert.ok(world.innerHTML.includes(`left:${p.x}px;top:${p.y}px;width:${p.w}px`));
  assert.match(world.innerHTML, /data-port/);
  // Server group metadata alone must have no effect on the project renderer.
  const before = world.innerHTML;
  graph.update({ ...model, groups: [] });
  assert.equal(world.innerHTML, before);
});

test("linear lanes remain fixed width as other lanes grow", () => {
  const base = createGraphGroups();
  const groups = { layout: (nodes, edges, groups) => base.layout(nodes, edges, groups, { layout: "linear" }) };
  const before = groups.layout(model.nodes, model.edges, model.groups);
  const after = groups.layout([node("d", "r3"), ...model.nodes], model.edges,
    [{ id: "r3", title: "Third", nodes: ["d"] }, ...model.groups].reverse());
  for (const n of model.nodes) assert.deepEqual(after.pos[n.id], before.pos[n.id]);
  assert.deepEqual(after.lanes.slice(0, 2), before.lanes);
  assert.equal(after.lanes[2].id, "r3");
  assert.ok(after.pos.d.x > after.pos.c.x);
  const grown = groups.layout([...model.nodes, node("e", "r1")], model.edges, model.groups);
  assert.deepEqual(grown.pos.c, before.pos.c); // Growth in another lane cannot cause overlap.
  const restored = groups.layout(model.nodes, model.edges, model.groups);
  assert.deepEqual(restored.pos, before.pos);
});

test("opts.groups renders request headers and falls back to node request and Unscoped", () => {
  const root = host(), graph = mountGraph(root, { groups: true });
  graph.update(model);
  const svg = root.elements.get(".world").innerHTML;
  assert.match(svg, /data-graph-group="r1"/);
  assert.match(svg, /First request/);
  assert.match(svg, /Second request/);
  const legacy = createGraphGroups().layout([node("x", "old"), node("y")], []);
  assert.deepEqual(legacy.lanes.map((l) => l.id), ["old", "unscoped"]);
  assert.equal(legacy.lanes[1].title, "Unscoped");
  const explicit = createGraphGroups().layout([node("x")], [], [{ id: "r", nodes: ["x"], title: '<script>"&' }]);
  assert.deepEqual(explicit.order, ["x"]);
  assert.match(renderGraphGroups(explicit.lanes), /&lt;script&gt;&quot;&amp;/);
  assert.doesNotMatch(renderGraphGroups(explicit.lanes), /<script>/);
});

test("empty request slots and mount state are independent", () => {
  const groups = createGraphGroups();
  const first = groups.layout([], [], [{ id: "pending", title: "Planning", nodes: [] }]);
  const next = groups.layout([node("x", "next")], []);
  assert.equal(next.lanes[0].id, "pending");
  assert.equal(next.lanes[0].x, first.lanes[0].x);
  assert.ok(next.pos.x.x > first.lanes[0].x);
  assert.equal(createGraphGroups().layout([node("x", "next")], []).lanes[0].x, 0);
});

test("mounted graph appends lanes across updates and accepts explicit opts.groups", () => {
  const root = host(), graph = mountGraph(root, { groups: true });
  graph.update(model);
  const world = root.elements.get(".world");
  const cards = (html) => [...html.matchAll(/data-key="([^"]+)"[^>]*style="([^"]+)"/g)]
    .map(([, id, style]) => [id, style]);
  const before = new Map(cards(world.innerHTML));
  graph.update({ ...model, nodes: [node("d", "r3"), ...model.nodes],
    groups: [{ id: "r3", title: "Third", nodes: ["d"] }, ...model.groups] });
  const after = new Map(cards(world.innerHTML));
  for (const [id, style] of before) assert.equal(after.get(id), style);
  assert.deepEqual([...world.innerHTML.matchAll(/data-graph-group="([^"]+)"/g)].map((m) => m[1]), ["r1", "r2", "r3"]);

  const explicit = host();
  mountGraph(explicit, { groups: [{ id: "chosen", title: "Chosen lane", nodes: ["a", "b", "c"] }] }).update(model);
  const html = explicit.elements.get(".world").innerHTML;
  assert.match(html, /data-graph-group="chosen"/);
  assert.doesNotMatch(html, /data-graph-group="r[12]"/);
  assert.match(html, /3 nodes/);
});


test("spread-out request lanes preserve global depth, fit wide rows and keep edges downward", () => {
  const nodes = [node("join", "r2"), node("a", "r1"), node("b", "r1"), node("c", "r2"), node("end", "r2")];
  const edges = [{ from: "a", to: "join" }, { from: "b", to: "join" }, { from: "join", to: "end" }, { from: "a", to: "end" }];
  const L = createGraphGroups().layout(nodes, edges);
  assert.equal(L.pos.a.y, L.pos.b.y);
  assert.equal(L.pos.a.y, L.pos.c.y);
  assert.ok(L.pos.b.x >= L.pos.a.x + L.pos.a.w);
  for (const e of edges) assert.ok(L.pos[e.to].y > L.pos[e.from].y);
  for (const lane of L.lanes) for (const n of nodes.filter((n) => n.request === lane.id)) {
    assert.ok(L.pos[n.id].x >= lane.x);
    assert.ok(L.pos[n.id].x + L.pos[n.id].w <= lane.x + lane.width);
    assert.ok(L.pos[n.id].y + 128 <= lane.height);
  }
  assert.ok(L.lanes[1].x >= L.lanes[0].x + L.lanes[0].width);
  assert.ok(L.width >= L.lanes[1].x + L.lanes[1].width);
});

test("pure layout uses longest path with joins, a chain, empty graphs and linear mode", () => {
  const nodes = ["end", "join", "a", "b", "mid"].map((id) => node(id));
  const edges = [{ from: "a", to: "mid" }, { from: "mid", to: "join" }, { from: "b", to: "join" }, { from: "join", to: "end" }];
  const L = layoutGraph(nodes, edges);
  assert.equal(L.pos.a.y, L.pos.b.y);
  assert.notEqual(L.pos.a.x, L.pos.b.x);
  assert.equal(L.pos.join.y, 2 * (128 + 56));
  for (const e of edges) assert.ok(L.pos[e.to].y > L.pos[e.from].y);
  const linear = layoutGraph(nodes, edges, { layout: "linear" });
  assert.equal(new Set(Object.values(linear.pos).map((p) => p.x)).size, 1);
  assert.equal(new Set(Object.values(linear.pos).map((p) => p.y)).size, nodes.length);
  assert.deepEqual(layoutGraph([], []).pos, {});
  const wide = layoutGraph(Array.from({ length: 12 }, (_, i) => node(`wide${i}`)), []);
  assert.ok(wide.width > 1000);
  assert.equal(new Set(Object.values(wide.pos).map((p) => p.y)).size, 1);
  for (const p of Object.values(wide.pos)) assert.ok(p.x >= 0 && p.x + p.w <= wide.width);
  const chain = layoutGraph(nodes.slice(0, 2), [{ from: "join", to: "end" }]);
  assert.equal(chain.pos.join.x, chain.pos.end.x);
  assert.ok(chain.pos.end.y > chain.pos.join.y);
});
