// Settings-only installation UI. No remote runtime assets; all copy is escaped.
import { $, esc } from "../utils/dom.js";
import { server } from "../data/server.js";
import { installDialog, button, kinds } from "./plugins_install_dialog.js";
import { currentJob, listeners, startJob, renderJob, dismissJob, hideRestart, restartServer } from "./plugin_jobs.js";

const states = new WeakMap();
let resume = null;

export function filterCatalog(catalog, query = "", kind = "") {
  return (catalog.plugins || []).filter(p => `${p.title} ${p.id} ${p.description}`.toLowerCase().includes(query.toLowerCase()) && (!kind || p.kinds?.includes(kind)))
    .sort((a, b) => Number(Boolean(b.update_available)) - Number(Boolean(a.update_available)) || (a.title || a.id).localeCompare(b.title || b.id));
}

export function renderCatalog(catalog, query = "", kind = "") {
  const advanced = button("Install from source…", "data-source", "ghost");
  if (!catalog.catalog_url) return `<div class="empty"><b>No plugin catalog is set.</b><p>ALFRD has no default catalog, so you choose whom to trust.</p><pre class="mono">${esc('{"catalog_url": "https://…/index.json"}')}</pre><p>Add this to <code>~/.config/alfrd/plugins.json</code> (or a <code>file://</code> path), then Refresh.</p>${advanced} <a href="https://github.com/avikhagol/alfrd/blob/HEAD/docs/plugins.md#catalog" target="_blank" rel="noopener noreferrer">Learn more</a></div>`;
  if (catalog.error && !catalog.plugins?.length) return `<p class="callout warn">${esc(catalog.error)} ${button("Try again", "data-catalog-refresh")}</p>${advanced}`;
  const matches = filterCatalog(catalog, query, kind);
  return `<div class="plug-browse-bar"><input type="search" class="input" aria-label="Search plugins" placeholder="Search plugins" value="${esc(query)}" data-search><select class="input" aria-label="Plugin kind" data-kind>${["", "viewer", "panel", "converter", "theme", "cli"].map(k => `<option value="${k}" ${k === kind ? "selected" : ""}>${k ? k[0].toUpperCase() + k.slice(1) : "All kinds"}</option>`).join("")}</select></div><p class="muted small">${esc(catalog.name || "Catalog")} · updated ${catalog.fetched_at ? esc(new Date(catalog.fetched_at).toLocaleString()) : "unknown"} ${catalog.stale ? `<span class="badge tone-warn" title="${esc(catalog.error)}">Offline copy</span>` : ""}</p>
    ${matches.length ? `<ul class="plug-list">${matches.map(p => `<li class="plug-row"><span class="plug-letter" aria-hidden="true">${esc((p.title || p.id)[0].toUpperCase())}</span><div><b>${esc(p.title || p.id)}</b> <small class="muted">v${esc(p.latest || "")}</small><div class="plug-kinds">${kinds(p)}</div><small class="muted plug-description">${esc(p.description || "")}</small>${/^https?:\/\//i.test(p.homepage || "") ? `<a class="small" href="${esc(p.homepage)}" target="_blank" rel="noopener noreferrer">Homepage ↗</a>` : ""}</div><div class="plug-actions">${!p.compatible ? `<span class="badge tone-muted" title="Requires alfrd API ${esc(p.alfrd_api)}">Needs a newer alfrd</span>` : p.update_available ? `<span class="badge tone-accent">Update ${esc(p.latest)}</span>${button("Update…", `data-install="${esc(p.id)}" data-update`)}` : p.installed ? '<span class="badge tone-ok">Installed</span>' : button("Install…", `data-install="${esc(p.id)}"`, "primary")}${p.missing_bin?.length ? `<span class="badge tone-warn">Needs ${esc(p.missing_bin.join(", "))}</span>` : ""}</div></li>`).join("")}</ul>` : `<div class="empty">No plugins match “${esc(query)}”. ${button("Clear search", "data-clear")}</div>`}<p>${advanced}</p>`;
}

async function json(url) {
  const response = await server.request(url, { cache: "no-store" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || `HTTP ${response.status}`);
  return body;
}

export function enhance(ctx, panel, list, refresh, restartTitles) {
  let state = states.get(panel);
  if (!state) {
    state = { tab: "installed", query: "", kind: "", catalog: null, ...resume };
    resume = null;
    states.set(panel, state);
    const listener = () => {
      if (!panel.isConnected) { listeners.delete(listener); return; }
      drawJob();
      if (currentJob?.job.status === "done" && state.finished !== currentJob.job.id) { state.finished = currentJob.job.id; load().finally(refresh); }
    };
    listener.visible = () => panel.isConnected && panel.getClientRects().length > 0;
    listeners.add(listener);
    state.listener = listener;
  }
  $("[data-restart-placeholder]", panel)?.remove();
  const tools = $("[data-plug-tools]", panel), installed = $("[data-plug-installed]", panel);
  if (!tools || !installed) return { click() {} };
  tools.innerHTML = `<div data-job></div><div data-restart></div><div class="plug-tabs" role="tablist" aria-label="Plugins">${["installed", "browse"].map(t => button(t === "installed" ? "Installed" : "Browse", `role="tab" id="plug-tab-${t}" data-tab="${t}" aria-selected="${state.tab === t}" tabindex="${state.tab === t ? 0 : -1}" aria-controls="plug-panel-${t}"`)).join("")}</div><div id="plug-panel-browse" role="tabpanel" aria-labelledby="plug-tab-browse" data-browse></div>`;
  installed.id = "plug-panel-installed"; installed.setAttribute("role", "tabpanel"); installed.setAttribute("aria-labelledby", "plug-tab-installed");
  const browsePanel = $("[data-browse]", panel);
  function visibility() {
    installed.hidden = state.tab !== "installed"; browsePanel.hidden = state.tab !== "browse";
    tools.querySelectorAll("[data-tab]").forEach(b => { b.setAttribute("aria-selected", String(b.dataset.tab === state.tab)); b.tabIndex = b.dataset.tab === state.tab ? 0 : -1; });
  }
  const drawBrowse = () => {
    const focused = browsePanel.contains(document.activeElement) && document.activeElement.matches("[data-search]");
    const cursor = focused ? document.activeElement.selectionStart : null;
    browsePanel.innerHTML = state.catalog ? renderCatalog(state.catalog, state.query, state.kind) : '<p class="muted">Loading catalog…</p>';
    if (focused) { const input = $("[data-search]", browsePanel); input?.focus(); input?.setSelectionRange(cursor, cursor); }
  };
  const load = async (force = false) => {
    try { state.catalog = await json(`/api/studio/plugins/catalog${force ? "?refresh=1" : ""}`); drawBrowse(); }
    catch (error) { browsePanel.innerHTML = `<p class="callout warn">${esc(error.message)} ${button("Try again", "data-catalog-refresh")}</p>`; }
  };
  const drawJob = () => renderJob(panel, restartTitles);
  const switchTab = async tab => { state.tab = tab; visibility(); if (tab === "browse") { drawBrowse(); await load(); } };
  const returnToSettings = () => {
    resume = { tab: state.tab, query: state.query, kind: state.kind, catalog: state.catalog };
    document.querySelector("#btn-settings")?.click();
  };
  const dialog = (p, action) => installDialog(ctx, state.catalog || { gui_install: list.gui_install }, p, action, returnToSettings);
  async function click(e) {
    const b = e.target.closest("[data-plug-open],[data-tab],[data-source],[data-install],[data-plug-more],[data-clear],[data-catalog-refresh],[data-dismiss],[data-later],[data-restart-now]");
    if (!b) return false;
    if (b.matches("[data-plug-open]")) await switchTab("browse");
    else if (b.dataset.tab) await switchTab(b.dataset.tab);
    else if (b.matches("[data-catalog-refresh]")) await load(true);
    else if (b.matches("[data-clear]")) { state.query = state.kind = ""; drawBrowse(); }
    else if (b.matches("[data-dismiss]")) { dismissJob(); drawJob(); }
    else if (b.matches("[data-later]")) { hideRestart(); drawJob(); }
    else if (b.matches("[data-restart-now]")) {
      if (ctx.isDirty && ctx.projects().some(p => ctx.isDirty(p.id)) && !confirm("Unsaved changes will be lost. Restart anyway?")) return true;
      b.disabled = true; b.textContent = "Restarting…";
      try { await restartServer(); } catch (error) { ctx.toast(error.message, "warn"); b.disabled = false; b.textContent = "Restart now"; }
    } else {
      if (!state.catalog) { await load(); if (!state.catalog) return true; }
      if (b.dataset.plugMore) {
        const record = list.plugins.find(p => p.id === b.dataset.plugMore);
        const p = state.catalog.plugins.find(p => p.id === record.id) || record;
        ctx.menu(b, [{ label: "Update", run: () => dialog(p, "update") }, { label: "Remove…", run: () => dialog(p, "remove") }]);
      } else dialog(state.catalog.plugins.find(p => p.id === b.dataset.install), b.hasAttribute("data-update") ? "update" : "install");
    }
    return true;
  }
  // Delegate alongside the existing Installed handlers, without replacing them.
  if (state.capture) panel.removeEventListener("click", state.capture);
  state.capture = e => {
    if (e.target.closest("[data-plug-open],[data-plug-more]")) return; // handled by settings_plugins
    if (e.target.closest("[data-plug-refresh]") && state.tab === "browse") { e.stopImmediatePropagation(); load(true); return; }
    click(e).catch(error => ctx.toast(error.message, "warn"));
  };
  panel.addEventListener("click", state.capture, true);
  tools.onkeydown = e => {
    if (!e.target.matches("[data-tab]") || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault(); const tab = e.key === "Home" ? "installed" : e.key === "End" ? "browse" : state.tab === "installed" ? "browse" : "installed";
    switchTab(tab); tools.querySelector(`[data-tab="${tab}"]`)?.focus();
  };
  browsePanel.oninput = e => { if (!e.target.matches("[data-search]")) return; state.query = e.target.value; clearTimeout(state.searchTimer); state.searchTimer = setTimeout(drawBrowse, 150); };
  browsePanel.onchange = e => { if (e.target.matches("[data-kind]")) { state.kind = e.target.value; drawBrowse(); } };
  visibility(); drawBrowse(); drawJob();
  if (server.pluginJob || list.job) startJob(server.pluginJob || list.job, ctx);
  if (!state.event) {
    state.event = e => { if (!panel.isConnected) { window.removeEventListener("plugin-job", state.event); listeners.delete(state.listener); return; } startJob(e.detail, ctx); };
    window.addEventListener("plugin-job", state.event);
  }
  return { click };
}
