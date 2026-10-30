import test from "node:test";
import assert from "node:assert/strict";
import { server } from "../../src/alfrd/web/js/data/server.js";

test("restored scope limits picker projects while Settings can show a new project", async () => {
  const originalFetch = globalThis.fetch;
  const originalLocation = globalThis.location;
  const projects = ["kept", "hidden", "new"].map((identifier) => ({ identifier, name: identifier }));
  let shown = ["kept"];
  globalThis.location = { protocol: "http:" };
  globalThis.fetch = async (url, init) => {
    let body;
    if (url === "/api/studio/session") body = { app: "alfrd", projects: shown, csrf_token: "test", mutations_enabled: true };
    else if (url === "/api/projects") body = { projects };
    else {
      assert.equal(url, "/api/studio/projects/new/visibility");
      assert.equal(init.headers["X-CSRF-Token"], "test");
      assert.deepEqual(JSON.parse(init.body), { state: "shown" });
      shown = [...shown, "new"];
      body = { projects: shown };
    }
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  };
  try {
    await server.detect();
    assert.deepEqual((await server.listProjects({ scoped: true })).map((p) => p.name), ["kept"]);
    assert.deepEqual((await server.listProjects()).map((p) => p.identifier), ["kept", "hidden", "new"]);
    await server.setProjectVisibility("new", "shown");
    assert.deepEqual((await server.listProjects({ scoped: true })).map((p) => p.name), ["kept", "new"]);
    server.session = null; // page reload: detect reads the restored scope from the server
    await server.detect();
    assert.deepEqual((await server.listProjects({ scoped: true })).map((p) => p.name), ["kept", "new"]);
    shown = null; // no saved state or no surviving saved identifiers
    await server.detect();
    assert.equal((await server.listProjects({ scoped: true })).length, 3);
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.location = originalLocation;
    server.session = null;
  }
});
