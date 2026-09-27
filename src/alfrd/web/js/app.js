// ALFRD Studio — application controller.
//
// Owns the single state object, persistence, the header/nav/footer shell,
// import/export, the log console and routing between the five workspace views.

import { $, $$, on, esc, icon, LOGO, storage, bytes, download, hms, loadUi, saveUi, resetUi } from "./utils/dom.js";
import { stepParamsFromConfig } from "./data/avica.js";
import { clearWorkdirCache, resetAttachments } from "./components/attach.js";
import { toCsv, aliasRules } from "./utils/csv_parser.js";
import { dumpYaml, parseYaml } from "./utils/yaml_parser.js";
import { readFiles, buildBundle, entriesFromScanBundle } from "./data/importers.js";
import { canPickDirectory, pickProjectFolder, scanProjectFolder, saveFolderHandle, loadFolderHandle, forgetFolderHandles, ensureReadPermission, readFileFromHandle, readFileRange, writeFileToHandle } from "./data/folder_scan.js";
import { createLive } from "./data/live.js";
import { demoBundle, DEMO_ALFRD_PROJECT } from "./data/demo.js";
import { server } from "./data/server.js";
import { mountFolderBrowser } from "./components/folder_browser.js";
import { defaultWorkflow, manifestToWorkflows, rollup, OVERALL_STATUS } from "./data/model.js";
import { loadTemplate, loadDefaultManifest, templateName, studioManifest, applyFieldAliases } from "./data/defs.js";
import { bindLogs, nudgeLogs, forgetLogs, setTails, openFileFull, dockedLogs, undockLog, mountDock } from "./components/logview.js";
import * as logs from "./components/logs.js";
import * as overview from "./components/overview.js";
import * as canvas from "./components/canvas.js";
import * as metadata from "./components/metadata.js";
import * as results from "./components/results.js";
import * as config from "./components/alfrd_config.js";

// In server mode this is replaced during boot with the canonical Python
// package version returned by /api/studio/session. Static exports have no
// Python process to ask, so identify them without duplicating that version.
export let VERSION = "standalone";

const VIEWS = [
  { id: "overview", label: "Overview", icon: "overview", mod: overview },
  { id: "workflow", label: "Workflow", icon: "workflow", mod: canvas },
  { id: "metadata", label: "Metadata", icon: "metadata", mod: metadata },
  { id: "results", label: "Results", icon: "results", mod: results },
  { id: "logs", label: "Logs", icon: "logs", mod: logs },
  { id: "config", label: "Settings", icon: "project", mod: config },
];

// ---------------------------------------------------------------------------
// State

const state = {
  mode: "browser", // "browser" | "server"
  source: "empty", // "demo" | "imported" | "server" | "empty"
  view: "overview",
  targets: [],
  projectTitles: {},
  // Project key → ALFRD project name shown in the UI. In server mode the key is the
  // long location identifier (host.path.name); the label is the alfrd.yaml `name`.
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
  selectedProject: loadUi("app", { selectedProject: "all" }).selectedProject,
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

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  renderFrame = requestAnimationFrame(() => {
    renderQueued = false;
    renderAll();
  });
}

export const ctx = {
  state,
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
  toast(text, tone = "info") {
    const host = $("#toasts");
    const el = document.createElement("div");
    el.className = `toast toast-${tone}`;
    el.setAttribute("role", "status");
    el.innerHTML = `${icon(tone === "fail" ? "xCircle" : tone === "ok" ? "checkCircle" : tone === "warn" ? "alert" : "info")}<span>${esc(text)}</span>`;
    host.appendChild(el);
    setTimeout(() => el.classList.add("out"), 3600);
    setTimeout(() => el.remove(), 4000);
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
    return state.selectedProject === "all" ? state.targets : state.targets.filter((t) => t.project === state.selectedProject);
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
  applyWorkflowInfo,
  /** Read a listed file (log) of a project: in-memory text, File handle, server, or the remembered folder. */
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
  /**
   * Read a log from byte `offset` on (null: its last 400 kB) — for live tails.
   * → {text, offset, id, size, mtime, reset, live}; `live: false` when the file
   * cannot be re-read (dropped files without a folder handle).
   */
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
        // Not below this folder handle: show what was imported (no live tail).
      }
    }
    if (offset != null) return { text: "", offset, reset: false, live: false };
    return { text: await ctx.readFile(project, rel), offset: null, reset: true, live: false };
  },
  /** A live tail read more of a log: keep the listed size current (no re-render). */
  onLogGrew(project, rel, size, mtime) {
    const item = state.trees?.[project]?.logFiles?.find((f) => f.rel === rel);
    if (item) { item.size = size; if (mtime) item.mtime = mtime; }
  },
  /** Read an avica.logs/ file by name (crash snapshots fall back to the parsed summary). */
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

// ---------------------------------------------------------------------------
// Persistence (per-browser only; see README "Where data lives")

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
    out[k] = { ...v, logFiles: (v.logFiles || []).map(({ file, text, ...rest }) => rest) };
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
  };
}

function stripHandles(avica) {
  const out = {};
  Object.entries(avica || {}).forEach(([k, v]) => {
    out[k] = { ...v, logs: (v.logs || []).map(({ file, text, ...rest }) => rest) };
  });
  return out;
}

/** Apply `avica pipe config --summary` (or avica.inp `<step>.<param>`) values to the workflow steps. */
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

// ---------------------------------------------------------------------------
// Data loading

function applyBundle(bundle, { replace = true, source = "imported", provider = "files", quiet = false, render = true } = {}) {
  const incoming = bundle.targets || [];
  if (replace === "project") {
    // Re-scan: swap one ALFRD project's data, keep the others.
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
  if (bundle.workflowInfo && bundle.workflowInfo.workflows.length) {
    applyWorkflowInfo(bundle.workflowInfo, bundle.manifestFile || "alfrd.yaml", bundle.manifestText);
  } else if (bundle.workflowInfo) {
    state.workflowFile = { ...state.workflowFile, validated: false, errors: bundle.workflowInfo.errors, warnings: bundle.workflowInfo.warnings };
  }
  // A project re-scan only invalidates that project's work dirs.
  clearWorkdirCache(replace === "project" ? bundle.alfrdProject : undefined);
  applyAvicaParams(bundle.avica, { quiet });
  if (!state.targets.some((t) => t.id === state.selectedTarget)) {
    const firstBad = state.targets.find((t) => rollup(t, ctx.steps()).status === "failed");
    state.selectedTarget = (firstBad || state.targets[0] || {}).id || null;
  }
  if (state.selectedProject !== "all" && !state.targets.some((t) => t.project === state.selectedProject)) state.selectedProject = "all";
  if (!quiet) {
    (bundle.messages || []).forEach((m) => ctx.log(m.level === "error" ? "error" : m.level === "warn" ? "warn" : "info", m.text, "import"));
    (bundle.aliases || []).forEach((a) => ctx.log("info", `Field alias ${a.from} → ${a.to} (${a.sources.length} file${a.sources.length === 1 ? "" : "s"})`, "schema"));
  }
  persist();
  if (render) scheduleRender();
}

function applyWorkflowInfo(info, fileName, text) {
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
  ctx.log(info.errors.length ? "error" : "info", `Workflow "${wf?.name ?? "?"}" loaded from ${fileName}: ${wf ? wf.steps.length : 0} steps, ${info.errors.length} error(s), ${info.warnings.length} warning(s).`, "workflow");
  canvas.resetSimulation?.();
  scheduleRender();
}

async function loadDemo({ quiet = false } = {}) {
  if (!state.demoEnabled) {
    ctx.toast("Demo data is only available with `alfrd serve --demo` (or ?demo=1)", "warn");
    return;
  }
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
    state.serverWorkflows = data.workflows;
    state.projectTitles = Object.fromEntries(data.projects.map((p) => [p.name, p.title || p.description || p.name]));
    state.projectNames = Object.fromEntries(data.projects.map((p) => [p.name, p.title || p.manifest_name || p.name]));
    applyBundle({ targets: data.targets, messages: data.messages, aliases: [], configs: [], logs: [], files: [] }, { replace: true, source: "server", provider: "server" });
    if (data.workflows.length) {
      const wf = data.workflows[0];
      state.workflow = wf;
      state.workflowFile = { name: `${ctx.projectName(wf.project)} / ${wf.name} (runtime)`, validated: true, errors: [], warnings: [], text: null, modified: false };
    }
    // Each connected project's tree, read by the server exactly as alfrd.yaml describes it.
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
    const def = server.session?.default_project;
    if (def && state.targets.some((t) => t.project === def) && (state.selectedProject === "all" || !state.targets.some((t) => t.project === state.selectedProject))) {
      state.selectedProject = def;
      const tree = state.trees[def];
      if (tree?.manifestText) {
        const info = manifestToWorkflows(parseYaml(tree.manifestText), tree.manifestFile || "alfrd.yaml");
        if (info.workflows.length) applyWorkflowInfo(info, tree.manifestFile || "alfrd.yaml", tree.manifestText);
      }
      const inScope = ctx.scopedTargets();
      if (!inScope.some((t) => t.id === state.selectedTarget)) state.selectedTarget = inScope[0]?.id || null;
    }
    applyAvicaParams(state.avica?.[ctx.target()?.project]);
    ctx.log("info", `Server: ${data.projects.length} project(s), ${state.targets.length} target(s).`, "server");
    scheduleRender();
    live?.sync();
    return { ...data, targets: state.targets };
  } catch (error) {
    ctx.log("error", `Server load failed: ${error.message}`, "server");
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

/** Live refresh: don't reset the workflow canvas unless alfrd.yaml changed; never drop unsaved edits. */
function keepWorkflow(project, bundle) {
  const before = state.trees?.[project]?.manifestText;
  if (!bundle.workflowInfo) return;
  if (before != null && before === bundle.manifestText) { delete bundle.workflowInfo; return; }
  if (state.workflowFile.modified) {
    delete bundle.workflowInfo;
    ctx.toast(`${bundle.manifestFile || "alfrd.yaml"} changed on disk — your unsaved workflow edits are kept (Re-scan to load the file).`, "warn");
    return;
  }
  if (state.selectedProject !== "all" && state.selectedProject !== project) delete bundle.workflowInfo;
  else ctx.log("info", `${ctx.projectName(project)}: alfrd.yaml changed on disk — workflow reloaded.`, "live");
}

/** Fetch the templates an alfrd.yaml refers to before it is parsed. */
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

// ---------------------------------------------------------------------------
// Import / export

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
        <button class="btn primary" data-act="pick-folder">${icon("folder")} Open project folder…</button>
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
      // Only alfrd.yaml picked: offer the targeted folder scan for its artifacts.
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
      // Chromium: a dropped folder gives a directory handle → targeted scan.
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
    /** Connect server paths (one or many), then reload the server projects. */
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
      browseBtn?.addEventListener("click", () => {
        if (browser) { closeBrowser(); return; }
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
      });
    }
  });
}

/** Snapshot / scan bundle / plain entries → applyBundle. Returns null when a snapshot was restored. */
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

/** Re-read the current ALFRD project: server layout (server mode) or the remembered folder. */
async function rescan() {
  if (state.mode === "server") {
    state.avica = {};
    clearWorkdirCache();
    await loadServer();
    ctx.toast("Re-scanned server projects", "ok");
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

// Walk dropped directories (Chromium/Firefox/Safari support webkitGetAsEntry).
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

// ---------------------------------------------------------------------------
// Writes: alfrd.yaml (Project settings) and avica.inp (Workflow inspector)

/** Save alfrd.yaml for a project: server (loopback), the opened folder, or a download. */
async function saveManifest(project, text) {
  const tree = state.trees[project] || {};
  const info = manifestToWorkflows(parseYaml(text), tree.manifestFile || "alfrd.yaml");
  if (state.mode === "server" && tree.provider === "server") {
    await server.saveManifest(project, text);
  } else if (state.folders[project] || (await loadFolderHandle(project))) {
    const folder = state.folders[project] || (await loadFolderHandle(project));
    await writeFileToHandle(folder, tree.manifestFile || "alfrd.yaml", text);
  } else {
    download("alfrd.yaml", text, "text/yaml");
    ctx.toast("No writable folder: alfrd.yaml downloaded instead", "warn");
  }
  await ensureTemplatesFor(text);
  state.trees = { ...state.trees, [project]: { ...tree, manifestText: text, manifestDefault: false, defs: studioManifest(parseYaml(text)) } };
  if (info.workflows.length) applyWorkflowInfo(info, tree.manifestFile || "alfrd.yaml", text);
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

/** Write `<step>.<param> = value` lines to avica.inp and show them as the new values. */
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
  // Show the new values right away (the cached summary is re-resolved by `alfrd avica summary`).
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

function modal(html, setup, cls = "") {
  const host = $("#modal-host");
  host.innerHTML = `<div class="modal-back" data-close></div><div class="modal ${cls}" role="dialog" aria-modal="true">${html}</div>`;
  host.hidden = false;
  const root = $(".modal", host);
  const close = () => {
    host.hidden = true;
    host.innerHTML = "";
    document.removeEventListener("keydown", esc_);
  };
  const esc_ = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", esc_);
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
  el.innerHTML = items.map((it, i) => (it === "-" ? `<hr>` : `<button role="menuitem" data-i="${i}" ${it.disabled ? "disabled" : ""}>${icon(it.icon || "caret")}<span>${esc(it.label)}</span>${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</button>`)).join("");
  document.body.appendChild(el);
  const r = anchor.getBoundingClientRect();
  el.style.top = `${r.bottom + 6}px`;
  el.style.left = `${Math.min(r.left, window.innerWidth - el.offsetWidth - 12)}px`;
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (!b) return;
    closeMenus();
    items[Number(b.dataset.i)].run();
  });
  setTimeout(() => document.addEventListener("click", closeMenus, { once: true }), 0);
}
function closeMenus() {
  $$(".menu").forEach((m) => m.remove());
}
ctx.menu = menu;

function openSettings() {
  modal(`
    <header class="modal-h"><h2>Studio settings</h2><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b">
      <dl class="kv">
        <dt>Mode</dt><dd>${state.mode === "server" ? `<code>alfrd</code> ${esc(server.session?.version || "")} — runtime API ${server.session?.runtime_enabled ? "available" : "not configured"}, mutations ${server.session?.mutations_enabled ? "enabled (loopback)" : "disabled"}` : "Browser-only (static files; no backend)"}</dd>
        <dt>Data source</dt><dd>${esc(state.source)}</dd>
        <dt>Local storage</dt><dd>${bytes(storage.size())} used by this Studio in this browser</dd>
      </dl>
      ${state.mode === "server" ? `<div class="row gap"><h3>${icon("database")} Known projects</h3><span class="grow"></span><button class="btn sm" id="set-rediscover" ${server.session?.mutations_enabled ? "" : "disabled title='Only from a browser on the same machine'"} title="Register the alfrd serve folder and projects forgotten since the server started again">${icon("sync")} Rediscover</button></div>
      <p class="muted small">Every project connected to <code>alfrd serve</code> is kept in the runtime database. Click a project's <b>opened / shown / hidden</b> badge to change how this Studio lists it (no re-connect). <b>Forget</b> removes it (and its runs) from there. Files on disk are not touched. <b>Rediscover</b> / <b>Restore</b> connect a forgotten project again without restarting <code>alfrd serve</code>.</p>
      <table class="tbl small" id="set-projects"><tbody><tr><td class="muted">Loading…</td></tr></tbody></table>` : ""}
      <label class="check set-live"><input type="checkbox" id="set-live" ${live.enabled ? "checked" : ""}> <span><b>Live updates</b> — follow the project folder and open logs without Re-scan${state.mode === "server" ? (server.session?.live?.enabled === false ? " (off on this server: <code>alfrd serve --live-interval 0</code>)" : ` (server checks every ${server.session?.live?.interval ?? 2} s while busy, ${server.session?.live?.idle ?? 5} s when idle)`) : " (the remembered folder is checked every 5–30 s)"}. Nothing runs while this tab is hidden.</span></label>
      <label class="field"><span>Rows per page</span><select id="set-page" class="input">${[10, 25, 50, 100].map((n) => `<option ${state.prefs.pageSize === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <p class="muted small">Field aliases, workflow stages, step metadata and logs are edited in <a href="#/config" data-close>Settings → Project settings</a> (alfrd.yaml).</p>
      <div class="row gap right"><button class="btn" id="set-reset-ui">${icon("reset")} Reset view state</button><button class="btn danger" id="set-clear">${icon("trash")} Clear saved Studio data</button></div>
      <p class="muted small">The Studio remembers filters, folded panels, zoom, selections and folder attachments in this browser. <b>Reset view state</b> forgets them; <b>Clear</b> also removes imported data and notes.</p>
      ${state.mode === "server" ? `<h3>${icon("power")} Stop the server</h3>
      <div class="row gap"><button class="btn danger" id="set-quit" ${server.session?.can_quit ? "" : "disabled"}>${icon("power")} Quit </button>
        <span class="muted small">${server.session?.can_quit ? "Stops <code>alfrd serve</code> on this machine. Same as Ctrl+C in its terminal." : "Only from a browser on the same machine, for a server started by <code>alfrd serve</code> (not <code>--debug</code>)."}</span></div>` : ""}
    </div>`, (root, close) => {
    if (state.mode === "server") drawProjects(root);
    $("#set-quit", root)?.addEventListener("click", () => { close(); quitServer(); });
    $("#set-live", root).addEventListener("change", (e) => setLive(e.target.checked));
    $("#set-page", root).addEventListener("change", (e) => { ctx.setPrefs({ pageSize: Number(e.target.value) }); scheduleRender(); });
    $("#set-reset-ui", root).addEventListener("click", () => {
      resetUi();
      ctx.toast("View state reset — reloading", "ok");
      setTimeout(() => location.reload(), 400);
    });
    $("#set-clear", root).addEventListener("click", () => {
      resetAttachments();
      storage.clear();
      forgetFolderHandles();
      state.folders = {};
      state.notes = {};
      ctx.toast("Saved Studio data cleared", "ok");
      close();
      if (state.demoEnabled) loadDemo({ quiet: true });
      else location.reload();
    });
  });
}

const VIS_STATES = [
  { id: "opened", label: "Opened", icon: "play", tone: "tone-run", hint: "shown and selected when the Studio loads" },
  { id: "shown", label: "Shown", icon: "check", tone: "", hint: "listed in this Studio" },
  { id: "hidden", label: "Hidden", icon: "minus", tone: "tone-muted", hint: "remembered, not listed" },
];

/** Opened / shown / hidden badge; a button (state menu) when this browser may change it. */
function visBadge(current, key, label, canWrite) {
  const st = VIS_STATES.find((s) => s.id === current);
  if (!canWrite) return `<span class="badge ${st.tone}">${st.id}</span>`;
  return `<button type="button" class="badge vis-badge ${st.tone}" data-vis="${esc(key)}" data-label="${esc(label)}" data-current="${st.id}" title="Change: opened / shown / hidden" aria-haspopup="menu">${st.id}${icon("chevron")}</button>`;
}

/** Remembered projects with visibility and Forget buttons (Studio settings, server mode). */
async function drawProjects(root) {
  const table = $("#set-projects", root);
  if (!table) return;
  let list = [];
  try {
    list = await server.listProjects();
  } catch (error) {
    table.innerHTML = `<tbody><tr><td class="muted">${esc(error.message)}</td></tr></tbody>`;
    return;
  }
  let lost = [];
  try { lost = await server.rediscoverable(); } catch { /* older server */ }
  // Scope of this server (null: --all-projects, everything is shown).
  const scope = Array.isArray(server.session?.projects) ? new Set(server.session.projects) : null;
  const visOf = (key) => (key === server.session?.default_project ? "opened" : !scope || scope.has(key) ? "shown" : "hidden");
  const canWrite = server.session?.mutations_enabled;
  const btn = $("#set-rediscover", root);
  if (btn) btn.hidden = !lost.length;
  const lostRows = lost.map((c) => `<tr class="muted">
      <td class="mono"><b>${esc(c.name || c.root.split("/").pop())}</b> <span class="badge tone-muted">${c.start ? "serve folder · forgotten" : "forgotten"}</span>${c.default_manifest ? ' <span class="badge" title="No alfrd.yaml in the folder: the default one is used">default alfrd.yaml</span>' : ""}</td>
      <td class="mono small">${esc(c.root)}${c.exists ? "" : ' <span class="fail-t">(folder missing)</span>'}</td>
      <td class="right"><button class="btn sm" data-restore="${esc(c.root)}" ${canWrite && c.exists ? "" : "disabled"}>${icon("sync")} Restore</button></td></tr>`).join("");
  table.innerHTML = `<thead><tr><th>Project</th><th>Folder</th><th></th></tr></thead><tbody>${list.map((p) => { const key = p.identifier || p.name; return `<tr>
      <td class="mono"><b>${esc(p.display_name || p.name)}</b> ${visBadge(visOf(key), key, p.display_name || p.name, canWrite)}</td>
      <td class="mono small">${esc(p.root_path || "")}</td>
      <td class="right"><button class="btn sm danger" data-forget="${esc(key)}" data-label="${esc(p.display_name || p.name)}" ${canWrite ? "" : "disabled title='Only from a browser on the same machine'"}>${icon("trash")} Forget</button></td></tr>`; }).join("") || (lost.length ? "" : '<tr><td class="muted" colspan="3">No projects remembered.</td></tr>')}${lostRows}</tbody>`;
  const restore = async (rootPath, b) => {
    if (b) b.disabled = true;
    try {
      const res = await server.rediscover(rootPath);
      res.restored.forEach((r) => ctx.log("info", `Project ${r.name} connected again (${r.root}${r.default_manifest ? ", default alfrd.yaml" : ""}).`, "server"));
      res.failed.forEach((f) => ctx.log("error", `${f.root}: ${f.error}`, "server"));
      ctx.toast(res.restored.length ? `Restored ${res.restored.map((r) => r.name).join(", ")}` : "Nothing to restore", res.failed.length ? "warn" : "ok");
      await loadServer();
      drawProjects(root);
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
        if (state.selectedProject === key) state.selectedProject = "all";
      }
      await loadServer();
      if (want === "opened" && state.targets.some((t) => t.project === key)) {
        state.selectedProject = key;
        const inScope = ctx.scopedTargets();
        if (!inScope.some((t) => t.id === state.selectedTarget)) state.selectedTarget = inScope[0]?.id || null;
        scheduleRender();
      }
      ctx.toast(`${label}: ${want}${want !== "hidden" && !state.targets.some((t) => t.project === key) ? " (no targets yet, so not in the header picker)" : ""}`, "ok");
    } catch (error) {
      ctx.toast(error.message, "fail");
    }
    drawProjects(root);
  };
  table.onclick = async (e) => {
    const v = e.target.closest("[data-vis]");
    if (v) {
      e.stopPropagation();
      const { vis: key, label, current } = v.dataset;
      const items = VIS_STATES.map((st) => ({
        icon: st.icon, label: st.label, hint: st.id === current ? "current" : st.hint, disabled: st.id === current,
        run: () => setVis(key, label, st.id),
      }));
      menu(v, items);
      return;
    }
    const r = e.target.closest("[data-restore]");
    if (r) { restore(r.dataset.restore, r); return; }
    const b = e.target.closest("[data-forget]");
    if (!b) return;
    const name = b.dataset.forget;
    const label = b.dataset.label || name;
    if (!confirm(`Forget "${label}"?\n\nIt is removed from the runtime database with its runs. Files in its folder are not touched. Rediscover (here) connects it again.`)) return;
    b.disabled = true;
    try {
      const res = await server.forgetProject(name);
      ctx.toast(`Forgot ${label} (${res.runs} run(s), ${res.datasets} dataset(s))`, "ok");
      ctx.log("info", `Project ${name} forgotten (runtime database only).`, "server");
      state.avica = { ...state.avica };
      delete state.avica[name];
      delete state.trees[name];
      if (state.selectedProject === name) state.selectedProject = "all";
      await loadServer();
      drawProjects(root);
    } catch (error) {
      b.disabled = false;
      ctx.toast(error.message, "fail");
    }
  };
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
  // Close the tab. Browsers only allow it for tabs opened by a script or with one
  // history entry (e.g. the tab `alfrd serve` opened); otherwise say so.
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

/**
 * Switch views without adding browser-history entries. A tab opened by
 * `alfrd serve` then keeps a single history entry, which is what lets
 * window.close() close it when the server quits.
 */
function goTo(view) {
  const url = `#/${view}`;
  if (location.hash !== url) location.replace(url);
}
ctx.openAliasRules = openAliasRules;

// ---------------------------------------------------------------------------
// Live updates (see data/live.js): refresh what changed, keep the reader's place

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
  // alfrd.yaml or a root config decides what is read at all: re-read everything.
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

/** Re-read one project after a change. Returns true when something was applied. */
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

/** The runtime database changed: refresh the target rows that come from it (debounced). */
function liveRuntime() {
  clearTimeout(runtimeTimer);
  runtimeTimer = setTimeout(async () => {
    try {
      const data = await server.loadAll();
      state.serverWorkflows = data.workflows;
      const served = new Set(data.projects.map((p) => p.name));
      // Only rows of server projects whose targets are not read from their folder.
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
  onResync: (project) => queueRefresh(project, { full: true }),
  onStatus: (status) => { state.liveStatus = status; renderLiveBadge(); },
});
live.enabled = state.prefs.live !== false;
setTails(live.enabled);

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

/** Stable key for an element inside the active view: id, or its child-index path. */
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

/**
 * Re-render after a live update without losing the reader's place: scroll
 * positions, the focused field and its cursor. Waits while a pointer is down
 * (dragging on the canvas) — the render happens on release.
 */
function liveRender() {
  if (pointerDown && Date.now() - pointerDown < 15000) { renderWaiting = true; return; }
  // Someone is typing, or editing a multi-line text (alfrd.yaml): re-render later, not under their cursor.
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

// ---------------------------------------------------------------------------
// Shell rendering

function renderShell() {
  $("#app").innerHTML = `
    <header class="topbar">
      <div class="brand">${LOGO}<h1>ALFRD Studio</h1></div>
      <span class="mode-badge" id="mode-badge"></span>
      <span class="vsep"></span>
      <label class="picker" title="Project">${icon("folder")}<span class="picker-l">Project:</span><select id="pick-project" aria-label="Project"></select></label>
      <label class="picker" title="Target">${icon("target")}<span class="picker-l">Target:</span><select id="pick-target" aria-label="Target"></select></label>
      <span class="grow"></span>
      <button class="live-badge live-off" id="btn-live" aria-pressed="false"><i></i><span>Live off</span></button>
      <button class="btn" id="btn-rescan" title="Re-read the whole project folder now (live updates only re-read what changed)">${icon("sync")} Re-scan</button>
      <button class="btn" id="btn-import">${icon("upload")} Import</button>
      <button class="btn" id="btn-export">${icon("download")} Export</button>
      <button class="icon-btn" id="btn-settings" aria-label="Settings" title="Settings">${icon("gear")}</button>
      <button class="icon-btn" id="btn-quit" aria-label="Quit alfrd" title="Quit alfrd" hidden>${icon("power")}</button>
    </header>
    <nav class="rail" aria-label="Workspace">
      ${VIEWS.map((v) => `<a href="#/${v.id}" data-view="${v.id}">${icon(v.icon)}<span>${v.label}</span></a>`).join("")}
    </nav>
    <main id="main" tabindex="-1">${VIEWS.map((v) => `<section class="view" id="view-${v.id}" data-view="${v.id}" hidden></section>`).join("")}</main>
    <footer class="footbar">
      <span>${icon("database")} <span id="foot-storage"></span></span>
      <span class="vsep"></span>
      <span id="foot-mode"></span>
      <span class="vsep"></span>
      <button class="link-btn" id="foot-logs">${icon("terminal")} <span id="foot-logcount"></span></button>
      <span class="grow"></span>
      <span id="footer-right"></span>
    </footer>
    <section class="console" id="console" hidden aria-label="Log stream"></section>`;

  $("#btn-import").addEventListener("click", () => openImport());
  $("#btn-rescan").addEventListener("click", () => rescan());
  $("#btn-live").addEventListener("click", onLiveBadge);
  $("#btn-export").addEventListener("click", (e) => menu(e.currentTarget, [
    { label: "Overview CSV (visible rows)", icon: "download", run: () => exportOverviewCsv(false) },
    { label: "Stage details CSV", icon: "download", run: () => exportOverviewCsv(true) },
    "-",
    { label: "Workflow YAML (with edits)", icon: "file", run: exportWorkflowYaml },
    { label: "Studio snapshot (JSON)", icon: "database", hint: "re-importable", run: exportSnapshot },
  ]));
  $("#btn-settings").addEventListener("click", openSettings);
  $("#btn-quit").addEventListener("click", quitServer);
  $("#pick-project").addEventListener("change", (e) => {
    state.selectedProject = e.target.value;
    const inScope = ctx.scopedTargets();
    if (!inScope.some((t) => t.id === state.selectedTarget)) state.selectedTarget = inScope[0]?.id || null;
    scheduleRender();
  });
  $("#pick-target").addEventListener("change", (e) => {
    state.selectedTarget = e.target.value;
    scheduleRender();
  });
  $("#foot-logs").addEventListener("click", () => {
    state.consoleOpen = !state.consoleOpen;
    renderConsole();
  });

  VIEWS.forEach((v) => v.mod.mount?.($(`#view-${v.id}`), ctx));
}

function renderHeader() {
  const badge = $("#mode-badge");
  const live = state.mode === "server";
  badge.className = `mode-badge ${live ? "live" : ""}`;
  const quit = $("#btn-quit");
  if (quit) quit.hidden = !(live && server.session?.can_quit);
  badge.innerHTML = `<i></i>${live ? "Server mode" : "Browser mode"}${state.source === "demo" ? " · demo" : state.source === "imported" ? " · imported" : state.source === "server" ? " · runtime" : ""}`;
  badge.title = live ? "alfrd: projects come from the runtime database; re-queue runs through the runtime API." : "Everything runs in this browser; no pipeline code is executed.";

  const projects = ctx.projects();
  $("#pick-project").innerHTML = `<option value="all">All Projects (${projects.length})</option>${projects.map((p) => `<option value="${esc(p.id)}" ${p.id === state.selectedProject ? "selected" : ""}>${esc(p.title || p.name)}</option>`).join("")}`;
  const targets = ctx.scopedTargets();
  $("#pick-target").innerHTML = targets.length
    ? targets.map((t) => `<option value="${esc(t.id)}" ${t.id === state.selectedTarget ? "selected" : ""}>${esc(t.name)}${state.selectedProject === "all" ? ` · ${esc(state.projectTitles[t.project] || ctx.projectName(t.project))}` : ""}</option>`).join("")
    : `<option value="">No targets</option>`;

  $$(".rail a").forEach((a) => a.classList.toggle("active", a.dataset.view === state.view));

}

function renderFooter() {
  const st = $("#foot-storage");
  if (!st) return;
  st.textContent = `Local Storage: ${bytes(storage.size())} / Saved`;
  $("#foot-mode").innerHTML = state.mode === "server"
    ? `${icon("server")} alfrd — runtime actions go through the ALFRD API`
    : `${icon("info")} Client-side evaluation — external runner for native CASA execution`;
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

/** Log stream panel: the Studio's own messages plus one tab per docked (followed) log file. */
function renderConsole() {
  const el = $("#console");
  el.hidden = !state.consoleOpen;
  document.body.classList.toggle("console-open", state.consoleOpen);
  renderFooter();
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
  $(".console-h", el).innerHTML = `<b class="console-title">${icon("terminal")} Log stream</b>
      <div class="ctabs" role="tablist" aria-label="Log stream tabs">${tabBtn("studio", "Studio")}${docks.map((d) => tabBtn(d.key, d.name, `<span class="live-dot" ${d.live ? "" : "hidden"}></span>`, `${d.rel} (following)`)).join("")}</div>
      <span class="grow"></span>${tools}
      <button class="icon-btn sm" data-act="close" aria-label="Hide log stream" title="Hide the panel (docked logs stay as tabs)">${icon("minus")}</button>`;
  const body = $(".console-b", el);
  if (tab === "studio") {
    const lines = state.consoleLines.filter((l) => filter === "all" || l.level === filter);
    body.dataset.tab = "studio";
    body.innerHTML = `<pre class="log">${lines.map((l) => `<span class="lvl-${l.level}">${l.t.toISOString().slice(11, 19)} [${esc(l.scope)}] ${esc(l.level.toUpperCase().padEnd(5))} ${esc(l.text)}</span>`).join("\n") || '<span class="muted">No log lines yet.</span>'}</pre>`;
    const pre = $("pre", body);
    pre.scrollTop = pre.scrollHeight;
  } else if (body.dataset.tab !== tab || !body.querySelector("pre[data-dock-log]")) {
    body.dataset.tab = tab;
    // Once loaded, redraw the tab strip (live dot) — the body is kept.
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
  // Tabs: ←/→ move between them.
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
/** A log was docked (logview.js): open the Log Stream on its tab. */
ctx.showDock = (key) => {
  state.consoleOpen = true;
  state.consoleTab = key;
  renderConsole();
  ctx.toast("Following in the Log Stream — switch views freely", "ok");
};

function renderAll() {
  saveUi("app", state, ["selectedProject", "selectedTarget"]);
  renderHeader();
  VIEWS.forEach((v) => {
    const el = $(`#view-${v.id}`);
    const active = v.id === state.view;
    el.hidden = !active;
    if (active) v.mod.render(el, ctx);
  });
  renderFooter();
  listeners.forEach((fn) => fn(state));
}

function route() {
  const id = (location.hash.match(/^#\/(\w+)/) || [])[1];
  const next = VIEWS.some((v) => v.id === id) ? id : "overview";
  if (next !== state.view) VIEWS.find((v) => v.id === state.view)?.mod.leave?.(ctx);
  state.view = next;
  scheduleRender();
}

// ---------------------------------------------------------------------------
// Boot

async function boot() {
  renderShell();
  bindLogs(ctx, document.body);
  consoleHeight(consoleUi.height);
  window.addEventListener("hashchange", route);
  // In-app links (#/view) replace the history entry instead of adding one.
  document.addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#/"]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    goTo(a.getAttribute("href").slice(2));
  });
  route();
  await loadTemplate("avica", parseYaml);
  await loadDefaultManifest();
  await ensureTemplatesFor(storage.get("workflow")?.text);

  const session = await server.detect();
  if (session?.version) VERSION = session.version;
  ctx.log("info", `Studio ${VERSION} started (${location.protocol === "file:" ? "file" : location.origin}).`);
  state.demoEnabled = Boolean(session?.demo) || new URLSearchParams(location.search).has("demo");
  if (session) {
    state.mode = "server";
    ctx.log("info", `Connected to alfrd ${session.version} (runtime ${session.runtime_enabled ? "on" : "off"}, mutations ${session.mutations_enabled ? "on" : "off"}).`, "server");
    const data = await loadServer();
    // default_project is the location identifier; name it once projectNames are known.
    if (session.default_project) ctx.log("info", `Opened project ${ctx.projectName(session.default_project)}.`, "server");
    if (!data || !data.targets.length) {
      if (state.demoEnabled) await loadDemo({ quiet: true });
      else ctx.log("info", "No project yet. Start `alfrd serve` in the folder that holds alfrd.yaml (or use Import → Connect).", "server");
    }
  } else if (state.demoEnabled) {
    await loadDemo({ quiet: true });
  } else if (restoreSaved()) {
    ctx.log("info", `Restored ${state.targets.length} imported target(s) from this browser.`);
    // Folder handles remembered for these projects (live updates, tails after a reload).
    if (canPickDirectory()) {
      for (const p of Object.keys(state.trees || {})) {
        const h = await loadFolderHandle(p);
        if (h) state.folders[p] = h;
      }
    }
  } else {
    state.source = "empty";
  }
  scheduleRender();
  live.start();
}

// Exposed for tests/devtools only.
window.alfrdStudio = { ctx, loadDemo, loadServer, applyBundle, rescan, importEntries };

boot().catch((error) => {
  console.error(error);
  const el = document.getElementById("app");
  el.insertAdjacentHTML("afterbegin", `<p class="fatal">Studio failed to start: ${esc(error.message)}</p>`);
});

export { hms, OVERALL_STATUS };
