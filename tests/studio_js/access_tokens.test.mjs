import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { server } from "../../src/alfrd/web/js/data/server.js";
import { createLive } from "../../src/alfrd/web/js/data/live.js";

globalThis.location = { protocol: "http:" };
const visibilityListeners = [];
globalThis.document = { hidden: false, addEventListener: (_, fn) => visibilityListeners.push(fn) };
globalThis.window = { addEventListener() {} };

test.beforeEach(() => { server.authRequired = false; server.session = null; });

test("session 401 requires the access link, clears the timeout and never retries", async (t) => {
  const calls = [];
  const timers = new Set();
  t.mock.method(globalThis, "setTimeout", (fn) => { timers.add(fn); return fn; });
  t.mock.method(globalThis, "clearTimeout", (fn) => timers.delete(fn));
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    // A proxy may send HTML; authentication must not depend on parsing JSON.
    return new Response("Unauthorized", { status: 401 });
  });
  assert.equal(await server.detect(), null);
  assert.equal(server.authRequired, true);
  assert.equal(await server.detect(), null);
  assert.deepEqual(calls, ["/api/studio/session"]);
  assert.equal(timers.size, 0);
});

test("404 still permits browser mode; a successful session is detected", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response("Not found", { status: 404 }));
  assert.equal(await server.detect(), null);
  assert.equal(server.authRequired, false);
  const session = { app: "alfrd", version: "test" };
  globalThis.fetch = async () => Response.json(session);
  assert.deepEqual(await server.detect(), session);
});

for (const [name, call] of [
  ["JSON", () => server.listProjects()],
  ["mutation", () => server.mutate("/studio/test", {})],
  ["log", () => server.avicaLog("p", "log")],
  ["file range", () => server.projectFileFrom("p", "a.log")],
  ["file", () => server.projectFile("p", "a.txt")],
  ["collection", () => server.request(server.collectionFileUrl("p", "c", "r", "a.csv"))],
]) {
  test(`${name} 401 uses the shared handler once and blocks later requests`, async (t) => {
    server.session = { mutations_enabled: true, csrf_token: "csrf" };
    let requests = 0, notices = 0;
    const unsubscribe = server.onAuthRequired(() => notices++);
    t.after(unsubscribe);
    t.mock.method(globalThis, "fetch", async () => { requests++; return new Response("", { status: 401 }); });
    await assert.rejects(call(), { status: 401 });
    assert.equal(server.session, null);
    await assert.rejects(server.listProjects(), { status: 401 });
    await assert.rejects(server.mutate("/studio/test", {}), { status: 401 });
    assert.equal(notices, 1);
    assert.equal(requests, 1);
  });
}

test("SSE failure checks HTTP via polling; 401 cancels stream and retry timers", async (t) => {
  const streams = [], timers = new Set();
  t.mock.method(globalThis, "setTimeout", (fn) => { timers.add(fn); return fn; });
  t.mock.method(globalThis, "clearTimeout", (fn) => timers.delete(fn));
  globalThis.EventSource = class {
    constructor() { streams.push(this); }
    addEventListener() {}
    close() { this.closed = true; }
  };
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => { requests++; return new Response("", { status: 401 }); });
  const live = createLive({ mode: () => "server", projects: () => ["p"], folders: () => [] });
  live.start();
  streams[0].onerror();
  await new Promise(setImmediate);
  assert.equal(server.authRequired, true);
  assert.equal(streams[0].closed, true);
  assert.equal(live.status.state, "auth-required");
  assert.equal(timers.size, 0);
  live.kick();
  visibilityListeners.forEach((fn) => fn());
  assert.equal(streams.length, 1);
  assert.equal(requests, 1);
  live.stop();
});

test("Studio startup 401 goes to the shared landing page without restoring data or demo", async () => {
  const app = readFileSync(new URL("../../src/alfrd/web/js/app.js", import.meta.url), "utf8");
  const source = app.slice(app.indexOf("async function boot()"), app.indexOf("boot().catch"));
  const redirects = [];
  let authHandler;
  const context = {
    server: { authRequired: false, onAuthRequired(fn) { authHandler = fn; }, async detect() { this.authRequired = true; authHandler(); return null; } },
    location: { replace: (url) => redirects.push(url) },
    window: { addEventListener() {} }, document: { body: {}, addEventListener() {} },
    renderShell() {}, bindLogs() {}, consoleHeight() {}, consoleUi: {}, route() {},
    ctx: { log() { assert.fail("must not start the workspace"); } },
    loadTemplate() {}, parseYaml() {}, loadDefaultManifest() {}, ensureTemplatesFor() {},
    storage: { get() {} },
    loadDemo() { assert.fail("must not load demo data"); },
    restoreSaved() { assert.fail("must not restore browser data"); },
    loadServer() {}, applyBundle() {}, rescan() {}, importEntries() {},
  };
  await runInNewContext(`${source}; boot()`, context);
  assert.deepEqual(redirects, ["/login"]);
});
