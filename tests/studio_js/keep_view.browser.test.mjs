// Metadata disclosures (design T2.1) in a real DOM: utils/keep_view.js in headless Chromium.
// Needs Playwright: ALFRD_PLAYWRIGHT=<path to playwright's index.mjs or package>, or a
// resolvable "playwright" package. Skipped when neither is available.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

let chromium = null;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* not installed */ }
const source = readFileSync(new URL("../../src/alfrd/web/js/utils/keep_view.js", import.meta.url), "utf8");

test("keep_view in a browser", { skip: chromium ? false : "Playwright not available" }, async (t) => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  t.after(() => browser.close());
  await page.setContent(`<!doctype html><input id="outside"><div id="scroller" style="height:200px;overflow:auto"><div id="main"></div></div>`);
  await page.evaluate(async (code) => {
    window.kv = await import(`data:text/javascript;base64,${btoa(unescape(encodeURIComponent(code)))}`);
    window.states = {};
    window.ui = { open: new Set(["meta"]), closed: new Set(), details: window.states };
    document.addEventListener("toggle", (e) => window.kv.noteDetailToggle(e, window.states), true);
    // A metadata render: one section card, two instances with the same file name, a long code block.
    window.render = (bytes = 10, extra = "") => `<details class="card sec" data-sec="meta" open><summary>Metadata files</summary><div class="sec-b">
      ${["wd1", "wd2"].map((wd) => `<details data-detail-key="meta|${wd}|task.md"><summary id="s-${wd}">task.md · ${bytes} B</summary>
        <pre style="width:200px;overflow:auto;white-space:pre">${"x".repeat(400)}\n${extra}</pre></details>`).join("")}
      <details data-detail-key="meta|wd1|notes.md" open><summary>notes.md</summary><p>notes</p></details>
      <div style="height:1200px"></div></div></details>`;
    window.show = (html, scope = "p1|t1") => window.kv.showKept(document.querySelector("#main"), html, scope, window.ui, document.querySelector("#scroller"));
    window.show(window.render());
  }, source);
  const flush = () => page.evaluate(() => new Promise((r) => setTimeout(r, 0))); // toggle events are async
  const openState = () => page.evaluate(() => [...document.querySelectorAll("#main details[data-detail-key]")].map((d) => [d.dataset.detailKey, d.open]));

  await t.test("explicit open and explicit closed both survive live re-renders", async () => {
    await page.click("#s-wd1"); // open task.md in instance 1 (default closed)
    await page.click("text=notes.md"); // close notes.md (default open)
    await flush();
    for (let i = 0; i < 5; i++) { await page.evaluate((n) => window.show(window.render(n)), 20 + i); await flush(); }
    assert.deepEqual(await openState(), [["meta|wd1|task.md", true], ["meta|wd2|task.md", false], ["meta|wd1|notes.md", false]]);
    // Every keyed disclosure's DOM state is snapshotted before a replacement (a queued toggle cannot be lost).
    assert.deepEqual(await page.evaluate(() => window.states), { "p1|t1|meta|wd1|task.md": true, "p1|t1|meta|wd2|task.md": false, "p1|t1|meta|wd1|notes.md": false });
  });

  await t.test("a file with the same name in another instance or context keeps its own state", async () => {
    assert.equal(await page.evaluate(() => document.querySelector('[data-detail-key="meta|wd2|task.md"]').open), false);
    await page.evaluate(() => window.show(window.render(), "p2|t9")); // another target: defaults
    assert.deepEqual(await openState(), [["meta|wd1|task.md", false], ["meta|wd2|task.md", false], ["meta|wd1|notes.md", true]]);
    await page.evaluate(() => window.show(window.render(), "p1|t1")); // back: the reader's states
    assert.deepEqual((await openState()).map(([, o]) => o), [true, false, false]);
  });

  await t.test("restoration does not overwrite the user's state with defaults", async () => {
    await page.evaluate(() => window.show(window.render(99)));
    await flush(); // toggle events fired by restoring `open` land here
    assert.equal(await page.evaluate(() => window.states["p1|t1|meta|wd1|notes.md"]), false);
    assert.equal(await page.evaluate(() => window.states["p1|t1|meta|wd1|task.md"]), true);
  });

  await t.test("a section card closed by the reader stays closed", async () => {
    await page.click("text=Metadata files");
    await flush();
    await page.evaluate(() => window.show(window.render(5)));
    assert.equal(await page.evaluate(() => document.querySelector("[data-sec=meta]").open), false);
    await page.click("text=Metadata files");
    await page.evaluate(() => window.show(window.render(6)));
    assert.equal(await page.evaluate(() => document.querySelector("[data-sec=meta]").open), true);
  });

  await t.test("reading position: page and code-block offsets are kept", async () => {
    await page.evaluate(() => {
      document.querySelector("#scroller").scrollTop = 150;
      document.querySelector('[data-detail-key="meta|wd1|task.md"] pre').scrollLeft = 120;
      window.show(window.render(7, "more"));
    });
    assert.deepEqual(await page.evaluate(() => [document.querySelector("#scroller").scrollTop,
      document.querySelector('[data-detail-key="meta|wd1|task.md"] pre').scrollLeft]), [150, 120]);
  });

  await t.test("the focused summary keeps focus by identity, without scrolling", async () => {
    await page.evaluate(() => { document.querySelector("#scroller").scrollTop = 0; document.querySelector("#s-wd2").focus({ preventScroll: true }); });
    await page.evaluate(() => { document.querySelector("#scroller").scrollTop = 140; window.show(window.render(8)); });
    assert.equal(await page.evaluate(() => document.activeElement.id), "s-wd2");
    assert.equal(await page.evaluate(() => document.querySelector("#scroller").scrollTop), 140);
    await page.evaluate(() => document.querySelector("[data-sec=meta] > summary").focus());
    await page.evaluate(() => window.show(window.render(9)));
    assert.equal(await page.evaluate(() => document.activeElement.textContent), "Metadata files");
  });

  await t.test("focus on another control is never stolen", async () => {
    await page.focus("#outside");
    await page.evaluate(() => window.show(window.render(10)));
    assert.equal(await page.evaluate(() => document.activeElement.id), "outside");
  });

  await t.test("an identical render leaves the DOM alone", async () => {
    const same = await page.evaluate(() => { const before = document.querySelector("#s-wd1"); window.show(window.render(10)); return before === document.querySelector("#s-wd1"); });
    assert.equal(same, true);
  });
});
