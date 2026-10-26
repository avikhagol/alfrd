import test from "node:test";
import assert from "node:assert/strict";
import { messageIdentity, identitySlot, messageTime, adjacentMessages } from "../../src/alfrd/web/js/data/chat_identity.js";
import { messageHtml, fencedCodeHtml, copyCode } from "../../src/alfrd/web/js/components/chat_message.js";
import { transcriptItems } from "../../src/alfrd/web/js/components/transcript.js";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

test("identity remains keyed across order, status, deleted agents and surfaces", () => {
  const message = { id: "m1", agent: "worker", role: "assistant", text: "hello", at: "2026-10-26T12:34:00Z" };
  const options = { agents: { worker: { label: "Build Expert", roles: ["development"] } }, orchestrator: "lead" };
  const who = messageIdentity(message, options);
  assert.deepEqual(who, { key: "worker", name: "Build Expert", role: "development", initials: "BE", slot: identitySlot("worker") });
  assert.equal(messageIdentity({ ...message, status: "failed" }, options).slot, who.slot);
  assert.equal(messageIdentity(message).name, "worker");
  assert.equal(messageIdentity(message, { orchestrator: () => "worker" }).role, "Orchestrator");
  assert.equal(messageIdentity({ role: "system", agent: "provider" }).name, "ALFRD");
  assert.equal(messageIdentity({ role: "user" }).name, "You");
  assert.ok([...messageIdentity(message, { agents: { worker: { label: "ß ß" } } }).initials].length <= 2);
  for (const compact of [false, true]) {
    const html = messageHtml(message, { ...options, compact });
    for (const value of ['data-msg="m1"', 'class="chat-text"', 'aria-hidden="true">BE', '@worker', 'development', '<time datetime=']) assert.ok(html.includes(value), value);
    assert.ok(html.includes(messageTime(message.at).short));
  }
  assert.equal(messageTime(null), null);
  assert.equal(messageTime("bad date"), null);
  assert.doesNotMatch(messageHtml({ ...message, at: null }), /<time/);
});

test("adjacency requires explicit request, role, agent and known nearby times", () => {
  const a = { role: "assistant", agent: "one", at: "2026-10-26T12:00:00Z" }, b = { ...a, at: "2026-10-26T12:02:00Z" };
  assert.equal(adjacentMessages(a, b, "r", "r"), true);
  for (const changed of [{ agent: "two" }, { role: "system" }, { kind: "delegated_decision" }, { at: null }, { at: "2026-10-26T12:02:01Z" }, { at: "2026-10-26T11:59:59Z" }]) assert.equal(adjacentMessages(a, { ...b, ...changed }, "r", "r"), false);
  assert.equal(adjacentMessages(a, b, "r", "other"), false);
  const items = transcriptItems([{ id: "r", request: { id: "r" }, messages: [{ ...a, id: "a" }, { ...b, id: "b" }, { ...b, id: "c" }] }]);
  assert.equal(Boolean(items[2].continuation), false, "activity breaks a speaker group");
  assert.equal(items[3].continuation, true);
  assert.ok(items[0].day);
});

test("source is escaped, protocol is optional and user JSON remains literal", () => {
  const json = '{"protocol":"alfrd.orchestration/1","kind":"result","result":{"status":"done","summary":"Answer","artifacts":["a@123"]}}';
  const assistant = messageHtml({ id: "a", role: "assistant", text: json });
  assert.match(assistant, /chat-text">Answer/);
  assert.match(assistant, /<details[^>]*><summary>Technical details/);
  assert.match(assistant, /data-artifact="a@123"/);
  assert.doesNotMatch(assistant, /<details open/);
  const user = messageHtml({ id: '"<x>', role: "user", text: json });
  assert.match(user, /&quot;protocol&quot;/);
  assert.doesNotMatch(user, /Technical details/);
  assert.doesNotMatch(messageHtml({ role: "assistant", text: '<img onerror="bad()">' }), /<img/);
  const code = fencedCodeHtml('```js\nconst x = "<script>";\n```');
  assert.match(code, /language-js/); assert.match(code, /&lt;script&gt;/); assert.doesNotMatch(code, /```|Copy code/);
  assert.equal(fencedCodeHtml(json), null);
});

test("copy uses only code and gives local success and recovery feedback", async () => {
  const status = { textContent: "" }, pre = { textContent: "const x = '" + "x".repeat(300) + "';\n" };
  const group = { querySelector: (selector) => selector === "pre" ? pre : status }, button = { closest: () => group };
  let copied;
  await copyCode(button, { writeText: async (text) => { copied = text; } });
  assert.equal(copied, pre.textContent); assert.equal(status.textContent, "Copied");
  await copyCode(button, { writeText: async () => { throw Error("denied"); } });
  assert.equal(status.textContent, "Couldn't copy. Select the code and copy it.");
  assert.equal(pre.textContent, copied);
});

let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional browser installation */ }
test("thread in Chromium: identity, code recovery, themes and 60-second concurrent continuity", { skip: !chromium && "Chromium/Playwright unavailable" }, async (t) => {
  const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));
  const service = createServer(async (req, res) => {
    const path = new URL(req.url, "http://localhost").pathname;
    if (path === "/") {
      res.setHeader("Content-Type", "text/html");
      return res.end('<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/chat-thread.css"><link id="theme" rel="stylesheet"><body><section class="chat" style="display:block;width:100%;height:100%"><div id="log" tabindex="-1" aria-label="Conversation" style="height:600px;overflow:auto"></div></section></body>');
    }
    const file = resolve(web, path.slice(1));
    if (!file.startsWith(resolve(web) + "/")) { res.writeHead(404); return res.end(); }
    try { res.setHeader("Content-Type", [".mjs", ".js"].includes(extname(file)) ? "text/javascript" : "text/css"); res.end(await readFile(file)); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => service.listen(0, "127.0.0.1", r));
  t.after(() => new Promise((r) => { service.close(r); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${service.address().port}/`);
  await page.evaluate(async () => {
    const { mountTranscript } = await import('/js/components/transcript.js');
    const { transcriptItems } = await import('/js/components/transcript.js');
    const list = ["lead", "builder", "reviewer"];
    window.state = { settings: { orchestrator: "lead", agents: { lead: { label: "Team Lead" }, builder: { label: "Build Expert", roles: ["development"] }, reviewer: { label: "Review Expert", roles: ["review"] } } }, requests: [], tasks: [], messages: Array.from({ length: 100 }, (_, n) => ({ id: `m${n}`, role: "assistant", agent: list[n % 3], at: new Date(Date.UTC(2026, 9, 26, 12, n)).toISOString(), text: n === 0 ? '```js\nconst example = "' + 'x'.repeat(300) + '";\n```' : n === 1 ? JSON.stringify({ protocol: "alfrd.orchestration/1", kind: "result", result: { status: "done", summary: "Finished" } }) : `Answer ${n}` })) };
    window.thread = mountTranscript(document.querySelector('#log'), { project: () => 'p', sid: () => 's' });
    thread.update(state);
    window.items = transcriptItems;
  });
  await page.waitForSelector('.chat-code button');
  await page.evaluate(() => {
    document.querySelector('#log').scrollTop = 0;
    window.copied = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text) => window.copied.push(text) } });
  });
  await page.locator('.chat-code button').click();
  assert.equal(await page.locator('.chat-copy-status').textContent(), "Copied");
  const source = await page.locator('.chat-code pre').textContent();
  assert.deepEqual(await page.evaluate(() => window.copied), [source]);
  await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw Error('denied'); }; });
  await page.locator('.chat-code button').click();
  assert.equal(await page.locator('.chat-copy-status').textContent(), "Couldn't copy. Select the code and copy it.");
  assert.ok(await page.locator('.chat-code pre').evaluate((el) => el.scrollWidth > el.clientWidth));
  await mkdir('/tmp/alfrd-chatux/thread', { recursive: true });
  for (const theme of ['obsidian-orbit', 'daylight-orbit']) {
    await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; document.querySelector('#theme').href = `/css/themes/${theme}/theme.css`; }, theme);
    for (const compact of [false, true]) {
      await page.setViewportSize({ width: compact ? 380 : 1440, height: 980 });
      await page.evaluate((compact) => { document.querySelector('section').className = compact ? 'chat-dock' : 'chat'; document.querySelector('#log').scrollTop = 0; }, compact);
      assert.ok(await page.locator('.chat-msg time').first().evaluate((el) => getComputedStyle(el).display !== "none" && el.getBoundingClientRect().height > 0));
      for (const key of ['lead', 'builder', 'reviewer']) assert.ok((await page.locator(`[data-agent="${key}"]`).first().textContent()).includes(`@${key}`));
      await page.screenshot({ path: `/tmp/alfrd-chatux/thread/${theme}-${compact ? 'dock' : 'page'}.png` });
    }
  }
  // Keep a selected answer, focusable output and an explicitly opened disclosure
  // unchanged while a different answer grows and completes over real wall time.
  await page.evaluate(() => {
    const log = document.querySelector('#log'); log.scrollTop = 0;
    document.querySelector('.chat-code pre').focus({ preventScroll: true });
    document.querySelector('.chat-msg details').open = true;
    const range = document.createRange(); range.selectNodeContents(document.querySelector('[data-msg="m2"] .chat-text'));
    getSelection().removeAllRanges(); getSelection().addRange(range);
    window.saved = { selection: String(getSelection()), pre: document.activeElement, top: log.scrollTop, first: document.querySelector('[data-msg="m2"]') };
  });
  const started = Date.now();
  for (let n = 0; n < 61; n++) {
    await page.evaluate((n) => {
      state.messages[99].text = `Update ${n}${n === 60 ? ' complete' : ''}`;
      if (n === 1) state.messages.push({ id: 'new', role: 'assistant', agent: 'builder', text: 'New answer', at: '2026-10-26T14:00:00Z' });
      thread.update(state);
      const log = document.querySelector('#log');
      if (String(getSelection()) !== saved.selection || document.activeElement !== saved.pre || log.scrollTop !== saved.top || document.querySelector('[data-msg="m2"]') !== saved.first || !document.querySelector('.chat-msg details').open) throw Error('Reading state changed');
    }, n);
    if (n < 60) await new Promise((r) => setTimeout(r, 1000));
  }
  assert.ok(Date.now() - started >= 60000);
  assert.equal(await page.locator('.chat-new-activity').textContent(), 'Jump to latest · 1 new');
  await page.locator('.chat-new-activity').focus(); await page.keyboard.press('Enter');
  assert.ok(await page.locator('.chat-new-activity').isHidden());
  assert.ok(await page.locator('#log').evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 2));
  await page.evaluate(async () => {
    thread.reset(); thread.update(state);
    if (!document.querySelector('.chat-msg details').open) throw Error('Open disclosure lost on navigation');
    document.querySelector('.chat-msg details').open = false;
    await new Promise((r) => setTimeout(r, 0));
    thread.reset(); thread.update(state);
    if (document.querySelector('.chat-msg details').open) throw Error('Explicit closed disclosure lost on navigation');
  });
});
