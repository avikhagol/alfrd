// VIEW — Project settings: edit and save the loaded alfrd.yaml.
//
// alfrd.yaml drives the whole Studio (template, stages, step labels/categories,
// metadata files, logs, the Overview MS path, field aliases). This view edits
// the file as text, with a small form for field aliases, and saves it back:
// through `alfrd serve` (loopback only), into the opened folder (Chrome/Edge),
// or as a download.

import { $, on, esc, icon, copyText, download } from "../utils/dom.js";
import { parseYaml, dumpYaml } from "../utils/yaml_parser.js";
import { manifestToWorkflows } from "../data/model.js";
import { studioManifest } from "../data/defs.js";

const ui = { project: null, text: null, dirty: false, report: null, aliases: null };

function project(ctx) {
  return ctx.target()?.project || (ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : null) || Object.keys(ctx.state.trees || {})[0] || null;
}

function loadedText(ctx, p) {
  return ctx.state.trees?.[p]?.manifestText ?? ctx.state.workflowFile.text ?? "";
}

function parse(text) {
  try {
    return { data: parseYaml(text), error: null };
  } catch (error) {
    return { data: null, error: error.message };
  }
}

function aliasRows(text) {
  const { data } = parse(text);
  const map = data?.project_settings?.field_aliases || {};
  return Object.entries(map).map(([from, to]) => ({ from: String(from), to: String(to) }));
}

/** Replace (or insert) the top-level `project_settings:` block, keeping the rest of the text. */
export function setProjectSettings(text, settings) {
  const lines = String(text || "").split("\n");
  const start = lines.findIndex((l) => /^project_settings\s*:/.test(l));
  const block = dumpYaml({ project_settings: settings }).trimEnd().split("\n");
  if (start >= 0) {
    let end = start + 1;
    while (end < lines.length && !/^[^\s#]/.test(lines[end])) end += 1;
    // Blank lines and comments just above the next key stay with that key.
    while (end > start + 1 && (/^\s*$/.test(lines[end - 1]) || /^#/.test(lines[end - 1]))) end -= 1;
    lines.splice(start, end - start, ...block);
  } else {
    const after = Math.max(lines.findIndex((l) => /^template\s*:/.test(l)), lines.findIndex((l) => /^name\s*:/.test(l)));
    lines.splice(after >= 0 ? after + 1 : lines.length, 0, "", ...block, "");
  }
  return lines.join("\n");
}

function validate(ctx, text) {
  const { data, error } = parse(text);
  if (error) return { ok: false, errors: [error], warnings: [] };
  if (!data || typeof data !== "object" || Array.isArray(data)) return { ok: false, errors: ["alfrd.yaml must be a YAML mapping."], warnings: [] };
  const errors = [];
  if (!String(data.name || "").trim()) errors.push("`name` is required.");
  const info = manifestToWorkflows(data, "alfrd.yaml", { aliases: false });
  const defs = studioManifest(data);
  const steps = info.workflows[0]?.steps || [];
  return {
    ok: !errors.length && !info.errors.length,
    errors: [...errors, ...info.errors],
    warnings: info.warnings,
    summary: {
      template: defs.template || "none",
      steps: steps.length,
      stages: info.workflows[0]?.stages.length || 0,
      metadata: steps.filter((s) => s.metadata?.length).length,
      logs: steps.filter((s) => s.logs?.length).length,
      ms: (defs.overview?.ms_path ? [].concat(defs.overview.ms_path) : []).length,
      aliases: Object.keys(defs.settings?.field_aliases || {}).length,
    },
  };
}

export function mount(el, ctx) {
  el.innerHTML = `<div class="ps" id="ps"></div>`;
  on(el, "input", "#ps-yaml", (e) => { ui.text = e.target.value; ui.dirty = true; ui.report = null; ui.aliases = null; renderStatus(el, ctx); });
  on(el, "keydown", "#ps-yaml", (e) => {
    if (e.key === "Tab") { // two-space indent instead of leaving the editor
      e.preventDefault();
      const t = e.target;
      const a = t.selectionStart;
      t.setRangeText("  ", a, t.selectionEnd, "end");
      t.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(el, ctx); }
  });
  on(el, "input", "[data-alias]", (e, inp) => {
    const [i, k] = inp.dataset.alias.split(":");
    ui.aliases[Number(i)][k] = inp.value;
  });
  on(el, "click", "[data-act]", async (e, b) => {
    const a = b.dataset.act;
    const p = project(ctx);
    if (a === "validate") { ui.report = validate(ctx, ui.text); renderStatus(el, ctx); }
    if (a === "save") save(el, ctx);
    if (a === "revert") { ui.text = loadedText(ctx, p); ui.dirty = false; ui.report = null; ui.aliases = null; render(el, ctx); }
    if (a === "download") download("alfrd.yaml", ui.text, "text/yaml");
    if (a === "copy") { const ok = await copyText(ui.text); ctx.toast(ok ? "Copied" : "Copy failed", ok ? "ok" : "fail"); }
    if (a === "alias-add") { ui.aliases.push({ from: "", to: "" }); render(el, ctx); }
    if (a === "alias-rm") { ui.aliases.splice(Number(b.dataset.i), 1); render(el, ctx); }
    if (a === "alias-apply") {
      const { data, error } = parse(ui.text);
      if (error) { ctx.toast(`Fix the YAML first: ${error}`, "fail"); return; }
      const settings = { ...(data?.project_settings || {}) };
      const map = Object.fromEntries(ui.aliases.filter((r) => r.from.trim() && r.to.trim()).map((r) => [r.from.trim(), r.to.trim()]));
      if (Object.keys(map).length) settings.field_aliases = map;
      else delete settings.field_aliases;
      ui.text = setProjectSettings(ui.text, settings);
      ui.dirty = true;
      ui.report = validate(ctx, ui.text);
      render(el, ctx);
      ctx.toast("Field aliases written into the editor — Save to keep them", "ok");
    }
    if (a === "tpl-copy") {
      const id = $("#ps-tpl-step", el)?.value;
      const defs = studioManifest(parse(ui.text).data || {});
      const def = defs.tplSteps?.[id] || defs.steps?.[id];
      if (!def) return;
      const text = dumpYaml([{ id, ...def }]).replace(/^/gm, "      ");
      const ok = await copyText(text);
      ctx.toast(ok ? `${id}: step definition copied — paste it under workflows[0].steps` : "Copy failed", ok ? "ok" : "fail");
    }
  });
}

async function save(el, ctx) {
  const p = project(ctx);
  const report = validate(ctx, ui.text);
  ui.report = report;
  if (!report.ok) { renderStatus(el, ctx); ctx.toast("Not saved: fix the errors first", "fail"); return; }
  try {
    await ctx.saveManifest(p, ui.text);
    ui.dirty = false;
    ctx.toast(`alfrd.yaml saved${ctx.state.mode === "server" ? " (previous version kept as alfrd.yaml.bak)" : ""}`, "ok");
    ctx.update();
  } catch (error) {
    ctx.toast(`Not saved: ${error.message}`, "fail");
  }
}

function renderStatus(el, ctx) {
  const box = $("#ps-status", el);
  if (!box) return;
  const r = ui.report;
  box.innerHTML = !r ? `<span class="muted">${ui.dirty ? "Unsaved changes." : "Saved version loaded."}</span>`
    : `${r.ok ? `<span class="badge tone-ok">${icon("checkCircle")}Valid</span>` : `<span class="badge tone-fail">${icon("xCircle")}${r.errors.length} error(s)</span>`}
      ${r.summary ? `<span class="muted"> template <b>${esc(r.summary.template)}</b> · ${r.summary.steps} steps in ${r.summary.stages} stages · ${r.summary.metadata} with metadata · ${r.summary.logs} with own logs · ${r.summary.ms} MS path pattern(s) · ${r.summary.aliases} field alias(es)</span>` : ""}
      ${[...r.errors.map((m) => `<li class="lvl-error">${esc(m)}</li>`), ...r.warnings.map((m) => `<li class="lvl-warn">${esc(m)}</li>`)].length ? `<ul class="msgs">${[...r.errors.map((m) => `<li class="lvl-error">${esc(m)}</li>`), ...r.warnings.map((m) => `<li class="lvl-warn">${esc(m)}</li>`)].join("")}</ul>` : ""}`;
  const saveBtn = $("[data-act=save]", el);
  if (saveBtn) saveBtn.classList.toggle("primary", ui.dirty);
}

export function render(el, ctx) {
  const p = project(ctx);
  if (ui.project !== p || (!ui.dirty && ui.text !== loadedText(ctx, p))) {
    ui.project = p;
    ui.text = loadedText(ctx, p);
    ui.dirty = false;
    ui.report = null;
    ui.aliases = null;
  }
  if (!ui.aliases) ui.aliases = aliasRows(ui.text);
  const tree = ctx.state.trees?.[p] || {};
  const where = ctx.state.mode === "server"
    ? (ctx.canWrite(p) ? "Saves through alfrd serve into the project folder (old file kept as alfrd.yaml.bak)." : "alfrd serve only accepts saves from a browser on the same machine; Save downloads the file.")
    : ctx.canWrite(p) ? "Saves into the opened folder (the browser asks for write access once)." : "No writable folder is open (Chrome/Edge: Import → Open project folder); Save downloads the file.";
  const defs = studioManifest(parse(ui.text).data || {});
  const tplSteps = Object.keys(defs.tplSteps || {});
  const found = ctx.state.aliases || [];
  $("#ps", el).innerHTML = `
    <div class="card"><div class="row gap wrap"><h2>Project settings</h2>${p ? `<span class="chip">${esc(ctx.projectName(p))}</span>` : ""}<span class="mono small muted">${esc(tree.manifestFile || ctx.state.workflowFile.name || "alfrd.yaml")}</span>${tree.manifestDefault ? `<span class="chip" title="This folder has no alfrd.yaml, so ALFRD's default one is shown. Save writes it into the folder; the local file then replaces the default.">default — not saved in the folder</span>` : ""}<span class="grow"></span>
      <button class="btn sm" data-act="validate">${icon("validate")} Validate</button>
      <button class="btn sm" data-act="revert">${icon("reset")} Revert</button>
      <button class="btn sm" data-act="download">${icon("download")} Download</button>
      <button class="btn sm ${ui.dirty ? "primary" : ""}" data-act="save">${icon("save")} Save alfrd.yaml</button></div>
      <p class="muted small">${esc(where)} Ctrl+S saves. After saving, the workflow, stages, metadata health, logs and field aliases are re-read from the file.</p>
      <div class="ps-status" id="ps-status"></div></div>
    ${!ui.text ? `<div class="card empty">No alfrd.yaml loaded. Open the project folder (Import) or start <code>alfrd serve</code> in it.</div>` : `
    <div class="ps-grid">
      <section class="card"><textarea id="ps-yaml" class="yaml-editor" spellcheck="false" aria-label="alfrd.yaml">${esc(ui.text)}</textarea></section>
      <div>
        <section class="card">
          <h4>${icon("arrows")} Field aliases</h4>
          <p class="muted small">Older names → names used now, applied to workflow steps, result CSV rows and folder/file names while reading (<code>project_settings.field_aliases</code>). End both sides with <code>*</code> to rewrite a prefix.</p>
          <div class="alias-rows">${ui.aliases.map((r, i) => `<div class="row gap"><input class="input mono sm grow" data-alias="${i}:from" value="${esc(r.from)}" placeholder="old name" aria-label="Old name"><span class="muted">→</span><input class="input mono sm grow" data-alias="${i}:to" value="${esc(r.to)}" placeholder="new name" aria-label="New name"><button class="icon-btn xs" data-act="alias-rm" data-i="${i}" aria-label="Remove alias">${icon("trash")}</button></div>`).join("") || '<p class="muted small">No field aliases.</p>'}</div>
          <div class="row gap"><button class="btn sm" data-act="alias-add">${icon("plus")} Add alias</button><button class="btn sm primary" data-act="alias-apply">Write into alfrd.yaml</button></div>
          ${found.length ? `<h5>Applied while reading</h5><table class="tbl small"><tbody>${found.map((a) => `<tr><td class="mono">${esc(a.from)}</td><td class="mono">${esc(a.to)}</td><td class="muted small">${esc(a.sources.slice(0, 2).join(", "))}${a.sources.length > 2 ? ` +${a.sources.length - 2}` : ""}</td></tr>`).join("")}</tbody></table>` : ""}
        </section>
        <section class="card">
          <h4>${icon("file")} What alfrd.yaml controls</h4>
          <ul class="small ps-help">
            <li><code>template: avica</code> — AVICA views (config summary, rPicard inputs, project-code folders) and default step definitions.</li>
            <li><code>stages:</code> — workflow groups (<code>{id, title}</code>); a step picks one with <code>stage:</code>.</li>
            <li>Step mapping under <code>workflows[0].steps</code> — <code>label</code>, <code>category</code>, <code>stage</code>, <code>description</code>, <code>icon</code>, <code>metadata</code>, <code>logs</code>.</li>
            <li><code>metadata:</code> — files in <code>{meta_dir}</code> the step writes (<code>file</code>, <code>label</code>, <code>require</code>) → Metadata health.</li>
            <li><code>logs:</code> — patterns such as <code>{workdir}/wd_{band}/avica_avg_*log*</code> → Workflow → Logs and the Logs view.</li>
            <li><code>overview.ms_path:</code> — patterns for the Overview "MS Storage Path" column.</li>
            <li>Artifacts with <code>kind: log</code> (or <code>show_in_logs: true</code>) — extra groups in the Logs view.</li>
          </ul>
          ${tplSteps.length ? `<div class="row gap"><select id="ps-tpl-step" class="input sm mono grow" aria-label="Template step">${tplSteps.map((k) => `<option>${esc(k)}</option>`).join("")}</select><button class="btn sm" data-act="tpl-copy">${icon("copy")} Copy template step YAML</button></div>` : ""}
        </section>
      </div>
    </div>`}`;
  renderStatus(el, ctx);
  ctx.setFooterRight(ui.dirty ? "alfrd.yaml: unsaved changes" : "alfrd.yaml");
}
