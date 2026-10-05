// Server folder browser for Import → Connect (server mode only).
//
// The browser's own folder picker opens folders on the viewer's computer, which
// is wrong over an SSH tunnel. This panel lists folders on the machine running
// `alfrd serve` (GET /api/studio/fs/list, loopback + CSRF) and connects ALFRD
// projects found there.

import { esc, icon } from "../utils/dom.js";
import { pathCrumbs, projectEntries } from "../data/paths.js";
import { server } from "../data/server.js";

/**
 * Mount the browser into `host`.
 * opts: { list(path, {hidden}) → Promise<listing>, connect(paths[]) → Promise, use(path), close(), start?,
 *         allowDefault? (folders without alfrd.yaml can be connected with the default manifest),
 *         mkdir?, session? (New folder; default: server's),
 *         select? (pick a folder only: no Connect actions; Cancel + Use this folder) }
 * Returns { destroy() }.
 */
export function mountFolderBrowser(host, opts) {
  let listing = null;
  let good = ""; // last folder that listed: an invalid typed path offers a way back
  let hidden = false;
  let busy = false;
  let create, loading; // New folder (folder_create.js)
  const canCreate = () => listing?.writable === true && (opts.session || server.session)?.mutations_enabled === true;

  host.hidden = false;
  host.classList.add("fsb");
  host.setAttribute("role", "region");
  host.setAttribute("aria-label", "Server folders");

  const render = (error = "") => {
    const crumbs = listing ? pathCrumbs(listing.path) : [];
    const projects = projectEntries(listing);
    const rows = (listing?.entries || []).map((e, i) => {
      const badge = e.is_project
        ? `<span class="badge tone-run" title="Has alfrd.yaml">${icon("project")}${esc(e.manifest_name || "ALFRD project")}</span>`
        : e.is_ms ? `<span class="chip">MS</span>` : "";
      const openBtn = e.is_ms
        ? `<span class="fsb-name muted">${icon("database")}<span>${esc(e.name)}</span></span>`
        : `<button type="button" class="fsb-name" data-fsb-open="${i}" title="Open ${esc(e.path)}">${icon("folder")}<span>${esc(e.name)}</span></button>`;
      const connect = e.is_project && !opts.select ? `<button type="button" class="btn sm primary" data-fsb-connect="${i}">Connect</button>` : "";
      return `<li class="fsb-row${e.is_project ? " is-project" : ""}${e.is_ms ? " is-ms" : ""}">${openBtn}${badge}<span class="grow"></span>${connect}</li>`;
    }).join("");
    host.innerHTML = `<p class="hint">${icon("server")} Folders on the ALFRD server</p>
      <div class="fsb-bar row gap">
        <button type="button" class="icon-btn" data-fsb-up ${listing?.parent ? "" : "disabled"} title="Parent folder (Backspace)" aria-label="Parent folder">↑</button>
        <nav class="fsb-crumbs grow" aria-label="Current server folder">${crumbs.map((c, i) => `<button type="button" class="link-btn mono" data-fsb-crumb="${i}">${esc(c.name)}</button>`).join('<span class="muted">/</span>')}</nav>
        ${canCreate() ? `<button type="button" class="btn sm" data-fsb-new>${icon("plus")} New folder</button>` : ""}
        <label class="check small"><input type="checkbox" data-fsb-hidden ${hidden ? "checked" : ""}> hidden</label>
        <button type="button" class="icon-btn" data-fsb-close title="Close (Esc)" aria-label="Close folder browser">${icon("close")}</button>
      </div>
      ${create?.html() || ""}
      ${listing?.is_project ? `<p class="hint">${icon("info")} This folder is itself an ALFRD project${listing.manifest_name ? ` (<b>${esc(listing.manifest_name)}</b>)` : ""}.</p>` : ""}
      ${error ? `<p class="callout warn">${icon("alert")}<span>${esc(error)}</span>${listing ? "" : ` <button type="button" class="btn sm" data-fsb-back>${good ? "Back to the last folder" : "Go to the default folder"}</button>`}</p>` : ""}
      <ul class="fsb-list" role="list">${rows || (listing && !error ? `<li class="muted small fsb-empty">No sub-folders.</li>` : "")}</ul>
      ${listing?.truncated ? `<p class="hint warn">Showing the first ${listing.entries.length} folders only.</p>` : ""}
      <div class="fsb-foot row gap wrap">${opts.select ? `<span class="grow"></span><button type="button" class="btn" data-fsb-close>Cancel</button><button type="button" class="btn primary" data-fsb-use ${listing ? "" : "disabled"}>Use this folder</button></div>` : `
        ${projects.length > 1 || (projects.length && !listing?.is_project) ? `<button type="button" class="btn" data-fsb-all>${icon("link")} Connect all projects here (${projects.length})</button>` : ""}
        <span class="grow"></span>
        ${listing?.is_project ? `<button type="button" class="btn primary" data-fsb-connect-here>Connect this project</button>`
          : listing && opts.allowDefault ? `<button type="button" class="btn" data-fsb-connect-here title="No alfrd.yaml here: the built-in default is used (name = folder name). Save Project settings later to write a local alfrd.yaml.">Connect this folder <span class="muted small">(default alfrd.yaml)</span></button>` : ""}
        <button type="button" class="btn" data-fsb-use ${listing ? "" : "disabled"}>Use this folder</button>
      </div>`}`;
    create?.after();
  };

  const focusFirst = () => {
    const el = host.querySelector("[data-fsb-open], [data-fsb-connect], [data-fsb-up]:not([disabled])");
    el?.focus();
  };

  const go = async (path, { focus = true } = {}) => {
    if (busy) return;
    busy = true;
    host.setAttribute("aria-busy", "true");
    try {
      listing = await opts.list(path, { hidden });
      good = listing.path;
      render();
      if (focus) focusFirst();
    } catch (error) {
      listing = null; // "Use this folder" never takes a folder that did not list
      render(error.message || String(error));
    } finally {
      busy = false;
      host.removeAttribute("aria-busy");
    }
  };

  const connect = async (paths) => {
    const buttons = host.querySelectorAll("button");
    buttons.forEach((b) => { b.disabled = true; });
    try {
      await opts.connect(paths);
    } finally {
      if (host.isConnected) render();
    }
  };

  const newFolder = () => (loading ||= import("./folder_create.js")).then((m) => {
    create ||= m.folderCreate(host, { ...opts, get listing() { return listing; }, go, render, canCreate });
    create.open();
  }, (error) => {
    loading = null;
    render(`Couldn’t create the folder. ${error.message}`);
  });

  const onClick = (e) => {
    const t = e.target.closest("button, input");
    if (!t || !host.contains(t) || create?.click(t)) return;
    const entries = listing?.entries || [];
    if (t.matches("[data-fsb-open]")) go(entries[Number(t.dataset.fsbOpen)].path);
    else if (t.matches("[data-fsb-connect]")) connect([entries[Number(t.dataset.fsbConnect)].path]);
    else if (t.matches("[data-fsb-crumb]")) go(pathCrumbs(listing.path)[Number(t.dataset.fsbCrumb)].path);
    else if (t.matches("[data-fsb-up]") && listing?.parent) go(listing.parent);
    else if (t.matches("[data-fsb-all]")) connect(projectEntries(listing).map((p) => p.path));
    else if (t.matches("[data-fsb-connect-here]")) connect([listing.path]);
    else if (t.matches("[data-fsb-use]") && listing) opts.use(listing.path);
    else if (t.matches("[data-fsb-new]")) newFolder();
    else if (t.matches("[data-fsb-back]")) go(good);
    else if (t.matches("[data-fsb-close]")) opts.close();
  };
  const onChange = (e) => {
    if (e.target.matches("[data-fsb-hidden]")) {
      hidden = e.target.checked;
      go(listing?.path || opts.start || "", { focus: false });
    }
  };
  const onKey = (e) => {
    if (create?.key(e)) return;
    if (e.key === "Escape") {
      e.stopPropagation();
      opts.close();
      return;
    }
    const inField = e.target.matches("input[type=text], input:not([type])");
    if (!inField && (e.key === "Backspace" || (e.altKey && e.key === "ArrowUp"))) {
      e.preventDefault();
      if (listing?.parent) go(listing.parent);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const rows = Array.from(host.querySelectorAll("[data-fsb-open], .fsb-row.is-ms .fsb-name"));
      const focusable = Array.from(host.querySelectorAll("[data-fsb-open]"));
      if (!focusable.length || rows.length === 0) return;
      e.preventDefault();
      const i = focusable.indexOf(document.activeElement);
      const next = e.key === "ArrowDown" ? Math.min(focusable.length - 1, i + 1) : Math.max(0, i - 1);
      focusable[i < 0 ? 0 : next].focus();
    }
  };
  host.addEventListener("click", onClick);
  host.addEventListener("change", onChange);
  host.addEventListener("keydown", onKey);
  render();
  go(opts.start || "");

  return {
    reload: () => go(listing?.path || opts.start || "", { focus: false }),
    destroy() {
      create?.destroy();
      host.removeEventListener("click", onClick);
      host.removeEventListener("change", onChange);
      host.removeEventListener("keydown", onKey);
      host.innerHTML = "";
      host.hidden = true;
    },
  };
}
