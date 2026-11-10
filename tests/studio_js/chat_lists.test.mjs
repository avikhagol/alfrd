import test from "node:test";
import assert from "node:assert/strict";
import { listHtml, listNewline } from "../../src/alfrd/web/js/components/chat_lists.js";
import { messageHtml, fencedCodeHtml } from "../../src/alfrd/web/js/components/chat_message.js";

test("list groups, start number and escaping work for sent and received messages", () => {
  for (const role of ["user", "assistant"]) {
    const html = messageHtml({ id: "list", role, text: '- x\n- y\n3. a\n4. b\n* <script>globalThis.injected = true</script>' });
    assert.ok(html.includes('<ul><li>x</li><li>y</li></ul>'));
    assert.ok(html.includes('<ol start="3"><li>a</li><li>b</li></ol>'));
    assert.ok(html.includes('<li>&lt;script&gt;globalThis.injected = true&lt;/script&gt;</li>'));
    assert.doesNotMatch(html, /<script>/);
  }
});

test("ordinary lines, fences and list boundaries remain readable", () => {
  assert.equal(listHtml('hello <b>\nworld'), 'hello &lt;b&gt;\nworld');
  assert.equal(listHtml('- x\n\n- y'), '<ul><li>x</li></ul>\n<ul><li>y</li></ul>');
  assert.equal(listHtml('1. a\n2. b\nend'), '<ol start="1"><li>a</li><li>b</li></ol>end');
  assert.doesNotMatch(listHtml('```text\n- literal\n```'), /<ul>/);
  assert.doesNotMatch(listHtml('~~~\n3. literal\n~~~'), /<ol>/);
  const html = fencedCodeHtml('- before\n```js\n- literal\n```\n3. after');
  assert.match(html, /<ul><li>before<\/li><\/ul>/);
  assert.match(html, /<code class="language-js">- literal/);
  assert.match(html, /<ol start="3"><li>after/);
});

test("newline edit handles middle-of-text, indentation, multi-digit markers and empty items", () => {
  assert.deepEqual(listNewline('- ab\nend', 3), { text: '- a\n- b\nend', caret: 6 });
  assert.deepEqual(listNewline('  99. x', 7), { text: '  99. x\n  100. ', caret: 15 });
  for (const marker of ['- ', '* ', '3. ']) assert.deepEqual(listNewline(marker, marker.length), { text: '', caret: 0 });
  assert.deepEqual(listNewline('- a\n-   \nend', 8), { text: '- a\n\nend', caret: 4 });
  assert.equal(listNewline('- item', 1), null);
  assert.equal(listNewline('- item', 2, 5), null);
  assert.equal(listNewline('ordinary', 8), null);
  assert.deepEqual(listNewline('99999999999999999999. x', 23), { text: '99999999999999999999. x\n100000000000000000000. ', caret: 47 });
});


let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
test("browser: semantic lists, safe HTML, newline keys and Markdown source survive rendering", { skip: !chromium && "Playwright unavailable" }, async (t) => {
  const { readFile } = await import("node:fs/promises");
  const web = new URL("../../src/alfrd/web/", import.meta.url);
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const page = await browser.newPage();
  // Serve the local modules through route interception; no test HTTP listener needed.
  await page.route("http://alfrd.test/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/") return route.fulfill({ contentType: "text/html", body: '<!doctype html><link rel="stylesheet" href="/css/chat.css"><main class="chat"><div id="composer"></div><div id="log"></div></main>' });
    try { await route.fulfill({ contentType: path.endsWith(".css") ? "text/css" : "text/javascript", body: await readFile(new URL(path.slice(1), web)) }); }
    catch { await route.fulfill({ status: 404, body: "" }); }
  });
  await page.goto("http://alfrd.test/");
  await page.evaluate(async () => {
    const { composerHtml, bindComposer } = await import("/js/components/chat_composer.js");
    document.querySelector("#composer").innerHTML = composerHtml("chat");
    window.sends = 0; window.drafts = [];
    bindComposer(document, "chat", { agents: () => ({}), orchestrator: () => "lead", send: () => sends++, onDraft: (text) => drafts.push(text) });
    const { mountTranscript } = await import("/js/components/transcript.js");
    window.state = { requests: [], tasks: [], messages: [
      { id: "u", role: "user", text: "- x\n- y" },
      { id: "a", role: "assistant", text: "3. a\n4. b" },
      { id: "x", role: "assistant", text: '- <script>window.injected = true</script>\n- <img src=x onerror="window.injected = true">' },
      { id: "code", role: "assistant", text: "- before\n```text\n- literal\n```\n3. after" },
    ] };
    window.thread = mountTranscript(document.querySelector("#log"), { project: () => "p", sid: () => "s" });
    thread.update(state);
  });
  assert.equal(await page.locator('[data-msg="u"] ul > li').count(), 2);
  assert.equal(await page.locator('[data-msg="a"] ol').getAttribute("start"), "3");
  assert.equal(await page.locator('[data-msg="a"] ol > li').count(), 2);
  assert.equal(await page.locator('[data-msg="x"] script, [data-msg="x"] img').count(), 0);
  assert.ok((await page.locator('[data-msg="x"] li').first().textContent()).includes("<script>"));
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await page.waitForSelector('[data-msg="code"] .chat-code');
  assert.equal(await page.locator('[data-msg="code"] ul > li').count(), 1);
  assert.equal(await page.locator('[data-msg="code"] ol').getAttribute("start"), "3");
  for (const [draft, expected] of [["- a", "- a\n- "], ["* a", "* a\n* "], ["1. a", "1. a\n2. "]]) {
    await page.locator("#chat-input").fill(draft);
    await page.locator("#chat-input").press("Shift+Enter");
    assert.equal(await page.locator("#chat-input").inputValue(), expected);
    assert.equal(await page.locator(".chat-list-preview").count(), 0);
    await page.locator("#chat-input").press("Shift+Enter");
    assert.equal(await page.locator("#chat-input").inputValue(), expected.slice(0, expected.lastIndexOf("\n") + 1));
  }
  await page.locator("#chat-input").press("Enter");
  assert.equal(await page.evaluate(() => sends), 1);
  await page.evaluate(async () => {
    const { registerViewer } = await import("/js/components/viewers.js");
    registerViewer({ id: "test-markdown", match: [".md"], render(file, el) {
      window.markdownSource = file.text;
      el.innerHTML = '<ol start="3"><li>rendered</li></ol><script>window.injected = true</script>';
    } });
    state.messages = [{ id: "rich", role: "assistant", text: "3. original\n4. source" }];
    thread.update(state);
  });
  await page.waitForSelector('[data-msg="rich"] .chat-markdown ol');
  assert.equal(await page.evaluate(() => markdownSource), "3. original\n4. source");
  assert.equal(await page.locator('[data-msg="rich"] ol').getAttribute("start"), "3");
  assert.equal(await page.locator('[data-msg="rich"] script').count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
});
