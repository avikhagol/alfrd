import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

function load(file, names, extra = {}) {
  const source = [file].flat().map(name => readFileSync(new URL(`../../src/alfrd/web/js/components/${name}`, import.meta.url), "utf8").replace(/^import .*;\n/gm, "").replace(/export /g, "")).join("\n");
  const box = { esc: s => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"), server: {}, encodeURIComponent, AbortSignal, ...extra };
  runInNewContext(`${source}; globalThis.api = {${names}}`, box);
  return box.api;
}
const catalog = () => ({ catalog_url: "file:///tmp/catalog.json", name: "Local", fetched_at: "2026-10-08T00:00:00Z", plugins: [
  { id: "new", title: "New viewer", latest: "1", kinds: ["viewer"], compatible: true, description: "Read notes" },
  { id: "current", title: "Current", latest: "1", installed: "1", kinds: ["theme"], compatible: true },
  { id: "update", title: "Update", latest: "2", installed: "1", update_available: true, kinds: ["converter"], compatible: true, missing_bin: ["gs"] },
  { id: "future", title: "Future", latest: "3", kinds: ["panel"], compatible: false, alfrd_api: ">=3" },
] });
const ui = load(["plugins_install_dialog.js", "plugins_browse.js"], "filterCatalog,renderCatalog,validSource");

test("catalog shows install/update/current/incompatible/missing-binary states", () => {
  const html = ui.renderCatalog(catalog());
  for (const text of ["Install…", "Update 2", "Installed", "Needs a newer alfrd", "Needs gs"]) assert.ok(html.includes(text), text);
  assert.ok(html.indexOf("Update</b>") < html.indexOf("Current</b>"));
  assert.doesNotMatch(html, /data-install="future"/);
});

test("catalog has explicit no-catalog, failed-load and no-match recovery", () => {
  assert.match(ui.renderCatalog({}), /No plugin catalog is set/);
  assert.match(ui.renderCatalog({}), /catalog_url/);
  assert.match(ui.renderCatalog({}), /Install from source/);
  assert.match(ui.renderCatalog({ catalog_url: "file:///missing", error: "failed", plugins: [] }), /Try again/);
  assert.match(ui.renderCatalog(catalog(), "unmatched"), /Clear search/);
});

test("search is case-insensitive across descriptions and combines with kind", () => {
  assert.deepEqual(Array.from(ui.filterCatalog(catalog(), "NOTES", "viewer"), p => p.id), ["new"]);
  assert.equal(ui.filterCatalog(catalog(), "notes", "theme").length, 0);
});

test("catalog copy escapes untrusted content and rejects executable homepage URLs", () => {
  const c = catalog(); c.plugins[0].title = '<img onerror="bad">'; c.plugins[0].homepage = "javascript:bad()";
  assert.doesNotMatch(ui.renderCatalog(c), /<img|href="javascript:/);
});

test("advanced confirmation requires a source and the exact valid id syntax", () => {
  for (const [source, id] of [["", "markdown"], ["pkg", "Markdown"], ["pkg", "a/b"], ["pkg", "a".repeat(42)]]) assert.equal(ui.validSource(source, id), false);
  assert.equal(ui.validSource("/tmp/plugin.whl", "markdown_1-test"), true);
});

test("job log tails pass offset and read status/restart headers", async () => {
  let url;
  const jobs = load("plugin_jobs.js", "tailJob", { server: { request: async u => { url = u; return { ok: true, text: async () => "done\n", headers: new Map([["X-Offset", "23"], ["X-Job-Status", "done"], ["X-Restart-Required", "1"]]) }; } } });
  const chunk = await jobs.tailJob({ id: "job1" }, 7);
  assert.equal(url, "/api/studio/plugins/jobs/job1?offset=7");
  assert.equal(chunk.offset, 23); assert.equal(chunk.status, "done"); assert.equal(chunk.restart, true);
});

test("restart posts once and waits for health down then up before reloading", async () => {
  const { restartServer } = load("plugin_jobs.js", "restartServer");
  let clock = 0, reloads = 0; const calls = [], health = [true, false, false, true];
  await restartServer({ mutate: async path => calls.push(path), now: () => clock, sleep: async ms => { clock += ms; }, request: async path => { calls.push(path); return { ok: health.shift() }; }, reload: () => reloads++ });
  assert.equal(calls[0], "/studio/restart"); assert.equal(calls.length, 5); assert.equal(reloads, 1); assert.equal(clock, 2000);
});

test("restart refusal and unavailable server never reload the Studio", async () => {
  const { restartServer } = load("plugin_jobs.js", "restartServer");
  let reloads = 0, clock = 0;
  await assert.rejects(restartServer({ mutate: async () => { throw new Error("busy"); }, reload: () => reloads++ }), /busy/);
  await assert.rejects(restartServer({ mutate: async () => {}, now: () => clock, sleep: async ms => { clock += ms; }, request: async () => { throw new Error("offline"); }, reload: () => reloads++ }), /has not returned/);
  assert.equal(reloads, 0);
});
