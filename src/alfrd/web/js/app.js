import { handleNotifications, browserEnabled } from "./components/notifications.js";

import { $, $$, on, esc, icon, LOGO, watchThemeAccent, storage, bytes, download, hms, loadUi, saveUi, parseRoute, routeHash, chooseTarget } from "./utils/dom.js";
import { projectLabels } from "./utils/text_fit.js";
import { mountPicker } from "./components/picker.js";
import { stepParamsFromConfig } from "./data/avica.js";
import { clearWorkdirCache, resetAttachments } from "./components/attach.js";
import { toCsv, aliasRules } from "./utils/csv_parser.js";
import { dumpYaml, parseYaml } from "./utils/yaml_parser.js";
import { readFiles, buildBundle, entriesFromScanBundle } from "./data/importers.js";
// Folder mode (File System Access) loads on demand; every call below is async.
let folderScan = null;
const folderMod = () => (folderScan ||= import("./data/folder_scan.js"));
const canPickDirectory = () => typeof window.showDirectoryPicker === "function";
const [pickProjectFolder, scanProjectFolder, saveFolderHandle, loadFolderHandle, forgetFolderHandles, ensureReadPermission, readFileFromHandle, readWholeFromHandle, readFileRange, writeFileToHandle] =
  "pickProjectFolder scanProjectFolder saveFolderHandle loadFolderHandle forgetFolderHandles ensureReadPermission readFileFromHandle readWholeFromHandle readFileRange writeFileToHandle"
    .split(" ").map((name) => async (...args) => (await folderMod())[name](...args));
import { createLive } from "./data/live.js";
import { server } from "./data/server.js";
import { defaultWorkflow, manifestToWorkflows, rollup, OVERALL_STATUS } from "./data/model.js";
import { loadTemplate, loadDefaultManifest, templateName, studioManifest, applyFieldAliases } from "./data/defs.js";
import { projectNotes } from "./data/notes.js";
import { bindLogs, nudgeLogs, forgetLogs, setTails, openFileFull, dockedLogs, undockLog, mountDock, dockInfo, setDockFollow, onDock } from "./components/logview.js";
import * as logs from "./components/logs.js";
import * as overview from "./components/overview.js";
import * as canvas from "./components/canvas.js";
import * as metadata from "./components/metadata.js";
import * as results from "./components/results.js";
import * as config from "./components/alfrd_config.js";
import { setActive, meta as wsMeta, owner, isDirty, dropWorkspace } from "./data/workspace.js";
import { forgetPlan, releasePlanPin, activeJobs, loadPlan, planOf, plansAvailable, openLinkedRun } from "./components/plans.js";
watchThemeAccent(); // tab icon follows the theme accent
const DEMO_ALFRD_PROJECT = "avica-demo"; // data/demo.js's key; the demo data itself loads on demand

export let VERSION = "standalone";

const VIEWS = [
  { id: "overview", label: "Overview", icon: "overview", mod: overview },
  { id: "workflow", label: "Workflow", icon: "workflow", mod: canvas },
  { id: "metadata", label: "Metadata", icon: "metadata", mod: metadata },
  { id: "results", label: "Results", icon: "results", mod: results },
  { id: "logs", label: "Logs", icon: "log", mod: logs },
  { id: "config", label: "Settings", icon: "project", mod: config },
];


const state = {
  mode: "browser", // "browser" | "server"
  source: "empty", // "demo" | "imported" | "server" | "empty"
  view: "overview",
  targets: [],
  projectTitles: {},
  projectNames: {},
  workflow: defaultWorkflow(),
  workflowFile: { name: "no alfrd.yaml loaded", validated: true, errors: [], warnings: [], text: null, modified: false },
  serverWorkflows: [],
  aliases: [],
  aliasBannerDismissed: false,
  avica: {}, // ALFRD project -> AVICA tree index (template avica: project codes, avica.meta, templates, logs)
  trees: {}, // ALFRD project -> {provider, defs, logFiles, msPaths, manifestText, manifestFile}
  demoEnabled: false,
  paramSource: "AVICA defaults",
  configs: [],
  importLogs: [],
  importFiles: [],
  folders: {}, // ALFRD project -> FileSystemDirectoryHandle (this tab; also kept in IndexedDB)
  selectedProject: loadUi("app", { selectedProject: null }).selectedProject, // null: no prior selection yet (resolved on load)
  selectedTarget: null,
  consoleLines: [],
  consoleOpen: false,
  consoleTab: "studio", // "studio" or the key of a docked log (see logview.js dockLog)
  notes: storage.get("notes", {}),
  prefs: { pageSize: 25, live: true, ...storage.get("prefs", {}) },
  scans: {}, // server mode: ALFRD project -> last /scan JSON (patched by live updates)
  targetOrigin: {}, // ALFRD project -> "tree" | "runtime" (where its target rows came from)
  liveStatus: { state: "off", detail: "", last: null, interval: null },
};

const listeners = new Set();
let renderQueued = false;
let renderFrame = 0;


const lazy = { targets: null, diagnostics: null, history: null, palette: null, search: null, notes: null, jobs: null, settings: null };

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  renderFrame = requestAnimationFrame(() => {
    renderQueued = false;
    renderAll();
  });
}

// Tab errors belong to the project that raised them ("plan:<project>" keys name it).
const tabErrors = new Map(); // `${project}\u0000${view}` -> {message, retry, key}
const errorOwner = (key) => (typeof key === "string" && key.startsWith("plan:") ? key.slice(5) : activeProject() || "all");
const errorKey = (project, view) => `${project}\u0000${view}`;
export const ctx = {
  showError(message, retry, view = state.view, key = null) { tabErrors.set(errorKey(errorOwner(key), view), { message, retry, key }); scheduleRender(); },
  clearError(view, key) { const k = errorKey(errorOwner(key), view); if (tabErrors.get(k)?.key === key) { tabErrors.delete(k); scheduleRender(); } },
  /** The project the editors work on: the selected one, never a fallback (null in All projects). */
  activeProject: () => activeProject(),
  switchProject: (project, opts) => switchProject(project, opts),
  owner: (project) => owner(project),
  state,
  isDirty,
  get VERSION() { return VERSION; },
  /** Mutate state via fn(state) and re-render. */
  update(fn) {
    if (typeof fn === "function") fn(state);
    else Object.assign(state, fn);
    scheduleRender();
  },
  subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  log(level, text, scope = "studio") {
    state.consoleLines.push({ t: new Date(), level, text, scope });
    if (state.consoleLines.length > 1500) state.consoleLines.splice(0, 500);
    renderFooter();
    if (state.consoleOpen) renderConsole();
  },
  toast(text, tone = "info", { sticky = false, link = null, linkLabel = "View run" } = {}) {
    const host = $("#toasts");
    const el = document.createElement("div");
    el.className = `toast toast-${tone}`;
    el.setAttribute("role", "status");
    el.innerHTML = `${icon(tone === "fail" ? "xCircle" : tone === "ok" ? "checkCircle" : tone === "warn" ? "alert" : "info")}<span>${esc(text)}</span>`;
    if (link) {
      const a = document.createElement("a"); a.className = "btn sm"; a.href = link; a.textContent = linkLabel; el.appendChild(a);
    }
    if (sticky) {
      const close = document.createElement("button"); close.className = "icon-btn sm"; close.setAttribute("aria-label", "Dismiss notification"); close.innerHTML = icon("close"); close.onclick = () => el.remove(); el.appendChild(close);
    }
    host.appendChild(el);
    if (sticky) return;
    setTimeout(() => el.classList.add("out"), tone === "fail" || tone === "warn" ? 8000 : 3600);
    setTimeout(() => el.remove(), tone === "fail" || tone === "warn" ? 8400 : 4000);
  },
  openRun(project, id = null) { canvas.showRun(ctx, project, id); },
  openLogs(opts = {}) { logs.showLogs(ctx, opts); },
  /** Pick a folder on the ALFRD server (never the viewer's machine) into `input`; Create project still decides. */
  browseFolder(host, input, more) {
    let browser, cancelled = false;
    const b = { destroy() { cancelled = true; browser?.destroy(); } };
    const done = (path) => {
      b.destroy();
      if (path != null) { input.value = path; ["input", "change"].forEach((t) => input.dispatchEvent(new Event(t, { bubbles: true }))); }
      input.focus();
    };
    import("./components/folder_browser.js").then(({ mountFolderBrowser }) => {
      if (!cancelled && host.isConnected) browser = mountFolderBrowser(host, { list: (path, opts) => server.listFolders(path, opts), start: input.value.trim(), select: true,
        use: done, close: () => done(null), ...more });
    }).catch((error) => { if (!cancelled && host.isConnected) ctx.toast(`Folder browser not loaded: ${error.message}`, "fail"); });
    return b;
  },
  navigate(view, params = {}) {
    if (params.target) state.selectedTarget = params.target;
    goTo(view);
  },
  steps() {
    return state.workflow.steps.map((s) => s.key);
  },
  /** Targets filtered by the header project selector. */
  scopedTargets() {
    return !activeProject() ? state.targets : state.targets.filter((t) => t.project === state.selectedProject);
  },
  target(id = state.selectedTarget) {
    return state.targets.find((t) => t.id === id) || null;
  },
  /** Label for a project key: the ALFRD project name, never the long identifier. */
  projectName(key) {
    return (key && state.projectNames[key]) || key || "";
  },
  projects() {
    const map = new Map();
    if (state.mode === "server") Object.keys(state.projectNames).forEach((key) => map.set(key, { id: key, name: ctx.projectName(key), title: ctx.projectName(key), targets: [] }));
    Object.keys(state.trees).forEach((id) => { if (!map.has(id)) map.set(id, { id, name: ctx.projectName(id), title: ctx.projectName(id), targets: [] }); });
    state.targets.forEach((t) => {
      if (!map.has(t.project)) map.set(t.project, { id: t.project, name: ctx.projectName(t.project), title: t.projectTitle || state.projectTitles[t.project] || "", targets: [] });
      map.get(t.project).targets.push(t);
    });
    return Array.from(map.values());
  },
  rollup(t) {
    return rollup(t, ctx.steps());
  },
  setNote(id, text) {
    state.notes[id] = text;
    storage.set("notes", state.notes);
    renderFooter();
  },
  setPrefs(patch) {
    Object.assign(state.prefs, patch);
    storage.set("prefs", state.prefs);
  },
  openImport,

  async openTargets(action = "import", project = null, names = []) {
    try {
      lazy.targets ||= import("./components/targets_dialog.js");
      return await (await lazy.targets).openTargets(ctx, action, project, names);
    } catch (error) {
      lazy.targets = null;
      ctx.toast(`Targets: ${error.message}`, "fail");
    }
  },
  applyWorkflowInfo,

  async readFile(project, rel) {
    const tree = state.trees?.[project];
    const item = tree?.logFiles?.find((f) => f.rel === rel);
    if (item?.file) return item.file.size > 400000 ? item.file.slice(item.file.size - 400000).text() : item.file.text();
    if (typeof item?.text === "string") return item.text;
    if (tree?.provider === "server" || state.avica?.[project]?.provider === "server") return server.projectFile(project, rel);
    const folder = state.folders[project] || (await loadFolderHandle(project));
    if (folder && (await ensureReadPermission(folder))) {
      state.folders[project] = folder;
      return readFileFromHandle(folder, rel);
    }
    throw new Error("This file is not in the imported data: re-open the project folder (Re-scan) or import a fresh scan.json.");
  },

  async readLogRange(project, rel, offset = null, { id = null, decoder = null } = {}) {
    const tree = state.trees?.[project];
    if (tree?.provider === "server" || state.avica?.[project]?.provider === "server") {
      return { ...(await server.projectFileFrom(project, rel, offset, id)), live: true };
    }
    const folder = state.folders[project] || (await loadFolderHandle(project));
    if (folder && canPickDirectory() && (await ensureReadPermission(folder, { request: offset == null }))) {
      state.folders[project] = folder;
      try {
        return { ...(await readFileRange(folder, rel, offset, { decoder })), live: true };
      } catch (error) {
        if (offset != null) throw error;
      }
    }
    if (offset != null) return { text: "", offset, reset: false, live: false };
    return { text: await ctx.readFile(project, rel), offset: null, reset: true, live: false };
  },

  onLogGrew(project, rel, size, mtime) {
    const item = state.trees?.[project]?.logFiles?.find((f) => f.rel === rel);
    if (item) { item.size = size; if (mtime) item.mtime = mtime; }
  },

  async readLog(project, name) {
    const index = state.avica?.[project];
    const log = index?.logs?.find((l) => l.name === name);
    if (log?.file) return log.file.text();
    if (log?.text) return log.text;
    try {
      return await ctx.readFile(project, `${index?.logsDir || "avica.logs"}/${name}`);
    } catch (error) {
      if (log?.crash) return JSON.stringify(log.crash, null, 1);
      throw error;
    }
  },
  async openLog(project, name) {
    const rel = `${state.avica?.[project]?.logsDir || "avica.logs"}/${name}`;
    if (!/\.json$/i.test(name) && state.trees?.[project]?.logFiles?.some((f) => f.rel === rel)) {
      try { await openFileFull(ctx, project, rel); } catch (error) { ctx.toast(error.message, "warn"); }
      return;
    }
    try {
      const text = await ctx.readLog(project, name);
      modal(`<header class="modal-h"><h2 class="mono">${esc(name)}</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
        <div class="modal-b"><pre class="log log-full">${esc(text.slice(-400000))}</pre></div>`, null, "full");
    } catch (error) {
      ctx.toast(error.message, "warn");
    }
  },
  saveManifest,
  writeAvicaConfig,

  async openHistory(project, opts = {}) {
    try {
      lazy.history ||= import("./components/history.js");
      return await (await lazy.history).openHistory(ctx, project, opts);
    } catch (error) {
      lazy.history = null;
      ctx.toast(`History: ${error.message}`, "fail");
    }
  },

  async refreshProject(project) {
    if (state.mode === "server" && state.trees?.[project]?.provider === "server") {
      await applyServerScan(project, await server.projectScan(project));
      return;
    }
    await rescan();
  },

  async readProjectText(project, rel) {
    const folder = state.folders[project] || (await loadFolderHandle(project));
    if (!folder || !canPickDirectory() || !(await ensureReadPermission(folder))) return null;
    return readWholeFromHandle(folder, rel);
  },

  async writeProjectFile(project, rel, text) {
    const folder = state.folders[project] || (await loadFolderHandle(project));
    if (!folder || !canPickDirectory()) return false;
    await writeFileToHandle(folder, rel, text);
    return true;
  },
  canWrite(project) {
    if (state.mode === "server") return Boolean(server.session?.mutations_enabled && state.trees?.[project]?.provider === "server");
    return Boolean(state.folders[project] && canPickDirectory());
  },
  persist,
  footerRight: "",
  setFooterRight(html) {
    ctx.footerRight = html;
    const el = $("#footer-right");
    if (el) el.innerHTML = html;
  },
};


function persist() {
  if (state.source === "demo" || state.source === "server") {
    storage.remove("data");
  } else if (state.source === "imported") {
    const ok = storage.set("data", {
      targets: state.targets,
      aliases: state.aliases,
      avica: stripHandles(state.avica),
      trees: stripTrees(state.trees),
      configs: state.configs,
      projectTitles: state.projectTitles,
      projectNames: state.projectNames,
      importFiles: state.importFiles,
    });
    if (!ok) ctx.log("warn", "Imported data is too large for browser storage; it will not survive a reload.");
  }
  if (state.workflowFile.modified || state.workflowFile.text) {
    storage.set("workflow", { text: state.workflowFile.text, name: state.workflowFile.name, edited: state.workflowFile.modified ? serializeWorkflow() : null });
  }
  renderFooter();
}

function stripTrees(trees) {
  const out = {};
  Object.entries(trees || {}).forEach(([k, v]) => {
    out[k] = { ...v, logFiles: (v.logFiles || []).map(({ file, text, ...rest }) => rest), targetsFile: v.targetsFile ? { rel: v.targetsFile.rel } : null };
  });
  return out;
}

function treeFromBundle(bundle, provider) {
  return {
    provider,
    defs: bundle.defs || studioManifest(bundle.manifest || {}),
    logFiles: bundle.logFiles || [],
    msPaths: bundle.msPaths || [],
    manifestText: bundle.manifestText || null,
    manifestFile: bundle.manifestFile || null,
    manifestDefault: Boolean(bundle.manifestDefault), // no local alfrd.yaml: the default one is shown
    template: (bundle.defs || {}).template || templateName(bundle.manifest) || null,
    configName: bundle.manifest?.avica?.config || "avica.inp",
    targetsFile: bundle.targetsFile || null, // {rel, text}: alfrd.yaml targets.csv as imported
    notesText: bundle.notesText ?? null, // alfrd.notes.jsonl (shared annotations; data/notes.js)
  };
}

function stripHandles(avica) {
  const out = {};
  Object.entries(avica || {}).forEach(([k, v]) => {
    out[k] = { ...v, logs: (v.logs || []).map(({ file, text, ...rest }) => rest) };
  });
  return out;
}


function applyAvicaParams(index, { quiet = false } = {}) {
  if (!index) return;
  const params = stepParamsFromConfig(index, ctx.steps());
  let n = 0;
  Object.entries(params).forEach(([key, values]) => {
    const step = state.workflow.steps.find((s) => s.key === key);
    if (!step) return;
    step.paramSources = { ...(step.paramSources || {}) };
    Object.entries(values).forEach(([k, { value, source }]) => {
      step.params[k] = value;
      step.paramSources[k] = source;
      n += 1;
    });
  });
  state.paramSource = index.summary ? (index.summary.file || "avica pipe config --summary") : Object.keys(params).length ? "avica.inp" : "AVICA defaults";
  if (n && !quiet) ctx.log("info", `${n} step parameter(s) applied from ${state.paramSource}.`, "config");
}

function serializeWorkflow() {
  const wf = state.workflow;
  return {
    alfrd_manifest: "1.0",
    project: { id: state.selectedProject !== "all" ? state.selectedProject : wf.name, name: wf.label },
    stages: wf.stages.map((s) => ({ id: s.id, title: s.title })),
    workflows: {
      [wf.name]: {
        label: wf.label,
        steps: wf.steps.map((s) => ({
          id: s.key,
          stage: s.stage,
          ...(s.depends.length && !(s.depends.length === 1 && wf.steps[wf.steps.indexOf(s) - 1]?.key === s.depends[0]) ? { depends_on: s.depends } : {}),
          ...(Object.keys(s.params).length ? { params: s.params } : {}),
        })),
      },
    },
  };
}


function applyBundle(bundle, { replace = true, source = "imported", provider = "files", quiet = false, render = true } = {}) {
  const incoming = bundle.targets || [];
  if (replace === "project") {
    const p = bundle.alfrdProject;
    state.targets = [...state.targets.filter((t) => t.project !== p), ...incoming];
    state.avica = { ...state.avica };
    delete state.avica[p];
    if (bundle.avica) state.avica[bundle.avica.project] = bundle.avica;
    state.trees = { ...state.trees, [p]: treeFromBundle(bundle, provider) };
    state.configs = [...state.configs.filter((c) => c.project !== p), ...(bundle.configs || []).map((c) => ({ ...c, project: p }))];
    state.importFiles = bundle.files || [];
    state.importLogs = bundle.logs || [];
  } else if (replace) {
    state.targets = incoming;
    state.avica = bundle.avica ? { [bundle.avica.project]: bundle.avica } : {};
    state.trees = bundle.alfrdProject ? { [bundle.alfrdProject]: treeFromBundle(bundle, provider) } : {};
    state.configs = bundle.configs || [];
    state.importLogs = bundle.logs || [];
    state.aliases = bundle.aliases || [];
    state.importFiles = bundle.files || [];
  } else {
    const byId = new Map(state.targets.map((t) => [t.id, t]));
    incoming.forEach((t) => byId.set(t.id, t));
    state.targets = Array.from(byId.values());
    if (bundle.avica) state.avica = { ...state.avica, [bundle.avica.project]: bundle.avica };
    if (bundle.alfrdProject) state.trees = { ...state.trees, [bundle.alfrdProject]: treeFromBundle(bundle, provider) };
    state.configs = [...state.configs, ...(bundle.configs || [])];
    state.importLogs = [...state.importLogs, ...(bundle.logs || [])];
    const aliasKey = (a) => `${a.from}>${a.to}`;
    const seen = new Set(state.aliases.map(aliasKey));
    (bundle.aliases || []).forEach((a) => !seen.has(aliasKey(a)) && state.aliases.push(a));
    state.importFiles = [...state.importFiles, ...(bundle.files || [])];
  }
  state.aliasBannerDismissed = false;
  state.source = source;
  const bp = bundle.alfrdProject || null;
  if (bp) workflowCache.delete(bp); // its alfrd.yaml was just read: build it again when shown
  if (bundle.workflowInfo && bundle.workflowInfo.workflows.length && bp && replace === "project" && bp !== activeProject()) {
    // Another project's alfrd.yaml: it is built from its tree when that project is opened.
  } else if (bundle.workflowInfo && bundle.workflowInfo.workflows.length) {
    applyWorkflowInfo(bundle.workflowInfo, bundle.manifestFile || "alfrd.yaml", bundle.manifestText, { quiet });
    state.workflowProject = bp;
  } else if (bundle.workflowInfo) {
    state.workflowFile = { ...state.workflowFile, validated: false, errors: bundle.workflowInfo.errors, warnings: bundle.workflowInfo.warnings };
  }
  clearWorkdirCache(replace === "project" ? bundle.alfrdProject : undefined);
  applyAvicaParams(bundle.avica, { quiet });
  // A project with no targets (agent-loop) stays selected; only a project that is gone falls back.
  if (activeProject() && !ctx.projects().some((p) => p.id === state.selectedProject)) switchProject("all", { render: false });
  const inScope = ctx.scopedTargets();
  if (!inScope.some((t) => t.id === state.selectedTarget)) {
    const firstBad = inScope.find((t) => rollup(t, ctx.steps()).status === "failed");
    state.selectedTarget = (firstBad || inScope[0] || {}).id || null;
  }
  if (!quiet) {
    (bundle.messages || []).forEach((m) => ctx.log(m.level === "error" ? "error" : m.level === "warn" ? "warn" : "info", m.text, "import"));
    (bundle.aliases || []).forEach((a) => ctx.log("info", `Field alias ${a.from} → ${a.to} (${a.sources.length} file${a.sources.length === 1 ? "" : "s"})`, "schema"));
  }
  persist();
  if (render) scheduleRender();
}

let linkedRoute = "";
const workflowCache = new Map(); // project -> {workflow, workflowFile}

function activeProject() {
  return state.selectedProject && state.selectedProject !== "all" ? state.selectedProject : null;
}

/** First load: keep a remembered choice; otherwise the server's default, else the sole project. */
function resolveInitialProject() {
  if (state.selectedProject && state.selectedProject !== "all" && !ctx.projects().some((p) => p.id === state.selectedProject)) state.selectedProject = null;
  if (state.selectedProject) return;
  const ids = ctx.projects().map((p) => p.id);
  const def = server.session?.default_project;
  switchProject(def && ids.includes(def) ? def : ids.length === 1 ? ids[0] : "all", { render: false });
}

// Keep each workspace's view, target and scroll when switching projects.
function switchProject(project, { render = true, restoreView = true, syncUrl = true } = {}) {
  const next = project && project !== "all" ? project : "all";
  const before = state.selectedProject || "all";
  if (before !== next) {
    linkedRoute = "";
    releasePlanPin(before);
    const out = wsMeta(before);
    out.target = state.selectedTarget;
    out.view = state.view;
    out.scroll[state.view] = $("#main")?.scrollTop || 0;
  }
  state.selectedProject = next;
  setActive(next);
  const incoming = wsMeta(next);
  const inScope = ctx.scopedTargets();
  state.selectedTarget = chooseTarget(inScope, storage.get("ui:app.targets", {})[next], incoming.target);
  rememberTarget();
  if (syncUrl) history.replaceState(null, "", routeHash(state.view, next, state.selectedTarget));
  if (before !== next && restoreView && incoming.view && incoming.view !== state.view) goTo(incoming.view);
  if (before !== next) {
    const top = incoming.scroll[incoming.view || state.view] || 0;
    requestAnimationFrame(() => requestAnimationFrame(() => { const m = $("#main"); if (m) m.scrollTop = top; }));
  }
  if (render) scheduleRender();
}


function syncWorkflow() {
  const p = activeProject();
  if (p === state.workflowProject && !(p && state.workflowFile.loading && state.trees?.[p]?.manifestText)) return;
  if (state.workflowProject && !state.workflowFile.loading) workflowCache.set(state.workflowProject, { workflow: state.workflow, workflowFile: state.workflowFile });
  if (!p) { state.workflowProject = null; return; } // All projects: the Workflow view asks for a project
  const tree = state.trees?.[p];
  if (!workflowCache.has(p) && !tree?.manifestText) {
    // Never keep showing the previous project's workflow while this one loads.
    state.workflow = { ...defaultWorkflow(), steps: [], stages: [] };
    state.workflowFile = { name: `Loading ${ctx.projectName(p)}…`, loading: true, validated: true, errors: [], warnings: [], text: null, modified: false };
    state.workflowProject = p;
    return;
  }
  const cached = workflowCache.get(p);
  if (cached) {
    state.workflow = cached.workflow;
    state.workflowFile = cached.workflowFile;
  } else {
    const info = manifestToWorkflows(parseYaml(tree.manifestText), tree.manifestFile || "alfrd.yaml");
    if (!info.workflows.length) state.workflow = defaultWorkflow();
    applyWorkflowInfo(info, tree.manifestFile || "alfrd.yaml", tree.manifestText, { quiet: true });
  }
  state.workflowProject = p;
  canvas.resetSimulation?.();
  applyAvicaParams(state.avica?.[p], { quiet: true });
}

function applyWorkflowInfo(info, fileName, text, { quiet = false } = {}) {
  const wf = info.workflows[0];
  if (wf) state.workflow = wf;
  state.workflowFile = {
    name: String(fileName).split("/").pop(),
    validated: !info.errors.length,
    errors: info.errors,
    warnings: info.warnings,
    text: text || null,
    modified: false,
    all: info.workflows.map((w) => w.name),
  };
  info.aliases.forEach((a) => {
    if (!state.aliases.some((x) => x.from === a.from)) state.aliases.push(a);
  });
  if (!quiet) ctx.log(info.errors.length ? "error" : "info", `Workflow "${wf?.name ?? "?"}" loaded from ${fileName}: ${wf ? wf.steps.length : 0} steps, ${info.errors.length} error(s), ${info.warnings.length} warning(s).`, "workflow");
  canvas.resetSimulation?.();
  scheduleRender();
}

async function loadDemo({ quiet = false } = {}) {
  if (!state.demoEnabled) {
    ctx.toast("Demo data is only available with `alfrd serve --demo` (or ?demo=1)", "warn");
    return;
  }
  const { demoBundle } = await import("./data/demo.js");
  const bundle = demoBundle();
  state.projectTitles = { [DEMO_ALFRD_PROJECT]: "AVICA demo reductions" };
  state.projectNames = {};
  state.selectedTarget = `${DEMO_ALFRD_PROJECT}/J1440+0127`;
  applyBundle(bundle, { replace: true, source: "demo", provider: "demo" });
  if (!quiet) ctx.toast("Demo data loaded — 18 targets under 3 AVICA project codes", "ok");
}

async function loadServer() {
  try {
    ctx.log("info", "Loading projects from alfrd serve…", "server");
    const data = await server.loadAll();
    const keep = { project: state.selectedProject, target: state.selectedTarget }; // the header, across a Re-scan
    state.serverWorkflows = data.workflows;
    state.projectTitles = Object.fromEntries(data.projects.map((p) => [p.name, p.title || p.description || p.name]));
    state.projectNames = Object.fromEntries(data.projects.map((p) => [p.name, p.title || p.manifest_name || p.name]));
    state.projectRoots = Object.fromEntries(data.projects.map((p) => [p.name, p.root_path || ""]));
    applyBundle({ targets: data.targets, messages: data.messages, aliases: [], configs: [], logs: [], files: [] }, { replace: true, source: "server", provider: "server" });
    if (data.workflows.length) {
      const wf = data.workflows[0];
      state.workflow = wf;
      state.workflowFile = { name: `${ctx.projectName(wf.project)} / ${wf.name} (runtime)`, validated: true, errors: [], warnings: [], text: null, modified: false };
      state.workflowProject = null; // replaced by the active project's alfrd.yaml below
    }
    const scans = await Promise.all(data.projects.map(async (p) => {
      try {
        return { p, scan: await server.projectScan(p.name) };
      } catch (error) {
        ctx.log("warn", `${p.title || p.name}: project folder not readable (${error.message}).`, "server");
        return { p, scan: null };
      }
    }));
    for (const { p, scan } of scans) {
      if (!scan) continue;
      const bundle = await applyServerScan(p.name, scan, { runtimeRows: state.targets.filter((t) => t.project === p.name) });
      ctx.log("info", `${p.title || p.name}: ${bundle.targets.length} target(s), ${(bundle.logFiles || []).length} log file(s) from ${scan.root}.`, "server");
    }
    // Restore the selected scope.
    if (keep.project && keep.project !== "all" && ctx.projects().some((p) => p.id === keep.project)) state.selectedProject = keep.project;
    const url = parseRoute(location.hash);
    if (url.project === "all" || ctx.projects().some((p) => p.id === url.project)) state.selectedProject = url.project;
    resolveInitialProject();
    setActive(state.selectedProject);
    state.selectedTarget = chooseTarget(ctx.scopedTargets(), url.target, storage.get("ui:app.targets", {})[state.selectedProject], keep.target, state.selectedTarget);
    rememberTarget();
    workflowCache.clear();
    state.workflowProject = null;
    syncWorkflow();
    // Poll background runs too.
    data.projects.forEach((p) => plansAvailable(ctx, p.name) && loadPlan(ctx, p.name, { quiet: true }));
    if (url.plan && url.project === state.selectedProject) applyRunLink(url);
    applyAvicaParams(state.avica?.[ctx.target()?.project]);
    ctx.log("info", `Server: ${data.projects.length} project(s), ${state.targets.length} target(s).`, "server");
    scheduleRender();
    live?.sync();
    return { ...data, targets: state.targets };
  } catch (error) {
    ctx.log("error", `Server load failed: ${error.message}`, "server");
    ctx.showError(`Could not load projects: ${error.message}`, loadServer);
    ctx.toast(`Server load failed: ${error.message}`, "fail");
    return null;
  }
}

/**
 * One project's /scan JSON → targets, tree, AVICA index. `live`: a background
 * refresh — no console messages, and the workflow is only replaced when
 * alfrd.yaml itself changed and there are no unsaved workflow edits.
 */
async function applyServerScan(project, scan, { runtimeRows = null, live: isLive = false } = {}) {
  state.scans[project] = scan;
  syncName(project, scan.project_name);
  const manifest = scan.files?.find((f) => /(^|\/)\.?alfrd\.ya?ml$/.test(f.rel))?.text;
  await ensureTemplatesFor(manifest);
  const entries = entriesFromScanBundle(scan);
  const bundle = buildBundle(entries, { source: "server", projectHint: project, rootName: scan.root_name });
  if (bundle.avica) bundle.avica.provider = "server";
  const rows = runtimeRows ?? state.targets.filter((t) => t.project === project && state.targetOrigin[project] === "runtime");
  state.targetOrigin[project] = bundle.targets.length || !rows.length ? "tree" : "runtime";
  if (!bundle.targets.length && rows.length) bundle.targets = rows;
  bundle.alfrdProject = project;
  if (isLive) keepWorkflow(project, bundle);
  applyBundle(bundle, { replace: "project", source: "server", provider: "server", quiet: isLive, render: !isLive });
  return bundle;
}

/** alfrd.yaml `name:` (synced to the DB by the server) → header picker and Settings list labels. */
function syncName(project, name) {
  if (name && state.projectNames[project] !== name) state.projectNames[project] = state.projectTitles[project] = name;
}

function keepWorkflow(project, bundle) {
  const before = state.trees?.[project]?.manifestText;
  if (!bundle.workflowInfo) return;
  if (before != null && before === bundle.manifestText) { delete bundle.workflowInfo; return; }
  if (state.workflowFile.modified) {
    delete bundle.workflowInfo;
    ctx.toast(`${bundle.manifestFile || "alfrd.yaml"} changed on disk — your unsaved workflow edits are kept (Re-scan to load the file).`, "warn");
    return;
  }
  if (activeProject() !== project) delete bundle.workflowInfo; // another project's file: only its cache refreshes
  else ctx.log("info", `${ctx.projectName(project)}: alfrd.yaml changed on disk — workflow reloaded.`, "live");
}


async function ensureTemplatesFor(text) {
  try {
    const name = templateName(parseYaml(text || ""));
    if (name) await loadTemplate(name, parseYaml);
  } catch { /* parse errors are reported by the importer */ }
}

function restoreSaved() {
  const saved = storage.get("data");
  if (saved && Array.isArray(saved.targets) && saved.targets.length) {
    Object.assign(state, {
      targets: saved.targets,
      aliases: saved.aliases || [],
      avica: saved.avica || {},
      trees: saved.trees || {},
      configs: saved.configs || [],
      projectTitles: saved.projectTitles || {},
      projectNames: saved.projectNames || {},
      importFiles: saved.importFiles || [],
      source: "imported",
    });
    const remembered = loadUi("app", {}).selectedTarget;
    state.selectedTarget = saved.targets.some((t) => t.id === remembered) ? remembered : saved.targets[0].id;
  }
  const wf = storage.get("workflow");
  if (wf && (wf.edited || wf.text)) {
    try {
      if (wf.text) applyFieldAliases(parseYaml(wf.text));
      const info = manifestToWorkflows(wf.edited || parseYaml(wf.text), wf.name || "alfrd.yaml");
      if (info.workflows.length) {
        applyWorkflowInfo(info, wf.name || "alfrd.yaml", wf.text);
        state.workflowFile.modified = Boolean(wf.edited);
      }
    } catch (error) {
      ctx.log("warn", `Saved workflow could not be restored: ${error.message}`);
    }
  }
  return state.source === "imported";
}


function openImport(tab = "files") {
  const serverBlock = state.mode === "server"
    ? `<section class="imp-sec"><h3>${icon("server")} ALFRD server</h3>
        <p class="muted">Register a project directory or <code>alfrd.yaml</code> path on the machine running <code>alfrd serve</code>. The server reads the manifest; it never modifies the project.${server.session?.default_manifest !== false ? " A folder without <code>alfrd.yaml</code> is connected with the built-in default." : ""}</p>
        <form id="imp-connect" class="row gap"><input class="input mono grow" name="path" placeholder="/path/to/project or /path/to/alfrd.yaml" autocomplete="off" required ${server.session?.mutations_enabled ? "" : "disabled"}>${server.canBrowse() ? `<button type="button" class="btn" data-act="browse-server" aria-expanded="false" aria-controls="imp-browse">${icon("folder")} Browse…</button>` : ""}<button class="btn primary" ${server.session?.mutations_enabled ? "" : "disabled"}>Connect</button></form>
        <div id="imp-browse" hidden></div>
        ${server.session?.mutations_enabled ? "" : `<p class="hint warn">Connecting is only allowed from a loopback browser on the server host.</p>`}
        <button class="btn" data-act="reload-server">${icon("sync")} Reload all server projects</button></section>`
    : "";
  modal(`
    <header class="modal-h"><h2>Import results &amp; configuration</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b">
      <p class="callout info">${icon("info")}<span>Files are read <b>in this browser only</b> - nothing is uploaded or executed. Pick the folder that contains <code>alfrd.yaml</code>: the Studio reads <code>alfrd.yaml</code> first and then opens only what it points to. ${canPickDirectory() ? "Measurement sets are never listed. The folder is remembered, so <b>Re-scan</b> refreshes it later." : "This browser cannot open folders selectively, so it lists the whole folder first (slow for large trees); Chrome or Edge read only what alfrd.yaml needs."}</span></p>
      <label class="drop" id="imp-drop" tabindex="0">
        ${icon("upload", "big")}
        <b>Drop the project folder, files or an <code>alfrd avica scan --bundle</code> JSON here</b>
        <span class="muted">alfrd.yaml decides what is read (template, layout, step logs, metadata)</span>
      </label>
      <div class="row gap wrap">
        <button class="btn primary" data-act="pick-folder">${icon("folder")} Open project folder on this computer…</button>
        <input type="file" id="imp-folder" webkitdirectory directory multiple hidden>
        <label class="btn">${icon("file")} Select files…<input type="file" id="imp-files" multiple hidden accept=".yaml,.yml,.csv,.tsv,.inp,.meta,.json,.log,.txt"></label>
        ${state.demoEnabled ? `<button class="btn" data-act="demo">${icon("sync")} Load demo data</button>` : ""}
        <label class="check grow right"><input type="checkbox" id="imp-replace" ${state.source === "demo" || state.source === "empty" ? "checked" : ""}> Replace current data</label>
      </div>
      ${canPickDirectory() ? `<p class="muted small">Other browsers / older setups: <button class="link-btn" data-act="full-folder">read the whole folder</button> instead.</p>` : ""}
      <p class="muted small">Want to load snapshot from another machine? <br> Run <code>alfrd avica scan &lt;folder&gt; --bundle scan.json</code> there and import <code>scan.json</code>.</p>
      <div id="imp-report"></div>
      ${serverBlock}
    </div>`, (root, close) => {
    const report = $("#imp-report", root);
    folderMod(); // load it now so the folder picker still has the click's user activation
    const replaceMode = () => ($("#imp-replace", root).checked ? true : false);
    const show = (bundle, entries) => {
      const msgs = bundle.messages.map((m) => `<li class="lvl-${m.level}">${esc(m.text)}</li>`).join("");
      const st = entries?.stats;
      report.innerHTML = `<div class="imp-summary">
        <div class="kpis small"><div><b>${bundle.targets.length}</b><span>targets</span></div><div><b>${bundle.manifest ? 1 : 0}</b><span>manifest</span></div><div><b>${Object.keys(bundle.avica?.codes || {}).length}</b><span>project codes</span></div><div><b>${bundle.aliases.length}</b><span>aliases</span></div></div>
        ${st ? `<p class="muted small">Read ${st.files} file(s) from ${st.workdirs} work dir(s) in ${(st.ms / 1000).toFixed(1)} s (target_dir <code>${esc(st.targetDir)}/</code>).</p>` : ""}
        ${msgs ? `<ul class="msgs">${msgs}</ul>` : ""}
        <div class="row gap right"><button class="btn primary" data-close>View results</button></div></div>`;
    };
    const handle = async (files) => {
      report.innerHTML = `<p class="muted">Reading ${files.length} file(s)…</p>`;
      const read = await readFiles(files);
      const done = await importEntries(read, { replace: replaceMode() });
      if (!done) { close(); return; }
      show(done.bundle, read);
      if (done.bundle.manifest && read.length === 1 && canPickDirectory()) {
        report.insertAdjacentHTML("afterbegin", `<p class="callout info">${icon("info")}<span>Workflow loaded from <code>${esc(read[0].name)}</code>. Browsers cannot open files next to a picked file, so allow read access to its folder once: <button class="btn sm" data-act="pick-folder">${icon("folder")} Read its artifacts</button></span></p>`);
      }
    };
    const scan = async (folder) => {
      report.innerHTML = `<p class="muted" id="imp-progress">Scanning <code>${esc(folder.name)}</code>…</p>`;
      try {
        const entries = await scanProjectFolder(folder, { onProgress: (t) => { const el = $("#imp-progress", root); if (el) el.textContent = `${folder.name}: ${t}`; } });
        const done = await importEntries(entries, { replace: replaceMode(), folder: entries.folder });
        if (done) show(done.bundle, entries);
      } catch (error) {
        report.innerHTML = `<p class="callout warn">${icon("alert")}<span>${esc(error.message)}</span></p>`;
      }
    };
    on(root, "click", "[data-act=pick-folder]", async () => {
      if (!canPickDirectory()) { $("#imp-folder", root).click(); return; }
      let folder;
      try { folder = await pickProjectFolder(); } catch { return; } // cancelled
      scan(folder);
    });
    on(root, "click", "[data-act=full-folder]", () => $("#imp-folder", root).click());
    $("#imp-folder", root).addEventListener("change", (e) => handle(e.target.files));
    $("#imp-files", root).addEventListener("change", (e) => handle(e.target.files));
    const drop = $("#imp-drop", root);
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("over")));
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      const items = Array.from(e.dataTransfer.items || []);
      const pending = items.length === 1 && items[0].getAsFileSystemHandle ? items[0].getAsFileSystemHandle() : null;
      const fallback = pending ? null : filesFromDrop(e.dataTransfer);
      const h = pending ? await pending.catch(() => null) : null;
      if (h && h.kind === "directory") return scan(h);
      handle(await (fallback || filesFromDrop(e.dataTransfer)));
    });
    on(root, "click", "[data-act=demo]", () => { loadDemo(); close(); });
    on(root, "click", "[data-act=reload-server]", async () => { await loadServer(); close(); });
    const form = $("#imp-connect", root);

    const connectPaths = async (paths) => {
      let ok = 0;
      for (const path of paths) {
        try {
          const project = await server.connect(String(path));
          ok += 1;
          const label = project.display_name || project.name;
          ctx.log("info", `Connected ${label} → ${project.root_path}${project.default_manifest ? " (no alfrd.yaml there: using the default; Project settings → Save writes one)" : ""}`, "server");
          if (paths.length === 1) ctx.toast(`Project ${label} connected${project.default_manifest ? " with the default alfrd.yaml" : ""}`, "ok");
        } catch (error) {
          ctx.toast(paths.length === 1 ? error.message : `${path}: ${error.message}`, "fail");
          ctx.log("error", `Connect failed (${path}): ${error.message}`, "server");
        }
      }
      if (paths.length > 1) ctx.toast(`Connected ${ok} of ${paths.length} project(s)`, ok === paths.length ? "ok" : "warn");
      if (ok) {
        await loadServer();
        close();
      }
    };
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await connectPaths([String(new FormData(form).get("path"))]);
      });
      let browser = null;
      const browseBtn = $("[data-act=browse-server]", root);
      const closeBrowser = () => {
        browser?.destroy();
        browser = null;
        browseBtn?.setAttribute("aria-expanded", "false");
        browseBtn?.focus();
      };
      browseBtn?.addEventListener("click", async () => {
        if (browser) { closeBrowser(); return; }
        browseBtn.disabled = true;
        try {
          const { mountFolderBrowser } = await import("./components/folder_browser.js");
          if (!root.isConnected) return;
          browseBtn.setAttribute("aria-expanded", "true");
          const typed = String(form.elements.path.value || "").trim();
          browser = mountFolderBrowser($("#imp-browse", root), {
            start: typed.startsWith("/") || /^[A-Za-z]:[\\/]/.test(typed) ? typed.replace(/[\\/]\.?alfrd\.ya?ml$/i, "") : "",
            list: (path, o) => server.listFolders(path, o),
            connect: connectPaths,
            allowDefault: server.session?.default_manifest !== false,
            use: (path) => { form.elements.path.value = path; closeBrowser(); form.elements.path.focus(); },
            close: closeBrowser,
          });
        } catch (error) { ctx.toast(`Folder browser not loaded: ${error.message}`, "fail"); }
        finally { browseBtn.disabled = false; }
      });
      root.addEventListener("beforeclose", () => browser?.destroy());
    }
  });
}


async function importEntries(entries, { replace = true, folder = null } = {}) {
  const json = entries.length === 1 && /\.json$/i.test(entries[0].name) ? entries[0] : null;
  if (json && /"alfrd_studio_snapshot"/.test(json.text || "")) {
    restoreSnapshot(JSON.parse(json.text));
    return null;
  }
  let use = entries;
  if (json && /"alfrd_avica_scan"/.test(json.text || "")) {
    use = entriesFromScanBundle(JSON.parse(json.text)) || entries;
    ctx.log("info", `Scan bundle ${json.name}: ${use.length} file(s) collected from ${use.scan?.root || "?"} at ${use.scan?.generated || "?"}.`, "import");
  }
  await ensureTemplatesFor(use.find((e) => /^\.?alfrd\.ya?ml$/i.test(e.name) && typeof e.text === "string")?.text);
  const bundle = buildBundle(use, { source: "import", rootName: use.rootName });
  if (folder) {
    state.folders[bundle.alfrdProject] = folder;
    if (await saveFolderHandle(bundle.alfrdProject, folder)) ctx.log("info", `Folder "${folder.name}" remembered for Re-scan (${bundle.alfrdProject}).`, "import");
  }
  applyBundle(bundle, { replace, source: "imported" });
  ctx.toast(`Imported ${bundle.targets.length} target(s)`, bundle.messages.some((m) => m.level === "error") ? "warn" : "ok");
  if (folder && state.mode !== "server") live.resetFolder(bundle.alfrdProject);
  return { bundle };
}


/** Every project in scope, from scratch (palette: Reload all projects). */
async function reloadAll() {
  server.clearFitsCache();
  state.avica = {};
  clearWorkdirCache();
  if (await loadServer()) ctx.toast("Reloaded all projects", "ok");
}

// Refresh the selected project.
async function rescanServerProject(project) {
  const title = ctx.projectName(project);
  try {
    const runtime = await server.loadProjectRuntime({ name: project, title });
    runtime.messages.forEach((m) => ctx.log(m.level, m.text, "server"));
    const scan = await server.projectScan(project);
    const keep = state.selectedTarget;
    if (state.avica) delete state.avica[project];
    clearWorkdirCache(project);
    server.clearFitsCache(project);
    workflowCache.delete(project);
    state.serverWorkflows = [...(state.serverWorkflows || []).filter((w) => w.project !== project), ...runtime.workflows];
    const bundle = await applyServerScan(project, scan, { runtimeRows: runtime.targets });
    ctx.log("info", `${title}: ${bundle.targets.length} target(s), ${(bundle.logFiles || []).length} log file(s) from ${scan.root} (Re-scan).`, "server");
    if (ctx.scopedTargets().some((t) => t.id === keep)) state.selectedTarget = keep;
    else if (!ctx.scopedTargets().some((t) => t.id === state.selectedTarget)) state.selectedTarget = ctx.scopedTargets()[0]?.id || null;
    if (state.workflowProject === project) state.workflowProject = null;
    syncWorkflow();
    if (plansAvailable(ctx, project)) loadPlan(ctx, project, { quiet: true });
    applyAvicaParams(state.avica?.[ctx.target()?.project]);
    scheduleRender();
    live?.sync();
    ctx.toast(`Re-scanned ${title}`, "ok");
  } catch (error) {
    ctx.log("error", `Re-scan of ${title} failed: ${error.message}`, "server");
    ctx.toast(`Re-scan of ${title} failed: ${error.message}`, "fail");
  }
}

async function rescan() {
  if (state.mode === "server") {
    const project = activeProject();
    // Only the opened project; with All projects selected there is none, so reload them all.
    if (project && state.trees?.[project]?.provider === "server") await rescanServerProject(project);
    else await reloadAll();
    return;
  }
  const project = state.selectedProject !== "all" ? state.selectedProject : ctx.target()?.project || ctx.projects()[0]?.id;
  const folder = project && (state.folders[project] || (await loadFolderHandle(project)));
  if (!folder || !canPickDirectory()) {
    ctx.toast(canPickDirectory() ? "No folder remembered for this project — open it once" : "This browser cannot re-read folders; open the folder again", "warn");
    openImport();
    return;
  }
  try {
    if (!(await ensureReadPermission(folder))) throw new Error(`Read access to "${folder.name}" was not granted.`);
    ctx.toast(`Re-scanning ${folder.name}…`);
    const entries = await scanProjectFolder(folder);
    const done = await importEntries(entries, { replace: "project", folder });
    ctx.log("info", `Re-scan of ${folder.name}: ${entries.stats.files} file(s), ${entries.stats.workdirs} work dir(s) in ${(entries.stats.ms / 1000).toFixed(1)} s.`, "import");
    return done;
  } catch (error) {
    ctx.toast(error.message, "fail");
    ctx.log("error", `Re-scan failed: ${error.message}`, "import");
  }
}

async function filesFromDrop(dt) {
  const items = Array.from(dt.items || []);
  const entries = items.map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return Array.from(dt.files || []);
  const out = [];
  const walk = (entry, prefix) => new Promise((resolve) => {
    if (entry.isFile) {
      entry.file((file) => {
        try { Object.defineProperty(file, "relativePath", { value: `${prefix}${file.name}` }); } catch { /* ignore */ }
        out.push(file);
        resolve();
      }, () => resolve());
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const all = [];
      const readBatch = () => reader.readEntries(async (batch) => {
        if (!batch.length) {
          for (const child of all) await walk(child, `${prefix}${entry.name}/`);
          resolve();
        } else {
          all.push(...batch);
          readBatch();
        }
      }, () => resolve());
      readBatch();
    } else resolve();
  });
  for (const entry of entries) await walk(entry, "");
  return out;
}

function exportOverviewCsv(details = false) {
  const steps = ctx.steps();
  const header = ["target", "project", "ms_path", "status", "stages_done", "stages_total", "runtime_seconds", ...steps.map((s) => `${s}_status`)];
  if (details) steps.forEach((s) => header.push(`${s}_seconds`, `${s}_attempts`, `${s}_note`));
  header.push("notes");
  const rows = [header];
  overview.visibleTargets(ctx).forEach((t) => {
    const r = ctx.rollup(t);
    const row = [t.name, t.project, t.msPath || "", r.status, r.done, r.total, r.runtime ?? "", ...steps.map((s) => t.steps?.[s]?.status || "pending")];
    if (details) steps.forEach((s) => { const st = t.steps?.[s] || {}; row.push(st.duration ?? "", st.attempts?.length || 0, st.note || ""); });
    row.push(state.notes[t.id] || "");
    rows.push(row);
  });
  download(`alfrd-studio-${details ? "stage-details" : "overview"}.csv`, toCsv(rows), "text/csv");
  ctx.log("info", `Exported ${rows.length - 1} row(s) to CSV.`);
}

function exportWorkflowYaml() {
  const text = `# Exported from ALFRD Studio ${VERSION}\n${dumpYaml(serializeWorkflow())}`;
  download(state.workflowFile.name.replace(/(\.ya?ml)?$/, ".studio.yaml").replace(/\s+/g, "_"), text, "text/yaml");
}

function exportSnapshot() {
  const snap = {
    alfrd_studio_snapshot: 1,
    version: VERSION,
    exported_at: new Date().toISOString(),
    source: state.source,
    targets: state.targets,
    projectTitles: state.projectTitles,
    projectNames: state.projectNames,
    aliases: state.aliases,
    notes: state.notes,
    workflow: serializeWorkflow(),
    avica: stripHandles(state.avica),
    configs: state.configs,
  };
  download("alfrd-studio-snapshot.json", JSON.stringify(snap, null, 1), "application/json");
}

function restoreSnapshot(snap) {
  try {
    const info = manifestToWorkflows(snap.workflow, "snapshot workflow");
    state.projectTitles = snap.projectTitles || {};
    state.projectNames = snap.projectNames || {};
    state.notes = { ...state.notes, ...(snap.notes || {}) };
    storage.set("notes", state.notes);
    applyBundle({ targets: snap.targets || [], aliases: snap.aliases || [], avica: null, configs: snap.configs || [], workflowInfo: info, manifestFile: "snapshot" }, { replace: true, source: "imported" });
    state.avica = snap.avica || {};
    ctx.toast("Snapshot restored", "ok");
  } catch (error) {
    ctx.toast(`Snapshot invalid: ${error.message}`, "fail");
  }
}




async function sha256(text) {
  if (!globalThis.crypto?.subtle) return null;
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Agent-loop runs of a project that a changed alfrd.yaml would stop before their next turn. */
async function loopRunsInProgress(project) {
  if (state.mode !== "server" || !plansAvailable(ctx, project)) return [];
  await loadPlan(ctx, project, { quiet: true }).catch(() => null);
  const s = planOf(project);
  const runs = new Map();
  for (const p of [...(s?.plans || []), s?.plan]) {
    if (p?.id && !runs.has(p.id) && ["running", "paused", "interrupted"].includes(p.status) && (p.loop || p.turns)) runs.set(p.id, p);
  }
  return [...runs.values()];
}

async function saveManifest(project, text, { force = false } = {}) {
  // The runner stops a loop whose alfrd.yaml changed (see scheduler.manifest_hash): ask first.
  const runs = force ? [] : await loopRunsInProgress(project);
  if (runs.length && !confirm(`A run is in progress:\n${runs.map((p) => `• ${p.target || p.id} (${p.status}${p.turn ? `, turn ${p.turn}/${p.turns}` : ""})`).join("\n")}\n\n`
    + `Saving alfrd.yaml stops ${runs.length > 1 ? "them before their" : "it before its"} next turn. To continue, restore the file and resume.\n\nSave anyway?`)) {
    throw new Error("a run is in progress; alfrd.yaml was left unchanged");
  }
  const tree = state.trees[project] || {};
  const info = manifestToWorkflows(parseYaml(text), tree.manifestFile || "alfrd.yaml");
  if (state.mode === "server" && tree.provider === "server") {
    const base = tree.manifestDefault || tree.manifestText == null ? {} : (await sha256(tree.manifestText)) ? { base_hash: await sha256(tree.manifestText) } : { base_text: tree.manifestText };
    syncName(project, (await server.saveManifest(project, text, { ...base, ...(force ? { force: true } : {}) }))?.project_name);
  } else if (state.folders[project] || (await loadFolderHandle(project))) {
    const folder = state.folders[project] || (await loadFolderHandle(project));
    await writeFileToHandle(folder, tree.manifestFile || "alfrd.yaml", text);
  } else {
    download("alfrd.yaml", text, "text/yaml");
    ctx.toast("No writable folder: alfrd.yaml downloaded instead", "warn");
  }
  await ensureTemplatesFor(text);
  state.trees = { ...state.trees, [project]: { ...tree, manifestText: text, manifestDefault: false, defs: studioManifest(parseYaml(text)) } };
  workflowCache.delete(project);
  // A save that finishes after a switch must not replace the now-active project's workflow.
  if (info.workflows.length && activeProject() === project) { applyWorkflowInfo(info, tree.manifestFile || "alfrd.yaml", text); state.workflowProject = project; }
  persist();
  return info;
}

function setKeyValues(text, changes) {
  const lines = String(text || "").split(/\r?\n/);
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  const pending = { ...changes };
  const fmt = (v) => (typeof v === "boolean" ? (v ? "True" : "False") : v === null ? "None" : Array.isArray(v) || (v && typeof v === "object") ? JSON.stringify(v) : String(v));
  lines.forEach((line, i) => {
    const [body, ...rest] = line.split("#");
    const m = /^(\s*)([A-Za-z_][\w.]*)\s*=/.exec(body);
    if (m && m[2] in pending) {
      lines[i] = `${m[1]}${m[2]} = ${fmt(pending[m[2]])}${rest.length ? `  #${rest.join("#")}` : ""}`;
      delete pending[m[2]];
    }
  });
  Object.entries(pending).forEach(([k, v]) => lines.push(`${k} = ${fmt(v)}`));
  return `${lines.join("\n")}\n`;
}


async function writeAvicaConfig(project, changes) {
  const tree = state.trees[project] || {};
  const name = tree.configName || "avica.inp";
  const index = state.avica?.[project];
  let where;
  if (state.mode === "server" && tree.provider === "server") {
    const res = await server.avicaConfig(project, changes);
    if (index && res.config) { index.values = res.config.values; index.summary = res.config.summary || index.summary; }
    where = `${name} (server)`;
  } else {
    const cfg = state.configs.find((c) => c.project === project || !c.project) || { text: "" };
    const text = setKeyValues(cfg.text, changes);
    const folder = state.folders[project] || (await loadFolderHandle(project));
    if (folder && canPickDirectory()) {
      await writeFileToHandle(folder, name, text);
      where = name;
    } else {
      download(name, text, "text/plain");
      where = `${name} (downloaded)`;
    }
    cfg.text = text;
  }
  if (index) {
    Object.entries(changes).forEach(([key, value]) => {
      const dot = key.lastIndexOf(".");
      const step = dot > 0 ? key.slice(0, dot) : "";
      const param = dot > 0 ? key.slice(dot + 1) : key;
      index.values = { ...(index.values || {}), [key]: value };
      (index.summary?.rows || []).forEach((r) => {
        if (r.parameter === param && (r.step === step || (!step && (!r.step || r.step === "other")))) { r.value = value; r.source = `${name}/studio`; }
      });
    });
    applyAvicaParams(index);
  }
  persist();
  ctx.log("info", `${Object.keys(changes).length} value(s) written to ${where}: ${Object.entries(changes).map(([k, v]) => `${k} = ${v}`).join(", ")}`, "config");
  return where;
}

// ---------------------------------------------------------------------------
// Modal + menus

let clearModalKeys;
function modal(html, setup, cls = "") {
  clearModalKeys?.();
  const previousFocus = document.activeElement;
  const host = $("#modal-host");
  if (host.hidden || !host.contains(previousFocus)) host.returnFocus = previousFocus;
  host.innerHTML = `<div class="modal-back" data-close></div><div class="modal ${cls}" role="dialog" aria-modal="true">${html}</div>`;
  host.hidden = false;
  const root = $(".modal", host);
  const close = () => {
    if (!root.dispatchEvent(new Event("beforeclose", { cancelable: true }))) return;
    host.hidden = true;
    host.innerHTML = "";
    document.removeEventListener("keydown", esc_);
    if (host.returnFocus?.isConnected) host.returnFocus.focus();
    else $("#ov-export")?.focus();
    host.returnFocus = null;
  };
  const esc_ = (e) => {
    if (e.key === "Escape") { close(); return; }
    if (e.key !== "Tab") return;
    const fields = [...root.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]')].filter((el) => !el.disabled && !el.closest("[inert]") && el.getClientRects().length);
    const first = fields[0], last = fields.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
  };
  document.addEventListener("keydown", esc_);
  clearModalKeys = () => document.removeEventListener("keydown", esc_);
  host.onclick = (e) => {
    if (e.target.closest("[data-close]")) close();
  };
  setup?.(root, close);
  const focusable = root.querySelector("input,button:not([data-close]),select,textarea");
  focusable?.focus();
  return close;
}
ctx.modal = modal;

function menu(anchor, items) {
  closeMenus();
  const el = document.createElement("div");
  el.className = "menu";
  el.setAttribute("role", "menu");
  el.id = `studio-menu-${Date.now()}`;
  anchor.setAttribute("aria-controls", el.id);
  anchor.setAttribute("aria-expanded", "true");
  el.innerHTML = items.map((it, i) => (it === "-" ? `<hr>` : `<button role="menuitem" data-i="${i}" ${it.disabled ? "disabled" : ""}>${icon(it.icon || "caret")}<span>${esc(it.label)}</span>${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</button>`)).join("");
  document.body.appendChild(el);
  const r = anchor.getBoundingClientRect();
  el.style.top = `${r.bottom + 6}px`;
  el.style.left = `${Math.min(r.left, window.innerWidth - el.offsetWidth - 12)}px`;
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (!b) return;
    closeMenus();
    anchor.focus();
    items[Number(b.dataset.i)].run?.();
  });
  const keys = (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenus(); anchor.focus(); }
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const buttons = [...el.querySelectorAll("button:not(:disabled)")];
      const current = buttons.indexOf(document.activeElement);
      const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
        : (current + (event.key === "ArrowUp" ? -1 : 1) + buttons.length) % buttons.length;
      buttons[index]?.focus();
    } else if (event.key === "Tab") closeMenus();
  };
  document.addEventListener("keydown", keys, true);
  el.cleanup = () => { document.removeEventListener("keydown", keys, true); document.removeEventListener("click", closeMenus); anchor.setAttribute("aria-expanded", "false"); };
  el.querySelector("button:not(:disabled)")?.focus();
  setTimeout(() => { if (el.isConnected) document.addEventListener("click", closeMenus, { once: true }); }, 0);
  return () => { el.cleanup(); el.remove(); };
}
function closeMenus() {
  $$(".menu").forEach((m) => { m.cleanup?.(); m.remove(); });
}
ctx.menu = menu;

// Studio settings: a lazy module (components/settings_dialog.js) driving these shell functions.
async function openSettings() {
  try {
    lazy.settings ||= import("./components/settings_dialog.js");
    (await lazy.settings).openSettings(ctx, { live, setLive, newProject, openProject, quitServer, loadServer, loadDemo, switchProject, removeWorkspace, resetAttachments, forgetFolderHandles });
  } catch (error) { lazy.settings = null; ctx.toast(`Settings: ${error.message}`, "fail"); }
}

const showProject = async (project, template) => {
  await loadServer();
  switchProject(project.identifier, { restoreView: false });
  ctx.navigate(template === "agent-loop" ? "workflow" : "overview");
};
// Create (path: prefilled folder) and Open (a folder with alfrd.yaml) live in agent_dialog.js.
async function newProject(path, open) {
  try { await (await import("./components/agent_dialog.js"))[open ? "openProject" : "openCreateProject"](ctx, showProject, path, newProject); }
  catch (error) { ctx.toast(error.message, "fail"); }
}
const openProject = () => newProject("", 1);

/** After removal: drop only that project's workspace, run polling and Log Stream tabs. */
function removeWorkspace(project) {
  state.avica = { ...state.avica };
  delete state.avica[project];
  delete state.trees[project];
  workflowCache.delete(project);
  overview.forgetProject(project);
  results.forgetProject(project);
  forgetPlan(project);
  dockedLogs().filter((d) => d.project === project).forEach((d) => undockLog(d.key));
  [...tabErrors.keys()].filter((k) => k.startsWith(`${project}\u0000`)).forEach((k) => tabErrors.delete(k));
  if (state.selectedProject === project) switchProject("all", { render: false });
  dropWorkspace(project);
  renderConsole();
}

async function quitServer() {
  if (!confirm("Quit alfrd?\n\nThe server stops. This page stops updating. Start it again with `alfrd serve`.")) return;
  try {
    await server.quit();
  } catch (error) {
    ctx.toast(error.message, "fail");
    return;
  }
  ctx.log("info", "alfrd serve is stopping.", "server");
  window.close();
  await new Promise((r) => setTimeout(r, 400));
  if (window.closed) return;
  modal(`<header class="modal-h"><h2>${icon("power")} alfrd serve stopped</h2></header>
    <div class="modal-b"><p>The server is shut down. The browser did not let the Studio close this tab — please close it yourself.</p>
    <p class="muted small">Start it again with <code>alfrd serve</code> in the project folder, then reload this page.</p>
    <div class="row gap right"><button class="btn" onclick="location.reload()">${icon("sync")} Reload</button></div></div>`, null);
  $("#modal-host").onclick = null; // keep the notice up
}

function openAliasRules() {
  goTo("config");
}


function rememberTarget() {
  if (!state.selectedProject || !state.selectedTarget) return;
  const targets = storage.get("ui:app.targets", {});
  storage.set("ui:app.targets", { ...targets, [state.selectedProject]: state.selectedTarget });
}

/** Make id the header target (as the Target picker does). */
function selectTarget(id) {
  state.selectedTarget = chooseTarget(ctx.scopedTargets(), id);
  rememberTarget();
  history.replaceState(null, "", routeHash(state.view, state.selectedProject, state.selectedTarget));
  scheduleRender();
}
ctx.selectTarget = selectTarget;

function goTo(view) {
  rememberTarget();
  const url = routeHash(view, state.selectedProject, state.selectedTarget);
  if (location.hash !== url) location.replace(url);
}
ctx.openAliasRules = openAliasRules;

ctx.palette = { providers: [], register(fn) { if (!this.providers.includes(fn)) this.providers.push(fn); } };
ctx.views = VIEWS.map(({ id, label, icon: ic }) => ({ id, label, icon: ic }));
ctx.goTo = goTo;
ctx.rescan = () => rescan();
ctx.reloadAll = () => reloadAll();
ctx.setLive = (on) => setLive(on);

ctx.openNotes = async (project, where = {}, opts = {}) => {
  try {
    lazy.notes ||= import("./components/notes_panel.js");
    (await lazy.notes).openNotes(ctx, project, where, opts);
  } catch (error) {
    lazy.notes = null;
    ctx.toast(`Notes: ${error.message}`, "fail");
  }
};
document.addEventListener("click", (e) => {
  const b = e.target.closest?.("[data-notes]");
  if (!b) return;
  e.stopPropagation();
  let where = {};
  try { where = JSON.parse(b.dataset.notes || "{}"); } catch { /* keep {} */ }
  ctx.openNotes(b.dataset.notesProject, where);
}, true);
ctx.palette.register((c) => Object.keys(state.trees || {}).flatMap((p) => projectNotes(c, p).filter((n) => n.status === "open").map((n) => ({
  id: `note:${n.id}`, group: "note", icon: "file", label: n.text.slice(0, 70),
  detail: `note · ${[n.anchor.target, n.anchor.step, n.anchor.file && `${n.anchor.file.split("/").pop()}${n.anchor.line ? `:${n.anchor.line}` : ""}`].filter(Boolean).join(" · ")}${n.tags.length ? ` · #${n.tags.join(" #")}` : ""}`,
  run: () => c.openNotes(p, {}, { focus: n.id }),
}))));


ctx.openSearch = async (query = "") => {
  if (state.mode !== "server") { ctx.toast("Full-text search needs alfrd serve", "warn"); return; }
  try {
    lazy.search ||= import("./components/search_view.js");
    (await lazy.search).openSearch(ctx, query);
  } catch (error) {
    lazy.search = null;
    ctx.toast(`Search: ${error.message}`, "fail");
  }
};
ctx.openPalette = async (query = "") => {
  try {
    lazy.palette ||= import("./components/palette.js");
    (await lazy.palette).openPalette(ctx, query);
  } catch (error) {
    lazy.palette = null;
    ctx.toast(`Palette: ${error.message}`, "fail");
  }
};
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); ctx.openPalette(); }
});


const MAX_ONLY = 150; // more changed files than this: re-read the whole project
const pending = new Map(); // project -> {changed:Set, removed:Set, full:boolean}
let refreshTimer = null;
let runtimeTimer = null;
let refreshing = false;

function projectsFollowed() {
  if (state.mode !== "server") return [];
  const shown = new Set([...Object.keys(state.trees || {}), ...ctx.projects().map((p) => p.id)]);
  if (Array.isArray(server.session?.projects)) server.session.projects.forEach((p) => shown.add(p));
  return [...shown].filter((p) => p && p !== DEMO_ALFRD_PROJECT).sort();
}

function queueRefresh(project, { changed = [], removed = [], full = false } = {}) {
  const job = pending.get(project) || { changed: new Set(), removed: new Set(), full: false };
  changed.forEach((r) => job.changed.add(r));
  removed.forEach((r) => job.removed.add(r));
  const layout = changed.some((rel) => /^\.?alfrd\.ya?ml$/.test(rel) || /^[^/]+\.(inp|json|txt)$/.test(rel));
  job.full = job.full || full || layout || job.changed.size > MAX_ONLY;
  pending.set(project, job);
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(runRefresh, 500); // debounce bursts (AVICA writes several files at once)
}

async function runRefresh() {
  if (refreshing) { refreshTimer = setTimeout(runRefresh, 500); return; }
  refreshing = true;
  const jobs = [...pending.entries()];
  pending.clear();
  let touched = false;
  try {
    for (const [project, job] of jobs) {
      try {
        touched = (await refreshProject(project, job)) || touched;
      } catch (error) {
        ctx.log("warn", `Live update of ${ctx.projectName(project)} failed: ${error.message}`, "live");
      }
    }
  } finally {
    refreshing = false;
  }
  if (touched) liveRender();
}


async function refreshProject(project, job) {
  if (state.mode === "server") {
    const old = state.scans[project];
    let scan;
    if (!old || job.full) scan = await server.projectScan(project);
    else {
      const rels = [...job.changed];
      const part = rels.length ? await server.projectScan(project, rels) : { files: [] };
      const byRel = new Map(old.files.map((f) => [f.rel, f]));
      job.removed.forEach((rel) => byRel.delete(rel));
      (part.files || []).forEach((f) => byRel.set(f.rel, f));
      rels.filter((rel) => !(part.files || []).some((f) => f.rel === rel)).forEach((rel) => byRel.delete(rel)); // gone again
      scan = { ...old, ...part, live: old.live, files: [...byRel.values()], ms_paths: part.ms_paths || old.ms_paths };
    }
    forgetLogs(project, [...job.removed]);
    await applyServerScan(project, scan, { live: true });
    ctx.log("info", `${ctx.projectName(project)}: ${job.full ? "re-read" : `${job.changed.size} file(s) re-read, ${job.removed.size} gone`} (live).`, "live");
    return true;
  }
  const folder = state.folders[project] || (await loadFolderHandle(project));
  if (!folder || !(await ensureReadPermission(folder, { request: false }))) return false;
  const entries = await scanProjectFolder(folder);
  await ensureTemplatesFor(entries.find((e) => /^\.?alfrd\.ya?ml$/i.test(e.name) && typeof e.text === "string")?.text);
  const bundle = buildBundle(entries, { source: "import", rootName: entries.rootName });
  keepWorkflow(project, bundle);
  forgetLogs(project, [...job.removed]);
  applyBundle(bundle, { replace: "project", source: "imported", quiet: true, render: false });
  ctx.log("info", `${ctx.projectName(project)}: folder re-read (${job.changed.size} changed, ${job.removed.size} gone; live).`, "live");
  return true;
}

function liveLogs(project, logs) {
  const files = state.trees?.[project]?.logFiles || [];
  Object.entries(logs).forEach(([rel, [size, mtime]]) => {
    const item = files.find((f) => f.rel === rel);
    if (item) { item.size = size; item.mtime = mtime * 1000; }
  });
  nudgeLogs(project, logs);
}


function liveRuntime() {
  clearTimeout(runtimeTimer);
  runtimeTimer = setTimeout(async () => {
    try {
      const data = await server.loadAll();
      state.serverWorkflows = data.workflows;
      const served = new Set(data.projects.map((p) => p.name));
      const fromRuntime = (p) => served.has(p) && state.targetOrigin[p] !== "tree";
      const keep = state.targets.filter((t) => !fromRuntime(t.project));
      const rows = data.targets.filter((t) => fromRuntime(t.project));
      rows.forEach((t) => { state.targetOrigin[t.project] = "runtime"; });
      state.targets = [...keep, ...rows];
      liveRender();
    } catch (error) {
      ctx.log("warn", `Runtime refresh failed: ${error.message}`, "live");
    }
  }, 800);
}

const live = createLive({
  mode: () => state.mode,
  watchHidden: () => state.mode === "server" && browserEnabled(),
  available: () => server.session?.live?.enabled !== false,
  projects: projectsFollowed,
  folders: () => (state.mode === "server" ? [] : Object.entries(state.folders).filter(([p, h]) => h && state.trees?.[p]).map(([project, handle]) => ({ project, handle }))),
  scanState: (project) => {
    const scan = state.scans[project];
    return scan ? { ts: scan.generated_ts ?? null, epoch: scan.live?.epoch, version: scan.live?.version ?? 0 } : null;
  },
  onTree: (project, diff) => queueRefresh(project, diff),
  onLogs: liveLogs,
  onRuntime: liveRuntime,
  onPluginJob: () => renderJobs(),
  onAlfrd: (project, events) => handleNotifications(ctx, project, events).catch((error) => ctx.log("warn", `Notifications: ${error.message}`, "server")),
  onResync: (project) => queueRefresh(project, { full: true }),
  onStatus: (status) => { state.liveStatus = status; renderLiveBadge(); },
});
live.enabled = state.prefs.live !== false;
setTails(live.enabled);
// Presence (Telegram "mute while active") loads on the first click or key, off the startup path.
let presence;
const loadPresence = () => (presence ||= import("./data/presence.js")
  .then((m) => m.trackPresence({ enabled: () => state.mode === "server" })(), () => {}));
for (const name of ["pointerdown", "keydown"]) document.addEventListener(name, loadPresence, { once: true, capture: true });

let pointerDown = 0; // time of an unreleased press (dragging on the canvas, selecting text)
let typingUntil = 0; // keys pressed in a field: hold live renders for a moment
let renderWaiting = false;
["keydown", "input", "compositionstart", "compositionupdate"].forEach((t) => document.addEventListener(t, (e) => {
  if (e.target?.closest?.("#main") && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) typingUntil = Date.now() + 2500;
}, true));
document.addEventListener("focusout", () => { if (renderWaiting) setTimeout(liveRender, 50); }, true);
const released = () => { pointerDown = 0; if (renderWaiting) setTimeout(liveRender, 0); };
document.addEventListener("pointerdown", () => { pointerDown = Date.now(); }, true);
["pointerup", "pointercancel"].forEach((t) => document.addEventListener(t, released, true));
window.addEventListener("blur", released);


function elementKey(el, root) {
  if (el.id) return `#${el.id}`;
  const path = [];
  for (let n = el; n && n !== root; n = n.parentElement) {
    if (n.id) { path.unshift(`#${n.id}`); break; }
    path.unshift(`${n.tagName}:${Array.prototype.indexOf.call(n.parentElement?.children || [], n)}`);
  }
  return path.join(">");
}
function findByKey(key, root) {
  if (key.startsWith("#") && !key.includes(">")) return document.getElementById(key.slice(1));
  let n = root;
  for (const part of key.split(">")) {
    if (!n) return null;
    if (part.startsWith("#")) { n = document.getElementById(part.slice(1)); continue; }
    const [tag, i] = part.split(":");
    const c = n.children[Number(i)];
    n = c && c.tagName === tag ? c : null;
  }
  return n;
}


function liveRender() {
  if (pointerDown && Date.now() - pointerDown < 15000) { renderWaiting = true; return; }
  const act = document.activeElement;
  const editing = act?.tagName === "TEXTAREA" && act.closest("#main");
  if (editing || Date.now() < typingUntil) {
    renderWaiting = true;
    if (!editing) setTimeout(liveRender, Math.max(200, typingUntil - Date.now() + 50));
    return;
  }
  renderWaiting = false;
  const view = $(`#view-${state.view}`);
  const main = $("#main");
  const scrolls = [];
  [main, ...(view ? view.querySelectorAll("*") : [])].forEach((el) => {
    if (el && (el.scrollTop || el.scrollLeft) && !el.matches?.("pre[data-log-body]")) scrolls.push([elementKey(el, view), el.scrollTop, el.scrollLeft]);
  });
  const active = document.activeElement;
  const focus = active && view?.contains(active) && active !== document.body
    ? { key: elementKey(active, view), start: active.selectionStart, end: active.selectionEnd, value: active.value }
    : null;
  if (renderQueued) cancelAnimationFrame(renderFrame);
  renderQueued = false;
  renderAll();
  scrolls.forEach(([key, top, left]) => {
    const el = key === "#main" ? main : findByKey(key, view);
    if (el) { el.scrollTop = top; el.scrollLeft = left; }
  });
  if (focus) {
    const el = findByKey(focus.key, view);
    if (el && el.focus) {
      el.focus({ preventScroll: true });
      if (focus.value != null && "value" in el && el.value !== focus.value && el.tagName !== "SELECT") el.value = focus.value;
      try { if (focus.start != null) el.setSelectionRange(focus.start, focus.end); } catch { /* not a text field */ }
    }
  }
}

function setLive(on) {
  ctx.setPrefs({ live: Boolean(on) });
  live.setEnabled(on);
  setTails(on);
  ctx.log("info", `Live updates ${on ? "on" : "off"}.`, "live");
}

const LIVE_LABEL = { live: "Live", polling: "Live", paused: "Paused", reconnecting: "Reconnecting", permission: "Live paused", unavailable: "Live off", off: "Live off" };

function renderLiveBadge() {
  const b = $("#btn-live");
  if (!b) return;
  const st = state.liveStatus || {};
  const kind = st.state || "off";
  b.className = `live-badge live-${kind}`;
  b.querySelector("span").textContent = LIVE_LABEL[kind] || kind;
  const ago = st.last ? Math.max(0, Math.round((Date.now() - st.last.getTime()) / 1000)) : null;
  b.title = [
    kind === "permission" ? "Click to allow reading the folder again" : live.enabled ? "Click to turn live updates off" : "Click to turn live updates on",
    st.detail,
    ago != null ? `last check ${ago < 2 ? "just now" : `${ago} s ago`}` : "",
    st.interval ? `every ${st.interval < 10 ? st.interval.toFixed(1) : Math.round(st.interval)} s while busy` : "",
  ].filter(Boolean).join(" · ");
  b.setAttribute("aria-pressed", live.enabled ? "true" : "false");
}
setInterval(() => { if (!document.hidden) renderLiveBadge(); }, 5000);

async function onLiveBadge() {
  if (state.liveStatus?.state === "permission") {
    for (const [, handle] of Object.entries(state.folders)) {
      try { await ensureReadPermission(handle); } catch { /* declined */ }
    }
    live.kick();
    return;
  }
  setLive(!live.enabled);
}


// Desktop sidebar: 240 px or a 64 px icon rail (≥1280 px; narrower windows keep their own layout).
// Collapsed (icon rail) by default; "shell.v2" so earlier saved "expanded" states start collapsed once.
const shellUi = loadUi("shell.v2", { sidebarCollapsed: true });
function setSidebar(collapsed) {
  shellUi.sidebarCollapsed = collapsed === true;
  $("#app").dataset.sidebar = shellUi.sidebarCollapsed ? "collapsed" : "expanded";
  const b = $("#btn-rail");
  if (b) {
    const label = `${shellUi.sidebarCollapsed ? "Expand" : "Collapse"} sidebar`;
    b.setAttribute("aria-expanded", String(!shellUi.sidebarCollapsed));
    b.setAttribute("aria-label", label);
    b.title = `${label} (Alt+Shift+S)`;
  }
  storage.set("ui:shell.v2", { sidebarCollapsed: shellUi.sidebarCollapsed }); // at once: a reload right after must keep it
}
document.addEventListener("keydown", (e) => {
  if (!(e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === "KeyS") || e.isComposing) return;
  if (e.target.closest?.("input, textarea, select, [contenteditable]") || !$("#modal-host").hidden || !matchMedia("(min-width: 1280px)").matches) return;
  e.preventDefault();
  setSidebar(!shellUi.sidebarCollapsed);
});
ctx.palette.register(() => (matchMedia("(min-width: 1280px)").matches ? [{
  id: "sidebar", group: "action", icon: "sidebar", label: shellUi.sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar", detail: "action · Alt+Shift+S",
  run: () => setSidebar(!shellUi.sidebarCollapsed),
}] : []));

const pickers = {};
const PROJECTS_LOCAL = "Creating or opening a project needs a browser on the server machine (loopback).";

/** The Projects button: New project (with Open… inside); browser mode just lists projects. */
function projectsAction() {
  if (state.mode !== "server") { pickers.project.open(); return; }
  if (!server.session?.mutations_enabled) {
    modal(`<header class="modal-h"><h2>${icon("folder")} Projects</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
      <div class="modal-b"><p class="callout warn small">${icon("alert")}<span>${esc(PROJECTS_LOCAL)}</span></p><p class="muted small">Switch between the projects already open with the project list next to this button.</p></div>`);
    return;
  }
  newProject();
}

/** Import / Export menu (header) and the palette: import first, then every export. */
function dataItems() {
  return [
    { label: "Import…", icon: "upload", hint: "results, configuration, snapshot", run: () => openImport() },
    "-",
    // Agent loops export run history (same exporter as the Overview), never a target-shaped file.
    ...(overview.loopOnly(ctx) ? [] : [
      { label: "Overview CSV (visible rows)", icon: "download", run: () => exportOverviewCsv(false) },
      { label: "Stage details CSV", icon: "download", run: () => exportOverviewCsv(true) }]),
    ...overview.historyItems(ctx),
    { label: "Workflow YAML (with edits)", icon: "file", run: exportWorkflowYaml },
    { label: "Studio snapshot (JSON)", icon: "database", hint: "re-importable", run: exportSnapshot },
  ];
}
ctx.dataItems = dataItems;
ctx.palette.register(() => [
  ...(state.mode === "server" && server.session?.mutations_enabled ? [
    { id: "project:new", group: "action", icon: "plus", label: "New project…", detail: "action · create a project", run: () => newProject() },
    ...(server.canBrowse() ? [{ id: "project:open", group: "action", icon: "folder", label: "Open project…", detail: "action · a folder with alfrd.yaml", run: openProject }] : [])] : []),
  { id: "project:switch", group: "action", icon: "folder", label: "Switch project…", detail: "action · project list", run: () => pickers.project?.open() },
  ...dataItems().filter((it) => it !== "-").map((it) => ({ id: `data:${it.label}`, group: "action", icon: it.icon, label: /^(Import|Export)/.test(it.label) ? it.label : `Export ${it.label}`, detail: "action · Import / Export", run: it.run })),
]);

function renderShell() {
  $("#app").dataset.sidebar = shellUi.sidebarCollapsed === true ? "collapsed" : "expanded";
  $("#app").innerHTML = `
    <header class="topbar">
      <div class="brand">${LOGO}<h1>alfrd</h1></div>
      <span class="mode-badge" id="mode-badge"></span>
      <span class="vsep"></span>
      <div class="picker split" role="group" aria-label="Project">
        <button class="picker-head" id="btn-projects" title="New project or open a project">${icon("folder")}<span class="picker-l">Projects</span></button>
        <button class="picker-btn" id="pick-project"><span class="picker-val"></span>${icon("chevron", "picker-chev")}</button>
      </div>
      <div class="picker"><button class="picker-btn" id="pick-target">${icon("target")}<span class="picker-l">Target</span><span class="picker-val"></span>${icon("chevron", "picker-chev")}</button></div>
      <button class="btn sm jobs-btn" id="btn-jobs" aria-haspopup="dialog" hidden>${icon("play")} <span id="jobs-label">Jobs</span></button><span class="sr-only" id="jobs-live" aria-live="polite"></span>
      <span class="grow"></span>
      <button class="icon-btn" id="btn-palette" aria-label="Command palette" title="Go to anything, run an action (Ctrl+K)">${icon("search")}</button>
      <button class="live-badge live-off" id="btn-live" aria-pressed="false"><i></i><span>Live off</span></button>
      <button class="icon-btn" id="btn-rescan" aria-label="Re-scan" title="Re-read the whole project folder now (live updates only re-read what changed)">${icon("sync")}</button>
      <button class="icon-btn" id="btn-data" aria-label="Import / Export" aria-haspopup="menu" title="Import / Export">${icon("transfer")}</button>
      <button class="icon-btn" id="btn-settings" aria-label="Studio settings" title="Studio settings">${icon("gear")}</button>
      <button class="icon-btn" id="btn-quit" aria-label="Quit alfrd" title="Quit alfrd" hidden>${icon("power")}</button>
    </header>
    <nav class="rail" aria-label="Workspace">
      <div class="rail-head"><span class="rail-label">Workspace</span></div>
      <div class="rail-links" id="rail-links">${VIEWS.map((v) => `<a href="${esc(routeHash(v.id, state.selectedProject, state.selectedTarget))}" data-view="${v.id}" title="${v.label}">${icon(v.icon)}<span>${v.label}</span></a>`).join("")}</div>
      <div class="rail-foot"><button class="icon-btn rail-toggle" id="btn-rail" aria-controls="rail-links" aria-keyshortcuts="Alt+Shift+S">${icon("sidebar")}</button></div>
    </nav>
    <main id="main" tabindex="-1">${VIEWS.map((v) => `<section class="view" id="view-${v.id}" data-view="${v.id}" hidden></section>`).join("")}</main>
    <footer class="footbar">
      <span>${icon("database")} <span id="foot-storage"></span></span>
      <span class="vsep"></span>
      <span id="foot-mode"></span>
      <span class="vsep"></span>
      <button class="link-btn" id="foot-logs">${icon("log")} <span id="foot-logcount"></span></button>
      <span class="grow"></span>
      <span id="footer-right"></span>
    </footer>
    <section class="dock-strip" id="dock-strip" hidden aria-label="Followed log"></section>
    <section class="console" id="console" hidden aria-label="Log stream"></section>`;
  $("#btn-jobs").addEventListener("click", async (e) => {
    try {
      lazy.jobs ||= import("./components/jobs_tray.js");
      (await lazy.jobs).openJobs(ctx, e.currentTarget);
    } catch (error) { lazy.jobs = null; ctx.toast(`Jobs: ${error.message}`, "fail"); }
  });
  $("#dock-strip").addEventListener("click", (e) => {
    const a = e.target.closest("[data-strip]");
    const key = $("#dock-strip").dataset.key;
    if (!a || !key) return;
    const d = dockedLogs().find((x) => x.key === key);
    if (!d) return;
    if (a.dataset.strip === "follow") setDockFollow(key, !dockInfo(key).follow);
    if (a.dataset.strip === "open") switchProject(d.project);
    if (a.dataset.strip === "expand") { state.consoleTab = key; state.consoleOpen = true; renderConsole(); }
  });

  $("#btn-rail").addEventListener("click", () => setSidebar(!shellUi.sidebarCollapsed));
  setSidebar(shellUi.sidebarCollapsed);
  $("#btn-projects").addEventListener("click", projectsAction);
  $("#btn-rescan").addEventListener("click", () => rescan());
  $("#btn-live").addEventListener("click", onLiveBadge);
  $("#btn-palette").addEventListener("click", () => ctx.openPalette());
  $("#btn-data").addEventListener("click", (e) => menu(e.currentTarget, dataItems()));
  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-quit").addEventListener("click", quitServer);
  pickers.project = mountPicker($("#pick-project"), { label: "Project", onChange: (value) => switchProject(value) });
  $("#main").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ws-open]");
    if (b) switchProject(b.dataset.wsOpen, { restoreView: false });
  });
  pickers.target = mountPicker($("#pick-target"), { label: "Target", empty: "No targets", onChange: selectTarget });
  $("#foot-logs").addEventListener("click", () => {
    state.consoleOpen = !state.consoleOpen;
    renderConsole();
  });

  VIEWS.forEach((v) => v.mod.mount?.($(`#view-${v.id}`), ctx));
}

function renderHeader() {
  VIEWS.forEach((v) => { const a = $(`[data-view="${v.id}"]`); if (a) a.href = routeHash(v.id, state.selectedProject, state.selectedTarget); });
  const badge = $("#mode-badge");
  const live = state.mode === "server";
  $("#btn-projects").title = live ? (server.session?.mutations_enabled ? "New project or open a project" : PROJECTS_LOCAL) : "Choose a project";
  badge.className = `mode-badge ${live ? "live" : ""}`;
  const quit = $("#btn-quit");
  if (quit) quit.hidden = !(live && server.session?.can_quit);
  const modeText = `${live ? "Server mode" : "Browser mode"}${state.source === "demo" ? " · demo" : state.source === "imported" ? " · imported" : state.source === "server" ? " · runtime" : ""}`;
  badge.innerHTML = `<i></i><span>${modeText}</span>`;
  badge.title = `${modeText}: ${live ? "projects come from the runtime database; re-queue runs through the runtime API." : "everything runs in this browser; no pipeline code is executed."}`;

  const rescanBtn = $("#btn-rescan");
  if (rescanBtn) rescanBtn.title = live ? (activeProject() ? `Re-scan ${ctx.projectName(activeProject())}` : "Re-scan all projects") : "Re-read the whole project folder now (live updates only re-read what changed)";

  const projects = ctx.projects();
  const labels = projectLabels(projects.map((p) => ({ id: p.id, label: p.title || p.name, root: state.projectRoots?.[p.id] || "" })));
  pickers.project.set([{ value: "all", label: `All projects (${projects.length})` }, ...projects.map((p) => ({
    value: p.id, label: labels[p.id], detail: state.projectRoots?.[p.id] || "", badge: isDirty(p.id) ? "unsaved" : "",
  }))], state.selectedProject || "all");
  pickers.target.set(ctx.scopedTargets().map((t) => ({
    value: t.id, label: t.name, detail: state.selectedProject === "all" ? state.projectTitles[t.project] || ctx.projectName(t.project) : "",
  })), state.selectedTarget);
  $$(".rail a").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === state.view);
    if (a.dataset.view === state.view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });

}

// Footer: CPU and RAM of the server machine as two small bars, every 5 s while
// the tab is visible; the numbers are in the tooltip.
let systemHtml = "", systemTitle = "", systemTimer = null;
const SYSTEM_HINT = "CPU and memory of the machine running alfrd serve";
const gib = (n) => (n / 1024 ** 3).toFixed(1);
function sysMeter(label, pct) {
  const v = Math.max(0, Math.min(100, Math.round(pct)));
  const level = v >= 90 ? "fail" : v >= 70 ? "warn" : "ok";
  return `<span class="sys-meter" role="meter" aria-label="${label}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${v}">${label}<i class="sys-bar"><b class="${level}" style="width:${v}%"></b></i></span>`;
}
async function pollSystem() {
  if (state.mode !== "server" || document.hidden) return;
  try {
    const s = await server.system();
    systemHtml = sysMeter("CPU", s.cpu) + sysMeter("RAM", s.mem_total ? (100 * s.mem_used) / s.mem_total : 0);
    systemTitle = `${SYSTEM_HINT}\nCPU ${Math.round(s.cpu)}% · RAM ${gib(s.mem_used)} / ${gib(s.mem_total)} GB`;
    const el = $("#foot-mode .sys");
    if (el) { el.title = systemTitle; el.lastElementChild.innerHTML = systemHtml; }
  } catch { clearInterval(systemTimer); systemTimer = null; } // older server or offline: keep the label
}
function startSystemPoll() {
  if (systemTimer || state.mode !== "server") return;
  pollSystem();
  systemTimer = setInterval(pollSystem, 5000);
}

function renderFooter() {
  const st = $("#foot-storage");
  if (!st) return;
  st.textContent = `Local Storage: ${bytes(storage.size())} / Saved`;
  $("#foot-mode").innerHTML = state.mode === "server"
    ? `<span class="sys" title="${esc(systemTitle || SYSTEM_HINT)}">${icon("server")}<span class="sys-meters">${systemHtml || "alfrd serve"}</span></span>`
    : `${icon("info")} Browser mode · preview workflows and import results`;
  const errors = state.consoleLines.filter((l) => l.level === "error").length;
  const warns = state.consoleLines.filter((l) => l.level === "warn").length;
  const info = state.consoleLines.length - errors - warns;
  const followed = dockedLogs().length;
  $("#foot-logcount").textContent = `Logs [${errors} errors, ${warns} warnings, ${info} info]${followed ? ` · ${followed} followed` : ""}`;
  $("#footer-right").innerHTML = ctx.footerRight;
}

const consoleUi = loadUi("console", { height: 280 });
function consoleHeight(h) {
  const max = Math.max(160, Math.round((window.innerHeight || 800) * 0.8));
  const v = Math.min(max, Math.max(120, Math.round(Number(h) || 280)));
  document.documentElement.style.setProperty("--console-h", `${v}px`);
  consoleUi.height = v;
  saveUi("console", consoleUi, ["height"]);
  return v;
}


// Dock tabs always name their project (a file basename alone is ambiguous across projects).
const runOf = (rel) => (/(?:^|\/)plans\/([^/]+)\//.exec(rel) || [])[1] || "";
const dockLabel = (d) => [ctx.projectName(d.project), runOf(d.rel), d.name].filter(Boolean).join(" · ");

/** Minimized Log Stream: a readable strip with the latest line, follow control and actions. */
function renderStrip() {
  const el = $("#dock-strip");
  if (!el) return;
  const docks = dockedLogs();
  const d = docks.find((x) => x.key === state.consoleTab) || docks.at(-1);
  el.hidden = state.consoleOpen || !d;
  document.body.classList.toggle("strip-open", !el.hidden);
  if (el.hidden) return;
  const info = dockInfo(d.key);
  const st = info.reconnecting ? "Reconnecting…" : info.follow ? "Following" : `Paused${info.unread ? ` · New output · ${info.unread}` : ""}`;
  el.dataset.key = d.key;
  el.innerHTML = `<b class="trunc strip-name" title="${esc(`${ctx.projectName(d.project)} · ${d.rel}`)}">${icon("log")} ${esc(dockLabel(d))}</b>
    <span class="mono trunc strip-last">${esc(info.loaded ? info.last || "(no output yet)" : info.error ? `(${info.error})` : "Loading…")}</span>
    <span class="strip-st${info.reconnecting ? " warn" : ""}">${esc(st)}</span>
    <button class="btn sm" data-strip="follow" aria-pressed="${!info.follow}">${info.follow ? `${icon("pause")} Pause follow` : `${icon("play")} Resume follow`}</button>
    ${d.project !== activeProject() ? `<button class="btn sm" data-strip="open">${icon("folder")} Open project</button>` : ""}
    <button class="btn sm" data-strip="expand">${icon("expand")} Expand</button>`;
}
onDock(() => renderStrip());

/** Jobs · N running: every active run across projects (switching never stops them). */
const jobStatus = new Map(); // `${project}\u0000${run}` -> label, for polite announcements
function jobLabel(s) {
  if (s.plan.start_at || s.loop?.phase === "awaiting_response" || s.loop?.phase === "awaiting_review" || s.waiting?.length) return "Waiting";
  return { running: "Running", paused: "Paused", interrupted: "Interrupted" }[s.plan.status] || s.plan.status;
}
function renderJobs() {
  const b = $("#btn-jobs");
  if (!b) return;
  const jobs = activeJobs();
  const plugin = server.pluginJob?.status === "running";
  b.hidden = state.mode !== "server" || (!jobs.length && !plugin);
  const labels = jobs.map((j) => jobLabel(j.status));
  if (plugin) labels.push("Running");
  const running = labels.filter((l) => l === "Running").length, waiting = labels.filter((l) => l === "Waiting").length;
  $("#jobs-label").textContent = `Jobs · ${running} running${waiting ? ` · ${waiting} waiting` : ""}${labels.length - running - waiting ? ` · ${labels.length - running - waiting} other` : ""}`;
  const changes = [];
  jobs.forEach((j, i) => {
    const k = `${j.project}\u0000${j.status.plan.id}`;
    if (jobStatus.has(k) && jobStatus.get(k) !== labels[i]) changes.push(`${ctx.projectName(j.project)} run ${j.status.plan.id}: ${labels[i]}`);
    jobStatus.set(k, labels[i]);
  });
  [...jobStatus.keys()].forEach((k) => {
    if (!jobs.some((j) => `${j.project}\u0000${j.status.plan.id}` === k)) { changes.push(`${ctx.projectName(k.split("\u0000")[0])} run ${k.split("\u0000")[1]} ended`); jobStatus.delete(k); }
  });
  if (changes.length) $("#jobs-live").textContent = changes.join(". ");
}
ctx.jobLabel = jobLabel;
ctx.refreshJobs = renderJobs;

function renderConsole() {
  const el = $("#console");
  el.hidden = !state.consoleOpen;
  document.body.classList.toggle("console-open", state.consoleOpen);
  renderFooter();
  renderStrip();
  if (!state.consoleOpen) return;
  const docks = dockedLogs();
  if (state.consoleTab !== "studio" && !docks.some((d) => d.key === state.consoleTab)) state.consoleTab = "studio";
  const tab = state.consoleTab;
  const filter = el.dataset.filter || "all";
  if (!el.querySelector(".console-h")) {
    el.innerHTML = `<div class="console-resize" role="separator" aria-orientation="horizontal" aria-label="Resize log stream (arrow keys)" tabindex="0"></div><header class="console-h"></header><div class="console-b"></div>`;
    bindConsole(el);
  }
  const tabBtn = (id, label, extra = "", title = "") => `<span class="ctab${id === tab ? " on" : ""}" role="presentation"><button role="tab" aria-selected="${id === tab}" data-tab="${esc(id)}" title="${esc(title || label)}">${extra}<span class="trunc">${esc(label)}</span></button>${id === "studio" ? "" : `<button class="ctab-x" data-undock="${esc(id)}" aria-label="Stop following ${esc(label)}" title="Stop following">${icon("close")}</button>`}</span>`;
  const active = docks.find((d) => d.key === tab);
  const tools = tab === "studio"
    ? `<div class="seg sm">${["all", "info", "warn", "error"].map((f) => `<button data-f="${f}" class="${f === filter ? "on" : ""}">${f}</button>`).join("")}</div>
      <button class="btn sm" data-act="dl">${icon("download")} Save</button>
      <button class="btn sm" data-act="clear">Clear</button>`
    : `<span class="muted small mono trunc ctab-path" title="${esc(active.rel)}">${esc(ctx.projectName(active.project))} · ${esc(active.rel)}</span>
      <button class="btn sm" data-act="full" title="Open full screen">${icon("expand")} Full screen</button>`;
  $(".console-h", el).innerHTML = `<b class="console-title">${icon("log")} Log stream</b>
      <div class="ctabs" role="tablist" aria-label="Log stream tabs">${tabBtn("studio", "Studio")}${docks.map((d) => tabBtn(d.key, dockLabel(d), `<span class="live-dot" ${d.live ? "" : "hidden"}></span>`, `${ctx.projectName(d.project)} · ${d.rel} (following)`)).join("")}</div>
      <span class="grow"></span>${tools}
      <button class="icon-btn sm" data-act="close" aria-label="Hide log stream" title="Hide the panel (docked logs stay as tabs)">${icon("minus")}</button>`;
  const body = $(".console-b", el);
  if (tab === "studio") {
    const lines = state.consoleLines.filter((l) => filter === "all" || l.level === filter);
    body.dataset.tab = "studio";
    body.innerHTML = `<pre class="log">${lines.map((l) => `<span class="lvl-${l.level}"><span class="log-time">${l.t.toISOString().slice(11, 19)}</span> [${esc(l.scope)}] <span class="log-level">${esc(l.level.toUpperCase().padEnd(5))}</span> ${esc(l.text)}</span>`).join("\n") || '<span class="muted">No log lines yet.</span>'}</pre>`;
    const pre = $("pre", body);
    pre.scrollTop = pre.scrollHeight;
  } else if (body.dataset.tab !== tab || !body.querySelector("pre[data-dock-log]")) {
    body.dataset.tab = tab;
    mountDock(ctx, body, tab).then(() => { if (state.consoleOpen && state.consoleTab === tab) renderConsole(); });
  }
}

function bindConsole(el) {
  el.addEventListener("click", (e) => {
    const f = e.target.closest("[data-f]");
    if (f) { el.dataset.filter = f.dataset.f; renderConsole(); return; }
    const x = e.target.closest("[data-undock]");
    if (x) {
      undockLog(x.dataset.undock);
      if (state.consoleTab === x.dataset.undock) state.consoleTab = "studio";
      renderConsole();
      return;
    }
    const t = e.target.closest("[data-tab]");
    if (t) { state.consoleTab = t.dataset.tab; renderConsole(); return; }
    const a = e.target.closest("[data-act]")?.dataset.act;
    if (a === "close") { state.consoleOpen = false; renderConsole(); }
    if (a === "clear") { state.consoleLines = []; renderConsole(); }
    if (a === "dl") download("alfrd-studio.log", state.consoleLines.map((l) => `${l.t.toISOString()} [${l.scope}] ${l.level} ${l.text}`).join("\n"));
    if (a === "full") {
      const d = dockedLogs().find((x2) => x2.key === state.consoleTab);
      if (d) openFileFull(ctx, d.project, d.rel).catch((error) => ctx.toast(error.message, "warn"));
    }
  });
  el.addEventListener("keydown", (e) => {
    if (!e.target.matches?.("[role=tab]") || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const tabs = $$("[role=tab]", el);
    const i = tabs.indexOf(e.target) + (e.key === "ArrowRight" ? 1 : -1);
    const next = tabs[(i + tabs.length) % tabs.length];
    state.consoleTab = next.dataset.tab;
    renderConsole();
    $(`[role=tab][data-tab="${CSS.escape(state.consoleTab)}"]`, el)?.focus();
  });
  const grip = $(".console-resize", el);
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    grip.setPointerCapture?.(e.pointerId);
    const startY = e.clientY;
    const startH = el.getBoundingClientRect().height;
    const move = (ev) => consoleHeight(startH + (startY - ev.clientY));
    const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
  });
  grip.addEventListener("keydown", (e) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      consoleHeight(consoleUi.height + (e.key === "ArrowUp" ? 40 : -40));
    }
  });
}
ctx.renderConsole = () => { state.consoleOpen = true; renderConsole(); };

ctx.showDock = (key) => {
  state.consoleOpen = true;
  state.consoleTab = key;
  renderConsole();
  ctx.toast("Following in the Log Stream — switch views freely", "ok");
};

// Editors never fall back to some project in All projects: they ask for one.
const NEEDS_PROJECT = { workflow: "edit its workflow", metadata: "browse its metadata", config: "edit its settings" };

function askForProject(el, view) {
  const loading = view === "workflow" && activeProject() && state.workflowFile.loading;
  const ask = !activeProject() && NEEDS_PROJECT[view] && ctx.projects().length > 0;
  el.setAttribute("aria-busy", String(Boolean(loading)));
  el.classList.toggle("ws-ask", Boolean(ask || loading));
  let box = el.querySelector(":scope > .ws-choose");
  if (!ask && !loading) { box?.remove(); return false; }
  if (!box) { box = document.createElement("div"); box.className = "ws-choose empty"; el.prepend(box); }
  if (loading) { box.innerHTML = `<h2 role="status">${esc(state.workflowFile.name)}</h2>`; return true; }
  box.innerHTML = `<h2>Choose a project to ${NEEDS_PROJECT[view]}</h2><div class="row gap wrap">${ctx.projects().map((p) => `<button class="btn" data-ws-open="${esc(p.id)}">${icon("folder")} ${esc(p.title || p.name)}</button>`).join("")}</div>`;
  return true;
}

function renderAll() {
  syncWorkflow();
  saveUi("app", state, ["selectedProject", "selectedTarget"]);
  renderHeader();
  VIEWS.forEach((v) => {
    const el = $(`#view-${v.id}`);
    const active = v.id === state.view;
    el.hidden = !active;
    if (active) {
      if (!askForProject(el, v.id)) v.mod.render(el, ctx);
      const ek = errorKey(activeProject() || "all", v.id); // only the active project's errors show
      const error = tabErrors.get(ek);
      el.querySelector(".tab-error")?.remove();
      if (error) {
        const banner = document.createElement("div"); banner.className = "callout fail tab-error"; banner.setAttribute("role", "alert");
        banner.innerHTML = `<span class="grow">${esc(error.message)}</span><button class="btn sm" data-retry>Retry</button><button class="btn sm" data-dismiss>Dismiss</button>`;
        banner.querySelector("[data-dismiss]").onclick = () => { tabErrors.delete(ek); banner.remove(); };
        banner.querySelector("[data-retry]").onclick = () => { if (!error.key) { tabErrors.delete(ek); banner.remove(); } error.retry(); };
        el.prepend(banner);
      }
    }
  });
  renderFooter();
  renderStrip();
  renderJobs();
  listeners.forEach((fn) => fn(state));
}

function applyRunLink(url) {
  if (!url.plan || !url.project || !plansAvailable(ctx, url.project)) return;
  const key = JSON.stringify([url.project, url.plan, url.unit]);
  if (linkedRoute === key) return;
  linkedRoute = key;
  openLinkedRun(ctx, url.project, url.plan, url.unit).catch((error) => ctx.toast(error.message, "warn"));
}
function route() {
  const url = parseRoute(location.hash);
  const id = url.view;
  if (url.project && (url.project === "all" || ctx.projects().some((p) => p.id === url.project))) {
    if (state.selectedProject !== url.project) switchProject(url.project, { render: false, restoreView: false, syncUrl: false });
    state.selectedTarget = chooseTarget(ctx.scopedTargets(), url.target, storage.get("ui:app.targets", {})[url.project], state.selectedTarget);
    rememberTarget();
  }
  const next = VIEWS.some((v) => v.id === id) ? id : "overview";
  if (next !== state.view) VIEWS.find((v) => v.id === state.view)?.mod.leave?.(ctx);
  state.view = next;
  if (!url.plan) linkedRoute = "";
  applyRunLink(url);
  scheduleRender();
}


async function boot() {
  renderShell();
  bindLogs(ctx, document.body);
  consoleHeight(consoleUi.height);
  window.addEventListener("hashchange", route);
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#/"]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const href = a.getAttribute("href"), url = parseRoute(href);
    if (url.plan || url.project) { location.hash = href; route(); }
    else goTo(url.view);
  });
  route();
  await loadTemplate("avica", parseYaml);
  await loadDefaultManifest();
  await ensureTemplatesFor(storage.get("workflow")?.text);

  const session = await server.detect();
  if (server.authRequired) return;
  if (session?.version) VERSION = session.version;
  ctx.log("info", `Studio ${VERSION} started (${location.protocol === "file:" ? "file" : location.origin}).`);
  state.demoEnabled = Boolean(session?.demo) || new URLSearchParams(location.search).has("demo");
  if (session) {
    state.mode = "server";
    startSystemPoll();
    ctx.log("info", `Connected to alfrd ${session.version} (runtime ${session.runtime_enabled ? "on" : "off"}, mutations ${session.mutations_enabled ? "on" : "off"}).`, "server");
    const data = await loadServer();
    if (server.authRequired) return;
    if (session.default_project) ctx.log("info", `Opened project ${ctx.projectName(session.default_project)}.`, "server");
    if (!data || !data.targets.length) {
      if (state.demoEnabled) await loadDemo({ quiet: true });
      else ctx.log("info", "No project yet. Start `alfrd serve` in the folder that holds alfrd.yaml (or use Import → Connect).", "server");
    }
  } else if (state.demoEnabled) {
    await loadDemo({ quiet: true });
  } else if (restoreSaved()) {
    ctx.log("info", `Restored ${state.targets.length} imported target(s) from this browser.`);
    if (canPickDirectory()) {
      for (const p of Object.keys(state.trees || {})) {
        const h = await loadFolderHandle(p);
        if (h) state.folders[p] = h;
      }
    }
  } else {
    state.source = "empty";
  }
  if (state.mode !== "server") resolveInitialProject(); // loadServer resolves it for server mode
  setActive(state.selectedProject);
  if (session && !state.demoEnabled && !server.authRequired) {
    import("./components/plugin_api.js").then(async (p) => p.activateAll(await p.fetchPlugins(), ctx))
      .catch((error) => { if (!server.authRequired) ctx.log("error", error.message, "plugins"); });
  }
  scheduleRender();
  live.start();
}

window.alfrdStudio = { ctx, loadDemo, loadServer, applyBundle, rescan, importEntries };

// Use the server-rendered recovery page so startup and expired sessions show
// exactly the same instructions, including the port, without exposing data.
server.onAuthRequired(() => location.replace("/login"));

boot().catch((error) => {
  if (server.authRequired) return;
  console.error(error);
  const el = document.getElementById("app");
  el.insertAdjacentHTML("afterbegin", `<p class="fatal">Studio failed to start: ${esc(error.message)}</p>`);
});

export { hms, OVERALL_STATUS };
