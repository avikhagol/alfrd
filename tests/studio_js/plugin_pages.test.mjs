// Plugin pages (api.registerPage) and plugin availability by project type, without a browser DOM.
import test from "node:test";
import assert from "node:assert/strict";

// A tiny document: loadCss sees its stylesheet already present; sections get plain objects.
const made = [];
globalThis.document = {
  querySelector: () => ({}),
  getElementById: () => null,
  createElement: (tag) => {
    const el = { tag, dataset: {}, children: [], hidden: false, attrs: {}, removed: false,
      append(...c) { this.children.push(...c); }, remove() { this.removed = true; },
      setAttribute(k, v) { this.attrs[k] = v; }, replaceChildren() { this.children = []; } };
    made.push(el);
    return el;
  },
};
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.location = { replace(url) { this.hash = url; }, hash: "" };

const { api } = await import("../../src/alfrd/web/js/components/plugin_api.js");
const pages = await import("../../src/alfrd/web/js/components/plugin_pages.js");
const { registerProjectSection, renderProjectSections } = await import("../../src/alfrd/web/js/components/project_sections.js");

const listFor = (project, type, extra = {}) => ({ project, project_type: type, plugins: [
  { id: "typed", available: type === "avica", unavailable_reason: type === "avica" ? null : `Not for ${type} projects: the plugin's default limits it to avica.`,
    applicability: { source: "default" }, project_types: ["avica"] },
  { id: "jsonly", available: true, applicability: { source: "default" }, project_types: null },
  { id: "overridden", available: true, applicability: { source: "global" }, project_types: null },
  ...(extra.plugins || []),
] });

function context(project) {
  const listeners = [];
  return { state: { selectedProject: project, selectedTarget: null, view: "overview" }, updates: 0, lines: [],
    update() { this.updates++; }, log(...a) { this.lines.push(a); }, subscribe(fn) { listeners.push(fn); }, listeners,
    palette: { providers: [], register(fn) { if (!this.providers.includes(fn)) this.providers.push(fn); } } };
}

const ctx = context("avi");
const fetched = [];
pages.bindPages(ctx, async (project) => { fetched.push(project); return listFor(project, project === "avi" ? "avica" : project === "lab" ? "my-lab" : "basic"); });
pages.setAvailability(listFor("avi", "avica"));

test("registerPage validates its spec", () => {
  const host = api(ctx, { sanitize: (h) => h }, "typed");
  for (const spec of [{}, { id: "Bad id", title: "x", render() {} }, { id: "x", render() {} }, { id: "x", title: "X" },
    { id: "x", title: "X", render() {}, order: NaN }, { id: "x", title: "X", render() {}, projectTypes: [] }, { id: "x", title: "X", render() {}, projectTypes: "avica" }]) {
    assert.throws(() => host.registerPage(spec), TypeError);
  }
  host.registerPage({ id: "typed-page", title: "Typed", render() {} });
  assert.equal(typeof host.fileExplorer, "function");
  assert.equal(typeof host.measurementSets, "function");
});

test("server availability decides; browser projectTypes act as the default only", () => {
  assert.equal(pages.applies("typed", null, "avi"), true);
  pages.setAvailability(listFor("plain", "basic"));
  assert.equal(pages.applies("typed", null, "plain"), false);
  assert.match(pages.reason("typed", null, "plain"), /^Not for basic projects/);
  assert.equal(pages.applies("jsonly", ["avica"], "avi"), true);
  assert.equal(pages.applies("jsonly", ["avica"], "plain"), false);
  assert.match(pages.reason("jsonly", ["avica"], "plain"), /plugin limits this to avica/);
  assert.equal(pages.applies("overridden", ["avica"], "plain"), true); // a global/project setting overrides the JS default
  assert.equal(pages.applies("unknown-plugin", null, "plain"), true);
  assert.equal(pages.applies("typed", null, null), true); // no project: nothing to decide
  assert.equal(pages.applies("typed", null, "never-loaded"), false); // unknown: hidden until the server answers
});

test("switching projects fetches availability once and re-renders", async () => {
  ctx.state.selectedProject = "lab";
  ctx.listeners.forEach((fn) => fn(ctx.state));
  ctx.listeners.forEach((fn) => fn(ctx.state));
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(fetched.filter((p) => p === "lab"), ["lab"]);
  assert.equal(pages.applies("typed", null, "lab"), false);
  assert.match(pages.reason("typed", null, "lab"), /^Not for my-lab projects/);
  assert.ok(ctx.updates > 0);
  pages.invalidate("lab");
  ctx.listeners.forEach((fn) => fn(ctx.state));
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(fetched.filter((p) => p === "lab"), ["lab", "lab"]);
  ctx.state.selectedProject = "avi";
});

test("palette commands and page entries follow the selected project", () => {
  const host = api(ctx, { sanitize: (h) => h }, "typed");
  host.registerCommand({ id: "typed-cmd", label: "Typed command", run() {} });
  const provider = ctx.palette.providers.find((fn) => fn().some?.((c) => c.id === "typed-cmd") || false) || ctx.palette.providers.at(-1);
  ctx.state.selectedProject = "avi";
  assert.ok(provider().some((c) => c.id === "typed-cmd"));
  const pagesProvider = ctx.palette.providers.find((fn) => fn().some((c) => c.id === "page:typed-page"));
  assert.ok(pagesProvider, "page command listed for an avica project");
  ctx.state.selectedProject = "plain";
  assert.equal(provider().some((c) => c.id === "typed-cmd"), false);
  assert.equal(pagesProvider().some((c) => c.id === "page:typed-page"), false);
  ctx.state.selectedProject = "avi";
});

test("Overview sections of an inapplicable plugin are not mounted and leave on a switch", () => {
  const host = api(ctx, { sanitize: (h) => h }, "typed");
  const renders = [];
  host.registerProjectSection({ id: "typed-card", title: "Typed", render(project) { renders.push(project); } });
  ctx.pluginApplies = (plugin, types, project) => pages.applies(plugin, types, project);
  const box = { children: [], append(...c) { this.children.push(...c); } };
  renderProjectSections(box, "avi", ctx);
  const section = box.children.find((c) => c.dataset.pluginSection === "typed-card");
  assert.ok(section && renders.includes("avi"));
  const fresh = { children: [], append(...c) { this.children.push(...c); } }; // Overview rebuilds its box per project
  renderProjectSections(fresh, "plain", ctx);
  assert.equal(fresh.children.some((c) => c.dataset.pluginSection === "typed-card"), false);
  assert.equal(renders.includes("plain"), false);
  renderProjectSections(box, "avi", ctx); // same box, project now known as inapplicable: the card is removed
  pages.setAvailability(listFor("avi", "basic"));
  renderProjectSections(box, "avi", ctx);
  assert.equal(section.removed, true);
  pages.setAvailability(listFor("avi", "avica"));
  registerProjectSection({ id: "typed-card", title: "Typed", render() {} }); // clean up: no plugin tag
});

test("page states: loading, missing, unavailable, choose a project, render error", async () => {
  const el = () => ({ dataset: {}, attrs: {}, innerHTML: "", setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    querySelector() { return { innerHTML: "", querySelector: () => ({}) }; } });
  ctx.state.pluginPage = "nope";
  const a = el();
  ctx.renderPluginPage(a);
  assert.match(a.innerHTML, /Loading plugins/); // activation still running
  pages.pagesReady();
  const b = el();
  ctx.renderPluginPage(b);
  assert.match(b.innerHTML, /Plugin page not found/);
  assert.match(b.innerHTML, /<code>nope<\/code>/);
  ctx.state.pluginPage = "typed-page";
  ctx.state.selectedProject = "plain";
  const c = el();
  ctx.renderPluginPage(c);
  assert.match(c.innerHTML, /Not available for this project/);
  assert.match(c.innerHTML, /Not for basic projects/);
  ctx.state.selectedProject = "all";
  const d = el();
  ctx.renderPluginPage(d);
  assert.match(d.innerHTML, /Choose a project/);
  ctx.state.selectedProject = "avi";
  const host = api(ctx, { sanitize: (h) => h }, "typed");
  const seen = [];
  host.registerPage({ id: "typed-page", title: "Typed", render(box, page) { seen.push(page); throw new Error("<b>boom</b>"); } });
  const e = el();
  ctx.renderPluginPage(e);
  assert.equal(seen[0].project, "avi");
  assert.equal(seen[0].projectType, "avica");
  assert.equal(typeof seen[0].signal.aborted, "boolean");
  const before = seen.length;
  ctx.renderPluginPage(e); // same page, project and registration: kept as it is
  assert.equal(seen.length, before);
});
