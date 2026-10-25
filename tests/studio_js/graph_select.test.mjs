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
  const elements = new Map([".gv-canvas", ".world", "[data-gv-live]", ".gv-empty", ".gv-zoom", "[data-gv-menu]", "[data-gv-box]"].map((s) => [s, element()]));
  const host = element(); host.querySelector = (s) => elements.get(s);
  let focused = null;
  elements.get(".world").querySelector = (s) => ({ setAttribute() {}, focus() { focused = s; } });
  const graph = mountGraph(host, opts);
  return { graph, host, canvas: elements.get(".gv-canvas"), world: elements.get(".world"), live: elements.get("[data-gv-live]"),
    menu: elements.get("[data-gv-menu]"), box: elements.get("[data-gv-box]"), focused: () => focused,
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

test("G6 keyboard: focus selects, arrows clamp at the ends, Enter opens, Escape clears and announces", () => {
  const selected = [], opened = [];
  const { graph, canvas, live, tick } = mounted({ onSelect: (id) => selected.push(id), onOpen: (id) => opened.push(id) });
  graph.update(model(["a", "b", "c"])); tick();
  const key = (k) => canvas.fire("keydown", { key: k });
  focusNode(canvas, "a");
  key("ArrowLeft"); key("ArrowUp");
  assert.equal(selected.at(-1), "a");
  key("ArrowRight"); key("ArrowDown"); key("ArrowRight"); key("ArrowRight");
  assert.equal(selected.at(-1), "c");
  key("Enter"); assert.deepEqual(opened, ["c"]);
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

test("G5 Shift+F10 opens the task context menu on the focused node", async () => {
  const r = mounted(); r.graph.update(model(["a", "b"])); r.tick();
  focusNode(r.canvas, "a");
  r.canvas.fire("keydown", { key: "F10", shiftKey: true });
  await Promise.resolve();
  assert.equal(r.menu.hidden, false); assert.match(r.menu.innerHTML, /a<\/p>/);
  r.menu.fire("keydown", { key: "Escape" });
  assert.equal(r.menu.hidden, true); assert.match(r.focused(), /data-key="a"/);
});

test("G5 right-click targets the clicked node; ContextMenu and overflow share the menu", async () => {
  const r = mounted(); r.graph.update(model(["a", "b"])); r.tick(); focusNode(r.canvas, "a");
  r.canvas.fire("contextmenu", { target: { closest: () => ({ dataset: { key: "b" } }) } });
  await Promise.resolve(); assert.match(r.menu.innerHTML, /b<\/p>/);
  r.menu.fire("keydown", { key: "Escape" }); assert.match(r.focused(), /data-key="b"/);
  r.canvas.fire("keydown", { key: "ContextMenu" });
  await Promise.resolve(); assert.equal(r.menu.hidden, false);
  r.menu.fire("keydown", { key: "Escape" });
  r.host.fire("click", { target: { closest: (s) => s === "[data-gv-menu-for]" ? { dataset: { gvMenuFor: "a" } } : null } });
  await Promise.resolve(); assert.match(r.menu.innerHTML, /a<\/p>/);
  assert.match(r.world.innerHTML, /aria-haspopup="menu"/);
  r.menu.fire("keydown", { key: "Escape" }); assert.match(r.focused(), /data-key="a"/);
});
test("G5 open menu defers graph updates and Escape flushes only the newest revision", async () => {
  const r = mounted(); r.graph.update(model(["a"])); r.tick(); focusNode(r.canvas, "a");
  r.canvas.fire("keydown", { key: "F10", shiftKey: true });
  await Promise.resolve();
  const before = r.world.innerHTML, transform = r.world.style.transform;
  r.graph.update({ ...model(["a", "discarded"]), rev: 2 });
  r.graph.update({ ...model(["a", "latest"]), rev: 4 });
  r.graph.update({ ...model(["a", "stale"]), rev: 3 });
  r.canvas.fire("pointerleave");
  assert.equal(r.world.innerHTML, before);
  assert.equal(r.menu.hidden, false);
  let stopped = false;
  r.menu.fire("keydown", { key: "Escape", stopPropagation() { stopped = true; } });
  assert.equal(stopped, true, "Escape must not dismiss the containing modal");
  assert.equal(r.menu.hidden, true);
  assert.match(r.world.innerHTML, /data-key="latest"/);
  assert.doesNotMatch(r.world.innerHTML, /discarded|stale/);
  assert.equal(r.world.style.transform, transform);
  assert.match(r.focused(), /data-key="a"/);
});

test("G5 overflow Enter and Space preserve native button activation", () => {
  const opened = [], r = mounted({ onOpen: (row) => opened.push(row) });
  r.graph.update(model(["a"])); r.tick(); focusNode(r.canvas, "a");
  for (const key of ["Enter", " "]) {
    r.canvas.fire("keydown", { key, target: { closest: (s) => s === "[data-gv-menu-for]" ? { dataset: { gvMenuFor: "a" } } : null },
      preventDefault() { assert.fail("overflow native activation must be preserved"); } });
  }
  assert.deepEqual(opened, []);
});

const nodeTarget = (key) => ({ closest: (s) => s === ".node" ? { dataset: { key } } : null });
const setMode = (r, mode) => r.host.fire("click", { target: { closest: (s) => s === "[data-gv-mode]" ? { dataset: { gvMode: mode } } : null } });
const clickNode = (r, key, shiftKey = false) => {
  r.canvas.fire("pointerdown", { target: nodeTarget(key), shiftKey });
  r.canvas.fire("pointerup");
};
const transform = (r) => r.world.style.transform.match(/-?[\d.]+/g).map(Number);
const toClient = (r, x, y) => {
  const [tx, ty, z] = transform(r), rect = r.canvas.getBoundingClientRect();
  return { clientX: rect.left + tx + x * z, clientY: rect.top + ty + y * z };
};
const rectangle = (r, a, b, extra = {}) => {
  r.canvas.fire("pointerdown", { ...toClient(r, ...a), ...extra });
  r.canvas.fire("pointermove", toClient(r, ...b));
  r.canvas.fire("pointerup", toClient(r, ...b));
};
const positions = (r) => [...r.world.innerHTML.matchAll(/data-key="([^"]+)"[^>]+style="left:([\d.]+)px;top:([\d.]+)px;width:([\d.]+)px"/g)]
  .map((m) => ({ key: m[1], x: +m[2], y: +m[3], w: +m[4] }));

test("G7 rectangle selection selects nodes at 20%, 100% and 200% zoom", () => {
  for (const grouped of [false, true]) for (const zoom of [0.2, 1, 2]) for (const reverse of [false, true]) {
    const opened = [], r = mounted({ groups: grouped, onOpen: (id) => opened.push(id) });
    r.canvas.getBoundingClientRect = () => ({ width: 1000, height: 600, left: 37, top: 91 });
    r.graph.update(model(["a", "b", "c"])); r.tick();
    for (let i = 0; i < 50; i++) r.canvas.fire("wheel", { ctrlKey: true, deltaY: zoom < 1 ? 1 : -1, clientX: 37, clientY: 91 });
    if (zoom === 1) r.canvas.fire("keydown", { key: "0" });
    assert.ok(Math.abs(transform(r)[2] - zoom) < 1e-9);
    r.canvas.fire("wheel", { deltaX: 123, deltaY: -57 });
    const before = r.world.style.transform, p = positions(r)[0];
    assert.ok(p);
    // A tiny rectangle touches only the bottom-right corner: test node dimensions, not centres.
    let a = [p.x + p.w - 1, p.y + 127], b = [p.x + p.w + 30, p.y + 160];
    if (reverse) [a, b] = [b, a];
    rectangle(r, a, b);
    assert.deepEqual(r.graph.selection(), [p.key], `groups=${grouped}, zoom=${zoom}, reverse=${reverse}`);
    assert.equal(r.box.hidden, true);
    assert.equal(r.world.style.transform, before);
    assert.deepEqual(opened, []);
    rectangle(r, [-100, -100], [-50, -50]); assert.deepEqual(r.graph.selection(), []);
  }
});

test("G7 Shift+click toggles membership, Shift+rectangle adds and never opens tasks", () => {
  const opened = [], r = mounted({ onOpen: (id) => opened.push(id) });
  r.graph.update(model(["a", "b", "c"])); r.tick();
  clickNode(r, "a", true); clickNode(r, "b", true);
  assert.deepEqual(r.graph.selection(), ["a", "b"]);
  clickNode(r, "a", true); assert.deepEqual(r.graph.selection(), ["b"]);
  const p = positions(r)[0]; rectangle(r, [p.x, p.y], [p.x + 20, p.y + 20], { shiftKey: true });
  assert.deepEqual(r.graph.selection(), ["b", "a"]);
  assert.deepEqual(opened, []);
  r.canvas.fire("keydown", { key: "Escape" }); assert.deepEqual(r.graph.selection(), []);
  clickNode(r, "c"); assert.deepEqual(opened, ["c"]);
});

test("G7 Pan control and held Space pan over nodes without activation; keyup and focus loss restore mode", () => {
  const opened = [], r = mounted({ onOpen: (id) => opened.push(id) });
  r.graph.update(model(["a"])); r.tick();
  setMode(r, "pan"); assert.equal(r.canvas.style.cursor, "grab");
  clickNode(r, "a"); assert.deepEqual(r.graph.selection(), []);
  setMode(r, "select"); assert.equal(r.canvas.style.cursor, "crosshair");
  focusNode(r.canvas, "a");
  r.canvas.fire("keydown", { key: " " });
  const before = r.world.style.transform;
  r.canvas.fire("pointerdown", { target: nodeTarget("a") });
  r.canvas.fire("pointermove", { clientX: 50, clientY: 25 }); r.canvas.fire("pointerup");
  assert.notEqual(r.world.style.transform, before); assert.deepEqual(opened, []);
  r.canvas.fire("keyup", { key: " " }); assert.equal(r.canvas.style.cursor, "crosshair");
  r.canvas.fire("keydown", { key: " " }); r.host.fire("focusout");
  assert.equal(r.canvas.style.cursor, "crosshair");
  r.canvas.fire("keydown", { key: "Enter" }); assert.deepEqual(opened, ["a"]);
});

for (const finish of ["pointercancel", "lostpointercapture", "Escape", "focusout"]) {
  test(`G7 ${finish} cancels and releases capture without activation, link or selection commit`, () => {
    for (const kind of ["node", "box", "link"]) {
      const opened = [], connected = [], r = mounted({ editable: kind === "link", onOpen: (id) => opened.push(id), onConnect: (...args) => connected.push(args) });
      r.graph.update(model(["a", "b"])); r.tick();
      let captured = null, releases = 0;
      r.canvas.setPointerCapture = (id) => { captured = id; };
      r.canvas.hasPointerCapture = (id) => captured === id;
      r.canvas.releasePointerCapture = (id) => { assert.equal(captured, id); captured = null; releases++; r.canvas.fire("lostpointercapture", { pointerId: id }); };
      const target = kind === "node" ? nodeTarget("a") : kind === "link" ? { closest: (s) => s === ".node" ? { dataset: { key: "a" } } : s === "[data-port]" ? {} : null } : undefined;
      r.canvas.fire("pointerdown", { pointerId: 7, ...(target ? { target } : {}) });
      if (kind === "box") r.canvas.fire("pointermove", { pointerId: 7, clientX: 900, clientY: 550 });
      if (kind === "link") {
        document.elementFromPoint = () => nodeTarget("b");
        r.canvas.fire("pointermove", { pointerId: 7, clientX: 1, clientY: 1 });
      }
      r.canvas.fire("pointerup", { pointerId: 99 }); assert.equal(captured, 7);
      if (finish === "Escape") r.canvas.fire("keydown", { key: "Escape" });
      else if (finish === "focusout") r.host.fire("focusout");
      else r.canvas.fire(finish, { pointerId: 7 });
      assert.equal(releases, 1); assert.equal(captured, null);
      r.canvas.fire("pointerup", { pointerId: 7 });
      assert.deepEqual(opened, []); assert.deepEqual(connected, []); assert.deepEqual(r.graph.selection(), []);
      assert.equal(r.box.hidden, true);
    }
  });
}

test("G7 rectangle defers updates, prunes removed ids with announcement and preserves viewport", () => {
  const r = mounted(); r.graph.update(model(["a", "b", "c"])); r.tick();
  clickNode(r, "a", true); clickNode(r, "b", true);
  r.canvas.fire("pointerdown", toClient(r, -10, -10));
  r.canvas.fire("pointermove", toClient(r, 1500, 1500));
  const html = r.world.innerHTML, viewport = r.world.style.transform;
  r.graph.update({ ...model(["a", "c"]), rev: 3 });
  r.graph.update({ ...model(["stale"]), rev: 2 });
  assert.equal(r.world.innerHTML, html);
  r.canvas.fire("pointerup");
  assert.deepEqual(r.graph.selection(), ["a", "c"]);
  assert.match(r.live.textContent, /1 removed task.*dropped from selection/);
  assert.equal(r.world.style.transform, viewport);
  assert.doesNotMatch(r.world.innerHTML, /data-key="b"|stale/);
  r.graph.update({ ...model([]), rev: 4 }); assert.deepEqual(r.graph.selection(), []);
  assert.match(r.live.textContent, /2 removed tasks/);
});
test("G7 selection paints every member, retains one tab stop and exposes mode button state", () => {
  const r = mounted(); r.graph.update(model(["a", "b"])); r.tick();
  const make = (dataset) => {
    const classes = new Set(), attrs = new Map();
    return { dataset, classes, attrs, classList: { toggle(k, on) { if (on) classes.add(k); else classes.delete(k); } }, setAttribute(k, v) { attrs.set(k, v); } };
  };
  const nodes = [make({ key: "a" }), make({ key: "b" })];
  const modes = [make({ gvMode: "select" }), make({ gvMode: "pan" })];
  r.world.querySelectorAll = (s) => s === ".node" ? nodes : [];
  r.host.querySelectorAll = (s) => s === "[data-gv-mode]" ? modes : [];
  clickNode(r, "a", true); clickNode(r, "b", true);
  for (const node of nodes) { assert.ok(node.classes.has("sel")); assert.equal(node.attrs.get("aria-pressed"), "true"); }
  assert.deepEqual(nodes.map((n) => n.tabIndex), [-1, 0]);
  r.graph.update({ ...model(["a", "b"]), rev: 2 });
  assert.deepEqual(r.graph.selection(), ["a", "b"]);
  assert.equal([...r.world.innerHTML.matchAll(/aria-pressed="true"/g)].length, 2);
  assert.equal([...r.world.innerHTML.matchAll(/tabindex="0"/g)].length, 1);
  setMode(r, "pan");
  assert.deepEqual(modes.map((m) => m.attrs.get("aria-pressed")), ["false", "true"]);
  r.canvas.fire("keydown", { key: " " }); r.canvas.fire("keyup", { key: " " });
  assert.equal(r.canvas.style.cursor, "grab", "temporary Pan restores the chosen mode");
  setMode(r, "select");
  assert.deepEqual(modes.map((m) => m.attrs.get("aria-pressed")), ["true", "false"]);
  r.graph.update({ ...model(["a", "b"]), rev: 1, stream: "another-session" });
  assert.deepEqual(r.graph.selection(), [], "session changes cannot reuse an old selection");
});

test("G7 cancellation retains the existing selection and ignores other pointers and navigation", () => {
  const r = mounted(); r.graph.update(model(["a", "b"])); r.tick(); clickNode(r, "a", true);
  const viewport = r.world.style.transform;
  r.canvas.fire("pointerdown", { pointerId: 7, ...toClient(r, -10, -10) });
  r.canvas.fire("pointerdown", { pointerId: 8 });
  r.canvas.fire("pointermove", { pointerId: 8, clientX: 500, clientY: 500 });
  assert.notEqual(r.box.hidden, false);
  r.canvas.fire("pointermove", { pointerId: 7, ...toClient(r, 1500, 1500) });
  assert.equal(r.box.hidden, false);
  for (const key of ["ArrowRight", "+", "0", "Enter"]) r.canvas.fire("keydown", { key });
  r.canvas.fire("wheel", { deltaX: 100, deltaY: 100 });
  assert.equal(r.world.style.transform, viewport);
  r.canvas.fire("pointercancel", { pointerId: 8 }); assert.equal(r.box.hidden, false);
  setMode(r, "pan"); assert.equal(r.box.hidden, true);
  assert.deepEqual(r.graph.selection(), ["a"]);
  r.canvas.fire("pointerup", { pointerId: 7 }); assert.deepEqual(r.graph.selection(), ["a"]);
});

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
