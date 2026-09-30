// Resource usage of plan commands (alfrd.runtime.usage): live sparklines of one
// command (tailing its .usage.jsonl like a log) and a per-step table across
// targets. Loaded with import() on first use.

import { $, esc, icon, bytes, short, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

const median = (xs) => {
  const v = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
};

/** Parse .usage.jsonl text (bad lines skipped). */
export function parseUsage(text) {
  return String(text || "").split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter((r) => r && Number.isFinite(r.t));
}

/** Inline SVG sparkline of `key` over `rows` (t on x). */
export function spark(rows, key, { w = 320, h = 56, cls = "" } = {}) {
  if (rows.length < 2) return `<svg class="spark ${cls}" width="${w}" height="${h}" role="img" aria-label="not enough samples yet"></svg>`;
  const t0 = rows[0].t;
  const t1 = rows[rows.length - 1].t || 1;
  const max = Math.max(...rows.map((r) => r[key] || 0)) || 1;
  const pts = rows.map((r) => `${(((r.t - t0) / (t1 - t0 || 1)) * (w - 2) + 1).toFixed(1)},${(h - 2 - ((r[key] || 0) / max) * (h - 4)).toFixed(1)}`).join(" ");
  return `<svg class="spark ${cls}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(key)} over time, max ${esc(max)}"><polyline fill="none" stroke="currentColor" stroke-width="1.5" points="${pts}"/></svg>`;
}

function summaryHtml(u) {
  if (!u) return '<p class="muted small">No summary yet (written when the command ends).</p>';
  const cell = (label, value) => `<div><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
  return `<div class="kpis small">${cell(u.peak_mem_kind === "pss" ? "peak memory (PSS)" : "peak memory", bytes(u.peak_mem || 0))}${cell("CPU", short(u.cpu_s || 0))}${cell("avg cores", u.avg_cores ?? "—")}${cell("read", bytes(u.read_bytes || 0))}${cell("written", bytes(u.write_bytes || 0))}${cell("wall", short(u.wall_s || 0))}${u.max_rss ? cell("largest process (max RSS)", bytes(u.max_rss)) : ""}</div>
    ${u.workdir_end ? `<p class="muted small">Work dir ${esc(u.workdir)}: ${bytes(u.workdir_start?.bytes || 0)} → ${bytes(u.workdir_end.bytes)}${u.workdir_end.partial ? " (partial)" : ""}</p>` : ""}
    ${u.limited ? '<p class="muted small">Not Linux: wall time only.</p>' : ""}${u.cgroup_memory_peak ? `<p class="muted small">systemd-run unit memory.peak: ${bytes(u.cgroup_memory_peak)} (whole runner)</p>` : ""}`;
}

/** One command: live sparklines (cores, memory) while it runs, then its summary. */
export function openUsage(ctx, project, unit, refresh = null) {
  loadCss("css/lazy.css");
  let timer = null;
  const close = ctx.modal(`<header class="modal-h"><h2>${icon("graph")} Resource usage · <span class="mono">${esc(unit.target || "*")} · ${esc((unit.steps || []).join(", "))}</span></h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b usage" id="us-b"><p class="muted small">Loading…</p></div>`, (root) => {
    const draw = async () => {
      const box = $("#us-b", root);
      if (!box) { clearTimeout(timer); return; }
      let rows = [];
      const u = (refresh?.() || unit);
      if (u.status === "running" || u.usage?.samples) {
        try { rows = parseUsage(await server.projectFile(project, unit.usage_file)); } catch { /* not written yet */ }
      }
      const last = rows[rows.length - 1];
      box.innerHTML = `${summaryHtml(u.usage)}
        <h5>Cores <span class="muted small">${last ? `now ${esc(last.cores)}` : ""}</span></h5>${spark(rows, "cores", { cls: "s-cpu" })}
        <h5>Memory <span class="muted small">${last ? `now ${bytes(last.mem)}` : ""}</span></h5>${spark(rows, "mem", { cls: "s-mem" })}
        <p class="muted small">${rows.length} sample(s) · every ${esc(u.usage?.interval_s ?? "5")} s · summed over every process of the command, MPI ranks on this machine included (ranks on other nodes are not seen).</p>`;
      if (u.status === "running") timer = setTimeout(draw, 3000);
    };
    draw();
  }, "wide");
  return close;
}

/** Per step: median / peak across the plan's finished commands. */
export function usageTable(units) {
  const by = new Map();
  units.filter((u) => u.usage && u.status !== "running").forEach((u) => (u.steps || []).slice(0, 1).forEach((st) => {
    if (!by.has(st)) by.set(st, []);
    by.get(st).push(u.usage);
  }));
  return [...by.entries()].map(([step, xs]) => ({
    step, n: xs.length,
    wall: median(xs.map((x) => x.wall_s)), cores: median(xs.map((x) => x.avg_cores)),
    mem: median(xs.map((x) => x.peak_mem)), peak: Math.max(...xs.map((x) => x.peak_mem || 0)),
    cpu: xs.reduce((a, x) => a + (x.cpu_s || 0), 0),
  }));
}

export function openUsageTable(ctx, project, status) {
  loadCss("css/lazy.css");
  const rows = usageTable(status?.units || []);
  ctx.modal(`<header class="modal-h"><h2>${icon("graph")} Resource usage per step · <span class="mono">${esc(status?.plan?.id || "")}</span></h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b">${rows.length ? `<table class="tbl small"><thead><tr><th>Step</th><th>Commands</th><th>Median wall</th><th>Median cores</th><th>Median peak memory</th><th>Highest peak</th><th>CPU total</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="mono">${esc(r.step)}</td><td class="tabular">${r.n}</td><td class="tabular">${esc(short(r.wall || 0))}</td><td class="tabular">${esc(r.cores ?? "—")}</td><td class="tabular">${bytes(r.mem || 0)}</td><td class="tabular">${bytes(r.peak || 0)}</td><td class="tabular">${esc(short(r.cpu))}</td></tr>`).join("")}</tbody></table>`
      : '<p class="muted small">No finished command has a usage summary yet (plans started with ALFRD 0.2.0.8 record one).</p>'}
      <p class="muted small">Per command, summed over all its processes on this machine (MPI ranks included); open a command's usage from its row for the curves.</p></div>`, null, "wide");
}
