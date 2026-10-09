import test from "node:test";
import assert from "node:assert/strict";

import { viewerFor, registerViewer, fileObject, rawUrl } from "../../src/alfrd/web/js/components/viewers.js";
import { panelRenderers, registerPanel, renderPanels } from "../../src/alfrd/web/js/components/panels.js";

test("viewerFor matches extension, then mime, then type/*; text is the fallback", () => {
  assert.equal(viewerFor("a/run.log").id, "text");
  assert.equal(viewerFor("a/run.txt", "text/plain").id, "text");
  assert.equal(viewerFor("plot.png").id, "image");
  assert.equal(viewerFor("PLOT.JPEG", "text/plain").id, "image"); // extension beats the mime
  assert.equal(viewerFor("Report.PDF").id, "pdf");
  assert.equal(viewerFor("download", "application/pdf").id, "pdf");
  assert.equal(viewerFor("icon.svg").id, "text"); // never rendered as an image
  assert.equal(viewerFor("data.unknown").id, "text");
});

test("a later registration wins over a built-in", () => {
  const seen = [];
  registerViewer({ id: "fancy-image", match: ["image/*", ".png"], render: (file) => seen.push(file.name) });
  assert.equal(viewerFor("x.png").id, "fancy-image");
  assert.equal(viewerFor("y.gif", "image/gif").id, "image"); // extension match beats fancy's image/*
  registerViewer({ id: "md", match: [".md"], render() {} });
  assert.equal(viewerFor("README.md", "text/plain").id, "md");
  assert.throws(() => registerViewer({ id: "x" }), TypeError);
});

test("file objects carry the raw URL", () => {
  const f = fileObject("p 1", "plots/a b.pdf");
  assert.equal(f.name, "a b.pdf");
  assert.equal(f.mime, "application/pdf");
  assert.equal(f.url, rawUrl("p 1", "plots/a b.pdf"));
  assert.match(f.url, /^\/api\/studio\/projects\/p%201\/file\?path=plots%2Fa\+b\.pdf&raw=1$/);
});

const view = (panel, inst) => ({ levels: [], panels: [{ index: 0, panel, title: "T", scope: "workdir", instances: [inst] }] });

test("panels render through the registry; custom and client kinds included", async () => {
  assert.deepEqual(Object.keys(panelRenderers).slice(0, 6), ["file_status", "files", "json_fields", "csv_table", "text", "image"]);
  registerPanel("chart", (inst, ctx, t, x) => `<i>${inst.points.join(",")}|${x.panel.panel}|${t.project}</i>`);
  const html = await renderPanels(view("chart", { points: [1, 2] }), {}, { project: "p" });
  assert.match(html, /<i>1,2\|chart\|p<\/i>/);
  const client = await renderPanels(view("avica_config", { client: true }), {}, { project: "p" }, { clientPanels: { avica_config: async () => "<b>cfg</b>" } });
  assert.match(client, /<b>cfg<\/b>/);
  assert.equal(panelRenderers.avica_config !== undefined, true);
  const unknown = await renderPanels(view("nope", { error: "unknown panel type 'nope'" }), {}, { project: "p" });
  assert.match(unknown, /unknown panel type &#39;nope&#39;|unknown panel type 'nope'/);
});

test("the text panel keeps its <pre> for text and leaves a viewer placeholder otherwise", async () => {
  const html = await renderPanels(view("text", { files: [{ rel: "a.out", text: "<x>" }, { rel: "b.png", text: "" }] }), {}, { project: "p" });
  assert.match(html, /<summary class="mono small">a\.out<\/summary><pre class="code small">&lt;x&gt;<\/pre><\/details>/);
  assert.match(html, /<div class="viewer" data-viewer="[a-z-]+" data-viewer-rel="b\.png"><\/div>/);
});
