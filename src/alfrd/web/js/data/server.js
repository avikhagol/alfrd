// Optional live data source: when the Studio is served by `alfrd serve`, the
// same origin exposes the ALFRD runtime API. On GitHub Pages or `alfrd studio`
// these requests simply 404 and the app stays in browser-only mode.

import { manifestToWorkflows, normalizeStatus } from "./model.js";

const API = "/api";

async function getJson(path, init) {
  const response = await fetch(`${API}${path}`, { credentials: "same-origin", headers: { Accept: "application/json" }, ...init });
  const type = response.headers.get("content-type") || "";
  const body = type.includes("json") ? await response.json() : null;
  if (!response.ok) {
    const message = body?.error?.message || `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

export const server = {
  session: null,

  /** Detect a same-origin ALFRD server. Resolves to the session or null. */
  async detect() {
    if (location.protocol === "file:") return null;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2500);
      const session = await getJson("/studio/session", { signal: controller.signal });
      clearTimeout(timer);
      if (session && session.app === "alfrd") {
        this.session = session;
        return session;
      }
    } catch {
      /* not served by alfrd serve */
    }
    return null;
  },

  async mutate(path, payload) {
    if (!this.session?.mutations_enabled) throw new Error("Runtime mutations are disabled on this server (loopback only).");
    return getJson(path, {
      method: "POST",
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

  // Plans: run a plan CSV (targets × steps) with alfrd.yaml's commands (alfrd.runtime.scheduler).
  executionInfo(project) {
    return getJson(`/studio/projects/${encodeURIComponent(project)}/execution`);
  },
  planPreview(project, payload) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/preview`, payload);
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
  planReconcile(project) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/plans/reconcile`, {});
  },

  retryStep(runId, stepKey) {
    return this.mutate(`/runtime/runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepKey)}/retry`, {});
  },

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
    const response = await fetch(`${API}/studio/avica/${encodeURIComponent(project)}/logs/${encodeURIComponent(name)}`, { credentials: "same-origin" });
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
    const response = await fetch(`${API}/studio/projects/${encodeURIComponent(project)}/file?${q}`, { credentials: "same-origin", cache: "no-store" });
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
    const response = await fetch(`${API}/studio/projects/${encodeURIComponent(project)}/file?${new URLSearchParams({ path: rel })}`, { credentials: "same-origin" });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).error?.message || message; } catch { /* text body */ }
      throw new Error(message);
    }
    return response.text();
  },

  saveManifest(project, text) {
    return this.mutate(`/studio/projects/${encodeURIComponent(project)}/manifest`, { text });
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
      let list = [];
      try {
        list = (await getJson(`/projects/${encodeURIComponent(project.name)}/workflows`)).workflows || [];
      } catch (error) {
        messages.push({ level: "warn", text: `${project.title}: ${error.message}` });
        continue;
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
    }
    return { targets, workflows, messages, projects, aliases: [] };
  },
};
