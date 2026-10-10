// Project-home slots shared by the overview and the full-trust plugin API.
const sections = new Map();
const actions = new Map();
const actionMounts = new WeakMap();
const mounted = new WeakMap();
let nextId = 0;

export function registerOverviewAction(spec, report) {
  if (!spec || !/^[a-z0-9_-]+$/.test(spec.id) || typeof spec.render !== "function"
      || (spec.order != null && !Number.isFinite(spec.order))) {
    throw new TypeError("registerOverviewAction({id,order?,render})");
  }
  actions.set(spec.id, { order: 100, ...spec, report });
}

/** Stable toolbar hosts; scope changes update plugins without rebuilding focused controls. */
export function renderOverviewActions(box, ctx) {
  box.hidden = ctx.state.source !== "server";
  if (box.hidden) return;
  let cache = actionMounts.get(box);
  if (!cache) { cache = new Map(); actionMounts.set(box, cache); }
  const project = ctx.state.selectedProject === "all" ? null : ctx.state.selectedProject;
  for (const spec of [...actions.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))) {
    let entry = cache.get(spec.id);
    if (!entry || entry.spec !== spec) {
      const host = document.createElement("span");
      host.dataset.pluginAction = spec.id;
      entry = { spec, host };
      cache.set(spec.id, entry);
      box.append(host);
    }
    if (entry.project === project) continue;
    entry.project = project;
    const fail = (error) => {
      spec.report?.(error?.message || String(error));
      ctx.log?.("error", `${spec.id}: overview action: ${error?.message || error}`, "plugins");
    };
    try { Promise.resolve(spec.render(project, entry.host, ctx)).catch(fail); } catch (error) { fail(error); }
  }
}

export function registerProjectSection(spec, report) {
  if (!spec || !/^[a-z0-9_-]+$/.test(spec.id) || !spec.title || typeof spec.render !== "function"
      || (spec.order != null && !Number.isFinite(spec.order))) {
    throw new TypeError("registerProjectSection({id,title,order?,render})");
  }
  sections.set(spec.id, { order: 100, ...spec, report });
}

/** Reattach existing sections on live refresh; render anew on project/registration changes. */
export function renderProjectSections(box, project, ctx) {
  if (!project || ctx.state.selectedProject === "all") { mounted.delete(box); return; }
  if (!sections.size) return;
  let cache = mounted.get(box);
  if (!cache || cache.project !== project) {
    cache = { project, nodes: new Map() };
    mounted.set(box, cache);
  }
  for (const spec of [...sections.values()].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))) {
    let entry = cache.nodes.get(spec.id);
    if (!entry || entry.spec !== spec) {
      const section = document.createElement("section");
      section.className = "card setup-card plug-section";
      section.dataset.pluginSection = spec.id;
      const heading = document.createElement("h2"), toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "link-btn plug-section-toggle";
      toggle.textContent = spec.title;
      const host = document.createElement("div");
      host.id = `plug-section-${++nextId}`;
      host.className = "plug-section-body";
      try { host.hidden = localStorage.getItem(`alfrd.section.${spec.id}`) === "collapsed"; } catch { /* storage optional */ }
      toggle.setAttribute("aria-controls", host.id);
      toggle.setAttribute("aria-expanded", String(!host.hidden));
      toggle.onclick = () => {
        host.hidden = !host.hidden;
        toggle.setAttribute("aria-expanded", String(!host.hidden));
        try { localStorage.setItem(`alfrd.section.${spec.id}`, host.hidden ? "collapsed" : "expanded"); } catch { /* storage optional */ }
      };
      heading.append(toggle);
      section.append(heading, host);
      entry = { spec, section, host };
      cache.nodes.set(spec.id, entry);
      box.append(section);
      const failed = (error) => {
        const message = error?.message || String(error);
        const warning = document.createElement("div");
        warning.className = "callout warn";
        warning.setAttribute("role", "alert");
        warning.textContent = message;
        host.replaceChildren(warning);
        spec.report?.(message);
        ctx.log("error", `${spec.id}: project section: ${message}`, "plugins");
      };
      try { Promise.resolve(spec.render(project, host, ctx)).catch(failed); } catch (error) { failed(error); }
    } else box.append(entry.section);
  }
}
