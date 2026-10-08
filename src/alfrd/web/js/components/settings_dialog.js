// Studio settings (gear button): four tabs, one visible at a time — Projects,
// Preferences, Browser data, Server. Loaded with import() on first open.
// `app` carries the shell functions the dialog drives (live, quit, project list …).

import { $, esc, icon, bytes, storage, loadUi, saveUi, resetUi, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

const TABS = [
  { id: "projects", label: "Projects", icon: "database" },
  { id: "prefs", label: "Preferences", icon: "gear" },
  { id: "data", label: "Browser data", icon: "reset" },
  { id: "server", label: "Server", icon: "power" },
];
const ui = loadUi("settings", { section: "projects" });

const VIS_STATES = [
  { id: "opened", label: "Opened", icon: "play", tone: "tone-run", hint: "shown and selected when the Studio loads" },
  { id: "shown", label: "Shown", icon: "check", tone: "", hint: "listed in this Studio" },
  { id: "hidden", label: "Hidden", icon: "minus", tone: "tone-muted", hint: "remembered, not listed" },
];

function visBadge(current, key, label, canWrite) {
  const st = VIS_STATES.find((s) => s.id === current);
  if (!canWrite) return `<span class="badge ${st.tone}">${st.id}</span>`;
  return `<button type="button" class="badge vis-badge ${st.tone}" data-vis="${esc(key)}" data-label="${esc(label)}" data-current="${st.id}" title="Change: opened / shown / hidden" aria-haspopup="menu">${st.id}${icon("chevron")}</button>`;
}

function shortPath(path, max = 56) {
  const text = String(path || "");
  if (text.length <= max) return text;
  const parts = text.split("/");
  let tail = parts.pop();
  while (parts.length > 2 && (parts.at(-1).length + tail.length + 1) < max - 12) tail = `${parts.pop()}/${tail}`;
  const head = parts.slice(0, 2).join("/");
  return `${head}/…/${tail}`.length < text.length ? `${head}/…/${tail}` : text;
}

export function openSettings(ctx, app) {
  const { state } = ctx;
  const sess = server.session || {};
  const serverMode = state.mode === "server";
  const canWrite = Boolean(sess.mutations_enabled);
  const liveNote = serverMode
    ? (sess.live?.enabled === false ? "Off on this server (<code>alfrd serve --live-interval 0</code>)." : `The server checks every ${sess.live?.interval ?? 2} s while busy, ${sess.live?.idle ?? 5} s when idle.`)
    : "The remembered folder is checked every 5–30 s.";
  const section = TABS.some((t) => t.id === ui.section) ? ui.section : "projects";
  loadCss("css/lazy.css");
  const panels = {
    projects: serverMode ? `
      <div class="set-sec-h"><h3>${icon("database")} Projects</h3><span class="grow"></span><button class="btn sm" id="set-create" ${canWrite ? "" : "disabled"}>${icon("plus")} New project</button><button class="btn sm" id="set-open" ${canWrite && server.canBrowse() ? "" : "disabled"}>${icon("folder")} Open project…</button>${canWrite ? "" : '<span class="muted small">Project creation needs a browser on the server machine.</span>'}<button class="btn sm" id="set-rediscover" hidden ${canWrite ? "" : "disabled"} title="Connect projects forgotten since the server started">${icon("sync")} Rediscover</button></div>
      <p class="muted small">How this Studio lists each project: <b>opened</b> (selected on load), <b>shown</b> or <b>hidden</b>. Remove → <b>Forget</b> drops a project and its runs from the runtime database and keeps its files; <b>Delete permanently</b> also deletes ALFRD's files (alfrd.yaml, .alfrd/, …) after you type its name, or the whole folder when you tick <i>Delete all files and folders</i>.</p>
      <div class="field"><label for="set-filter">Filter projects</label><div class="row gap"><label class="search grow">${icon("search")}<input id="set-filter" type="search" placeholder="Search name or path…"></label><button type="button" class="icon-btn" id="set-filter-clear" aria-label="Clear project filter" title="Clear project filter" hidden>${icon("close")}</button></div></div>
      <p class="muted small tabular" id="set-filter-count" role="status" aria-live="polite"></p>
      <ul class="set-projects" id="set-projects"><li class="muted small">Loading projects…</li></ul>`
      : `<p class="muted">Project management is available when connected to an ALFRD server.</p>`,
    prefs: `<div class="set-prefs">
        <label class="check"><input type="checkbox" id="set-live" ${app.live.enabled ? "checked" : ""}> <span><b>Live updates</b><small>Follow the folder and open logs without Re-scan. ${liveNote} Paused while the tab is hidden.</small></span></label>
        <label class="field"><span>Rows per page</span><select id="set-page" class="input sm">${[10, 25, 50, 100].map((n) => `<option ${state.prefs.pageSize === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      </div>`,
    data: `<p class="muted small">Filters, folded panels, zoom, selections, folder attachments and unsaved task drafts are remembered in this browser. <b class="tabular">${bytes(storage.size())}</b> saved.</p>
      <div class="row gap wrap"><button class="btn sm" id="set-reset-ui">${icon("reset")} Reset view state</button><button class="btn sm danger" id="set-clear" title="Also removes imported data, notes and unsaved task drafts">${icon("trash")} Clear all saved data</button></div>
      <p class="muted small">Reset view state keeps your drafts. Clear all saved data also removes unsaved task drafts and notes.</p>`,
    server: serverMode ? `<div class="set-facts">
        <div><span>Mode</span><b>alfrd ${esc(sess.version || "")}</b><small>runtime ${sess.runtime_enabled ? "on" : "off"} · changes ${canWrite ? "allowed (this machine)" : "read-only"}</small></div>
        <div><span>Data</span><b>${esc(state.source)}</b></div>
      </div>
      <div class="row gap wrap"><button class="btn sm danger" id="set-quit" ${sess.can_quit ? "" : "disabled"}>${icon("power")} Quit alfrd serve</button>
        <span class="muted small">${sess.can_quit ? "Same as Ctrl+C in its terminal." : "Only from this machine, for a server started by <code>alfrd serve</code> (not <code>--debug</code>)."}</span></div>`
      : `<p class="muted">Browser-only mode. No ALFRD server is connected.</p><div class="set-facts"><div><span>Data</span><b>${esc(state.source)}</b></div></div>`,
  };
  ctx.modal(`
    <header class="modal-h"><h2>Studio settings</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="seg set-tabs" role="tablist" aria-label="Settings sections">${TABS.map((t) => `<button type="button" role="tab" id="set-tab-${t.id}" aria-controls="set-panel-${t.id}" aria-selected="${t.id === section}" tabindex="${t.id === section ? 0 : -1}" class="${t.id === section ? "on" : ""}" data-tab="${t.id}">${icon(t.icon)}<span>${t.label}</span></button>`).join("")}</div>
    <div class="modal-b set set-panels">${TABS.map((t) => `<section class="set-panel" role="tabpanel" id="set-panel-${t.id}" aria-labelledby="set-tab-${t.id}" ${t.id === section ? "" : "hidden"}>${panels[t.id]}</section>`).join("")}</div>`, (root, close) => {
    const tabs = [...root.querySelectorAll("[role=tab]")];
    const select = (id) => {
      tabs.forEach((b) => { const on = b.dataset.tab === id; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1; });
      root.querySelectorAll("[role=tabpanel]").forEach((p) => { p.hidden = p.id !== `set-panel-${id}`; });
      ui.section = id;
      saveUi("settings", ui, ["section"]);
    };
    const bar = $(".set-tabs", root);
    bar.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) select(b.dataset.tab); });
    // Manual activation: arrows/Home/End move focus; Enter/Space (native button click) selects.
    bar.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(document.activeElement);
      const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (i < 0 || to === undefined) return;
      e.preventDefault();
      tabs[(to + tabs.length) % tabs.length].focus();
    });
    if (serverMode) drawProjects(ctx, app, root);
    $("#set-create", root)?.addEventListener("click", () => { close(); app.newProject(); });
    $("#set-open", root)?.addEventListener("click", () => { close(); app.openProject(); });
    $("#set-quit", root)?.addEventListener("click", () => { close(); app.quitServer(); });
    $("#set-live", root).addEventListener("change", (e) => app.setLive(e.target.checked));
    $("#set-page", root).addEventListener("change", (e) => { ctx.setPrefs({ pageSize: Number(e.target.value) }); ctx.update(); });
    $("#set-reset-ui", root).addEventListener("click", () => {
      resetUi();
      ctx.toast("View state reset — reloading", "ok");
      setTimeout(() => location.reload(), 400);
    });
    $("#set-clear", root).addEventListener("click", async () => {
      if (!confirm("Clear all saved Studio data?\n\nUnsaved task drafts and notes will be removed, with imported data, filters and folder attachments. Project files are not touched.")) return;
      // Demo mode stays on this page: the tab's in-memory drafts must go as well.
      try { (await import("./agent_dialog.js")).forgetTaskDrafts(); } catch { /* never loaded: no drafts in memory */ }
      app.resetAttachments();
      storage.clear();
      app.forgetFolderHandles();
      state.folders = {};
      state.notes = {};
      ctx.toast("Saved Studio data cleared", "ok");
      close();
      if (state.demoEnabled) app.loadDemo({ quiet: true });
      else location.reload();
    });
  }, "set-dlg");
  queueMicrotask(() => $(`#set-tab-${section}`)?.focus());
}

/** Projects tab: fetched once per draw; the filter works on that list and keeps its input mounted. */
async function drawProjects(ctx, app, root) {
  const table = $("#set-projects", root);
  if (!table) return;
  const filter = $("#set-filter", root);
  const { state } = ctx;
  const canWrite = server.session?.mutations_enabled;
  let list = [];
  let lost = [];
  try {
    list = await server.listProjects();
  } catch (error) {
    table.innerHTML = `<li class="callout fail small" role="alert">Couldn’t load projects. <span class="muted">${esc(error.message)}</span> <button class="btn sm" type="button" id="set-retry">Retry</button></li>`;
    $("#set-retry", root).onclick = () => { table.innerHTML = '<li class="muted small">Loading projects…</li>'; drawProjects(ctx, app, root); };
    return;
  }
  try { lost = await server.rediscoverable(); } catch { /* older server */ }
  const scope = Array.isArray(server.session?.projects) ? new Set(server.session.projects) : null;
  const visOf = (key) => (key === server.session?.default_project ? "opened" : !scope || scope.has(key) ? "shown" : "hidden");
  const btn = $("#set-rediscover", root);
  if (btn) btn.hidden = !lost.length;
  const item = ({ name, badges, path, missing, actions, muted }) => `<li class="set-proj${muted ? " muted" : ""}">
      <div class="set-proj-t"><b class="set-proj-n">${esc(name)}</b>${badges}</div>
      <div class="set-proj-p mono small" title="${esc(path)}" tabindex="0" aria-label="${esc(path)}">${esc(shortPath(path))}${missing ? ' <span class="fail-t">(folder missing)</span>' : ""}</div>
      <div class="set-proj-a">${actions}</div></li>`;
  const entries = [
    ...list.map((p) => {
      const key = p.identifier || p.name;
      const label = p.display_name || p.name;
      return { text: `${label}\n${p.root_path || ""}`, html: item({ name: label, path: p.root_path || "", badges: visBadge(visOf(key), key, label, canWrite),
        actions: `<button class="icon-btn sm" data-forget="${esc(key)}" data-label="${esc(label)}" ${canWrite ? "" : "disabled"} title="${canWrite ? "Forget (runtime database only)" : "Only from a browser on the same machine"}" aria-label="Forget ${esc(label)}">${icon("trash")}</button>` }) };
    }),
    ...lost.map((c) => ({ text: `${c.name || c.root.split("/").pop()}\n${c.root}`, html: item({
      muted: true, name: c.name || c.root.split("/").pop(), path: c.root, missing: !c.exists,
      badges: `<span class="badge tone-muted">${c.start ? "serve folder · forgotten" : "forgotten"}</span>${c.default_manifest ? ' <span class="badge" title="No alfrd.yaml in the folder: the default one is used">default alfrd.yaml</span>' : ""}`,
      actions: `<button class="btn sm" data-restore="${esc(c.root)}" ${canWrite && c.exists ? "" : "disabled"}>${icon("sync")} Restore</button>`,
    }) })),
  ];
  // Case-insensitive substring over the full name and path; never changes visibility or selection.
  let touched = false;
  const show = () => {
    const q = filter.value.trim().toLowerCase();
    const shown = entries.filter((e) => !q || e.text.toLowerCase().includes(q));
    $("#set-filter-clear", root).hidden = !filter.value;
    table.innerHTML = !entries.length
      ? `<li class="muted small">No connected projects yet.${canWrite ? ' <button class="btn sm" type="button" data-set-new>New project</button>' : ""}</li>`
      : shown.map((e) => e.html).join("") || `<li class="muted small">No projects match ‘${esc(filter.value.trim())}’. <button class="link-btn" type="button" data-set-clear-filter>Clear filter</button></li>`;
    if (touched) $("#set-filter-count", root).textContent = `${shown.length} of ${entries.length} projects`;
  };
  filter.oninput = () => { touched = true; show(); };
  $("#set-filter-clear", root).onclick = () => { filter.value = ""; touched = true; show(); filter.focus(); };
  show();
  const redraw = () => drawProjects(ctx, app, root);
  const restore = async (rootPath, b) => {
    if (b) b.disabled = true;
    try {
      const res = await server.rediscover(rootPath);
      res.restored.forEach((r) => ctx.log("info", `Project ${r.name} connected again (${r.root}${r.default_manifest ? ", default alfrd.yaml" : ""}).`, "server"));
      res.failed.forEach((f) => ctx.log("error", `${f.root}: ${f.error}`, "server"));
      ctx.toast(res.restored.length ? `Restored ${res.restored.map((r) => r.name).join(", ")}` : "Nothing to restore", res.failed.length ? "warn" : "ok");
      await app.loadServer();
      redraw();
    } catch (error) {
      if (b) b.disabled = false;
      ctx.toast(error.message, "fail");
    }
  };
  if (btn) btn.onclick = () => restore(null, btn);
  const setVis = async (key, label, want) => {
    try {
      await server.setProjectVisibility(key, want);
      ctx.log("info", `Project ${label} ${want} in the Studio (runtime database and files unchanged).`, "server");
      if (want === "hidden") {
        state.avica = { ...state.avica };
        delete state.avica[key];
        delete state.trees[key];
        if (state.selectedProject === key) app.switchProject("all", { render: false });
      }
      await app.loadServer();
      if (want === "opened" && ctx.projects().some((p) => p.id === key)) app.switchProject(key);
      ctx.toast(`${label}: ${want}`, "ok");
    } catch (error) {
      ctx.toast(error.message, "fail");
    }
    redraw();
  };
  table.onclick = async (e) => {
    if (e.target.closest("[data-set-clear-filter]")) { filter.value = ""; show(); filter.focus(); return; }
    if (e.target.closest("[data-set-new]")) { $("#set-create", root)?.click(); return; }
    const v = e.target.closest("[data-vis]");
    if (v) {
      e.stopPropagation();
      const { vis: key, label, current } = v.dataset;
      ctx.menu(v, VIS_STATES.map((st) => ({
        icon: st.icon, label: st.label, hint: st.id === current ? "current" : st.hint, disabled: st.id === current,
        run: () => setVis(key, label, st.id),
      })));
      return;
    }
    const r = e.target.closest("[data-restore]");
    if (r) { restore(r.dataset.restore, r); return; }
    const b = e.target.closest("[data-forget]");
    if (!b) return;
    const name = b.dataset.forget;
    const label = b.dataset.label || name;
    try {
      const { openRemoval } = await import("./removal_dialog.js");
      await openRemoval(ctx, { key: name, label, root: b.closest(".set-proj")?.querySelector(".set-proj-p")?.title || "", onDone: async (res) => {
        if (!res?.deleted) ctx.log("info", `Project ${name} forgotten (runtime database only).`, "server");
        app.removeWorkspace(name);
        await app.loadServer();
        redraw();
      } });
    } catch (error) {
      ctx.toast(error.message, "fail");
    }
  };
}
