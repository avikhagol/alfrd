import test from "node:test";
import assert from "node:assert/strict";

import { middleTruncate, projectLabels } from "../../src/alfrd/web/js/utils/text_fit.js";
import { filterItems, moveIndex, typeahead } from "../../src/alfrd/web/js/components/picker.js";

const chars = (s) => [...s].length; // 1 unit per character

test("middle truncation keeps short names and both ends of long ones", () => {
  assert.equal(middleTruncate("task", 10, chars), "task");
  assert.equal(middleTruncate("", 5, chars), "");
  const name = "alfrd_brainstorm (alfrd_workdir_production)";
  const out = middleTruncate(name, 20, chars);
  assert.equal(chars(out), 20);
  assert.ok(out.includes("…"));
  assert.ok(out.startsWith("alfrd_brai") && out.endsWith("oduction)"), out);
});

test("middle truncation is the longest fit and degrades to an ellipsis", () => {
  const out = middleTruncate("t-idea-alfrd-ui-improvements", 12, chars);
  assert.equal(out, "t-idea…ments");
  assert.ok(chars(out) <= 12 + 1 && middleTruncate("abcdef", 1, chars) === "…");
  assert.equal(middleTruncate("abcdef", 0, chars), "…");
});

test("duplicate project names get their folder", () => {
  const labels = projectLabels([
    { id: "a", label: "alfrd_brainstorm", root: "/w/alfrd_brainstorm" },
    { id: "b", label: "alfrd_brainstorm", root: "/w/alfrd_workdir/" },
    { id: "c", label: "avica", root: "/w/avica" },
    { id: "d", label: "solo", root: "" },
  ]);
  assert.deepEqual(labels, { a: "alfrd_brainstorm (alfrd_brainstorm)", b: "alfrd_brainstorm (alfrd_workdir)", c: "avica", d: "solo" });
});

test("picker filter, keys and type-to-jump", () => {
  const items = [{ label: "task" }, { label: "task-plugin-ui", detail: "alfrd_brainstorm" }, { label: "t-idea-alfrd" }, { label: "avica" }];
  assert.deepEqual(filterItems(items, "BRAIN").map((i) => i.label), ["task-plugin-ui"]);
  assert.equal(filterItems(items, " ").length, 4);
  assert.equal(moveIndex("ArrowDown", 3, 4), 3);
  assert.equal(moveIndex("ArrowUp", 0, 4), 0);
  assert.equal(moveIndex("End", 0, 4), 3);
  assert.equal(moveIndex("Home", 2, 4), 0);
  assert.equal(moveIndex("x", 2, 4), null);
  assert.equal(moveIndex("ArrowDown", 0, 0), null);
  assert.equal(typeahead(items, 0, "t"), 1); // a new letter moves past the current item
  assert.equal(typeahead(items, 2, "t"), 0); // and wraps
  assert.equal(typeahead(items, 0, "t-"), 2);
  assert.equal(typeahead(items, 0, "z"), null);
});
