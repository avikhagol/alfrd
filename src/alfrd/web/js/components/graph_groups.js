// Request lanes are local to one graph mount. Fixed-width columns can grow downwards
// without shifting their neighbours; keep empty/temporarily absent slots until unmount.
import { layoutGraph } from "../data/chat.js";
import { esc } from "../utils/dom.js";

export function createGraphGroups() {
  const slots = new Map();
  return {
    layout(nodes, edges, groups = [], { w = 260, h = 128, gapX = 36, gapY = 56 } = {}) {
      const headers = new Map(groups.map((g) => [g.id, g]));
      const membership = new Map();
      for (const g of groups) for (const id of g.nodes || []) if (!membership.has(id)) membership.set(id, g.id);
      const buckets = new Map(groups.map((g) => [g.id, []]));
      for (const n of nodes) {
        const id = membership.get(n.id) ?? n.request ?? "unscoped";
        if (!buckets.has(id)) buckets.set(id, []);
        buckets.get(id).push(n);
      }
      for (const id of buckets.keys()) if (!slots.has(id)) slots.set(id, slots.size);
      const pad = 16, header = 56, laneWidth = w + pad * 2;
      const pos = {}, order = [], lanes = [];
      let height = header + h + pad;
      for (const [id, slot] of slots) {
        const members = buckets.get(id) || [];
        const local = layoutGraph(members, edges, { w, h, gapX, gapY });
        const x = slot * (laneWidth + gapX);
        local.order.forEach((key, i) => {
          pos[key] = { x: x + pad, y: header + i * (h + gapY), w };
          order.push(key);
        });
        const laneHeight = header + Math.max(h, members.length * (h + gapY) - gapY) + pad;
        height = Math.max(height, laneHeight);
        const g = headers.get(id);
        lanes.push({ id, title: g?.title || (id === "unscoped" ? "Unscoped" : id),
          count: members.length, x, y: 0, width: laneWidth, height: laneHeight });
      }
      return { pos, order, lanes, width: Math.max(laneWidth, slots.size * (laneWidth + gapX) - gapX), height };
    },
  };
}

/** SVG-only styling: no dependency on chat.css; labels never intercept canvas input. */
export function renderGraphGroups(lanes) {
  return lanes.map((lane) => `<g data-graph-group="${esc(lane.id)}" pointer-events="none">
    <rect x="${lane.x}" y="${lane.y}" width="${lane.width}" height="${lane.height}" rx="10" fill="var(--surface, transparent)" stroke="var(--border, currentColor)" stroke-opacity="0.5"/>
    <text x="${lane.x + 16}" y="24" fill="var(--text, currentColor)" font-size="13" font-family="inherit"><title>${esc(lane.title)}</title>${esc(String(lane.title).slice(0, 32))}</text>
    <text x="${lane.x + 16}" y="43" fill="var(--muted, currentColor)" font-size="11" font-family="inherit">${lane.count} node${lane.count === 1 ? "" : "s"}</text>
  </g>`).join("");
}

/** Per-mount delivery gate. Advance the revision on receipt, including queued updates,
 * so a late response cannot replace a newer pending graph. Rev-less drafts remain supported
 * until a versioned stream starts; equal revisions may carry a new selection/filter. */
export function createGraphUpdates(apply, interacting) {
  let revision = null, pending = null, stream;
  const flush = () => {
    if (!pending || interacting()) return;
    const next = pending;
    pending = null;
    apply(...next);
  };
  return {
    update(model, selected) {
      if (model.stream !== stream) { stream = model.stream; revision = null; pending = null; }
      const rev = Number.isFinite(model.rev) ? model.rev : null;
      if (model.unchanged || (revision !== null && (rev === null || rev < revision))) return;
      if (rev !== null) revision = rev;
      pending = [model, selected];
      flush();
    },
    flush,
  };
}
