import test from "node:test";
import assert from "node:assert/strict";
import { action, api } from "../../src/alfrd/web/js/components/plugin_api.js";
import { registerProjectSection } from "../../src/alfrd/web/js/components/project_sections.js";
import { server } from "../../src/alfrd/web/js/data/server.js";

test("project action encodes each path component, returns data and preserves errors", async (t) => {
  const original = server.mutate;
  t.after(() => { server.mutate = original; });
  let request;
  server.mutate = async (...args) => { request = args; return { ok: true, data: { attached: true } }; };
  assert.deepEqual(await action("p /one", "gsheet", "save", { mapping: {} }), { attached: true });
  assert.deepEqual(request, ["/studio/projects/p%20%2Fone/plugins/gsheet/actions/save", { mapping: {} }, "POST"]);
  server.mutate = async () => ({ ok: false, error: "The mapping changed on disk; reload." });
  await assert.rejects(action("p", "gsheet", "save"), /changed on disk/);
  server.mutate = async () => { throw Object.assign(new Error("Actions are disabled"), { status: 403 }); };
  await assert.rejects(action("p", "gsheet", "save"), { message: "Actions are disabled", status: 403 });
});

test("browser API exposes the additive project helpers and validates sections", () => {
  const host = api({}, {});
  for (const key of ["registerProjectSection", "action", "dialog", "confirm"]) assert.equal(typeof host[key], "function");
  for (const spec of [{}, { id: "../bad", title: "Bad", render() {} }, { id: "x", title: "X", render() {}, order: NaN }]) {
    assert.throws(() => registerProjectSection(spec), TypeError);
  }
});
