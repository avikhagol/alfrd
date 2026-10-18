import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync(new URL("../../src/alfrd/web/js/components/manifest_location.js", import.meta.url), "utf8")
  .replace(/^import .*;\n/gm, "").replace(/export /g, "");
const esc = (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");

function harness(location, plan) {
  const calls = [], toasts = [], modals = [];
  const server = {
    request: async () => ({ json: async () => location }),
    mutate: async (path, payload) => { calls.push(payload); return plan.preview && payload.preview ? plan : { done: true }; },
  };
  const sandbox = { server, esc, icon: (n) => `<i ${n}></i>`, copyText: async () => {}, loadCss() {}, diffHtml: (d) => `<pre>${esc(d)}</pre>`,
    $: (sel, root) => root.nodes?.[sel] || { focus() {} }, Promise };
  runInNewContext(`${source};globalThis.showLocation = showLocation;`, sandbox);
  const ctx = { toast: (...a) => toasts.push(a), refreshProject: async () => {},
    modal: (html, setup) => { modals.push(html); const root = { nodes: {}, querySelector: () => ({ value: "rename" }) }; setup?.(root, () => {}); return root; } };
  const host = { isConnected: true, innerHTML: "" };
  return { sandbox, ctx, host, calls, toasts, modals };
}
const loc = (active, rels) => ({ manifest_location: { active, files: [".alfrd/alfrd.yaml", "alfrd.yaml", ".alfrd.yaml"].map((rel) => ({
  rel, exists: rels.includes(rel), active: rel === active, shadowed: rels.includes(rel) && rel !== active })) } });

test("nested manifest alone: the path, no notice", async () => {
  const h = harness(loc(".alfrd/alfrd.yaml", [".alfrd/alfrd.yaml"]));
  await h.sandbox.showLocation(h.host, h.ctx, "p");
  assert.match(h.host.innerHTML, /id="ps-active">\.alfrd\/alfrd\.yaml</);
  assert.ok(!h.host.innerHTML.includes("callout"));
});

test("a shadowed root file is announced as ignored, not as an error", async () => {
  const h = harness(loc(".alfrd/alfrd.yaml", [".alfrd/alfrd.yaml", "alfrd.yaml"]));
  await h.sandbox.showLocation(h.host, h.ctx, "p");
  assert.match(h.host.innerHTML, /role="status"/);
  assert.match(h.host.innerHTML, /alfrd\.yaml<\/code> also exists and is ignored/);
  assert.match(h.host.innerHTML, /Review…/);
  assert.ok(!h.host.innerHTML.includes("callout fail"));
});

test("a legacy root manifest offers the move", async () => {
  const h = harness(loc("alfrd.yaml", ["alfrd.yaml"]));
  await h.sandbox.showLocation(h.host, h.ctx, "p");
  assert.match(h.host.innerHTML, /older location/);
  assert.match(h.host.innerHTML, /Move to \.alfrd\/…/);
});

test("no manifest: nothing is shown", async () => {
  const h = harness(loc(null, []));
  await h.sandbox.showLocation(h.host, h.ctx, "p");
  assert.equal(h.host.innerHTML, "");
});

test("the move dialog previews first, never writes before the button, and warns on a conflict", async () => {
  const plan = { preview: true, status: "conflict", source: "alfrd.yaml", rename_to: "alfrd.yaml.migrated", valid: true, diff: "-name: a\n+name: <b>" };
  const h = harness(loc(".alfrd/alfrd.yaml", [".alfrd/alfrd.yaml", "alfrd.yaml"]), plan);
  await h.sandbox.showLocation(h.host, h.ctx, "p");
  h.host.onclick({ target: { closest: () => ({ dataset: { loc: "move" } }) } });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(JSON.stringify(h.calls), JSON.stringify([{ preview: true }]));
  const html = h.modals[0];
  assert.match(html, /Two different configurations/);
  assert.match(html, /class="btn danger" id="loc-go"/);
  assert.ok(html.includes("&lt;b>") && !html.includes("<b>"), "diff is escaped");
  assert.match(html, /alfrd\.yaml\.migrated/);
});
