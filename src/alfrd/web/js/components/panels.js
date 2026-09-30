// Metadata panels declared by the template (views.metadata; alfrd.layout_generic):
// file_status, files, json_fields, csv_table, text, image, plus client panels
// drawn by a renderer the caller passes (AVICA's config / inputs). Pure rendering
// of the server's /view reply; nothing here knows a pipeline. Loaded with import().

import { esc, icon, bytes } from "../utils/dom.js";
import { server } from "../data/server.js";

const TONE = { ok: "ok", invalid: "fail", missing: "fail", notrun: "muted" };
const LABEL = { ok: "Present", invalid: "Invalid", missing: "Missing", notrun: "Not run" };
let levels = []; // hierarchy order (JSON replies sort their keys)
const where = (w) => Object.entries(w || {}).filter(([k]) => k !== "target")
  .sort(([a], [b]) => (levels.indexOf(a) + 1 || 99) - (levels.indexOf(b) + 1 || 99)).map(([, v]) => v).join(" / ");
const short = (v) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "—"));

/** Step status of the target (for "not run" instead of "missing"). */
function stepState(t, step) {
  const s = (t?.steps || []).find?.((x) => x.key === step) || t?.steps?.[step];
  return s?.status || null;
}

function fileStatus(inst, t) {
  const rows = (inst.entries || []).map((e) => {
    const st = e.status === "missing" && ["pending", "queued", "unknown", null, undefined].includes(stepState(t, e.step)) ? "notrun" : e.status;
    return `<tr class="st-${st}"><td class="mono">${esc(e.step || "")}</td><td><b>${esc(e.label)}</b><div class="mono small muted">${esc(e.pattern || "")}${e.require?.length ? ` · requires ${esc(e.require.join(", "))}` : ""}</div></td>
      <td><span class="badge tone-${TONE[st]}">${esc(LABEL[st])}</span></td>
      <td class="small">${(e.files || []).map((f) => `<div><span class="mono">${esc(f.name)}</span>${f.band ? ` <span class="code-chip">${esc(f.band)}</span>` : ""}${f.note ? ` <span class="${f.status === "ok" ? "muted" : "fail-t"}">${esc(f.note)}</span>` : ""}</div>`).join("") || '<span class="muted">—</span>'}</td></tr>`;
  }).join("");
  const c = inst.counts || {};
  return `<p class="small muted">${c.ok || 0} present · ${c.invalid || 0} invalid · ${c.missing || 0} missing</p><table class="tbl small"><thead><tr><th>Step</th><th>File</th><th>Status</th><th>Found</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function jsonFields(inst) {
  if (!inst.source) return '<p class="muted small">No file.</p>';
  return `<p class="mono small muted">${esc(inst.source)}</p>${inst.error ? `<p class="bad small">${esc(inst.error)}</p>` : ""}<table class="tbl small"><tbody>${(inst.fields || []).map((f) => `<tr><td class="mono">${esc(f.field)}</td><td class="mono small">${esc(short(f.value).slice(0, 600))}</td></tr>`).join("")}</tbody></table>`;
}

function csvTable(inst) {
  return (inst.tables || []).map((tb) => {
    const cols = tb.columns.slice(0, 10);
    return `<p class="mono small muted">${esc(tb.rel)} · ${tb.rows.length} row(s)</p><div class="grid-scroll"><table class="tbl small"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${tb.rows.slice(-50).map((r) => `<tr>${cols.map((c) => `<td class="mono small">${esc(String(r[c] ?? "").slice(0, 120))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }).join("") || '<p class="muted small">No table.</p>';
}

function text(inst) {
  return (inst.files || []).map((f) => `<details><summary class="mono small">${esc(f.rel)}${f.truncated ? " (first 64 KB)" : ""}</summary><pre class="code small">${esc(f.text)}</pre></details>`).join("") || '<p class="muted small">No file.</p>';
}

function images(inst, view, panel, project) {
  if (!inst.images?.length) return '<p class="muted small">No image.</p>';
  return `<div class="pn-imgs">${inst.images.map((im) => { const src = server.viewFileUrl(project, view.query, panel.index, im.rel); return `<a href="${esc(src)}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(src)}" alt="${esc(im.name)}" title="${esc(im.rel)}"></a>`; }).join("")}</div>${inst.more ? `<p class="muted small">+${inst.more} more</p>` : ""}`;
}

function jsonTable(d) {
  if (d && typeof d === "object" && !Array.isArray(d)) {
    return `<table class="tbl small"><tbody>${Object.entries(d).slice(0, 200).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono small">${esc(short(v).slice(0, 400))}</td></tr>`).join("")}</tbody></table>`;
  }
  return `<pre class="code small">${esc(JSON.stringify(d, null, 1).slice(0, 20000))}</pre>`;
}

function fileBody(f) {
  if (f.format === "json") return (f.error ? `<p class="bad small">${esc(f.error)}</p>` : "") + jsonTable(f.data);
  return (f.error ? `<p class="bad small">${esc(f.error)}</p>` : "") + (f.text != null ? `<pre class="code small">${esc(f.text)}</pre>` : "");
}

/** Files of a folder, grouped by the step that writes them. */
function files(inst, format) {
  const list = inst.files || [];
  if (!list.length) return '<p class="muted small">No files.</p>';
  const groups = new Map();
  list.forEach((f) => { const g = f.step || "(no step declares it)"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(f); });
  const order = [...groups.keys()].sort((a, b) => (a.startsWith("(")) - (b.startsWith("(")));
  return order.map((g) => [g, groups.get(g)]).map(([g, items]) => `<h5>${esc(g)}</h5>${items.map((f) => {
    let body;
    try { body = format ? format(f) : fileBody(f); } catch { body = fileBody(f); }
    return `<details class="meta-file"><summary><span class="mono">${esc(f.name)}</span><span class="muted small">${esc(f.label || "")}</span>${f.band ? `<span class="code-chip">${esc(f.band)}</span>` : ""}<span class="grow"></span><span class="muted small">${bytes(f.size || 0)}</span></summary>${body}</details>`;
  }).join("")}`).join("");
}

function counts(p) {
  if (p.panel === "file_status") {
    const c = { ok: 0, invalid: 0, missing: 0 };
    (p.instances || []).forEach((i) => Object.keys(c).forEach((k) => { c[k] += i.counts?.[k] || 0; }));
    return `<span class="count tone-ok"><i></i>${c.ok}</span><span class="count tone-fail"><i></i>${c.invalid + c.missing}</span>`;
  }
  if (p.panel === "files") return `<span class="muted small">${(p.instances || []).reduce((n, i) => n + (i.files?.length || 0), 0)} file(s)</span>`;
  return "";
}

/**
 * The view as cards (one per panel). A panel with several instances (work dirs,
 * chips, nights …) gets tabs; the caller wires `[data-pn-tab]` clicks.
 * opts: {closed: Set of panel keys, tabs: {key: index}, formatFile(file), clientPanels: {name: async (inst, ctx, t) => html}}
 */
export async function renderPanels(view, ctx, t, opts = {}) {
  const list = view?.panels || [];
  levels = view?.levels || [];
  if (!list.length) return `<div class="card"><p class="muted small">Nothing declares <code>views.metadata</code> panels for this project (template or alfrd.yaml).</p></div>`;
  const errors = view.errors?.length ? `<p class="callout warn small">${icon("alert")}<span>${esc(view.errors.join("; "))}</span></p>` : "";
  const cards = await Promise.all(list.map(async (p) => {
    const key = `${p.index}:${p.panel}`;
    const instances = p.instances || [];
    const chosen = Math.min(opts.tabs?.[key] ?? 0, Math.max(0, instances.length - 1));
    const bodies = await Promise.all(instances.map(async (inst) => {
      try {
        if (inst.client) {
          const draw = opts.clientPanels?.[p.panel];
          return draw ? await draw(inst, ctx, t) : `<p class="muted small">${esc(p.panel)} is drawn by a Studio module that is not loaded.</p>`;
        }
        return p.panel === "file_status" ? fileStatus(inst, t) : p.panel === "files" ? files(inst, opts.formatFile)
          : p.panel === "json_fields" ? jsonFields(inst) : p.panel === "csv_table" ? csvTable(inst) : p.panel === "text" ? text(inst)
            : p.panel === "image" ? images(inst, view, p, t.project) : `<p class="muted small">${esc(inst.error || `unknown panel ${p.panel}`)}</p>`;
      } catch (error) {
        return `<p class="callout warn small">${icon("alert")}<span>${esc(error.message)}</span></p>`;
      }
    }));
    const tabs = instances.length > 1
      ? `<div class="seg pn-tabs" role="tablist">${instances.map((inst, i) => `<button data-pn-tab="${i}" class="${i === chosen ? "on" : ""}" title="${esc(inst.path || "")}">${esc(where(inst.where) || `#${i + 1}`)}</button>`).join("")}</div>`
      : instances.length === 1 && where(instances[0].where) ? `<p class="mono small muted">${esc(where(instances[0].where))}</p>` : "";
    const body = bodies.length ? bodies.map((b, i) => `<div data-pn-inst="${i}" ${i === chosen ? "" : "hidden"}>${b}</div>`).join("") : '<p class="muted small">Nothing for this target here.</p>';
    return `<details class="card sec" data-pn="${esc(key)}" ${opts.closed?.has(key) ? "" : "open"}><summary><span class="sec-t">${esc(p.title)}</span><span class="muted small">per ${esc(p.scope)}</span><span class="grow"></span>${counts(p)}</summary><div class="sec-b">${tabs}${body}</div></details>`;
  }));
  return errors + cards.join("");
}
