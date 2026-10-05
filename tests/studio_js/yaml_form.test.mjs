import test from "node:test";
import assert from "node:assert/strict";
import { parseYaml } from "../../src/alfrd/web/js/utils/yaml_parser.js";
import { formTree, coerce, applyFormEdit, hintFor } from "../../src/alfrd/web/js/data/yaml_form.js";

const TEXT = `name: demo
# kept: only the edited section is rewritten
description: A demo.
entrypoint:
  - name: claude
    cmd: [claude, -p]
execution:
  timeout: 60
  verbose: true
`;

const find = (nodes, ...path) => path.reduce((ns, k) => (Array.isArray(ns) ? ns : ns.children).find((n) => n.key === k), nodes);

test("every key becomes a field; known keys missing from the file show their defaults", () => {
  const tree = formTree(parseYaml(TEXT));
  assert.equal(find(tree, "name").type, "text");
  assert.equal(find(tree, "execution", "verbose").type, "bool");
  assert.equal(find(tree, "execution", "timeout").type, "number");
  const mode = find(tree, "execution", "mode");
  assert.equal(mode.unset, true);
  assert.equal(mode.type, "select");
  assert.equal(mode.hint.default, "step");
  const item = find(tree, "entrypoint", 0);
  assert.equal(item.label, "claude");
  assert.equal(find(tree, "entrypoint", 0, "cmd").type, "list");
  assert.equal(find(tree, "entrypoint", 0, "manual").type, "bool");
  const loop = find(tree, "loop");
  assert.equal(loop.kind, "group");
  assert.equal(loop.unset, true);
  assert.equal(hintFor(["entrypoint", 3, "adapter"]).type, "select");
});

test("an edit rewrites only its top-level section", () => {
  let text = applyFormEdit(TEXT, ["execution", "mode"], "batch");
  text = applyFormEdit(text, ["loop", "workspace"], "worktree");
  text = applyFormEdit(text, ["entrypoint", 0, "cmd"], ["claude", "-p", "--verbose"]);
  assert.match(text, /# kept: only the edited section is rewritten/);
  const data = parseYaml(text);
  assert.deepEqual(data.execution, { timeout: 60, verbose: true, mode: "batch" });
  assert.deepEqual(data.loop, { workspace: "worktree" });
  assert.deepEqual(data.entrypoint[0].cmd, ["claude", "-p", "--verbose"]);
  const back = parseYaml(applyFormEdit(applyFormEdit(text, ["loop", "workspace"], undefined), ["description"], undefined));
  assert.deepEqual(back.loop, {});
  assert.equal("description" in back, false);
});

test("inputs coerce to YAML values", () => {
  assert.equal(coerce("number", "12"), 12);
  assert.throws(() => coerce("number", "0", { hint: { min: 1 } }), /at least 1/);
  assert.throws(() => coerce("text", " ", { hint: { required: true } }), /required/);
  assert.equal(coerce("text", "", { original: "x" }), undefined);
  assert.deepEqual(coerce("list", "a\n b \n\n"), ["a", "b"]);
  assert.deepEqual(coerce("list", "1\n2", { original: [5] }), [1, 2]);
  assert.equal(coerce("bool", true), true);
});

test("new list items copy the shape of the first item", async () => {
  const { newItem, blankLike } = await import("../../src/alfrd/web/js/data/yaml_form.js");
  assert.deepEqual(newItem([{ name: "claude", cmd: ["claude", "-p"], manual: true, env: { A: "1" } }]), { name: "", cmd: [], manual: false, env: { A: "" } });
  assert.equal(newItem(["a", "b"]), "");
  assert.equal(newItem([]), "");
  assert.deepEqual(blankLike({ n: 3 }), { n: "" });
  const text = applyFormEdit(TEXT, ["entrypoint", 1], newItem(parseYaml(TEXT).entrypoint));
  assert.deepEqual(parseYaml(text).entrypoint[1], { name: "", cmd: [] });
});
