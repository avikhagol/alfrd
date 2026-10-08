// VIEW 3 — Metadata of the selected target.
//
//   • alfrd serve: the panels the template (or alfrd.yaml) declares under
//     views.metadata, evaluated by the server (alfrd.layout_generic) and drawn by
//     the lazy panels.js. Nothing here knows a pipeline; AVICA's configuration and
//     rPicard inputs are client panels drawn by the lazy metadata_avica.js.
//   • a folder opened in the browser: the AVICA reader (metadata_avica.js).

import { $, on, esc, icon, loadUi, saveUi } from "../utils/dom.js";
import { avicaIndex, codeChips, detachCode, ensureServerIndex, loadView, openAttachPicker, targetCodes, clearWorkdirCache, workdirGeneration } from "./attach.js";
import { server } from "../data/server.js";
import { scoped } from "../data/workspace.js";

const UI_FIELDS = ["open", "closed", "code", "templateFolder", "filter", "details"];
// Per project: template folder, selected file, expanded sections and loaded view.
// `details`: open/closed file disclosures (utils/keep_view.js).
const ui = scoped("metadata", () => {
  const s = {
    ...loadUi("metadata", { open: ["health", "meta", "config", "inputs"], closed: [], code: {}, templateFolder: {}, filter: "", details: {} }),
    wd: null,
    wdKey: null,
    view: { key: null, html: "" }, // generic view (alfrd serve)
    tabs: {},                       // panel key -> chosen instance
    loading: false,
    error: null,
  };
  s.open = new Set(s.open);
  s.closed = new Set(s.closed);
  if (!s.details || typeof s.details !== "object") s.details = {};
  if (s.open.has("hdu")) s.open.add("health");
  return s;
});
const remember = () => saveUi("metadata", ui, UI_FIELDS);

let avica = null; // metadata_avica.js once loaded
let panels = null; // panels.js once loaded
let kept = null; // utils/keep_view.js: loaded with either, before any panel is shown
const loadKept = () => import("../utils/keep_view.js").then((m) => { kept = m; });
const loadAvica = () => Promise.all([import("./metadata_avica.js"), loadKept()]).then(([m]) => { m.useState(ui); avica = m; return m; });
const loadPanels = () => Promise.all([import("./panels.js"), loadKept()]).then(([m]) => { panels = m; return m; });

/** Put a render into #md-main keeping open/closed disclosures and the reading position. */
function show(main, html, scope) {
  if (!kept) { main.innerHTML = html; return; }
  if (kept.showKept(main, html, scope, ui, $("#main"))) remember();
}

const generic = (ctx, t) => ctx.state.mode === "server" && ctx.state.trees?.[t.project]?.provider === "server";

export function mount(el, ctx) {
  el.innerHTML = `<div class="md"><div class="card md-head" id="md-head"></div><div id="md-main"></div></div>`;
  el.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement)) return;
    if (kept?.noteDetailToggle(e, ui.details)) { remember(); return; }
    if (d.dataset.pn) { d.open ? ui.closed.delete(d.dataset.pn) : ui.closed.add(d.dataset.pn); remember(); return; }
    if (!d.dataset.sec) return;
    d.open ? ui.open.add(d.dataset.sec) : ui.open.delete(d.dataset.sec);
    remember();
  }, true);
  on(el, "click", "[data-attach]", () => { const t = ctx.target(); if (t) openAttachPicker(ctx, t); });
  on(el, "click", "[data-detach]", (e, b) => { const t = ctx.target(); if (t) { detachCode(ctx, t, b.dataset.detach); clearWorkdirCache(); ui.wdKey = null; ui.view.key = null; } });
  on(el, "click", "[data-code-tab]", (e, b) => { const t = ctx.target(); ui.code[t.id] = b.dataset.codeTab; ui.wdKey = null; remember(); render(el, ctx); });
  on(el, "click", "[data-pn-tab]", (e, b) => {
    // Instance tabs of a panel (one per work dir, chip, night …): switch in place.
    const card = b.closest("[data-pn]");
    ui.tabs[card.dataset.pn] = Number(b.dataset.pnTab);
    card.querySelectorAll("[data-pn-tab]").forEach((x) => x.classList.toggle("on", x === b));
    card.querySelectorAll("[data-pn-inst]").forEach((x) => { x.hidden = x.dataset.pnInst !== b.dataset.pnTab; });
  });
  on(el, "change", "#md-tpl", (e) => {
    const t = ctx.target();
    ui.templateFolder[t.id] = e.target.value;
    remember();
    ui.view.key = null;
    render(el, ctx);
  });
  on(el, "input", "#md-filter", (e) => { ui.filter = e.target.value; remember(); avica?.renderConfig(el, ctx); });
  on(el, "click", "#md-run-summary", async () => {
    const t = ctx.target();
    try {
      await server.avicaSummary(t.project);
      delete ctx.state.avica[t.project];
      clearWorkdirCache();
      ui.view.key = null;
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
  if (generic(ctx, t)) { renderGeneric(el, ctx, t, head, main); return; }
  if (avica) { avica.renderFolder(el, ctx, { activeCode, rerender: () => render(el, ctx), show }); return; }
  main.innerHTML = `<div class="card empty">Loading…</div>`;
  loadAvica().then(() => render(el, ctx)).catch((error) => { main.innerHTML = `<div class="card empty">${esc(error.message)}</div>`; });
}

/** alfrd serve: the declared panels for the target (and the chosen project code, when it has several). */
function renderGeneric(el, ctx, t, head, main) {
  const tree = ctx.state.trees?.[t.project];
  const index = avicaIndex(ctx, t.project);
  if (!index && tree?.defs?.template === "avica") ensureServerIndex(ctx, t.project);
  const codes = index ? targetCodes(ctx, t) : [];
  const code = codes.length > 1 ? activeCode(ctx, t) : null;
  head.innerHTML = `
    <div class="row gap wrap"><h2>Metadata</h2><span class="chip">${esc(t.name)}</span><span class="muted small">project <b>${esc(ctx.projectName(t.project))}</b></span><span class="grow"></span>
      ${index ? `<span class="small muted">Project code${codes.length === 1 ? "" : "s"}:</span> <span class="codes">${codeChips(ctx, t, { editable: true })}</span>` : ""}</div>
    ${codes.length > 1 ? `<div class="seg md-codes" role="tablist" aria-label="Project code">${codes.map((c) => `<button data-code-tab="${esc(c.code)}" class="${c.code === code ? "on" : ""}">${esc(c.code)}</button>`).join("")}</div>` : ""}`;
  const [projectCode, workdir] = code ? String(code).split("/") : [];
  const entity = { project: t.project, target: t.name, project_code: projectCode, workdir };
  const gen = `${workdirGeneration(t.project)}|${Object.keys(index?.codes || {}).length}|${ui.templateFolder[t.id] || ""}`;
  const scope = `${t.project}|${t.id}|${JSON.stringify(entity)}|${ui.templateFolder[t.id] || ""}`;
  const key = `${scope}|${gen}`;
  if (ui.view.key !== key) {
    // A live update re-reads the same context: keep the current render until the new one is ready.
    ui.view = ui.view.scope === scope ? { ...ui.view, key } : { key, scope, html: `<div class="card empty">Reading ${esc(t.name)}…</div>` };
    Promise.all([loadView(ctx, t.project, entity), panels || loadPanels()])
      .then(async ([view, mod]) => {
        const client = (view.panels || []).some((p) => p.instances?.[0]?.client) ? (avica || await loadAvica()) : null;
        const html = await mod.renderPanels(view, ctx, t, {
          closed: ui.closed, tabs: ui.tabs, formatFile: client?.formatFile || avica?.formatFile, clientPanels: client?.clientPanels || {},
        });
        if (ui.view.key === key) { ui.view.html = html; render(el, ctx); }
      })
      .catch((error) => { if (ui.view.key === key) { ui.view.html = `<div class="card"><p class="callout warn">${icon("alert")}<span>${esc(error.message)}</span></p></div>`; render(el, ctx); } });
  }
  const attach = index && !codes.length
    ? `<div class="card"><p class="callout info small">${icon("info")}<span>No work folder is attached to <b>${esc(t.name)}</b> (nothing under <code>${esc(index.targetDir || "target_dir")}/</code> names it).</span> <button class="btn sm" data-attach>${icon("link")} Attach folder…</button></p></div>`
    : "";
  show(main, attach + ui.view.html, scope);
  panels?.mountViewers(main, t.project);
  ctx.setFooterRight(`${esc(t.name)} · views.metadata`);
}
