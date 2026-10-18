// Plugin pages (api.registerPage), plugin availability per project type and the sidebar "Plugins" group.
// Loaded with plugin_api.js (server mode only). A page lives at #/plugin/<id>?p=<project> and renders in the main area.
import { esc, icon, loadCss, routeHash } from "../utils/dom.js";

const pages = new Map();
const avail = new Map(); // project -> {type, plugins: {id: {available, reason, source, declared}}} | "loading" | "failed"
let host = null; // {ctx, fetch}
let ready = false;
let railKey = "";
let seq = 0;
const ID = /^[a-z0-9_-]+$/;

export function bindPages(ctx, fetch) {
  if (host?.ctx === ctx) return;
  host = { ctx, fetch };
  ctx.pluginApplies = (plugin, projectTypes, project) => applies(plugin, projectTypes, project);
  ctx.renderPluginPage = (el) => renderPage(el);
  ctx.subscribe?.(() => { const p = current(); if (p) ensure(p); renderRail(); });
}

const commands = () => [...pages.values()].filter((s) => applies(s.plugin, s.projectTypes))
  .map((s) => ({ id: `page:${s.id}`, group: "Plugins", icon: s.icon, label: `Open ${s.title}`, detail: "plugin page", run: () => open(s.id) }));

export function pagesReady() { ready = true; host?.ctx.update?.(); }

export function registerPage(spec, plugin, report) {
  if (!spec || !ID.test(spec.id) || !spec.title || typeof spec.render !== "function"
      || (spec.order != null && !Number.isFinite(spec.order))
      || (spec.projectTypes != null && (!Array.isArray(spec.projectTypes) || !spec.projectTypes.length || !spec.projectTypes.every((t) => typeof t === "string" && t)))) {
    throw new TypeError("registerPage({id,title,render,order?,icon?,projectTypes?})");
  }
  pages.set(spec.id, { order: 100, icon: "metadata", ...spec, plugin, report, seq: ++seq });
  host?.ctx.palette?.register(commands); // once: the palette ignores a repeated provider
  railKey = "";
  host?.ctx.update?.();
}

const current = () => {
  const s = host?.ctx.state;
  return s && s.selectedProject && s.selectedProject !== "all" ? s.selectedProject : null;
};

/** Seed or refresh availability from a GET /studio/plugins?project= reply. */
export function setAvailability(list) {
  if (!list?.project || !list.project_type) return;
  avail.set(list.project, { type: list.project_type, plugins: Object.fromEntries((list.plugins || []).map((p) => [p.id, {
    available: p.available !== false, reason: p.unavailable_reason || null, source: p.applicability?.source || "default", declared: p.project_types ?? null }])) });
}

export function invalidate(project = null) { if (project) avail.delete(project); else avail.clear(); railKey = ""; }

function ensure(project) {
  if (avail.has(project) || !host?.fetch) return;
  avail.set(project, "loading");
  host.fetch(project).then((list) => { setAvailability(list); if (avail.get(project) === "loading") avail.set(project, "failed"); })
    .catch((error) => { avail.set(project, "failed"); host.ctx.log?.("error", `plugin availability: ${error.message}`, "plugins"); })
    .finally(() => { railKey = ""; host.ctx.update?.(); });
}

/** Why a plugin (or one of its registrations) is not available for a project; null when it is. */
export function reason(plugin, projectTypes, project = current()) {
  if (!project || !plugin) return null;
  const info = avail.get(project);
  if (typeof info !== "object") return info === "failed" ? null : "Checking whether this plugin applies…";
  const p = info.plugins[plugin];
  if (!p) return null;
  if (!p.available) return p.reason || `Not for ${info.type} projects.`;
  // A browser-declared list is the plugin's default: it applies only when no setting overrides the default.
  if (projectTypes && p.source === "default" && p.declared == null && !projectTypes.includes(info.type)) {
    return `Not for ${info.type} projects: the plugin limits this to ${projectTypes.join(", ")}.`;
  }
  return null;
}

/** Unknown availability (still loading) hides contributions; a failed lookup shows them (the server still enforces). */
export function applies(plugin, projectTypes, project = current()) {
  return !reason(plugin, projectTypes, project);
}

function open(id) {
  const s = host.ctx.state;
  location.hash = routeHash(`plugin/${id}`, s.selectedProject, s.selectedTarget); // a history entry: Back returns
}

function visible() {
  return [...pages.values()].filter((s) => applies(s.plugin, s.projectTypes))
    .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));
}

export function renderRail() {
  const box = document.getElementById("rail-plugins");
  if (!box || !host) return;
  const s = host.ctx.state, list = visible();
  const key = JSON.stringify([list.map((p) => [p.id, p.title, p.icon]), s.selectedProject, s.selectedTarget]);
  if (key !== railKey) {
    railKey = key;
    box.hidden = !list.length;
    box.innerHTML = list.length ? `<div class="rail-head"><span class="rail-label" id="rail-plugins-label">Plugins</span></div><div class="rail-links" role="group" aria-labelledby="rail-plugins-label">${list.map((p) =>
      `<a href="${esc(routeHash(`plugin/${p.id}`, s.selectedProject, s.selectedTarget))}" data-page="${esc(p.id)}" title="${esc(p.title)}">${icon(p.icon)}<span>${esc(p.title)}</span></a>`).join("")}</div>` : "";
  }
  for (const a of box.querySelectorAll("a[data-page]")) {
    const on = s.view === "plugin" && s.pluginPage === a.dataset.page;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
}

function state(el, title, body, tone = "") {
  el.innerHTML = `<div class="plugin-page plugin-page-state"><h2 tabindex="-1">${esc(title)}</h2><div class="${tone ? `callout ${tone}` : "empty"}" ${tone === "fail" ? 'role="alert"' : 'role="status"'}>${body}</div></div>`;
}

function renderPage(el) {
  const ctx = host.ctx, id = ctx.state.pluginPage || "", project = current();
  const spec = pages.get(id);
  const info = project ? avail.get(project) : null;
  const why = spec ? reason(spec.plugin, spec.projectTypes, project) : null;
  const phase = !spec ? (ready ? "missing" : "loading") : !project ? "project" : typeof info !== "object" && info !== "failed" ? "checking" : why ? "unavailable" : "page";
  const key = JSON.stringify([id, project, phase, why, spec?.seq || 0]);
  if (el.dataset.pageKey === key) return;
  el.dataset.pageKey = key;
  el._pageAbort?.abort();
  loadCss("css/lazy.css");
  if (project) ensure(project);
  const title = spec?.title || id || "Plugin page";
  if (phase === "loading" || phase === "checking") {
    el.setAttribute("aria-busy", "true");
    return state(el, title, `<p>${phase === "loading" ? "Loading plugins…" : "Checking whether this plugin applies to the project…"}</p>`);
  }
  el.removeAttribute("aria-busy");
  if (phase === "missing") {
    return state(el, "Plugin page not found", `<p>No plugin provides the page <code>${esc(id)}</code>. The plugin may be turned off, not installed, not applicable here or failed to load: see Settings → Plugins (Diagnostics).</p>
      <p><a class="btn sm" href="${esc(routeHash("overview", ctx.state.selectedProject, ctx.state.selectedTarget))}">Back to Overview</a></p>`);
  }
  if (phase === "project") return state(el, title, "<p>Choose a project in the header to open this page.</p>");
  if (phase === "unavailable") {
    return state(el, title, `<p><b>Not available for this project.</b> ${esc(why)}</p><p class="small">Change where it applies in Studio settings → Plugins.</p>`, "warn");
  }
  const ac = new AbortController();
  el._pageAbort = ac;
  el.innerHTML = `<section class="plugin-page" aria-labelledby="plugin-page-title"><header class="plugin-page-head"><h2 id="plugin-page-title" tabindex="-1">${icon(spec.icon)} ${esc(spec.title)}</h2></header><div class="plugin-page-body"></div></section>`;
  const body = el.querySelector(".plugin-page-body");
  const fail = (error) => {
    if (ac.signal.aborted) return;
    const message = error?.message || String(error);
    body.innerHTML = `<div class="callout fail" role="alert"><p>${esc(spec.title)} could not render: ${esc(message)}</p><button type="button" class="btn sm" data-page-retry>Retry</button></div>`;
    body.querySelector("[data-page-retry]").onclick = () => { el.dataset.pageKey = ""; renderPage(el); };
    spec.report?.(message);
    ctx.log?.("error", `${spec.plugin || spec.id}: page: ${message}`, "plugins");
  };
  const page = { project, projectType: info?.type || null, signal: ac.signal, refresh: () => { el.dataset.pageKey = ""; renderPage(el); } };
  try { Promise.resolve(spec.render(body, page)).catch(fail); } catch (error) { fail(error); }
}
