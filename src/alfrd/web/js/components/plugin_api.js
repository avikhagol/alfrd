// Full-trust plugin activation. Loaded only by the server-mode boot hook or Settings.
// DOMPurify is preloaded before activate() so api.sanitize(html) is synchronous.
import { server } from "../data/server.js";
import { registerViewer, setConverters, convertFile } from "./viewers.js";
import { registerPanel } from "./panels.js";

import { registerProjectSection, registerOverviewAction } from "./project_sections.js";
import { dialog, confirm } from "./plugin_dialogs.js";
import { bindPages, registerPage, setAvailability, pagesReady, applies } from "./plugin_pages.js";

export const pluginErrors = [];
const activated = new Set();
const reported = new Set();

/** Same-origin API paths only; mutations retain the host's loopback and CSRF checks. */
export async function fetchJSON(path, { method = "GET", body } = {}) {
  if (!/^\/(?:api\/)?(?!\/)/.test(path) || path.includes("\\")) throw new TypeError("Expected a same-origin API path");
  path = path.replace(/^\/api\//, "/");
  if (method.toUpperCase() !== "GET") return server.mutate(path, body, method.toUpperCase());
  const response = await server.request(`/api${path}`, { headers: { Accept: "application/json" }, cache: "no-store" });
  const result = await response.json();
  if (!response.ok) throw new Error(result?.error?.message || `${response.status} ${response.statusText}`);
  return result;
}

export async function action(project, pluginId, actionId, payload = {}) {
  const parts = [project, pluginId, actionId].map(encodeURIComponent);
  const result = await fetchJSON(`/studio/projects/${parts[0]}/plugins/${parts[1]}/actions/${parts[2]}`, { method: "POST", body: payload });
  if (!result.ok) throw new Error(result.error || "The action failed.");
  return result.data;
}

/** The plugin list; with a project it also says which plugins apply to it (available, unavailable_reason). */
export const fetchPlugins = (project = null) => fetchJSON(`/studio/plugins${project && project !== "all" ? `?project=${encodeURIComponent(project)}` : ""}`);
const projectPath = (project, rest) => `/studio/projects/${encodeURIComponent(project)}/${rest}`;

export function reportServerErrors(list, ctx) {
  for (const p of list.plugins || []) {
    if (["ok", "disabled"].includes(p.status)) continue;
    const message = p.error || (p.missing_bin?.length ? `missing binary: ${p.missing_bin.join(", ")}` : p.status);
    const key = `${p.id}:${p.status}:${message}`;
    if (!reported.has(key)) { reported.add(key); ctx.log("error", `${p.id}: ${message}`, "plugins"); }
  }
}

function stylesheet(href, attribute, value = "") {
  let link = [...document.querySelectorAll(`link[${attribute}]`)].find((l) => l.getAttribute(attribute) === value);
  if (!link) {
    link = document.createElement("link");
    link.rel = "stylesheet";
    link.setAttribute(attribute, value);
    document.head.appendChild(link);
  }
  link.href = href;
  return link;
}

/** Refresh the built-in sheet and apply the chosen plugin sheet. Returns a rollback. */
export function applyTheme(list, id) {
  const base = document.querySelector('link[href^="theme.css"],link[href^="/studio/theme.css"]');
  const href = base?.getAttribute("href");
  const previous = [...document.querySelectorAll("link[data-plugin-theme]")].map((l) => l.getAttribute("href"));
  document.querySelectorAll("link[data-plugin-theme]").forEach((l) => l.remove());
  if (base) base.href = `theme.css?v=${Date.now()}`;
  const plugin = (list.plugins || []).find((p) => p.id === id && p.active && p.theme_css);
  if (plugin) stylesheet(plugin.theme_css, "data-plugin-theme");
  return () => {
    if (base && href != null) base.href = href;
    document.querySelectorAll("link[data-plugin-theme]").forEach((l) => l.remove());
    previous.forEach((url) => stylesheet(url, "data-plugin-theme"));
  };
}

/** The browser API; converters resolve to a Blob, caller owns any object URL it creates.
 * convert(path, to, project?) defaults to the currently selected project. `plugin` (the plugin id) tags
 * sections, pages, overview actions and commands so they follow the plugin's availability per project.
 */
export function api(ctx, purify, plugin = null) {
  const commands = [];
  let provider = false;
  const once = (id, title, detail) => {
    if (!pluginErrors.some((error) => error.id === id && error.message === detail)) pluginErrors.push({ id, title, message: detail });
  };
  return {
    registerViewer, registerPanel, action,
    registerOverviewAction: (spec) => registerOverviewAction({ ...spec, plugin }, (message) => {
      pluginErrors.push({ id: spec.id, title: spec.title || spec.id, message: `overview action: ${message}` });
    }),
    menu: (...args) => ctx.menu(...args),
    registerProjectSection(spec) {
      registerProjectSection(spec && { ...spec, plugin }, (message) => once(spec.id, spec.title, `project section: ${message}`));
    },
    /** A page in the sidebar's Plugins group (#/plugin/<id>): render(box, {project, projectType, signal, refresh}). */
    registerPage(spec) {
      registerPage(spec, plugin || spec?.id, (message) => once(spec.id, spec.title, `page: ${message}`));
    },
    /** Measurement Sets the project's alfrd.yaml declares (artifacts: kind directory, pattern ending in .ms). */
    measurementSets: (project) => fetchJSON(projectPath(project, "measurement-sets")),
    /** One folder of the project (confined to its root; .ms folders are leaves). */
    listFiles: (project, path = "") => fetchJSON(projectPath(project, `files?path=${encodeURIComponent(path)}`)),
    /** Mount the project file explorer in box; opts: select(entry, listing), selectable(entry), selectLabel, start. */
    async fileExplorer(box, { project, ...opts }) {
      const { mountExplorer } = await import("./file_explorer.js");
      return mountExplorer(box, { list: (path) => fetchJSON(projectPath(project, `files?path=${encodeURIComponent(path)}`)), ...opts });
    },
    dialog: (options) => dialog(ctx, options),
    confirm: (text, options) => confirm(ctx, text, options),
    registerCommand(command) {
      if (!command?.id || !command.label || typeof command.run !== "function") throw new TypeError("registerCommand({id,label,run})");
      const index = commands.findIndex((c) => c.id === command.id);
      if (index >= 0) commands.splice(index, 1);
      commands.push({ group: "Plugins", icon: "metadata", ...command, plugin });
      if (!provider) { ctx.palette.register(() => commands.filter((c) => applies(c.plugin, c.projectTypes, c.plugin && current(ctx)))); provider = true; }
    },
    toast: (...args) => ctx.toast(...args), fetchJSON,
    project: () => ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : ctx.target()?.project || null,
    /** Target names already loaded for a project (no request). */
    targets: (project) => (ctx.state.targets || []).filter((t) => t.project === project).map((t) => t.name).filter(Boolean),
    projectName: (project) => ctx.projectName?.(project) || project,
    sanitize: (html) => purify.sanitize(html, { USE_PROFILES: { html: true } }),
    convert: (path, to = "pdf", project = ctx.state.selectedProject) => convertFile(project, path, to),
  };
}

const current = (ctx) => ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : ctx.target?.()?.project || null;

export async function activateAll(list, ctx) {
  reportServerErrors(list, ctx);
  bindPages(ctx, (project) => fetchPlugins(project));
  setAvailability(list);
  setConverters((list.plugins || []).filter((p) => p.active).flatMap((p) => (p.converters || []).map((c) => ({ ...c, plugin: p.id, title: p.title }))));
  const theme = (list.plugins || []).find((p) => p.id === list.theme && p.active && p.theme_css);
  if (theme) stylesheet(theme.theme_css, "data-plugin-theme");
  let purify;
  for (const p of list.plugins || []) {
    if (!p.active || !p.enabled || activated.has(p.id)) continue;
    try {
      if (p.web?.css) stylesheet(p.web.css, "data-plugin-style", p.id);
      if (p.web?.js) {
        purify ||= (await import("../vendor/purify.es.mjs")).default;
        const mod = await import(p.web.js);
        if (typeof mod.activate !== "function") throw new TypeError("Plugin must export activate(api)");
        await mod.activate(api(ctx, purify, p.id));
      }
      activated.add(p.id);
    } catch (error) {
      const message = error.message || String(error);
      pluginErrors.push({ id: p.id, title: p.title || p.id, message });
      ctx.log("error", `${p.id}: browser: ${message}`, "plugins");
    }
  }
  pagesReady();
  ctx.update();
}
