// Entity paths (data/entities.js), mirrors tests/test_entities.py.
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "../../src/alfrd/web/js");
const E = await import(path.join(web, "data/entities.js"));

test("entities: round trip and stable order", () => {
  const e = { line: 812, file: "reductions/BV019/wd_1/casa.log", step: "rpicard", workdir: "wd_1", project_code: "BV019", target: "J0742+103", project: "avica-t-0.3" };
  const q = E.entityToQuery(e);
  assert.ok(q.startsWith("project=avica-t-0.3&target=J0742%2B103&project_code=BV019&workdir=wd_1&step=rpicard&file="));
  const back = E.entityFromQuery(q);
  assert.deepEqual(Object.keys(back), ["project", "target", "project_code", "workdir", "step", "file", "line"]);
  assert.deepEqual(E.entityFromQuery(E.entityToFragment(e)), back);
  assert.equal(E.entityFromQuery("project=p&target=J0742+103").target, "J0742+103");
  assert.equal(E.entityLabel(back), "J0742+103 · BV019/wd_1 · rpicard · casa.log:812");
});

test("entities: unknown level rejected, hierarchy levels", () => {
  assert.throws(() => E.makeEntity({ project: "p", galaxy: "M87" }), /unknown entity level/);
  assert.throws(() => E.makeEntity({ target: "A" }), /needs a project/);
  assert.throws(() => E.makeEntity({ project: "p", line: 3 }), /needs a file/);
  const levels = E.levelsFrom({ hierarchy: [{ level: "night" }, { level: "chip" }] });
  assert.deepEqual(levels, ["target", "night", "chip"]);
  assert.deepEqual(E.makeEntity({ chip: "3", project: "p" }, levels), { project: "p", chip: "3" });
  assert.throws(() => E.makeEntity({ project: "p", workdir: "wd" }, levels));
  // Python and JS write the same query string.
  assert.equal(E.entityToQuery({ project: "p", target: "A B/+" }), "project=p&target=A%20B%2F%2B");
});
