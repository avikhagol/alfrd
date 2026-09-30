// Attaching AVICA project-code work folders (<target_dir>/<CODE>/wd) to targets.
//
// ALFRD project  = alfrd.yaml (groups targets).
// Project code   = AVICA observation code folder (BV019, RDV41, ...). A target
//                  may be attached to several codes. Codes are auto-detected
//                  from wd_<band>_<target>/ and avica.meta/*_<band>_<target>.avica;
//                  manual attachments/detachments are remembered per browser.

import { esc, icon, on, storage } from "../utils/dom.js";
import { workdirFromIndex, indexFromServerLayout, detectCodes } from "../data/avica.js";
import { server } from "../data/server.js";

const attachments = storage.get("attach", {}); // targetId -> {add:[], remove:[]}
const workdirCache = new Map();

function saveAttachments() {
  storage.set("attach", attachments);
}

/** AVICA index for an ALFRD project (files or server provider). */
export function avicaIndex(ctx, project) {
  return ctx.state.avica?.[project] || null;
}

/** Effective codes for a target: detected + declared + manual, minus removed. */
export function targetCodes(ctx, t) {
  if (!t) return [];
  const a = attachments[t.id] || { add: [], remove: [] };
  const base = (t.codes || []).map((c) => (typeof c === "string" ? { code: c, auto: false } : c));
  // Work dirs that mention this target (server layouts arrive after the targets).
  detectCodes(avicaIndex(ctx, t.project), t.name).forEach((code) => {
    if (!base.some((c) => c.code === code)) base.push({ code, auto: true });
  });
  const out = base.filter((c) => !a.remove.includes(c.code));
  a.add.forEach((code) => { if (!out.some((c) => c.code === code)) out.push({ code, auto: false, manual: true }); });
  return out;
}

export function attachCode(ctx, t, code) {
  const a = (attachments[t.id] ||= { add: [], remove: [] });
  a.remove = a.remove.filter((c) => c !== code);
  if (!(t.codes || []).some((c) => (c.code || c) === code) && !a.add.includes(code)) a.add.push(code);
  saveAttachments();
  ctx.log("info", `${t.name}: attached ${code} work folder.`, "avica");
  ctx.update();
}

export function detachCode(ctx, t, code) {
  const a = (attachments[t.id] ||= { add: [], remove: [] });
  a.add = a.add.filter((c) => c !== code);
  if (!a.remove.includes(code)) a.remove.push(code);
  saveAttachments();
  ctx.log("info", `${t.name}: detached ${code}.`, "avica");
  ctx.update();
}

export function resetAttachments() {
  Object.keys(attachments).forEach((k) => delete attachments[k]);
  storage.remove("attach");
}

/** Load the server layout for an ALFRD project once (server mode). */
const inflight = new Map();
export function ensureServerIndex(ctx, project) {
  if (ctx.state.mode !== "server" || ctx.state.avica?.[project]) return Promise.resolve(avicaIndex(ctx, project));
  if (!inflight.has(project)) inflight.set(project, fetchServerIndex(ctx, project).finally(() => inflight.delete(project)));
  return inflight.get(project);
}

async function fetchServerIndex(ctx, project) {
  try {
    const layout = await server.avicaLayout(project);
    const index = indexFromServerLayout(layout);
    index.project = project;
    index.provider = "server";
    ctx.state.avica = { ...(ctx.state.avica || {}), [project]: index };
    ctx.log("info", `AVICA layout for ${project}: ${Object.keys(index.codes).length} project code folder(s) under ${index.targetDir}/.`, "avica");
    ctx.update();
    return index;
  } catch (error) {
    ctx.state.avica = { ...(ctx.state.avica || {}), [project]: { error: error.message, codes: {}, logs: [], update: {}, values: {} } };
    ctx.log("warn", `AVICA layout for ${project} unavailable: ${error.message}`, "avica");
    return null;
  }
}

// Bumped by clearWorkdirCache (per project, or all at once) so a request
// that started before a clear never stores its now-stale result.
const workdirGen = new Map();
let workdirGenAll = 0;

export function workdirGeneration(project) {
  return `${workdirGenAll}:${workdirGen.get(project) || 0}`;
}

/** avica.meta + rPicard templates for (target, code). Resolves synchronously for folder imports. */
export async function loadWorkdir(ctx, t, code) {
  const index = avicaIndex(ctx, t.project);
  if (!index) return null;
  const key = `${t.project}|${code}|${t.name}`;
  if (workdirCache.has(key)) return workdirCache.get(key);
  const gen = workdirGeneration(t.project);
  let wd = null;
  if (index.provider === "server") {
    wd = await server.avicaWorkdir(t.project, code, t.name);
  } else {
    wd = workdirFromIndex(index, code, t.name);
  }
  if (workdirGeneration(t.project) === gen) workdirCache.set(key, wd);
  return wd;
}

/** Entity path → query string (same order and escaping as data/entities.js, without importing it at startup). */
export function entityQuery(entity) {
  return Object.entries(entity).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
}

/** The template's panels (alfrd.layout_generic) for an entity, cached with the work dirs (same generation guard). */
export async function loadView(ctx, project, entity) {
  const query = entityQuery(entity);
  const key = `${project}|view|${query}`;
  if (workdirCache.has(key)) return workdirCache.get(key);
  const gen = workdirGeneration(project);
  const view = await server.projectView(project, query);
  view.query = query;
  if (workdirGeneration(project) === gen) workdirCache.set(key, view);
  return view;
}

/** Forget cached work dirs: one project's, or (no argument) every project's. */
export function clearWorkdirCache(project) {
  if (!project) {
    workdirCache.clear();
    workdirGenAll += 1;
    return;
  }
  workdirGen.set(project, (workdirGen.get(project) || 0) + 1);
  const prefix = `${project}|`;
  for (const key of workdirCache.keys()) {
    if (key.startsWith(prefix)) workdirCache.delete(key);
  }
}

/** Searchable picker: attach a <target_dir>/<CODE> folder to the target. */
export function openAttachPicker(ctx, t) {
  const index = avicaIndex(ctx, t.project);
  const codes = Object.values(index?.codes || {});
  const current = new Set(targetCodes(ctx, t).map((c) => c.code));
  ctx.modal(`
    <header class="modal-h"><h2>Attach AVICA work folder to <span class="mono">${esc(t.name)}</span></h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b">
      ${index?.targetDir ? `<p class="muted small">Folders under <code>${esc(index.targetDir)}/</code> (target_dir from ${esc(index.summary ? "avica pipe config --summary" : "avica.inp / alfrd.yaml")}). Each is an AVICA project code; work dirs match <code>${esc((index.patterns?.workdir || []).join("</code> or <code>"))}</code> (alfrd.yaml <code>avica.workdir</code>).</p>` : ""}
      ${!codes.length ? `<p class="callout warn">${icon("alert")}<span>${index?.error ? esc(index.error) : "No <code>&lt;target_dir&gt;/&lt;CODE&gt;/wd</code> folders found. Open the folder that contains <code>alfrd.yaml</code> (Import → Open project folder) so the Studio can see <code>target_dir</code>."}</span></p>` : ""}
      <label class="search">${icon("search")}<input id="att-q" type="search" placeholder="Search project codes, e.g. BV019, RDV41, or a band/target…" autocomplete="off"></label>
      <ul class="att-list" id="att-list" role="listbox"></ul>
    </div>`, (root, close) => {
    const list = root.querySelector("#att-list");
    const draw = (q) => {
      const query = q.trim().toLowerCase();
      const rows = codes
        .map((c) => ({ c, match: c.targets.includes(t.name) }))
        .filter(({ c }) => !query || (c.id || c.code).toLowerCase().includes(query) || c.targets.some((x) => x.toLowerCase().includes(query)) || c.bands.join(" ").toLowerCase().includes(query))
        .sort((a, b) => Number(b.match) - Number(a.match) || (a.c.id || a.c.code).localeCompare(b.c.id || b.c.code));
      list.innerHTML = rows.map(({ c, match }) => `<li><button data-code="${esc(c.id || c.code)}" role="option" ${current.has(c.id || c.code) ? 'aria-selected="true"' : ""}>
          <b class="mono">${esc(c.id || c.code)}</b>${c.wd ? `<span class="muted small mono">${esc(c.wd)}</span>` : ""}
          ${match ? `<span class="badge tone-ok">${icon("check")}has ${esc(t.name)}</span>` : ""}
          ${current.has(c.id || c.code) ? '<span class="badge tone-run">attached</span>' : ""}
          <span class="muted small">${c.bands.length ? `bands ${esc(c.bands.join(", "))}` : "no band dirs"} · ${c.targets.length} target${c.targets.length === 1 ? "" : "s"}${c.targets.length ? `: ${esc(c.targets.slice(0, 4).join(", "))}${c.targets.length > 4 ? "…" : ""}` : ""} · ${c.meta ? c.meta.length : (c.metaFiles || []).length} avica.meta files</span>
        </button></li>`).join("") || '<li class="muted small">No matching folders.</li>';
    };
    const input = root.querySelector("#att-q");
    input.addEventListener("input", () => draw(input.value));
    on(list, "click", "[data-code]", (e, b) => {
      attachCode(ctx, t, b.dataset.code);
      clearWorkdirCache();
      close();
    });
    draw("");
  });
}

/** Chips for a target's codes with attach/detach controls. */
export function codeChips(ctx, t, { editable = false } = {}) {
  const codes = targetCodes(ctx, t);
  const chips = codes.map((c) => `<span class="code-chip ${c.auto ? "auto" : ""}" title="${c.auto ? "Auto-detected from the work folder" : "Attached"}">${esc(c.code)}${editable ? `<button class="x" data-detach="${esc(c.code)}" aria-label="Detach ${esc(c.code)}">${icon("close")}</button>` : ""}</span>`).join("");
  return `${chips || (editable ? "" : '<span class="muted">—</span>')}${editable ? `<button class="btn sm" data-attach="${esc(t.id)}">${icon("link")} Attach folder</button>` : ""}`;
}
