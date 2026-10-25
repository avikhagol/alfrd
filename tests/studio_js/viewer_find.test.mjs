// Copy and Find in the log, prompt and reply viewers: reachable in two clicks, labelled with the unit and attempt (run with `node --test`).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document ||= { hidden: false, addEventListener() {}, querySelectorAll: () => [], getElementById: () => null };
const { findSpans, attemptLabel, textToolsHtml, textFindHtml } = await import("../../src/alfrd/web/js/components/viewers.js");
const { logItem, openFileFull } = await import("../../src/alfrd/web/js/components/logview.js");

/** A ctx whose modal() keeps the HTML it is given; the setup callback needs a DOM, so it is not run. */
function ctxWithModal(text = "alpha\nBeta beta\n") {
  const modals = [];
  return {
    modals,
    state: { trees: {} },
    readLogRange: async () => ({ text, offset: text.length, live: true, id: "t", size: text.length, mtime: 0 }),
    modal(html, setup, size) { modals.push({ html, setup, size }); },
    toast() {},
  };
}

test("findSpans matches every occurrence, ignoring case, and nothing for an empty query", () => {
  assert.deepEqual(findSpans("Beta beta BETA", "beta"), [[0, 4], [5, 9], [10, 14]]);
  assert.deepEqual(findSpans("aaaa", "aa"), [[0, 2], [2, 4]]);
  assert.deepEqual(findSpans("abc", "x"), []);
  assert.deepEqual(findSpans("abc", ""), []);
  assert.deepEqual(findSpans(undefined, "a"), []);
});

test("attemptLabel names the unit and the attempt, marking the latest one", () => {
  assert.equal(attemptLabel({ unit: "build", attempt: 3, latest: true }), "build · attempt 3 (latest)");
  assert.equal(attemptLabel({ unit: "build", attempt: 1, latest: false }), "build · attempt 1");
  assert.equal(attemptLabel({ attempt: 2 }), "attempt 2");
  assert.equal(attemptLabel({}), "");
  assert.equal(attemptLabel(), "");
});

test("the Copy and Find buttons are in the header, visible without opening anything", () => {
  const header = textToolsHtml("build · attempt 2");
  assert.match(header, /data-text-copy[^>]*>[\s\S]*Copy<\/button>/);
  assert.match(header, /data-text-find-open[^>]*>[\s\S]*Find<\/button>/);
  assert.match(header, /data-viewer-label[^>]*>build · attempt 2</);
  assert.doesNotMatch(textToolsHtml(), /data-viewer-label/, "no label span without a label");
  // The find bar starts hidden and only Find reveals it.
  assert.match(textFindHtml(), /<div[^>]*data-text-find hidden>/);
});

test("labels are escaped", () => {
  assert.equal(attemptLabel({ unit: "<img>", attempt: 1 }), "<img> · attempt 1", "attemptLabel is plain text");
  assert.match(textToolsHtml(attemptLabel({ unit: "<img>", attempt: 1 })), /&lt;img&gt; · attempt 1/, "the markup escapes it");
});

test("the log row's expand button opens the full-screen log with Copy and Find in its header (two clicks)", async () => {
  const row = logItem({ rel: "plans/r1/build.log", name: "build.log", size: 9, mtime: 0 }, { project: "p" });
  assert.match(row, /data-log-zoom="plans\/r1\/build\.log"/, "click 1: the expand button");

  const ctx = ctxWithModal();
  await openFileFull(ctx, "p", "plans/r1/build.log", { unit: "build", attempt: 2, latest: true });
  assert.equal(ctx.modals.length, 1);
  const { html } = ctx.modals[0];
  const header = html.slice(0, html.indexOf("</header>"));
  assert.match(header, /data-text-copy/, "click 2: Copy is in the header");
  assert.match(header, /data-text-find-open/, "click 2: Find is in the header");
  assert.match(header, /build · attempt 2 \(latest\)/, "the unit and attempt are labelled");
  assert.match(html, /<div[^>]*data-text-find hidden>/, "the find bar is ready behind Find");
});

test("the reply (handoff response.md) opens with the same tools and its attempt label", async () => {
  const ctx = ctxWithModal("# Reply\n");
  await openFileFull(ctx, "p", "handoffs/0016/response.md", { unit: "write", attempt: 1, latest: false });
  const { html } = ctx.modals[0];
  assert.match(html, /data-text-copy/);
  assert.match(html, /data-text-find-open/);
  assert.match(html, /write · attempt 1/);
  assert.doesNotMatch(html, /\(latest\)/);
});

test("without attempt information the header still has Copy and Find, and no label", async () => {
  const ctx = ctxWithModal();
  await openFileFull(ctx, "p", "a/plain.log");
  const { html } = ctx.modals[0];
  assert.match(html, /data-text-copy/);
  assert.match(html, /data-text-find-open/);
  assert.doesNotMatch(html, /data-viewer-label/);
});
