// Optional live data source: when the Studio is served by `alfrd serve`, the
// same origin exposes the ALFRD runtime API. On GitHub Pages or `alfrd studio`
// these requests simply 404 and the app stays in browser-only mode.

import { manifestToWorkflows, normalizeStatus } from "./model.js";

const API = "/api";
const fitsCache = new Map();
const authListeners = new Set();

function requireAuth() {
  if (server.authRequired) return;
  server.authRequired = true;
  server.session = null;
  fitsCache.clear();
  for (const listener of authListeners) listener();
}

function accessError() {
  const error = new Error("This ALFRD server needs its access link.");
  error.status = 401;
  return error;
}

// All API fetches share this check, including text and collection files.
async function request(url, init) {
  if (server.authRequired) throw accessError();
  const response = await fetch(url, { credentials: "same-origin", ...init });
  if (response.status === 401) {
    requireAuth();
    throw accessError();
  }
  return response;
}

async function getJson(path, init) {
  const response = await request(`${API}${path}`, { headers: { Accept: "application/json" }, ...init });
  const type = response.headers.get("content-type") || "";
  const body = type.includes("json") ? await response.json() : null;
  if (!response.ok) {
    const message = body?.errors?.join("; ") || body?.error?.message || `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

export const server = {
  session: null,
  authRequired: false,
  request,
  onAuthRequired(listener) {
    authListeners.add(listener);
    if (this.authRequired) listener();
    return () => authListeners.delete(listener);
  },

  /** Detect a same-origin ALFRD server. Resolves to the session or null. */
  async detect() {
    if (this.authRequired || location.protocol === "file:") return null;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2500);
    try {
      const session = await getJson("/studio/session", { signal: controller.signal });
      if (session && session.app === "alfrd") {
        this.session = session;
        return session;
      }
    } catch {
      /* not served by alfrd serve */
    } finally {
      clearTimeout(timer);
    }
    return null;
  },

  async mutate(path, payload, method = "POST") {
    if (this.authRequired) throw accessError();
    if (!this.session?.mutations_enabled) throw new Error("Runtime mutations are disabled on this server (loopback only).");
    return getJson(path, {
      method,
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": this.session.csrf_token },
      body: JSON.stringify(payload || {}),
    });
  },

  /** Register a project directory or alfrd.yaml path with the server. */
  async connect(path) {
    const project = await this.mutate("/projects/connect", { path });
    const key = project?.identifier || project?.name;
    if (Array.isArray(this.session?.projects) && key && !this.session.projects.includes(key)) this.session.projects.push(key);
    return project;
  },

  projectTemplates() { return getJson("/studio/project-templates"); },
  async createProject(payload) {
    const project = await this.mutate("/studio/projects/create", payload);
    if (Array.isArray(this.session.projects)) this.session.projects.push(project.identifier);
    this.session.default_project = project.identifier;
    return project;
  },
  handoffs(project, id) { return getJson(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/handoffs`); },
  handoffArtifact(project, id, unit, artifact, offset = 0) { return getJson(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/handoffs/${encodeURIComponent(unit)}/${artifact}?${new URLSearchParams({ offset })}`); },
  handoff(project, file) { return getJson(`/studio/projects/${encodeURIComponent(project)}/handoff?${new URLSearchParams({ file })}`); },
  handoffSave(project, payload) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/handoff`, payload); },
  planResponse(project, id, payload) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/response`, payload); },
  planReview(project, id, payload) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/review`, payload); },
  planReject(project, id, payload) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/reject`, payload); },
  planTurns(project, id) { return getJson(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/turns`); },
  planTurnSet(project, id, step, payload) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/turns/${encodeURIComponent(step)}`, payload); },
  system() { return getJson("/studio/system"); },
  tasks(project) { return getJson(`/studio/projects/${encodeURIComponent(project)}/tasks`); },
  quickstart(project) { return getJson(`/studio/projects/${encodeURIComponent(project)}/quickstart`); },
  quickstartApply(project, form, values) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/quickstart/${encodeURIComponent(form)}`, { values }); },
  taskCreate(project, payload) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/tasks`, payload); },
  taskRename(project, target, name) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/tasks/${encodeURIComponent(target)}`, { name }, "PATCH"); },
  task(project, target = null) { return getJson(`/studio/projects/${encodeURIComponent(project)}/${target ? `tasks/${encodeURIComponent(target)}/task` : "task"}`); },
  taskSave(project, payload, target = null) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/${target ? `tasks/${encodeURIComponent(target)}/task` : "task"}`, payload); },

  // The project's target list (alfrd.targets.csv; alfrd.targets_csv).
  targets(project) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/targets`);
  },
  targetsPreview(project, payload) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/targets/preview`, payload);
  },
  targetsSave(project, payload) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/targets`, payload);
  },
  targetsRemove(project, targets) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/targets/remove`, { targets });
  },
  // Collections (kind: collection artifacts, e.g. rPicard diagnostics_*); lazy, read-only.
  collections(project, query) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/collections?${new URLSearchParams(query)}`);
  },
  collectionFiles(project, name, run, query = {}) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/collections/${encodeURIComponent(name)}/files?${new URLSearchParams({ run, ...query })}`);
  },
  /** URL of one collection file (for <img src>, links, fetch). */
  collectionFileUrl(project, name, run, path, download = false) {
    const q = new URLSearchParams({ run, path, ...(download ? { download: "1" } : {}) });
    return `${API}/studio/projects/${encodeURIComponent(project)}/collections/${encodeURIComponent(name)}/file?${q}`;
  },

  // Plans: run a plan CSV (targets × steps) with alfrd.yaml's commands (alfrd.runtime.scheduler).
  executionInfo(project, target = null) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/execution${target ? `?${new URLSearchParams({ target })}` : ""}`);
  },
  planPreview(project, payload) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/preview`, payload);
  },
  notificationRoutes(project) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/notify`);
  },
  notificationTest(project, index) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/notify/test`, { index });
  },
  planStatus(project, id = null) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/plans${id ? `?id=${encodeURIComponent(id)}` : ""}`);
  },
  planStart(project, payload) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans`, payload);
  },
  planAction(project, id, action, payload = {}) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/${action}`, payload);
  },
  planCells(project, id, cells) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/cells`, { cells });
  },
  /** Append one row {target, files, code, workdir?, steps?} to a plan's CSV (works while it runs). */
  planAddRow(project, id, row) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/${encodeURIComponent(id)}/rows`, row);
  },
  planReconcile(project) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/reconcile`, {});
  },

  retryStep(runId, stepKey) {
    return this.mutate(`/runtime/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepKey)}/retry`, {});
  },

  avicaFitsFiles(project) {
    if (!fitsCache.has(project)) {
      const pending = getJson(`/studio/avica/${encodeURIComponent(project)}/fits-files`, { headers: { "X-CSRF-Token": this.session?.csrf_token || "" } });
      fitsCache.set(project, pending);
      pending.catch(() => { if (fitsCache.get(project) === pending) fitsCache.delete(project); });
    }
    return fitsCache.get(project);
  },
  clearFitsCache(project) { project ? fitsCache.delete(project) : fitsCache.clear(); },

  avicaLayout(project) {
    return getJson(`/studio/avica/${encodeURIComponent(project)}/layout`);
  },

  avicaWorkdir(project, code, target) {
    const q = new URLSearchParams({ code, ...(target ? { target } : {}) });
    return getJson(`/studio/avica/${encodeURIComponent(project)}/workdir?${q}`);
  },

  avicaSummary(project) {
    return this.mutate(`/studio/avica/${encodeURIComponent(project)}/summary`, {});
  },

  async avicaLog(project, name) {
    const response = await request(`${API}/studio/avica/${encodeURIComponent(project)}/logs/${encodeURIComponent(name)}`);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.text();
  },

  /** Files the Studio reads for a connected project (alfrd.yaml-driven; logs listed only). `only`: just these rels. */
  projectScan(project, only = null) {
    const q = only ? `?${new URLSearchParams(only.map((rel) => ["only", rel]))}` : "";
    return getJson(`/studio/projects/${encodeURIComponent(project)}/scan${q}`);
  },

  /** A log from byte `offset` on (null: its last 400 kB). → {text, offset, id, size, mtime, reset} */
  async projectFileFrom(project, rel, offset = null, id = null) {
    const q = new URLSearchParams({ path: rel, ...(offset != null ? { offset: String(offset) } : {}), ...(id ? { id } : {}) });
    const response = await request(`${API}/studio/projects/${encodeURIComponent(project)}/file?${q}`, { cache: "no-store" });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).error?.message || message; } catch { /* text body */ }
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    const h = (k) => response.headers.get(k);
    return { text: await response.text(), offset: Number(h("X-Offset")), id: h("X-File-Id"), size: Number(h("X-File-Size")), mtime: Number(h("X-File-Mtime")) * 1000, reset: h("X-Reset") !== "0" };
  },

  /** Live updates: Server-Sent Events URL for these projects (runtime events come too). */
  eventsUrl(projects) {
    return `${API}/studio/events?${new URLSearchParams({ projects: projects.join(",") })}`;
  },

  /** Poll fallback for the event stream. `since`: {key: "epoch:version"}. */
  changes(projects, since) {
    return getJson(`/studio/changes?${new URLSearchParams({ projects: projects.join(","), since: JSON.stringify(since || {}) })}`, { cache: "no-store" });
  },

  async projectFile(project, rel) {
    const response = await request(`${API}/studio/projects/${encodeURIComponent(project)}/file?${new URLSearchParams({ path: rel })}`);
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).error?.message || message; } catch { /* text body */ }
      throw new Error(message);
    }
    return response.text();
  },

  /** extra: {base_hash | base_text, force}: 409 (error.body: current_text, diff) when the file changed on disk. */
  saveManifest(project, text, extra = {}) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/manifest`, { text, ...extra });
  },
  // Template-driven views (alfrd.layout_generic); `entity` is an entity-path query string
  projectView(project, entity, view = "metadata") {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/view?${new URLSearchParams({ entity, view })}`);
  },
  viewFileUrl(project, entity, panel, path) {
    return `${API}/studio/projects/${encodeURIComponent(project)}/view/file?${new URLSearchParams({ entity, panel, path })}`;
  },
  // Shared notes (alfrd.notes.jsonl)
  notes(project) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/notes`);
  },
  noteCreate(project, payload) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/notes`, payload);
  },
  noteChange(project, id, payload) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/notes/${encodeURIComponent(id)}`, payload);
  },
  // Full-text search (alfrd.search)
  search(projects, q, limit = 50) {
    return getJson(`/studio/search?${new URLSearchParams({ projects: projects.join(","), q, limit })}`);
  },
  searchContext(project, path, line, around = 30) {
    return getJson(`/studio/search/context?${new URLSearchParams({ project, path, line, around })}`);
  },
  // Version history of tracked files (alfrd.yaml …; alfrd.history)
  history(project, file = "alfrd.yaml") {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/history?${new URLSearchParams({ file })}`);
  },
  historyVersion(project, version, file = "alfrd.yaml") {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/history/${encodeURIComponent(version)}?${new URLSearchParams({ file })}`);
  },
  historyDiff(project, a, b = "current", file = "alfrd.yaml") {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/history/diff?${new URLSearchParams({ file, a, b })}`);
  },
  historyRestore(project, version, file = "alfrd.yaml", extra = {}) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/history/${encodeURIComponent(version)}/restore?${new URLSearchParams({ file })}`, extra);
  },

  avicaConfig(project, changes) {
    return this.mutate(`/studio/avica/${encodeURIComponent(project)}/config`, { changes });
  },

  /** Every project remembered in the runtime database (not only the ones shown). */
  async listProjects() {
    return (await getJson("/projects")).projects || [];
  },

  async forgetProject(project) {
    const res = await this.mutate(`/studio/projects/${encodeURIComponent(project)}/forget`, {});
    if (Array.isArray(this.session?.projects)) this.session.projects = this.session.projects.filter((p) => p !== project);
    return res;
  },
  /** What Forget / Delete would remove and whether removal is allowed now (read-only). */
  removalPreview(project) { return getJson(`/studio/projects/${encodeURIComponent(project)}/removal-preview`); },
  /** Size of the project folder for "Delete all files and folders" (walks the folder; can be slow). */
  removalSize(project) { return getJson(`/studio/projects/${encodeURIComponent(project)}/removal-size`); },
  /** Permanent deletion; the server refuses (409) while no deletion scope is configured. */
  deleteProject(project, payload = {}) { return this.mutate(`/studio/projects/${encodeURIComponent(project)}/delete`, payload); },

  /** Show, hide or open a remembered project here (`state`: "hidden" | "shown" | "opened"). No re-connect needed. */
  async setProjectVisibility(project, state) {
    const res = await this.mutate(`/studio/projects/${encodeURIComponent(project)}/visibility`, { state });
    if (this.session) {
      this.session.projects = Array.isArray(res.projects) ? res.projects : null;
      this.session.default_project = res.default_project || null;
    }
    return res;
  },

  /** Forgotten projects (and the `alfrd serve` folder) that Rediscover can register again. */
  async rediscoverable() {
    return (await getJson("/studio/projects/rediscover")).candidates || [];
  },

  /** Register forgotten projects again: one folder (`root`) or all of them. */
  async rediscover(root = null) {
    const res = await this.mutate("/studio/projects/rediscover", root ? { root } : {});
    if (this.session) {
      if (Array.isArray(res.projects)) this.session.projects = res.projects;
      if (res.default_project) this.session.default_project = res.default_project;
    }
    return res;
  },

  quit() {
    return this.mutate("/studio/quit", {});
  },

  /** True when Import → Connect may browse the server's folders (loopback + CSRF). */
  canBrowse() {
    return !!(this.session?.can_browse ?? this.session?.mutations_enabled);
  },

  /** Sub-folders of `path` on the server (default: the `alfrd serve` folder, else home). */
  listFolders(path = "", { hidden = false } = {}) {
    const q = new URLSearchParams();
    if (path) q.set("path", path);
    if (hidden) q.set("hidden", "1");
    return getJson(`/studio/fs/list?${q}`, {
      headers: { Accept: "application/json", "X-CSRF-Token": this.session?.csrf_token || "" },
    });
  },

  /** → {path, name, parent}; errors get reason "permission" | "session" | "". */
  mkdir(parent, name) {
    return this.mutate("/studio/fs/mkdir", { parent, name }).catch((error) => {
      error.reason = error.body?.error?.reason || (error.status === 403 || !this.session?.mutations_enabled ? "session" : "");
      throw error;
    });
  },

  runLogs(runId) {
    return getJson(`/runtime/runs/${encodeURIComponent(runId)}/logs`);
  },

  datasetSummary(project, workflow, datasetId) {
    return getJson(`/projects/${encodeURIComponent(project)}/workflows/${encodeURIComponent(workflow)}/datasets/${encodeURIComponent(datasetId)}`);
  },

  /** Load every project/workflow matrix into an import bundle. */
  async loadAll() {
    const { projects: all = [] } = await getJson("/projects");
    // `alfrd serve` started for one project shows only that one (see --all-projects).
    const scope = Array.isArray(this.session?.projects) ? this.session.projects : null;
    const selected = scope ? all.filter((p) => scope.includes(p.identifier || p.name)) : all;
    // The rest of the Studio treats `name` as its project key. In server mode
    // that key must be the location identifier, while labels remain human-readable.
    const projects = selected.map((p) => ({
      ...p,
      manifest_name: p.name,
      title: p.display_name || p.name,
      name: p.identifier || p.name,
    }));
    const targets = [];
    const workflows = [];
    const messages = [];
    for (const project of projects) {
      const one = await this.loadProjectRuntime(project);
      targets.push(...one.targets);
      workflows.push(...one.workflows);
      messages.push(...one.messages);
    }
    return { targets, workflows, messages, projects, aliases: [] };
  },

  /** One project's runtime workflows and matrix rows (as targets); `project` is an entry of loadAll().projects. */
  async loadProjectRuntime(project) {
    const targets = [];
    const workflows = [];
    const messages = [];
    let list = [];
    try {
      list = (await getJson(`/projects/${encodeURIComponent(project.name)}/workflows`)).workflows || [];
    } catch (error) {
      messages.push({ level: "warn", text: `${project.title}: ${error.message}` });
      return { targets, workflows, messages };
    }
    if (!list.length) messages.push({ level: "info", text: `${project.title}: connected, but its manifest declares no runtime workflows.` });
    for (const wf of list) {
      const info = manifestToWorkflows({ name: project.name, workflows: [{ name: wf.name, description: wf.description, steps: wf.sequence || [] }] }, `${project.title} (server)`, { aliases: false });
      const workflow = info.workflows[0];
      if (workflow) workflows.push({ project: project.name, ...workflow });
      let matrix;
      try {
        matrix = await getJson(`/projects/${encodeURIComponent(project.name)}/workflows/${encodeURIComponent(wf.name)}/matrix`);
      } catch (error) {
        messages.push({ level: "warn", text: `${project.title}/${wf.name}: ${error.message}` });
        continue;
      }
      if (!(matrix.rows || []).length) {
        messages.push({ level: "info", text: `${project.title}/${wf.name}: no datasets yet — import results with \`alfrd import avica-run\` or start a run.` });
      }
      (matrix.rows || []).forEach((row) => {
        const steps = {};
        Object.entries(row.cells || {}).forEach(([key, cell]) => {
          const status = normalizeStatus(cell.status);
          steps[key] = {
            step: key,
            status: cell.status === "queued" ? "queued" : status,
            attempt: cell.attempt,
            attempts: [],
            started: cell.started_at,
            finished: cell.finished_at,
            duration: cell.duration_seconds,
            note: cell.error_summary || cell.result_summary || "",
            executionId: cell.execution_id,
            artifactCount: cell.artifact_count,
          };
        });
        const name = row.dataset_external_id || row.dataset_name || row.dataset_id;
        targets.push({
          id: `${project.name}/${name}`,
          name,
          project: project.name,
          projectTitle: project.title || project.description || null,
          workflow: wf.name,
          datasetId: row.dataset_id,
          runId: row.run_id,
          runStatus: row.run_status,
          msPath: null,
          fitsidi: null,
          meta: null,
          steps,
          history: [],
          artifacts: [],
          columns: {},
          source: { kind: "server", file: `${API}/projects/${project.name}/workflows/${wf.name}/matrix`, origin: "server" },
        });
      });
    }
    return { targets, workflows, messages };
  },
};
