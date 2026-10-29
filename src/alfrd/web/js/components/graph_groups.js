// Request lanes are local to one graph mount. Keep empty/temporarily absent slots
// until unmount; spread-out lanes grow horizontally to fit parallel tasks.
import { layoutGraph } from "../data/chat.js";
import { esc } from "../utils/dom.js";

export function createGraphGroups() {
  const slots = new Map();
  return {
    layout(nodes, edges, groups = [], { w = 260, h = 128, gapX = 36, gapY = 56, layout = "spread" } = {}) {
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
      const pad = 16, header = 56;
      // Compute depth across the whole graph, including dependencies between requests.
      const global = layoutGraph(nodes, edges, { w, h, gapX, gapY });
      const pos = {}, order = [], lanes = [];
      let height = header + h + pad, x = 0;
      for (const id of slots.keys()) {
        const members = buckets.get(id) || [];
        const local = layoutGraph(members, edges, { w, h, gapX, gapY, layout: "linear" });
        const rows = new Map();
        for (const key of local.order) {
          const y = layout === "linear" ? local.pos[key].y : global.pos[key].y;
          if (!rows.has(y)) rows.set(y, []);
          rows.get(y).push(key);
        }
        const widest = Math.max(1, ...[...rows.values()].map((row) => row.length));
        const contentWidth = widest * w + (widest - 1) * gapX, laneWidth = contentWidth + pad * 2;
        for (const [y, row] of rows) row.forEach((key, i) => {
          const left = (contentWidth - (row.length * w + (row.length - 1) * gapX)) / 2;
          pos[key] = { x: x + pad + left + i * (w + gapX), y: header + y, w };
        });
        order.push(...local.order);
        const laneHeight = header + Math.max(h, ...[...rows.keys()].map((y) => y + h)) + pad;
        height = Math.max(height, laneHeight);
        const g = headers.get(id);
        lanes.push({ id, title: g?.title || (id === "unscoped" ? "Unscoped" : id),
          count: members.length, x, y: 0, width: laneWidth, height: laneHeight });
        x += laneWidth + gapX;
      }
      return { pos, order, lanes, width: Math.max(w + pad * 2, x - gapX), height };
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
