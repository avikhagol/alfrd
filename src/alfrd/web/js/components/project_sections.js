// Project-home slots shared by the overview and the full-trust plugin API.
const sections = new Map();
const mounted = new WeakMap();
let nextId = 0;

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
