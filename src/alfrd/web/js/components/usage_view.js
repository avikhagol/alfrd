// On-demand resource and token usage for plan commands.
import { $, esc, icon, bytes, short, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

const median = (xs) => {
  const v = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
};

export function parseUsage(text) {
  return String(text || "").split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter((r) => r && Number.isFinite(r.t));
}

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

const tokenKeys = ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "total_tokens"];
const number = (value) => Number.isFinite(value) ? value.toLocaleString() : "—";
const cost = (value) => Number.isFinite(value) ? `$${value.toFixed(4)}` : "—";

// Missing metrics remain unknown.
export function agentTotals(units) {
  const sum = (values) => {
    const known = values.filter(Number.isFinite);
    return known.length ? known.reduce((a, b) => a + b, 0) : null;
  };
  return { ...Object.fromEntries(tokenKeys.map((key) => [key, sum(units.map((u) => u.agent_usage?.[key]))])),
    total_cost_usd: sum(units.map((u) => u.total_cost_usd)) };
}

export function agentUsageHtml(units, planTotals = null) {
  const agents = units.filter((u) => u.handoff || u.agent || Object.hasOwn(u, "agent_usage"));
  if (!agents.length) return "";
  const cells = (usage, totalCost) => tokenKeys.map((key) => `<td class="tabular">${esc(number(usage?.[key]))}</td>`).join("") + `<td class="tabular">${esc(cost(totalCost))}</td>`;
  const totals = planTotals || agentTotals(agents);
  return `<h4>Agent token usage</h4><table class="tbl small"><thead><tr><th>Turn</th><th>Input</th><th>Output</th><th>Cache read</th><th>Cache creation</th><th>Total tokens</th><th>Cost (USD)</th></tr></thead><tbody>${agents.map((u) => `<tr><td class="mono">${esc(u.id || u.agent || "Turn")}</td>${cells(u.agent_usage, u.total_cost_usd)}</tr>`).join("")}</tbody><tfoot><tr><th>${planTotals || agents.length > 1 ? "Plan" : "Turn"} total (reported)</th>${cells(totals, totals.total_cost_usd)}</tr></tfoot></table><p class="muted small">— means unavailable. Totals sum reported values only.</p>`;
}

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
      box.innerHTML = `${agentUsageHtml([u])}${summaryHtml(u.usage)}
        <h5>Cores <span class="muted small">${last ? `now ${esc(last.cores)}` : ""}</span></h5>${spark(rows, "cores", { cls: "s-cpu" })}
        <h5>Memory <span class="muted small">${last ? `now ${bytes(last.mem)}` : ""}</span></h5>${spark(rows, "mem", { cls: "s-mem" })}
        <p class="muted small">${rows.length} sample(s) · every ${esc(u.usage?.interval_s ?? "5")} s · summed over every process of the command, MPI ranks on this machine included (ranks on other nodes are not seen).</p>`;
      if (u.status === "running") timer = setTimeout(draw, 3000);
    };
    draw();
  }, "wide");
  return close;
}

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
    <div class="modal-b">${agentUsageHtml(status?.units || [], status?.agent_totals)}${rows.length ? `<table class="tbl small"><thead><tr><th>Step</th><th>Commands</th><th>Median wall</th><th>Median cores</th><th>Median peak memory</th><th>Highest peak</th><th>CPU total</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="mono">${esc(r.step)}</td><td class="tabular">${r.n}</td><td class="tabular">${esc(short(r.wall || 0))}</td><td class="tabular">${esc(r.cores ?? "—")}</td><td class="tabular">${bytes(r.mem || 0)}</td><td class="tabular">${bytes(r.peak || 0)}</td><td class="tabular">${esc(short(r.cpu))}</td></tr>`).join("")}</tbody></table>`
      : '<p class="muted small">No finished command has a usage summary yet (plans started with ALFRD 0.2.0.8 record one).</p>'}
      <p class="muted small">Per command, summed over all its processes on this machine (MPI ranks included); open a command's usage from its row for the curves.</p></div>`, null, "wide");
}
