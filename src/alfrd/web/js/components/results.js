// VIEW 4 — Results & Analytics: KPIs, step-duration radar, calibration
// progress, per-step outcome bars and the per-target progress ladder.

import { $, on, esc, icon, hms, short, when, elapsed, loadUi, saveUi } from "../utils/dom.js";
import { STEP_STATUS } from "../data/model.js";
import { readFiles, buildBundle } from "../data/importers.js";
import { resultStats, attemptsOf } from "../data/results_stats.js";

const saved = loadUi("results", { history: false, targetOnly: false });
const ui = { open: new Set(), table: false, history: saved.history, targetOnly: saved.targetOnly };

function stats(ctx) {
  const t = ctx.target();
  const scoped = ui.targetOnly && t ? [t] : ctx.scopedTargets();
  const results = ctx.state.trees?.[t?.project || ctx.state.selectedProject]?.defs?.results || {};
  return resultStats(scoped, ctx.steps(), { history: ui.history, rollup: (x) => ctx.rollup(x), results });
}

function radar(st) {
  const keys = st.steps;
  const n = keys.length;
  if (n < 3) return `<p class="muted">Radar needs at least three steps.</p>`;
  const avg = keys.map((k) => (st.per[k].n ? st.per[k].sum / st.per[k].n : 0));
  const max = Math.max(...avg, 1);
  const R = 120;
  const cx = 170;
  const cy = 150;
  const pt = (i, r) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  };
  // Square-root radius so a 25-minute rPicard run doesn't flatten 20-second steps.
  const scale = (v) => Math.sqrt(v / max) * R;
  const rings = [0.25, 0.5, 0.75, 1].map((f) => `<polygon class="rd-ring" points="${keys.map((_, i) => pt(i, R * f).join(",")).join(" ")}"/>`).join("");
  const spokes = keys.map((_, i) => `<line class="rd-spoke" x1="${cx}" y1="${cy}" x2="${pt(i, R)[0]}" y2="${pt(i, R)[1]}"/>`).join("");
  const poly = keys.map((_, i) => pt(i, scale(avg[i])).join(",")).join(" ");
  const dots = keys.map((k, i) => {
    const [x, y] = pt(i, scale(avg[i]));
    return `<g class="rd-hit" tabindex="0"><circle cx="${x}" cy="${y}" r="12" class="rd-target"/><circle cx="${x}" cy="${y}" r="4" class="rd-dot"/><title>${esc(k)}: mean ${short(avg[i]) || "n/a"} over ${st.per[k].n} completed run(s)</title></g>`;
  }).join("");
  const labels = keys.map((k, i) => {
    const [x, y] = pt(i, R + 18);
    const anchor = Math.abs(x - cx) < 8 ? "middle" : x > cx ? "start" : "end";
    return `<text x="${x}" y="${y + 4}" text-anchor="${anchor}" class="rd-label">${esc(st.labels?.[k] || k)}<tspan class="rd-val" x="${x}" dy="13">${esc(short(avg[i]) || "—")}</tspan></text>`;
  }).join("");
  return `<svg viewBox="0 0 340 310" class="radar" role="img" aria-label="Mean completed duration per pipeline step">${rings}${spokes}<polygon class="rd-area" points="${poly}"/>${dots}${labels}</svg>`;
}

function stacked(parts, total) {
  const segs = parts.filter((p) => p.value > 0);
  return `<div class="stack" role="img" aria-label="${esc(parts.map((p) => `${p.label} ${p.value}`).join(", "))}">${segs.map((p) => `<span class="seg-${p.tone}" style="flex:${p.value}" title="${esc(p.label)}: ${p.value} (${Math.round((p.value / Math.max(1, total)) * 100)}%)"></span>`).join("") || '<span class="seg-muted" style="flex:1"></span>'}</div>
    <ul class="legend">${parts.map((p) => `<li><i class="sw seg-${p.tone}"></i>${esc(p.label)}: <b class="tabular">${p.value}</b></li>`).join("")}</ul>`;
}

/** Collection artifacts (e.g. rPicard diagnostics_*) of the project in view. */
function collectionsOf(ctx) {
  const t = ctx.target();
  const project = t?.project || (ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : null);
  const tree = project && ctx.state.trees?.[project];
  const list = (tree?.defs?.artifacts || []).filter((a) => a.kind === "collection");
  return { project, tree, list };
}

let diagnostics = null; // components/diagnostics.js, loaded when a collection card is opened

export function mount(el, ctx) {
  el.innerHTML = `<div class="rs" id="rs"></div><div class="rs" id="rs-coll"></div>`;
  on(el, "click", "[data-coll-open]", async (e, b) => {
    try {
      diagnostics ||= import("./diagnostics.js");
      (await diagnostics).openCollection($("#rs-coll", el), ctx, b.dataset.collOpen);
    } catch (error) {
      diagnostics = null;
      ctx.toast(`Diagnostics: ${error.message}`, "fail");
    }
  });
  on(el, "click", "[data-ladder]", (e, row) => {
    const k = row.dataset.ladder;
    ui.open.has(k) ? ui.open.delete(k) : ui.open.add(k);
    render(el, ctx);
  });
  on(el, "click", "#rs-table", () => { ui.table = !ui.table; render(el, ctx); });
  on(el, "click", "[data-hist]", (e, b) => { ui.history = b.dataset.hist === "all"; saveUi("results", ui, ["history", "targetOnly"]); render(el, ctx); });
  on(el, "click", "[data-scope]", (e, b) => { ui.targetOnly = b.dataset.scope === "target"; saveUi("results", ui, ["history", "targetOnly"]); render(el, ctx); });
  on(el, "change", "#rs-csv", async (e) => {
    const read = await readFiles(e.target.files);
    const t = ctx.target();
    const bundle = buildBundle(read, { source: "import", projectHint: t?.project });
    ctx.state.targets = [...ctx.state.targets.filter((x) => !bundle.targets.some((b) => b.id === x.id)), ...bundle.targets];
    bundle.aliases.forEach((a) => !ctx.state.aliases.some((x) => x.from === a.from) && ctx.state.aliases.push(a));
    if (bundle.targets[0]) ctx.state.selectedTarget = bundle.targets[0].id;
    bundle.messages.forEach((m) => ctx.log(m.level === "error" ? "error" : "info", m.text, "import"));
    ctx.state.source = "imported";
    ctx.persist();
    ctx.toast(`Ladder loaded from ${read.length} file(s)`, bundle.messages.some((m) => m.level === "error") ? "warn" : "ok");
    ctx.update();
  });
}

export function render(el, ctx) {
  const st = stats(ctx);
  st.labels = Object.fromEntries(ctx.state.workflow.steps.map((s) => [s.key, s.short || s.key]));
  const t = ctx.target();
  const H = ui.history;
  const one = ui.targetOnly && t;
  const scopeLabel = one ? `target ${esc(t.name)}` : `${st.targets.length} targets`;
  const nCal = st.calibratedTargets.length;
  const nFail = st.failedTargets.length;
  const calStep = esc(st.calStep || "—");
  const outcome = st.steps.map((k) => {
    const b = st.per[k];
    const total = b.completed + b.failed + b.warning + b.running + b.pending;
    return `<tr><td class="mono small">${esc(k)}</td><td class="w100">${stacked([
      { label: "Completed", value: b.completed, tone: "ok" },
      { label: "Partial", value: b.warning, tone: "warn" },
      { label: "Failed", value: b.failed, tone: "fail" },
      { label: "Running", value: b.running, tone: "run" },
      { label: "Not run", value: b.pending, tone: "muted" },
    ], total).replace('<ul class="legend">', '<ul class="legend" hidden>')}</td><td class="tabular small">${b.completed}/${total}</td>${H ? `<td class="tabular small">${b.retries || ""}</td>` : ""}</tr>`;
  }).join("");

  $("#rs", el).innerHTML = `
    <div class="card"><div class="row gap wrap"><h2>Results &amp; Analytics</h2><span class="chip">${st.targets.length} target${st.targets.length === 1 ? "" : "s"} · ${one ? esc(t.name) : ctx.state.selectedProject === "all" ? "all projects" : esc(ctx.projectName(ctx.state.selectedProject))}</span><span class="grow"></span>
      <div class="seg" role="group" aria-label="Targets counted"><button data-scope="all" class="${ui.targetOnly ? "" : "on"}" title="Every target of the selected project">All targets</button><button data-scope="target" class="${ui.targetOnly ? "on" : ""}" ${t ? "" : "disabled"} title="${t ? `Only ${esc(t.name)} (selected in the header)` : "Select a target in the header"}">Target only</button></div>
      <div class="seg" role="group" aria-label="Attempts counted"><button data-hist="recent" class="${H ? "" : "on"}" title="Most recent attempt of each step">Most Recent</button><button data-hist="all" class="${H ? "on" : ""}" title="Every attempt in the result CSVs, including earlier failures">Full history</button></div></div>
      <div class="kpis">
        <div><span>Total elapsed time</span><b class="tabular">${elapsed(st.total)}</b><small>${H ? "all attempts" : "most recent attempt per step"} · ${scopeLabel}</small></div>
        <div><span>Average step duration</span><b class="tabular">${st.avg == null ? "—" : elapsed(st.avg)}</b><small>${st.attempts} attempt(s) counted</small></div>
        <div><span>Calibrated targets</span><b class="tabular">${nCal}</b><small title="${esc(st.calibrators.join(", "))}">${H ? "any" : "latest"} ${calStep} run completed · + ${st.calibrators.length} calibrator${st.calibrators.length === 1 ? "" : "s"}</small></div>
        <div><span>Failed items</span><b class="tabular ${st.failedItems ? "fail-t" : ""}">${st.failedItems}</b><small>${H ? "failed or partial attempts" : "targets whose latest attempt failed"}</small></div>
      </div></div>
    <div class="rs-grid">
      <div class="card"><div class="row between"><h4>Step duration radar${one ? ` — <span class="mono">${esc(t.name)}</span>` : ""}</h4><span class="muted small">mean of ${H ? "all completed attempts" : "latest completed runs"} · √ scale</span></div>${radar(st)}</div>
      <div class="card">
        <h4>Calibration progress <span class="muted small">(unique targets · ${H ? "any" : "latest"} ${calStep} run)</span></h4>
        ${stacked([
          { label: "Calibrated targets", value: nCal, tone: "ok" },
          { label: "Failed", value: nFail, tone: "fail" },
        ], Math.max(1, st.targets.length))}
        <p class="small muted">Calibrated calibrators: <b class="tabular">${st.calibrators.length}</b>${st.calibrators.length ? ` <span class="mono" title="${esc(st.calibrators.join(", "))}">(${esc(st.calibrators.slice(0, 12).join(", "))}${st.calibrators.length > 12 ? ", …" : ""})</span>` : ""}</p>
        <div class="row between"><h4>Outcome per step</h4><button class="link-btn small" id="rs-table">${ui.table ? "Hide" : "Show"} data table</button></div>
        <table class="tbl outcome"><thead><tr><th>Step</th><th>Completed · Partial · Failed · Running · Not run</th><th>Done</th>${H ? "<th>Retries</th>" : ""}</tr></thead><tbody>${outcome}</tbody></table>
        ${ui.table ? `<table class="tbl small"><thead><tr><th>Step</th><th>Mean runtime</th><th>Completed</th><th>Partial</th><th>Failed</th><th>Running</th><th>Not run</th></tr></thead><tbody>${st.steps.map((k) => { const b = st.per[k]; return `<tr><td class="mono">${esc(k)}</td><td class="tabular">${b.n ? short(b.sum / b.n) : "—"}</td><td>${b.completed}</td><td>${b.warning}</td><td>${b.failed}</td><td>${b.running}</td><td>${b.pending}</td></tr>`; }).join("")}</tbody></table>` : ""}
      </div>
    </div>
    <div class="card">
      <div class="row gap wrap"><h4>Progress ladder${t ? ` — <span class="mono">${esc(t.name)}</span>` : ""}</h4><span class="muted small">${H ? "every attempt" : "most recent attempt per step"}</span><span class="muted small mono">${esc(t?.source?.file || "")}</span><span class="grow"></span>
        <label class="btn sm">${icon("upload")} Load reductions/&lt;target&gt;_result.csv<input type="file" id="rs-csv" accept=".csv,.tsv" multiple hidden></label></div>
      ${t ? ladder(ctx, t) : '<p class="muted">Select a target in the header.</p>'}
    </div>`;
  renderCollections(el, ctx);
  ctx.setFooterRight(`${st.targets.length} targets · ${st.attempts} attempts`);
}

/** Collapsed cards for kind: collection artifacts; nothing is fetched until one is opened. */
function renderCollections(el, ctx) {
  const host = $("#rs-coll", el);
  const { project, tree, list } = collectionsOf(ctx);
  const key = `${project}|${list.map((c) => c.name).join(",")}`;
  if (host.dataset.key === key) {
    diagnostics?.then((m) => m.refresh?.(host, ctx)).catch(() => {});
    return; // an open card keeps its state across renders
  }
  host.dataset.key = key;
  if (!list.length) { host.innerHTML = ""; return; }
  const served = tree?.provider === "server";
  host.innerHTML = list.map((c) => `<div class="card coll-card" data-coll="${esc(c.name)}">
    <div class="row gap wrap"><h4>${icon("graph")} ${esc(c.description || c.name)}</h4><span class="muted small mono">${esc(c.path_pattern)}</span><span class="grow"></span>
    ${served ? `<button class="btn sm" data-coll-open="${esc(c.name)}">${icon("folder")} Show</button>` : `<span class="muted small">Needs <code>alfrd serve</code> (files are read on the server when opened).</span>`}</div>
    <div class="coll-body" hidden></div></div>`).join("");
}

function ladder(ctx, t) {
  const steps = ctx.state.workflow.steps;
  const H = ui.history;
  let runtime = 0;
  let hasRuntime = false;
  const rows = steps.map((s, i) => {
    const st = t.steps?.[s.key];
    const status = st?.status || "pending";
    const m = STEP_STATUS[status] || STEP_STATUS.pending;
    // Most Recent: only the latest attempt of the step; Full history: all of them.
    const attempts = st ? attemptsOf(st, H) : [];
    attempts.forEach((a) => { if (Number.isFinite(a.duration)) { runtime += a.duration; hasRuntime = true; } });
    const dur = H ? attempts.reduce((n, a) => n + (Number.isFinite(a.duration) ? a.duration : 0), 0) || null : st?.duration;
    const ok = attempts.reduce((n, a) => n + (a.success_count || 0), 0);
    const bad = attempts.reduce((n, a) => n + (a.failed_count || 0), 0);
    const open = ui.open.has(`${t.id}:${s.key}`);
    const retries = H && attempts.length > 1 ? ` <small class="muted">(${attempts.length - 1} retr${attempts.length === 2 ? "y" : "ies"})</small>` : "";
    const main = `<tr class="st-${status} ${attempts.length ? "clickable" : ""}" ${attempts.length ? `data-ladder="${esc(t.id)}:${esc(s.key)}" aria-expanded="${open}"` : ""}>
      <td>${attempts.length ? `<span class="caret ${open ? "open" : ""}">${icon("caret")}</span>` : ""}${i + 1}</td>
      <td class="mono"><b>${esc(s.key)}</b>${st?.alias ? ` <small class="muted">(from ${esc(st.alias)})</small>` : ""}</td>
      <td><span class="badge tone-${m.tone}">${icon(m.icon)}${esc(m.label)}</span></td>
      <td class="tabular">${H ? attempts.length : st ? `#${st.attempt || 1}` : 0}${retries}</td>
      <td class="tabular">${esc(short(dur) || "—")}</td>
      <td class="tabular"><span class="ok-t">${ok}</span> / <span class="fail-t">${bad}</span></td>
      <td class="mono small">${esc(when(st?.started))}</td>
      <td class="small">${esc(st?.note || "")}</td></tr>`;
    const detail = open ? attempts.map((a) => `<tr class="sub st-${a.status}"><td></td><td class="mono small">attempt ${a.attempt}</td><td>${esc(STEP_STATUS[a.status]?.label || a.status)}</td><td class="small mono">${esc(JSON.stringify(a.items || []))}</td><td class="tabular">${esc(short(a.duration) || "—")}</td><td class="tabular">${a.success_count ?? "—"} / ${a.failed_count ?? "—"}</td><td class="mono small">${esc(when(a.started))} → ${esc(when(a.finished))}</td><td class="small">${esc(a.note || "")}${a.detail && typeof a.detail === "object" ? `<div class="mono small muted">${esc(Object.entries(a.detail).map(([k, v]) => `${k}: ${v}`).join(" · "))}</div>` : ""}</td></tr>`).join("") : "";
    return main + detail;
  }).join("");
  const r = ctx.rollup(t);
  return `<table class="tbl ladder-tbl"><thead><tr><th>#</th><th>Step</th><th>Status</th><th>${H ? "Attempts" : "Attempt"}</th><th>Runtime</th><th>Pass / Fail</th><th>Started</th><th>Detail</th></tr></thead><tbody>${rows}</tbody>
    <tfoot><tr><td></td><td><b>Total</b></td><td>${r.done}/${r.total} completed</td><td></td><td class="tabular"><b>${hms(hasRuntime ? runtime : null)}</b></td><td colspan="3"></td></tr></tfoot></table>`;
}
