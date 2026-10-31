import test from "node:test";
import assert from "node:assert/strict";

globalThis.document = { hidden: false, addEventListener() {}, querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
globalThis.window = { addEventListener() {}, removeEventListener() {} };
const { activationLabel, render } = await import("../../src/alfrd/web/js/components/skills_explorer.js");
const { server } = await import("../../src/alfrd/web/js/data/server.js");

test("activation checkbox labels show configured models and escape role and model text", () => {
  assert.equal(activationLabel("orchestrator", { orchestrator: "Claude Opus 5.5" }), "Orchestrator (Claude Opus 5.5)");
  assert.equal(activationLabel("role:development", { "role:development": "GPT (Codex)" }), "development (GPT (Codex))");
  assert.equal(activationLabel("role:writing"), "writing");
  assert.equal(activationLabel("role:<img>", { "role:<img>": "<script>" }), "&lt;img&gt; (&lt;script&gt;)");
});

test("mounted activation save sends the current CSRF token through the shared mutation helper", async () => {
  const nodes = new Map();
  const el = { isConnected: true, closest: () => null, querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, { setAttribute() {} });
    return nodes.get(selector);
  } };
  const oldFetch = globalThis.fetch, oldSession = server.session;
  const sent = [];
  globalThis.fetch = async (url, init = {}) => {
    sent.push({ url, ...init });
    return new Response(JSON.stringify(url.endsWith("/inventory") ? { skills: [] } :
      { activations: [], roles: [], hash: "missing", legacy: false } ), { headers: { "Content-Type": "application/json" } });
  };
  try {
    server.session = { mutations_enabled: true, csrf_token: "old-token" };
    render(el, { state: { mode: "server", selectedProject: "demo" } });
    await new Promise(resolve => setImmediate(resolve));
    server.session.csrf_token = "current-token";
    await el.querySelector("[data-activation-save]").onclick();
    const put = sent.find(request => request.method === "PUT");
    assert.equal(put.url, "/api/studio/projects/demo/chat/skills/activations");
    assert.equal(put.headers["X-CSRF-Token"], "current-token");
    assert.deepEqual(JSON.parse(put.body), { version: 1, activations: [], base_hash: "missing" });
    assert.match(el.querySelector("[data-activation-status]").textContent, /Activations saved/);
  } finally { globalThis.fetch = oldFetch; server.session = oldSession; }
});
