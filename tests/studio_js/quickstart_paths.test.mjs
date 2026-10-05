import test from "node:test";
import assert from "node:assert/strict";
import { getPath, setPath } from "../../src/alfrd/web/js/components/quickstart.js";

test("dotted paths address list items by name or index, like alfrd/quickstart.py", () => {
  const data = { entrypoint: [{ name: "claude", cmd: ["claude", "-p"] }], workflows: [{ repeat: { iterations: 4 } }] };
  assert.equal(getPath(data, "entrypoint.claude.cmd.0"), "claude");
  assert.equal(getPath(data, "workflows.0.repeat.iterations"), 4);
  assert.equal(getPath(data, "entrypoint.codex.model"), undefined);
  setPath(data, "entrypoint.claude.model", "opus");
  setPath(data, "loop.workspace", "shared");
  setPath(data, "entrypoint.claude.cmd.0", "/usr/bin/claude");
  assert.deepEqual(data.entrypoint[0], { name: "claude", cmd: ["/usr/bin/claude", "-p"], model: "opus" });
  assert.deepEqual(data.loop, { workspace: "shared" });
  setPath(data, "entrypoint.claude.model", undefined);
  assert.equal("model" in data.entrypoint[0], false);
  assert.throws(() => setPath(data, "entrypoint.codex.model", "x"), /not in alfrd.yaml/);
});
