// A small graph canvas with the Workflow view's renderer (node cards, status classes, edge paths,
// arrow markers and CSS from canvas.js / studio.css): the chat task graph (read-only) and the
// New-project canvas (editable draft). Pan by dragging, Ctrl/Cmd+wheel or +/- to zoom, 0 fits.
// Keyboard: arrows move between nodes (pan without selection), Enter opens one; editable: N adds, C connects, Delete removes.
import { $, $$, esc, icon } from "../utils/dom.js";
import { edgePath, NODE_H } from "./canvas.js";
import { layoutGraph } from "../data/chat.js";
import { createGraphGroups, renderGraphGroups, createGraphUpdates } from "./graph_groups.js";
import { statusIcon, STATUS_ICON_STATES } from "./status_icons.js";

const TONES = ["ok", "fail", "run", "idle", "ready"];
let seq = 0;

/** Mount on `host`. opts: {label, editable, empty, onSelect(id), onOpen(id), onConnect(from, to) → problem|null,
 *  onDisconnect(from, to), onDelete(id), onAdd(), groups: true|group[]}. With groups:true, use
 *  model.groups (or node.request for legacy models). Models may carry a monotonic numeric rev.
 *  Returns {update(model, selected), select(id), fit(), focus()}. */
export function mountGraph(host, opts = {}) {
  const id = `gv${++seq}`;
  let groups = opts.groups ? createGraphGroups() : null;
  const view = { zoom: 1, x: 0, y: 0, fitted: false, model: { nodes: [], edges: [] }, selected: null, L: null, link: null };
  host.innerHTML = `<div class="gv">
    <div class="gv-tools" role="toolbar" aria-label="${esc(opts.label || "Graph")} controls">
      ${opts.editable ? `<button type="button" class="btn sm" data-gv="add" aria-keyshortcuts="N">${icon("plus")} Add step</button><button type="button" class="btn sm" data-gv="connect" aria-keyshortcuts="C">${icon("link")} Connect</button>` : ""}
      <span class="grow"></span>
      <button type="button" class="icon-btn sm" data-gv="out" aria-label="Zoom out">${icon("minus")}</button><span class="gv-zoom muted small tabular" aria-live="off">100%</span>
      <button type="button" class="icon-btn sm" data-gv="in" aria-label="Zoom in">${icon("plus")}</button>
      <button type="button" class="icon-btn sm" data-gv="fit" aria-label="Fit to view" aria-keyshortcuts="0">${icon("fit")}</button>
    </div>
    ${opts.editable ? "" : `<details><summary>Status legend and keyboard help</summary><div style="display:flex;flex-wrap:wrap;gap:12px">${STATUS_ICON_STATES.map((s) => statusIcon(s)).join("")}</div><p>Tab to a node; arrows move between nodes. Enter opens it. Escape clears selection; arrows then pan. 0 fits.</p></details>`}
    <div class="canvas gv-canvas" tabindex="0" role="group" aria-roledescription="graph" aria-label="${esc(opts.label || "Graph")}. Tab to a node; arrows pan without a selection or move between selected nodes. Enter opens it; Escape clears selection${opts.editable ? "; N adds a step, C connects, Delete removes" : ""}." >
      <div class="world"></div><p class="gv-empty muted" hidden></p>
    </div><p class="sr-only" aria-live="polite" data-gv-live></p></div>`;
  const canvas = $(".gv-canvas", host), world = $(".world", host), live = $("[data-gv-live]", host);
  const say = (text) => { live.textContent = text; };
  let drag = null, hovering = false;
  const apply = () => {
    world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`;
    $(".gv-zoom", host).textContent = `${Math.round(view.zoom * 100)}%`;
  };
  const zoomAt = (z, cx, cy) => {
    view.fitted = true; // User navigation also cancels a pending initial fit.
    const nz = Math.min(2, Math.max(0.2, z));
    view.x = cx - ((cx - view.x) * nz) / view.zoom; view.y = cy - ((cy - view.y) * nz) / view.zoom; view.zoom = nz; apply();
  };
  const zoomBy = (f) => { const r = canvas.getBoundingClientRect(); zoomAt(view.zoom * f, r.width / 2, r.height / 2); };
  const fit = () => {
    const r = canvas.getBoundingClientRect();
    if (!r.width || !view.L || !view.model.nodes.length) return;
    const z = Math.min(1, Math.max(0.2, Math.min((r.width - 32) / view.L.width, (r.height - 32) / view.L.height)));
    view.zoom = z; view.x = (r.width - view.L.width * z) / 2; view.y = 16; apply(); view.fitted = true;
  };
  const worldAt = (cx, cy) => { const r = canvas.getBoundingClientRect(); return { x: (cx - r.left - view.x) / view.zoom, y: (cy - r.top - view.y) / view.zoom }; };
  const order = () => view.L?.order || [];
  const nodeEl = (key) => key && world.querySelector(`.node[data-key="${CSS.escape(key)}"]`);

  function draw() {
    const { nodes, edges } = view.model;
    const L = (view.L = groups
      ? groups.layout(nodes, edges, Array.isArray(opts.groups) ? opts.groups : view.model.groups, { h: NODE_H })
      : layoutGraph(nodes, edges, { h: NODE_H }));
    $(".gv-empty", host).hidden = Boolean(nodes.length);
    $(".gv-empty", host).textContent = opts.empty || "Nothing to show yet.";
    world.style.width = `${L.width}px`; world.style.height = `${L.height}px`;
    const paths = edges.filter((e) => L.pos[e.from] && L.pos[e.to]).map((e) => {
      const d = edgePath(L.pos[e.from], L.pos[e.to]), tone = TONES.includes(e.tone) ? e.tone : "idle";
      const hl = view.selected === e.from || view.selected === e.to ? " e-hl" : "";
      return `<path d="${d}" class="edge e-${tone}${hl}${e.kind === "delegated" ? " e-deleg" : e.kind === "planned" ? " e-planned" : ""}" marker-end="url(#${id}-ar-${tone})"><title>${esc(e.from)} → ${esc(e.to)}</title></path>${opts.editable
        ? `<path d="${d}" class="edge-hit" data-from="${esc(e.from)}" data-to="${esc(e.to)}"><title>${esc(e.to)} waits for ${esc(e.from)} (click to disconnect)</title></path>` : ""}`;
    }).join("");
    world.innerHTML = `<svg class="edges" width="${L.width}" height="${L.height}" aria-hidden="true"><defs>${TONES.map((k) => `<marker id="${id}-ar-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ar ar-${k}"/></marker>`).join("")}</defs>${groups ? renderGraphGroups(L.lanes) : ""}${paths}<path class="edge e-link" data-gv-link d=""/></svg>`
      + nodes.map((n) => {
        const p = L.pos[n.id], sel = view.selected === n.id;
        return `<div class="node st-${esc(n.st)}${sel ? " sel" : ""}${n.orch ? " gv-orch" : ""}" data-key="${esc(n.id)}" role="button" tabindex="${sel || (!view.selected && n.id === order()[0]) ? 0 : -1}" style="left:${p.x}px;top:${p.y}px;width:${p.w}px"
          aria-label="${n.badge ? `${esc(n.badge)}: ` : ""}${esc(n.title)}. ${esc(n.label)}. ${esc(n.sub || "")}. ${esc(n.hint || "")}" aria-pressed="${sel}" title="${esc(n.id)} · ${esc(n.sub || "")} · ${esc(n.hint || "")}">
          ${opts.editable ? '<span class="node-port" data-port title="Drag onto another step: that step then waits for this one"></span>' : ""}
          ${n.st === "running" ? '<div class="node-prog"><i style="width:60%"></i></div>' : ""}
          <div class="node-h"><span class="node-ic">${icon(n.nodeIcon || "pipeline")}</span><div class="node-t"><b class="mono">${esc(n.id)}</b><span>${esc(n.title)}</span></div>${n.badge ? `<span class="badge tone-muted gv-badge"><span>${esc(n.badge)}</span></span>` : ""}</div>
          <div class="node-b"><span class="small muted grow gv-sub">${esc(n.sub || "")}</span><span class="badge tone-${esc(n.tone)}">${n.status ? statusIcon(n.status, n.label) : `${icon(n.icon || "info")}<span>${esc(n.label)}</span>`}</span></div>
        </div>`;
      }).join("");
    if (!view.fitted) requestAnimationFrame(() => { if (!view.fitted) fit(); });
    apply();
  }

  function select(key, { focus = false, announce = false } = {}) {
    if (!key || !view.model.nodes.some((n) => n.id === key)) return;
    view.selected = key;
    $$(".node", world).forEach((n) => { const on = n.dataset.key === key; n.classList.toggle("sel", on); n.tabIndex = on ? 0 : -1; n.setAttribute("aria-pressed", String(on)); });
    $$(".edge:not(.e-link)", world).forEach((p) => p.classList.toggle("e-hl", /→/.test(p.textContent) && p.textContent.split(" → ").includes(key)));
    if (focus) nodeEl(key)?.focus({ preventScroll: true });
    if (announce) { const n = view.model.nodes.find((x) => x.id === key); say(`${n.title}: ${n.label}`); }
    opts.onSelect?.(key);
  }

  function connectTo(from, to) {
    view.link = null; canvas.classList.remove("gv-linking");
    const problem = opts.onConnect?.(from, to);
    say(problem ? `Not connected: ${problem}` : `${to} now waits for ${from}.`);
    updates.flush();
  }

  host.addEventListener("click", (e) => {
    const b = e.target.closest("[data-gv]");
    if (!b) return;
    const a = b.dataset.gv;
    if (a === "in") zoomBy(1.15); else if (a === "out") zoomBy(1 / 1.15); else if (a === "fit") fit();
    else if (a === "add") opts.onAdd?.();
    else if (a === "connect") startConnect();
  });
  function startConnect() {
    if (!view.selected) { say("Select the step that runs first, then press C."); return; }
    view.link = { from: view.selected, keyboard: true };
    canvas.classList.add("gv-linking");
    say(`Connecting from ${view.selected}: pick the step that waits for it with the arrows, Enter to connect, Escape to cancel.`);
    nodeEl(view.selected)?.focus();
  }

  canvas.addEventListener("pointerenter", (e) => { if (e.pointerType !== "touch") hovering = true; });
  canvas.addEventListener("pointerleave", () => { hovering = false; updates.flush(); });
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const node = e.target.closest(".node");
    if (opts.editable && node && e.target.closest("[data-port]")) drag = { kind: "link", from: node.dataset.key, over: null };
    else if (node) drag = { kind: "node", key: node.dataset.key, sx: e.clientX, sy: e.clientY, moved: false };
    else drag = { kind: "pan", sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y, moved: false, edge: e.target.closest(".edge-hit") };
    canvas.setPointerCapture?.(e.pointerId);
    if (drag.kind === "pan") canvas.classList.add("panning");
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if (drag.kind === "pan") {
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      view.fitted = true;
      view.x = drag.ox + dx; view.y = drag.oy + dy; apply();
    } else if (drag.kind === "link") {
      const a = view.L.pos[drag.from], p = worldAt(e.clientX, e.clientY);
      $("[data-gv-link]", world)?.setAttribute("d", `M${a.x + a.w / 2},${a.y + NODE_H} L${p.x},${p.y}`);
      const over = document.elementFromPoint(e.clientX, e.clientY)?.closest?.(".node");
      drag.over = over && over.dataset.key !== drag.from ? over.dataset.key : null;
      $$(".node", world).forEach((n) => n.classList.toggle("link-cand", n.dataset.key === drag.over));
    } else if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  });
  const end = () => {
    const d = drag; drag = null;
    canvas.classList.remove("panning");
    if (!d) return;
    if (d.kind === "link") {
      $("[data-gv-link]", world)?.setAttribute("d", "");
      $$(".node.link-cand", world).forEach((n) => n.classList.remove("link-cand"));
      if (d.over) connectTo(d.from, d.over);
    } else if (d.kind === "node" && !d.moved) {
      if (view.link) connectTo(view.link.from, d.key);
      else { select(d.key); opts.onOpen?.(d.key); }
    } else if (d.kind === "pan" && !d.moved && d.edge && opts.editable) opts.onDisconnect?.(d.edge.dataset.from, d.edge.dataset.to);
    updates.flush();
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("lostpointercapture", end);
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) { const r = canvas.getBoundingClientRect(); zoomAt(view.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), e.clientX - r.left, e.clientY - r.top); }
    else { view.fitted = true; view.x -= e.deltaX; view.y -= e.deltaY; apply(); }
  }, { passive: false });
  canvas.addEventListener("focusin", (e) => {
    const key = e.target.closest(".node")?.dataset.key;
    if (!opts.editable && key && key !== view.selected && !view.link) select(key, { announce: true });
  });
  canvas.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const list = order(), i = list.indexOf(view.selected);
    const move = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (move) {
      e.preventDefault();
      if (!view.selected && !opts.editable) {
        view.fitted = true;
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") view.x -= move * 40;
        else view.y -= move * 40;
        apply(); return;
      }
      const next = list[i < 0 ? 0 : Math.max(0, Math.min(list.length - 1, i + move))];
      if (view.link) { view.selected = next; $$(".node", world).forEach((n) => n.classList.toggle("link-cand", n.dataset.key === next && next !== view.link.from)); nodeEl(next)?.focus(); say(`Connect to ${next}?`); }
      else select(next, { focus: true, announce: true });
    } else if (e.key === "Escape" && view.link) {
      e.preventDefault(); e.stopPropagation();
      const from = view.link.from; view.link = null; canvas.classList.remove("gv-linking");
      $$(".node.link-cand", world).forEach((n) => n.classList.remove("link-cand"));
      select(from, { focus: true }); say("Connecting cancelled."); updates.flush();
    } else if (e.key === "Escape" && !opts.editable && view.selected) {
      e.preventDefault(); e.stopPropagation();
      view.selected = null;
      $$(".node", world).forEach((n) => { n.classList.remove("sel"); n.tabIndex = n.dataset.key === order()[0] ? 0 : -1; n.setAttribute("aria-pressed", "false"); });
      $$(".edge", world).forEach((p) => p.classList.remove("e-hl"));
      canvas.focus(); say("Selection cleared. Arrow keys pan."); opts.onSelect?.(null);
    } else if ((e.key === "Enter" || e.key === " ") && view.selected) {
      e.preventDefault();
      if (view.link) { const { from } = view.link; $$(".node.link-cand", world).forEach((n) => n.classList.remove("link-cand")); connectTo(from, view.selected); }
      else opts.onOpen?.(view.selected);
    } else if (e.key === "+" || e.key === "=") zoomBy(1.15);
    else if (e.key === "-") zoomBy(1 / 1.15);
    else if (e.key === "0") { e.preventDefault(); fit(); }
    else if (opts.editable && (e.key === "n" || e.key === "N")) { e.preventDefault(); opts.onAdd?.(); }
    else if (opts.editable && (e.key === "c" || e.key === "C")) { e.preventDefault(); startConnect(); }
    else if (opts.editable && (e.key === "Delete" || e.key === "Backspace") && view.selected) { e.preventDefault(); opts.onDelete?.(view.selected); }
  });

  const updates = createGraphUpdates((model, selected) => {
    const key = JSON.stringify([model, selected]);
    if (key === view.key) return;
    view.key = key;
    const hadFocus = host.contains(document.activeElement) && document.activeElement.closest?.(".node");
    if (view.model.scope !== model.scope) {
      if (groups) groups = createGraphGroups();
      view.fitted = false;
    }
    view.model = model;
    view.selected = model.nodes.some((n) => n.id === selected) ? selected : null;
    draw();
    if (hadFocus) (nodeEl(view.selected) || canvas).focus({ preventScroll: true });
  }, () => hovering || Boolean(drag) || Boolean(view.link));

  return {
    update: (model, selected = view.selected) => updates.update(model, selected),
    select: (key) => select(key, { focus: true }),
    fit,
    focus: () => canvas.focus(),
  };
}
