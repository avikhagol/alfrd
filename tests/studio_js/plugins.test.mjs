import test from "node:test";
import assert from "node:assert/strict";
import { activateAll, api, fetchJSON, pluginErrors, reportServerErrors } from "../../src/alfrd/web/js/components/plugin_api.js";
import { server } from "../../src/alfrd/web/js/data/server.js";
import { converterFor, setConverters, conversionButton, conversionUrl, convertFile, registerViewer, renderViewer, fileObject } from "../../src/alfrd/web/js/components/viewers.js";
import { renderPanels, rendererGeneration, registerPanel } from "../../src/alfrd/web/js/components/panels.js";

const context = () => ({ state: { selectedProject: "p" }, lines: [], providers: [],
  log(...args) { this.lines.push(args); }, update() {}, toast() {},
  palette: { register(fn) { this.providers ||= []; this.providers.push(fn); } },
});

test("one failing activate is diagnosed while the following plugin activates", async () => {
  const ctx = context();
  const module = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
  await activateAll({ plugins: [
    { id: "broken", title: "Broken", enabled: true, active: true, status: "ok", web: { js: module("export function activate(){throw new Error('bad plugin')} ") } },
    { id: "good", title: "Good", enabled: true, active: true, status: "ok", web: { js: module("export function activate(api){api.registerCommand({id:'good-command',label:'Good command',run(){}})}") } },
    { id: "disabled", enabled: false, active: false, status: "disabled", web: { js: "invalid" } },
  ] }, ctx);
  assert.deepEqual(pluginErrors.map((p) => [p.id, p.message]), [["broken", "bad plugin"]]);
  assert.deepEqual(ctx.lines, [["error", "broken: browser: bad plugin", "plugins"]]);
  assert.equal(ctx.palette.providers[0]()[0].id, "good-command");
});

test("server load errors are logged once without a toast storm", () => {
  const ctx = context();
  const list = { plugins: [{ id: "gs-error", status: "missing_bin", missing_bin: ["gs"] }, { id: "off", status: "disabled" }] };
  reportServerErrors(list, ctx); reportServerErrors(list, ctx);
  assert.deepEqual(ctx.lines, [["error", "gs-error: missing binary: gs", "plugins"]]);
});

test("fetchJSON keeps GET same-origin and mutations behind the server CSRF helper", async (t) => {
  const savedRequest = server.request, savedMutate = server.mutate;
  t.after(() => { server.request = savedRequest; server.mutate = savedMutate; });
  const seen = [];
  server.request = async (url, init) => { seen.push([url, init]); return { ok: true, json: async () => ({ plugins: [] }) }; };
  server.mutate = async (...args) => { seen.push(args); return { saved: true }; };
  assert.deepEqual(await fetchJSON("/api/studio/plugins"), { plugins: [] });
  assert.equal(seen[0][0], "/api/studio/plugins");
  assert.deepEqual(await fetchJSON("/studio/theme", { method: "post", body: { id: "daylight-orbit" } }), { saved: true });
  assert.deepEqual(seen[1], ["/studio/theme", { id: "daylight-orbit" }, "POST"]);
  await assert.rejects(fetchJSON("https://evil.invalid"), /same-origin/);
  await assert.rejects(fetchJSON("//evil.invalid"), /same-origin/);
  await assert.rejects(fetchJSON("/\\evil.invalid"), /same-origin/);
});

test("commands are validated and a single provider reflects later registrations", () => {
  const ctx = context();
  const host = api(ctx, { sanitize: (html) => html });
  assert.throws(() => host.registerCommand({ id: "x" }), TypeError);
  host.registerCommand({ id: "one", label: "One", run() {} });
  host.registerCommand({ id: "one", label: "Replacement", run() {} });
  host.registerCommand({ id: "two", label: "Two", run() {} });
  assert.equal(ctx.palette.providers.length, 1);
  assert.deepEqual(ctx.palette.providers[0]().map((c) => c.label), ["Replacement", "Two"]);
});

test("Convert to PDF appears only for a matching converter with the fallback text viewer", async () => {
  setConverters([{ src: [".ps", ".eps"], to: "pdf", plugin: "ps2pdf", title: "PostScript to PDF" }]);
  assert.equal(converterFor("PLOT.EPS").plugin, "ps2pdf");
  assert.match(conversionButton("plot.eps"), /Convert to PDF/);
  assert.equal(conversionButton("readme.txt"), "");
  assert.equal(conversionButton("plot.pdf"), "");
  registerViewer({ id: "custom-ps", match: [".ps"], render() {} });
  assert.equal(conversionButton("plot.ps"), "");
  const view = { levels: [], panels: [{ index: 0, panel: "text", title: "Plots", scope: "workdir", instances: [{ files: [{ rel: "plot.eps", text: "%EPS" }] }] }] };
  const html = await renderPanels(view, {}, { project: "p" });
  assert.match(html, /data-convert-rel="plot.eps"/);
  assert.match(html, /<pre class="code small">%EPS<\/pre>/);
  setConverters([]);
});

test("conversion uses escaped project/path query and propagates server JSON errors", async (t) => {
  assert.equal(conversionUrl("p 1", "a b.eps"), "/api/studio/projects/p%201/convert?path=a+b.eps&to=pdf");
  assert.throws(() => conversionUrl("all", "x.eps"), /Select a project/);
  const saved = server.request;
  t.after(() => { server.request = saved; });
  server.request = async () => ({ ok: false, status: 504, json: async () => ({ error: { message: "Conversion timed out after 120 s" } }) });
  await assert.rejects(convertFile("p", "plot.eps"), /timed out/);
  const pdf = new Blob(["%PDF-1.4"], { type: "application/pdf" });
  server.request = async () => ({ ok: true, blob: async () => pdf });
  assert.equal(await convertFile("p", "plot.eps"), pdf);
});

test("custom text viewers receive fetched content and panels can pass existing content", async (t) => {
  const saved = server.projectFile;
  t.after(() => { server.projectFile = saved; });
  let requests = 0;
  server.projectFile = async () => { requests++; return "# Report"; };
  const rendered = [];
  const viewer = { id: "markdown", render(file) { rendered.push(file.text); } };
  await renderViewer(viewer, fileObject("p", "README.md"), {});
  await renderViewer(viewer, fileObject("p", "README.md", { text: "# Panel report" }), {});
  assert.deepEqual(rendered, ["# Report", "# Panel report"]);
  assert.equal(requests, 1);
});

test("late plugin contributions invalidate cached panel HTML; repeated client registration is stable", () => {
  let before = rendererGeneration();
  registerViewer({ id: "late-markdown", match: [".md"], render() {} });
  assert.ok(rendererGeneration() > before);
  before = rendererGeneration();
  const render = () => "<p>custom panel</p>";
  registerPanel("late-panel", render);
  assert.ok(rendererGeneration() > before);
  before = rendererGeneration();
  registerPanel("late-panel", render);
  assert.equal(rendererGeneration(), before);
  setConverters([{ src: [".eps"], to: "pdf", plugin: "late-converter" }]);
  assert.ok(rendererGeneration() > before);
  before = rendererGeneration();
  setConverters([{ src: [".eps"], to: "pdf", plugin: "late-converter" }]);
  assert.equal(rendererGeneration(), before);
  setConverters([]);
});
