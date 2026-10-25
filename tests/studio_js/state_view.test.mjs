import test from "node:test";
import assert from "node:assert/strict";
import { empty, loading, error } from "../../src/alfrd/web/js/components/state_view.js";

test("empty escapes its title and next step", () => {
  const view = empty("No <files>", "Add a <file>");
  assert.match(view, /No &lt;files&gt;/);
  assert.match(view, /Add a &lt;file&gt;/);
  assert.match(view, /state-empty/);
});

test("error maps every frozen HTTP status to plain text", () => {
  const expected = new Map([
    [0, "Server unreachable"],
    [403, "Read-only or CSRF"],
    [404, "Gone"],
    [409, "Changed elsewhere"],
    [413, "Too large"],
    [415, "Unsupported type"],
    [422, "Unsupported adapter capability"],
  ]);
  for (const [status, text] of expected) {
    assert.match(error({ status, message: "untrusted detail" }), new RegExp(text));
    assert.doesNotMatch(error({ status, message: "untrusted detail" }), /untrusted detail/);
  }
});

test("loading renders retry and invokes it after the timeout", async () => {
  let retries = 0;
  const view = loading("Loading files…", { timeoutMs: 5, onRetry: () => { retries++; } });
  assert.match(view, /Loading files…/);
  assert.match(view, /data-state-retry/);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(retries, 1);
});

test("retry is optional", () => {
  assert.doesNotMatch(loading("Loading"), /data-state-retry/);
  assert.doesNotMatch(error({ status: 404 }), /data-state-retry/);
});
