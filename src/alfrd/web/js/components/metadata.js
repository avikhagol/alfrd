// VIEW 3 — Metadata: the AVICA work folder attached to the selected target.
//
//   • metadata health: each step's `metadata:` files from alfrd.yaml, checked
//     against the step's status (present, parseable, required keys non-empty)
//   • avica.meta/ contents grouped by the step that writes them
//   • `avica pipe config --summary` (or avica.inp) parameters
//   • rPicard input parameters (input_template*/ *.inp) with keys superseded by
//     picard_input_template_update shown in bold
//
// Everything is read from the folder the user opened (or from `alfrd serve`).

import { $, on, esc, icon, bytes, storage, loadUi, saveUi } from "../utils/dom.js";
import { avicaIndex, codeChips, detachCode, ensureServerIndex, loadWorkdir, openAttachPicker, targetCodes, clearWorkdirCache } from "./attach.js";
import { server } from "../data/server.js";
import { metadataHealth, metaOwner } from "../data/defs.js";

const UI_FIELDS = ["open", "code", "templateFolder", "filter"];
const ui = {
  ...loadUi("metadata", { open: ["health", "meta", "config", "inputs"], code: {}, templateFolder: {}, filter: "" }),
  wd: null,
  wdKey: null,
  loading: false,
  error: null,
};
ui.open = new Set(ui.open);
if (ui.open.has("hdu")) ui.open.add("health");
const remember = () => saveUi("metadata", ui, UI_FIELDS);

const TONE = { passed: "ok", warning: "warn", failed: "fail", missing: "fail", notrun: "muted" };
const ICON = { passed: "checkCircle", warning: "alert", failed: "xCircle", missing: "xCircle", notrun: "clock" };
const LABEL = { passed: "Passed", warning: "Warning", failed: "Failed", missing: "Missing", notrun: "Not run" };

// ---------------------------------------------------------------------------
// avica.meta renderers

function list(values, limit = 40) {
  const arr = values || [];
  return `<span class="mono small">${esc(arr.slice(0, limit).join(", "))}${arr.length > limit ? ` … (+${arr.length - limit})` : ""}</span>`;
}

function renderMetaBody(m) {
  if (m.error) return `<p class="callout warn small">${icon("alert")}<span>${esc(m.error)}</span></p>`;
  if (m.format === "text") return `<pre class="code small">${esc(m.text || "")}</pre>`;
  const d = m.data;
  try {
    if (m.kind === "refants" && Array.isArray(d?.refant)) return `<p>${d.refant.map((a, i) => `<span class="code-chip">${i + 1}. ${esc(a)}</span>`).join(" ")}</p>`;
    if (m.kind === "sources" && Array.isArray(d?.NAME)) {
      return `<table class="tbl small"><thead><tr><th>FIELD_ID</th><th>NAME</th><th>SNR</th></tr></thead><tbody>${d.NAME.map((n, i) => `<tr><td class="tabular">${esc(d.FIELD_ID?.[i])}</td><td class="mono">${esc(n)}</td><td class="tabular">${Number.isFinite(d.SNR?.[i]) ? d.SNR[i].toFixed(2) : esc(d.SNR?.[i])}</td></tr>`).join("")}</tbody></table>`;
    }
    if (m.kind === "sources_snr" || m.kind === "sources_ms") {
      const roles = m.kind === "sources_snr" ? Object.values(d)[0] || {} : d;
      return `<table class="tbl small"><tbody>${Object.entries(roles).map(([role, v]) => `<tr><td class="mono">${esc(role)}</td><td>${v === null ? '<span class="muted">none</span>' : list(Array.isArray(v) ? v : [v])}</td></tr>`).join("")}</tbody></table>`;
    }
    if (d?.bands_dict) {
      const bands = Object.entries(d.bands_dict).filter(([, v]) => v && typeof v === "object" && v.spws);
      return `<table class="tbl small"><thead><tr><th>Band</th><th>spws</th><th>Ref. freqs (GHz)</th><th>nobs</th></tr></thead><tbody>${bands.map(([b, v]) => `<tr><td class="mono">${esc(b)}</td><td class="mono">${esc(v.spws.join(", "))}</td><td class="mono">${esc((v.reffreqs || []).map((f) => (f / 1e9).toFixed(3)).join(", "))}</td><td>${esc(v.nobs)}</td></tr>`).join("")}</tbody></table>
        ${d.c_target ? `<p class="small">Target position: <span class="mono">${esc(d.c_target)}</span></p>` : ""}
        ${Array.isArray(d.other_sources) ? `<p class="small">${d.other_sources.length} other sources: ${list(d.other_sources, 20)}</p>` : ""}
        ${Array.isArray(d.scanlist_seq) ? `<p class="small">${d.scanlist_seq.length} scans in sequence</p>` : ""}`;
    }
    if (m.kind === "listobs" && d?.listobs) {
      const rows = Object.values(d.listobs);
      return `<table class="tbl small"><thead><tr><th>Scan</th><th>Source id</th><th>Start</th><th>End</th><th>Rows</th></tr></thead><tbody>${rows.slice(0, 60).map((r) => `<tr><td>${esc(r.scan)}</td><td>${esc(r.source_id)}</td><td class="mono">${esc(r.start_time)}</td><td class="mono">${esc(r.end_time)}</td><td class="tabular">${esc(r.nrows)}</td></tr>`).join("")}</tbody></table>${rows.length > 60 ? `<p class="muted small">+${rows.length - 60} more scans</p>` : ""}`;
    }
    if (d && typeof d === "object" && !Array.isArray(d)) {
      return `<table class="tbl small"><tbody>${Object.entries(d).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td>${Array.isArray(v) ? list(v.flat ? v.flat() : v, 12) : v && typeof v === "object" ? `<span class="mono small">${esc(JSON.stringify(v).slice(0, 400))}</span>` : `<span class="mono">${esc(v)}</span>`}</td></tr>`).join("")}</tbody></table>`;
    }
  } catch { /* fall back to raw */ }
  return `<pre class="code small">${esc(JSON.stringify(d, null, 1).slice(0, 20000))}</pre>`;
}

function section(id, title, body, extra = "") {
  return `<details class="card sec" data-sec="${id}" ${ui.open.has(id) ? "open" : ""}><summary><span class="sec-t">${title}</span>${extra}</summary><div class="sec-b">${body}</div></details>`;
}

// ---------------------------------------------------------------------------

export function mount(el, ctx) {
  el.innerHTML = `<div class="md"><div class="card md-head" id="md-head"></div><div id="md-main"></div></div>`;
  el.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.dataset.sec) return;
    d.open ? ui.open.add(d.dataset.sec) : ui.open.delete(d.dataset.sec);
    remember();
  }, true);
  on(el, "click", "[data-attach]", () => { const t = ctx.target(); if (t) openAttachPicker(ctx, t); });
  on(el, "click", "[data-detach]", (e, b) => { const t = ctx.target(); if (t) { detachCode(ctx, t, b.dataset.detach); clearWorkdirCache(); ui.wdKey = null; } });
  on(el, "click", "[data-code-tab]", (e, b) => { const t = ctx.target(); ui.code[t.id] = b.dataset.codeTab; ui.wdKey = null; remember(); render(el, ctx); });
  on(el, "change", "#md-tpl", (e) => { const t = ctx.target(); ui.templateFolder[t.id] = e.target.value; remember(); render(el, ctx); });
  on(el, "input", "#md-filter", (e) => { ui.filter = e.target.value; remember(); renderConfig(el, ctx); });
  on(el, "click", "#md-run-summary", async () => {
    const t = ctx.target();
    try {
      await server.avicaSummary(t.project);
      delete ctx.state.avica[t.project];
      clearWorkdirCache();
      await ensureServerIndex(ctx, t.project);
      ctx.toast("avica pipe config --summary cached", "ok");
    } catch (error) {
      ctx.toast(error.message, "fail");
    }
  });
}

function activeCode(ctx, t) {
  const codes = targetCodes(ctx, t).map((c) => c.code);
  const chosen = ui.code[t.id];
  return codes.includes(chosen) ? chosen : codes[0] || null;
}

export function render(el, ctx) {
  const t = ctx.target();
  const head = $("#md-head", el);
  const main = $("#md-main", el);
  if (!t) {
    head.innerHTML = `<h2>Metadata</h2>`;
    main.innerHTML = `<div class="card empty">Select a target in the header.</div>`;
    return;
  }
  const index = avicaIndex(ctx, t.project);
  if (!index && ctx.state.mode === "server") ensureServerIndex(ctx, t.project);
  const codes = targetCodes(ctx, t);
  const code = activeCode(ctx, t);
  head.innerHTML = `
    <div class="row gap wrap"><h2>Metadata</h2><span class="chip">${esc(t.name)}</span><span class="muted small">ALFRD project <b>${esc(ctx.projectName(t.project))}</b></span><span class="grow"></span>
      <span class="small muted">AVICA project code${codes.length === 1 ? "" : "s"}:</span> <span class="codes">${codeChips(ctx, t, { editable: true })}</span></div>
    ${codes.length > 1 ? `<div class="seg md-codes" role="tablist" aria-label="Project code">${codes.map((c) => `<button data-code-tab="${esc(c.code)}" class="${c.code === code ? "on" : ""}">${esc(c.code)}</button>`).join("")}</div>` : ""}
    <p class="muted small">${index && !index.error ? `target_dir <code>${esc(index.targetDir)}/</code> · ${Object.keys(index.codes || {}).length} project code folder(s)${index.logs?.length ? ` · ${index.logs.length} file(s) in ${esc(index.logsDir || "avica.logs")}/` : ""}` : "Open the folder that contains alfrd.yaml (Import → Open project folder) to read avica.meta."}</p>`;

  if (!code) {
    main.innerHTML = `<div class="card"><p class="callout info">${icon("info")}<span>No AVICA work folder is attached to <b>${esc(t.name)}</b>. Attach <code>${esc(index?.targetDir || "<target_dir>")}/&lt;CODE&gt;</code> (e.g. BV019, RDV41) to read its <code>wd/avica.meta/</code> and rPicard inputs.</span></p><button class="btn primary" data-attach>${icon("link")} Attach folder…</button></div>${configSection(ctx, t, index)}`;
    ctx.setFooterRight("No work folder attached");
    return;
  }
  const key = `${t.id}|${code}|${index ? Object.keys(index.codes || {}).length : 0}`;
  if (ui.wdKey !== key) {
    ui.wdKey = key;
    ui.wd = null;
    ui.error = null;
    ui.loading = true;
    loadWorkdir(ctx, t, code)
      .then((wd) => { if (ui.wdKey === key) { ui.wd = wd; ui.loading = false; render(el, ctx); } })
      .catch((error) => { if (ui.wdKey === key) { ui.error = error.message; ui.loading = false; render(el, ctx); } });
  }
  if (ui.loading) { main.innerHTML = `<div class="card empty">Reading ${esc(code)}/wd…</div>`; return; }
  if (ui.error || !ui.wd) {
    main.innerHTML = `<div class="card"><p class="callout warn">${icon("alert")}<span>${esc(ui.error || `${code}/wd is not in the opened folder. Re-open the folder that contains alfrd.yaml.`)}</span></p></div>${configSection(ctx, t, index)}`;
    return;
  }
  const wd = ui.wd;
  const defs = ctx.state.trees?.[t.project]?.defs;
  main.innerHTML = `${healthSection(ctx, t, wd, defs)}${metaSection(wd, defs, t)}${configSection(ctx, t, index)}${inputsSection(ctx, t, wd)}`;
  ctx.setFooterRight(`${esc(wd.wd)} · ${wd.meta.length} avica.meta file(s)`);
}

function healthSection(ctx, t, wd, defs) {
  const order = ctx.steps();
  const rows = defs ? metadataHealth(defs, wd, t.name, t.steps, order) : [];
  const count = (st) => rows.filter((r) => r.status === st).length;
  const body = rows.length ? `<table class="tbl health"><thead><tr><th>Step</th><th>Step status</th><th>Metadata file (alfrd.yaml)</th><th>Health</th><th>Evidence</th></tr></thead><tbody>
    ${rows.map((r) => r.entries.map((e, i) => `<tr class="st-${e.status} ${i === 0 ? "grp-start" : ""}">
      ${i === 0 ? `<td rowspan="${r.entries.length}" class="mono"><b>${esc(r.step)}</b></td><td rowspan="${r.entries.length}"><span class="muted small">${esc(r.stepStatus)}</span></td>` : ""}
      <td><b>${esc(e.label)}</b><div class="mono small muted">${esc(e.pattern)}${e.require.length ? ` · requires ${esc(e.require.join(", "))}` : ""}</div></td>
      <td><span class="badge tone-${TONE[e.status]}">${icon(ICON[e.status])}${LABEL[e.status]}</span></td>
      <td class="small">${e.files.map((f) => `<div><span class="mono">${esc(f.name)}</span>${f.note ? ` <span class="${f.status === "failed" ? "fail-t" : "muted"}">${esc(f.note)}</span>` : ""}</div>`).join("")}
        ${e.missingBands?.length ? `<div class="muted">no file for band ${esc(e.missingBands.join(", "))}</div>` : ""}
        ${!e.files.length ? `<span class="muted">${e.status === "notrun" ? "step not run yet" : e.status === "failed" ? "step failed; no file written" : "step ran but wrote no file"}</span>` : ""}</td></tr>`).join("")).join("")}
    </tbody></table>`
    : `<p class="muted small">No step declares <code>metadata:</code> in alfrd.yaml${defs?.template ? "" : " (and no template is set)"}. Add a list under a step, e.g. <code>metadata: [{file: fitsfiles_used.avica, label: FITS files used, require: [filepath]}]</code>.</p>`;
  return section("health", "Metadata health", body, rows.length ? `<span class="grow"></span><span class="count tone-ok"><i></i>${count("passed")}</span><span class="count tone-warn"><i></i>${count("warning")}</span><span class="count tone-fail"><i></i>${count("failed") + count("missing")}</span><span class="count">${count("notrun")} not run</span>` : "");
}

function metaSection(wd, defs, t) {
  const groups = new Map();
  wd.meta.forEach((m) => {
    const owner = defs ? metaOwner(defs, m.name, t.name) : null;
    const key = owner?.step || "(not declared in alfrd.yaml)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ ...m, ownerLabel: owner?.label });
  });
  const body = wd.meta.length ? [...groups.entries()].map(([g, list]) => `<h5>${esc(g)}</h5>
    ${list.map((m) => `<details class="meta-file"><summary><span class="mono">${esc(m.name)}</span><span class="muted small">${esc(m.ownerLabel || "")}</span>${m.band ? `<span class="code-chip">${esc(m.band)}</span>` : ""}${m.legacy ? `<span class="badge tone-warn" title="read from a folder renamed by field_aliases">${esc(m.legacy)}</span>` : ""}<span class="grow"></span><span class="muted small">${bytes(m.size || 0)}</span></summary>${renderMetaBody(m)}</details>`).join("")}`).join("")
    : `<p class="muted">No files in ${esc(wd.wd)}/avica.meta.</p>`;
  return section("meta", `avica.meta <span class="mono small muted">${esc(wd.wd)}/avica.meta</span>`, body, `<span class="grow"></span><span class="muted small">${wd.meta.length} file(s) · bands ${esc((wd.bands || []).join(", ") || "—")}</span>`);
}

function configRows(index) {
  if (index?.summary?.rows?.length) return { rows: index.summary.rows, from: index.summary.file || "avica pipe config --summary" };
  const rows = Object.entries(index?.values || {}).map(([k, v]) => {
    const [step, param] = k.includes(".") ? k.split(/\.(.+)/) : ["other", k];
    return { step, parameter: param, source: index?.sources?.[k] || "avica.inp", value: v };
  }).sort((a, b) => (a.step === "other") - (b.step === "other") || a.step.localeCompare(b.step));
  return { rows, from: "avica.inp" };
}

function configTableBody(rows) {
  const q = ui.filter.trim().toLowerCase();
  const shown = rows.filter((r) => !q || `${r.step} ${r.parameter} ${r.value} ${r.source}`.toLowerCase().includes(q));
  let lastStep = null;
  return shown.map((r) => {
    const first = r.step !== lastStep;
    lastStep = r.step;
    const origin = String(r.source || "").split("/")[0];
    return `<tr class="${first ? "grp-start" : ""}"><td class="mono">${first ? esc(r.step) : ""}</td><td class="mono">${esc(r.parameter)}</td><td><span class="src src-${esc(origin)}">${esc(r.source)}</span></td><td class="mono">${esc(typeof r.value === "object" && r.value !== null ? JSON.stringify(r.value) : r.value)}</td></tr>`;
  }).join("") || '<tr><td colspan="4" class="muted">No parameters.</td></tr>';
}

function configSection(ctx, t, index) {
  const { rows, from } = configRows(index);
  const serverBtn = ctx.state.mode === "server" && server.session?.mutations_enabled ? `<button class="btn sm" id="md-run-summary">${icon("sync")} Run avica pipe config --summary</button>` : "";
  const body = `
    ${index?.summary ? "" : `<p class="callout info small">${icon("info")}<span>Showing <code>avica.inp</code> only. For the full resolved configuration run <code>alfrd avica summary</code> next to <code>alfrd.yaml</code> (it runs <code>avica pipe config --summary</code> and writes <code>avica.summary.json</code>), or save <code>avica pipe config --summary &gt; avica.summary.txt</code>, then re-open the folder.</span></p>`}
    <div class="row gap"><label class="search grow">${icon("search")}<input id="md-filter" type="search" placeholder="Filter parameters…" value="${esc(ui.filter)}"></label>${serverBtn}</div>
    <table class="tbl small cfg"><thead><tr><th>Step</th><th>Parameter</th><th>Source</th><th>Value</th></tr></thead><tbody id="md-cfg-body">${configTableBody(rows)}</tbody></table>`;
  return section("config", `AVICA configuration <span class="mono small muted">${esc(from)}</span>`, body, `<span class="grow"></span><span class="muted small">${rows.length} parameter(s)</span>`);
}

function renderConfig(el, ctx) {
  const t = ctx.target();
  const tbody = el.querySelector("#md-cfg-body");
  if (!t || !tbody) return;
  tbody.innerHTML = configTableBody(configRows(avicaIndex(ctx, t.project)).rows);
}

function inputsSection(ctx, t, wd) {
  const folders = [...new Set(wd.templates.map((x) => x.folder))];
  const choice = folders.includes(ui.templateFolder[t.id]) ? ui.templateFolder[t.id] : folders.find((f) => /wd_[A-Z]/.test(f)) || folders[0];
  const upd = wd.template_update || {};
  const updFiles = upd.files || {};
  const files = wd.templates.filter((x) => x.folder === choice);
  const note = upd.folder
    ? `Keys in <b>bold</b> are superseded by <code>picard_input_template_update = ${esc(upd.folder)}</code> (${Object.keys(updFiles).length} file(s)); rpicard merges them into these inputs.`
    : upd.detected
      ? `<code>picard_input_template_update</code> is not set. Found <code>${esc(upd.detected)}/</code> next to alfrd.yaml — set it in avica.inp to apply its overrides.`
      : "No <code>picard_input_template_update</code> configured.";
  const body = !folders.length ? `<p class="muted">No input_template*/ *.inp files in ${esc(wd.wd)}.</p>` : `
    <p class="small muted">${note}</p>
    <label class="field"><span>Template folder</span><select id="md-tpl" class="input mono sm">${folders.map((f) => `<option value="${esc(f)}" ${f === choice ? "selected" : ""}>${esc(f.replace(`${wd.wd}/`, ""))}</option>`).join("")}</select></label>
    ${files.map((f) => {
      const updated = f.updated || updFiles[f.file] || {};
      const keys = Object.keys(f.values);
      const added = Object.keys(updated).filter((k) => !(k in f.values));
      const nSup = keys.filter((k) => k in updated).length;
      return `<details class="inp-file"><summary><span class="mono">${esc(f.file)}</span><span class="muted small">${keys.length} keys</span>${nSup ? `<span class="badge tone-warn">${nSup} superseded</span>` : ""}${added.length ? `<span class="badge tone-run">+${added.length} from update</span>` : ""}</summary>
        <table class="tbl small inp"><tbody>
        ${keys.map((k) => (k in updated
          ? `<tr class="sup"><td class="mono"><b>${esc(k)}</b></td><td class="mono"><b>${esc(updated[k])}</b> <s class="muted">${esc(f.values[k])}</s></td></tr>`
          : `<tr><td class="mono">${esc(k)}</td><td class="mono">${esc(f.values[k])}</td></tr>`)).join("")}
        ${added.map((k) => `<tr class="sup"><td class="mono"><b>${esc(k)}</b></td><td class="mono"><b>${esc(updated[k])}</b> <span class="muted small">added by update</span></td></tr>`).join("")}
        </tbody></table></details>`;
    }).join("")}`;
  return section("inputs", `Input parameters <span class="muted small">rPicard *.inp</span>`, body, `<span class="grow"></span><span class="muted small">${folders.length} template folder(s)</span>`);
}
