// VIEW — Logs: every log file alfrd.yaml declares (each step's `logs:` and the
// artifacts with `kind: log`), grouped and collapsible. A file loads when it is
// opened and scrolls in place; the expand button opens it full screen.

import { $, on, esc, icon, loadUi, saveUi } from "../utils/dom.js";
import { groupLabel } from "../data/defs.js";
import { targetCodes } from "./attach.js";
import { logItem, logForTarget, projectLogs } from "./logview.js";

const UI_FIELDS = ["scope", "by", "q", "open"];
const ui = { ...loadUi("logs", { scope: "target", by: "group", q: "", open: [] }), limit: {} };
ui.open = new Set(ui.open);
const remember = () => saveUi("logs", ui, UI_FIELDS);
const PAGE = 100;

export function mount(el, ctx) {
  el.innerHTML = `<div class="lg"><div class="card lg-head" id="lg-head"></div><div id="lg-body"></div></div>`;
  on(el, "click", "[data-scope]", (e, b) => { ui.scope = b.dataset.scope; remember(); render(el, ctx); });
  on(el, "click", "[data-by]", (e, b) => { ui.by = b.dataset.by; remember(); render(el, ctx); });
  on(el, "input", "#lg-q", (e) => { ui.q = e.target.value; remember(); renderBody(el, ctx); });
  on(el, "click", "[data-more]", (e, b) => { ui.limit[b.dataset.more] = (ui.limit[b.dataset.more] || PAGE) + PAGE; renderBody(el, ctx); });
  on(el, "click", "[data-act=collapse]", () => { ui.open.clear(); remember(); renderBody(el, ctx); });
  on(el, "click", "[data-act=expand]", () => { groups(ctx).forEach((g) => ui.open.add(g.id)); remember(); renderBody(el, ctx); });
  el.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.dataset.lgGroup) return;
    d.open ? ui.open.add(d.dataset.lgGroup) : ui.open.delete(d.dataset.lgGroup);
    remember();
  }, true);
}

function project(ctx) {
  return ctx.target()?.project || (ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : ctx.projects()[0]?.id) || Object.keys(ctx.state.trees || {})[0];
}

function files(ctx) {
  const p = project(ctx);
  const t = ctx.target();
  const codes = t ? targetCodes(ctx, t).map((c) => c.code) : [];
  const q = ui.q.trim().toLowerCase();
  return projectLogs(ctx, p).filter((f) => (ui.scope === "all" || logForTarget(ctx, f, t, codes)) && (!q || f.rel.toLowerCase().includes(q)));
}

function groups(ctx) {
  const defs = ctx.state.trees?.[project(ctx)]?.defs;
  const map = new Map();
  const add = (id, label, f) => {
    if (!map.has(id)) map.set(id, { id, label, files: [] });
    map.get(id).files.push(f);
  };
  const order = ctx.steps();
  files(ctx).forEach((f) => {
    if (ui.by === "folder") add(`dir:${f.rel.slice(0, f.rel.lastIndexOf("/")) || "."}`, f.rel.slice(0, f.rel.lastIndexOf("/")) || "(project folder)", f);
    else (f.groups?.length ? f.groups : ["other"]).forEach((g) => add(g, g === "other" ? "Other" : groupLabel(g, defs), f));
  });
  const rank = (g) => (g.id.startsWith("step:") ? order.indexOf(g.id.slice(5)) : 1000);
  return [...map.values()].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}

export function render(el, ctx) {
  const t = ctx.target();
  const p = project(ctx);
  const all = projectLogs(ctx, p);
  $("#lg-head", el).innerHTML = `
    <div class="row gap wrap"><h2>Logs</h2>${p ? `<span class="chip">${esc(p)}</span>` : ""}
      <div class="seg" role="group" aria-label="Scope"><button data-scope="target" class="${ui.scope === "target" ? "on" : ""}">${icon("target")} ${esc(t?.name || "Selected target")}</button><button data-scope="all" class="${ui.scope === "all" ? "on" : ""}">All files (${all.length})</button></div>
      <div class="seg" role="group" aria-label="Group by"><button data-by="group" class="${ui.by === "group" ? "on" : ""}">By step / artifact</button><button data-by="folder" class="${ui.by === "folder" ? "on" : ""}">By folder</button></div>
      <span class="grow"></span>
      <label class="search">${icon("search")}<input id="lg-q" type="search" placeholder="Filter file names…" value="${esc(ui.q)}"></label>
      <button class="btn sm" data-act="expand">Expand groups</button><button class="btn sm" data-act="collapse">Collapse</button></div>
    <p class="muted small">Files matching each step's <code>logs:</code> and the artifacts with <code>kind: log</code> in alfrd.yaml. A file is read when you open it (last 400 kB); ${icon("expand")} opens it full screen.</p>`;
  renderBody(el, ctx);
  ctx.setFooterRight(`${all.length} log file(s) declared by alfrd.yaml`);
}

function renderBody(el, ctx) {
  const p = project(ctx);
  const defs = ctx.state.trees?.[p]?.defs;
  const gs = groups(ctx);
  const body = $("#lg-body", el);
  if (!projectLogs(ctx, p).length) {
    body.innerHTML = `<div class="card empty">${p ? `No log files found for <b>${esc(p)}</b>. Declare them in alfrd.yaml (a step's <code>logs:</code> list, or an artifact with <code>kind: log</code>) and Re-scan.` : "Open a project folder (Import) to see its logs."}</div>`;
    return;
  }
  body.innerHTML = gs.map((g) => {
    const limit = ui.limit[g.id] || PAGE;
    return `<details class="card lg-group" data-lg-group="${esc(g.id)}" ${ui.open.has(g.id) ? "open" : ""}>
      <summary><span class="caret">${icon("caret")}</span><b>${esc(g.label)}</b><span class="count">${g.files.length}</span></summary>
      <div class="lg-files">${g.files.slice(0, limit).map((f) => logItem(f, { project: p, showGroups: ui.by === "folder", defs })).join("")}
      ${g.files.length > limit ? `<button class="btn sm" data-more="${esc(g.id)}">Show ${Math.min(PAGE, g.files.length - limit)} more</button>` : ""}</div></details>`;
  }).join("") || `<div class="card empty">No logs match.</div>`;
}
