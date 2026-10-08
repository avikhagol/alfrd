import test from "node:test";
import assert from "node:assert/strict";
import { parseRoute, routeHash, chooseTarget } from "../../src/alfrd/web/js/utils/dom.js";

test("links round-trip project and target including reserved characters", () => {
  assert.deepEqual(parseRoute(routeHash("logs", "p & 1", "p & 1/J+2")), { view: "logs", project: "p & 1", target: "p & 1/J+2" });
  assert.deepEqual(parseRoute("#/results"), { view: "results", project: null, target: null });
});
test("URL wins, invalid URL falls back to the project's stored target", () => {
  const targets = [{ id: "p/A" }, { id: "p/B" }];
  assert.equal(chooseTarget(targets, "p/B", "p/A"), "p/B");
  assert.equal(chooseTarget(targets, "q/B", "p/B"), "p/B");
  assert.equal(chooseTarget(targets, "q/B"), "p/A");
  assert.equal(chooseTarget([]), null);
});
