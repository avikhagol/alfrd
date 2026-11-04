import test from "node:test";
import assert from "node:assert/strict";
import { staleState } from "../../src/alfrd/web/js/data/chat.js";

// The poll code applies a response only when `staleState` lets it through (chat_view.js, chat_dock.js).
const apply = (shown, response) => (staleState(shown, response) ? shown : response);

test("a lower-rev state response that arrives late is ignored", () => {
  const r5 = { rev: 5, messages: ["new"] }, r3 = { rev: 3, messages: ["old"] };
  let shown = null;
  for (const response of [r5, r3]) shown = apply(shown, response); // delivered out of order
  assert.equal(shown, r5);
});

test("equal and higher revs are applied; the first response always is", () => {
  const a = { rev: 2 }, b = { rev: 2, again: true }, c = { rev: 4 };
  assert.equal(apply(null, a), a);
  assert.equal(apply(a, b), b);
  assert.equal(apply(b, c), c);
});

test("an unchanged short-circuit never replaces the shown state; a rev-less state still applies", () => {
  const shown = { rev: 7 };
  assert.equal(apply(shown, { rev: 7, unchanged: true }), shown);
  const legacy = { messages: [] };
  assert.equal(apply(shown, legacy), legacy);
});
