// Handoff Markdown controls in Chromium with the real Studio modal shell and a fake action backend.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));
const plugin = resolve(fileURLToPath(new URL("../../examples/plugins/alfrd-markdown/alfrd_markdown/web/", import.meta.url)));

test("Handoff Markdown: themes, mobile, paging, source, copy and fallback", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") {
      const theme = url.searchParams.get("theme") || "obsidian-orbit";
      return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${theme}/theme.css"><link rel="stylesheet" href="/plugin/index.css"><body style="display:block;height:auto"><div id="home"></div><div id="modal-host" hidden></div></body>`);
    }
    if (url.pathname === "/shell.js") return send(`import { $ } from "/js/utils/dom.js"; let clearModalKeys; export ${shell}`, "text/javascript");
    const [root, rel] = url.pathname.startsWith("/plugin/") ? [plugin, url.pathname.slice(8)] : [web, url.pathname.slice(1)];
    const path = resolve(root, rel);
    if (!path.startsWith(root + sep)) return send("missing", "text/plain", 404);
    try { return send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { return send("missing", "text/plain", 404); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());


  const first = '# Résumé\n\n- item one\n- item two\n\n| Name | Result |\n| --- | --- |\n| α | Done |\n\n```py\n' + 'x'.repeat(240);
  const last = '\nprint(1)\n```\n\n## Complete\n\n<script>window.xss=1</script><img src=x onerror="window.xss=1"><a href="javascript:window.xss=1">Unsafe</a><style>body{display:none}</style>';
  const open = async (theme, width, pluginEnabled = true) => {
    const page = await browser.newPage({ viewport: { width, height: 844 } });
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async ({ first, last, pluginEnabled }) => {
      const { modal } = await import('/shell.js');
      const { server } = await import('/js/data/server.js');
      const { api } = await import('/js/components/plugin_api.js');
      const ctx = { modal, state: {}, toast() {}, update() {} };
      window.pages = []; window.copied = [];
      Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (s) => window.copied.push(s) } });
      if (pluginEnabled) (await import('/plugin/index.js')).activate(api(ctx, (await import('/js/vendor/purify.es.mjs')).default));
      const bytes = (s) => new TextEncoder().encode(s).length;
      Object.assign(server, {
        session: { mutations_enabled: true },
        handoffs: async () => ({ handoffs: [{ id: 'u1', iteration_label: '1', agent: 'codex', phase: 'done', status: 'done', steps: ['t001-x'], prompt_bytes: bytes(first + last), response_bytes: bytes(first + last) }] }),
        executionInfo: async () => ({ steps: [] }),
        handoffArtifact: async (_p, _id, unit, kind, offset) => {
          window.pages.push({ kind, offset });
          if (window.pageError) { window.pageError = false; throw new Error('Page unavailable'); }
          return { content: offset ? last : first, returned_bytes: bytes(offset ? last : first), total_bytes: bytes(first + last), truncated: !offset };
        },
        planTurns: async () => ({ turns: [] }),
      });
      await (await import('/js/components/agent_dialog.js')).openHandoffs(ctx, 'p', 'run-1');
    }, { first, last, pluginEnabled });
    await page.locator('.ho-turn summary').click();
    await page.locator('[data-artifact="response"] [data-notice]').filter({ hasText: 'incomplete response' }).waitFor();
    return page;
  };
  for (const theme of ['obsidian-orbit', 'daylight-orbit']) for (const width of [1280, 390]) {
    await t.test(`${theme} at ${width}px: render, source, paging, copy`, async () => {
      const page = await open(theme, width); t.after(() => page.close());
      const errors = []; page.on('pageerror', (e) => errors.push(e.message));
      const response = page.locator('[data-artifact="response"]');
      const rendered = response.locator('[data-rendered]');
      await rendered.locator('h1').waitFor();
      assert.equal(await rendered.locator('h1').innerText(), 'Résumé');
      assert.equal(await rendered.evaluate((el) => getComputedStyle(el).whiteSpace), 'normal');
      assert.equal(await rendered.locator('li').count(), 2);
      assert.equal(await rendered.locator('table').count(), 1);
      assert.equal(await rendered.locator('pre code').count(), 1);
      assert.equal(await rendered.locator('h2').count(), 0);
      assert.equal(await page.evaluate(() => window.pages.length), 2);
      await response.getByRole('button', { name: 'Source', exact: true }).click();
      assert.equal(await response.locator('pre.handoff-artifact').textContent(), first);
      assert.equal(await rendered.isVisible(), false);
      // A failed page preserves loaded source and remains retryable.
      await page.evaluate(() => { window.pageError = true; });
      await response.getByRole('button', { name: 'Load more' }).click();
      await response.locator('[data-notice]').filter({ hasText: 'Page unavailable' }).waitFor();
      assert.equal(await response.locator('pre.handoff-artifact').textContent(), first);
      await response.getByRole('button', { name: 'Load more' }).click();
      await response.getByRole('button', { name: 'Load more' }).waitFor({ state: 'hidden' });
      assert.equal(await response.locator('pre.handoff-artifact').textContent(), first + last);
      await response.getByRole('button', { name: 'Rendered', exact: true }).click();
      await rendered.locator('h2').waitFor();
      assert.equal(await rendered.locator('h2').innerText(), 'Complete');
      assert.equal(await rendered.locator('script, style, [onerror], [href^="javascript:"]').count(), 0);
      assert.equal(await page.evaluate(() => !!window.xss), false);
      assert.equal(await response.locator('[data-notice]').innerText(), '');
      await response.getByRole('button', { name: 'Copy', exact: true }).click();
      await page.waitForFunction(() => window.copied.length === 1);
      assert.equal(await page.evaluate(() => window.copied[0]), first + last);
      assert.equal(await page.evaluate(() => window.pages.filter((p) => p.kind === 'response' && p.offset > 0).every((p) => p.offset === new TextEncoder().encode(document.querySelector('[data-artifact="prompt"] pre').textContent).length)), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.equal(await rendered.evaluate((el) => el.getBoundingClientRect().right <= innerWidth), true);
      await page.screenshot({ path: `/tmp/handoff-markdown-${theme}-${width}.png` });
      assert.deepEqual(errors, []);
    });
  }
  await t.test('render failure keeps source and retry; stale preview cannot replace Source', async () => {
    const page = await open('obsidian-orbit', 1280); t.after(() => page.close());
    const response = page.locator('[data-artifact="response"]');
    await response.locator('[data-rendered] h1').waitFor();
    await page.evaluate(async () => {
      const viewer = (await import('/js/components/viewers.js')).viewerFor('handoff.md');
      window.originalRender = viewer.render;
      viewer.render = () => { throw new Error('Renderer failed'); };
    });
    await response.getByRole('button', { name: 'Source', exact: true }).click();
    await response.getByRole('button', { name: 'Rendered', exact: true }).click();
    await response.locator('[data-render-status]').filter({ hasText: 'preview unavailable' }).waitFor();
    assert.equal(await response.locator('pre.handoff-artifact').textContent(), first);
    await page.evaluate(async () => {
      const viewer = (await import('/js/components/viewers.js')).viewerFor('handoff.md');
      viewer.render = window.originalRender;
    });
    await response.getByRole('button', { name: 'Rendered', exact: true }).click();
    await response.locator('[data-render-status]').filter({ hasText: 'preview unavailable' }).waitFor({ state: 'hidden' });
    assert.equal(await response.locator('[data-rendered]').isVisible(), true);
    await page.evaluate(async () => {
      const viewer = (await import('/js/components/viewers.js')).viewerFor('handoff.md');
      viewer.render = async (file, host) => { await new Promise((resolve) => { window.finishRender = resolve; }); window.originalRender(file, host); };
    });
    await response.getByRole('button', { name: 'Source', exact: true }).click();
    await response.getByRole('button', { name: 'Rendered', exact: true }).click();
    await response.getByRole('button', { name: 'Source', exact: true }).click();
    await page.evaluate(() => window.finishRender());
    assert.equal(await response.locator('[data-rendered]').isVisible(), false);
    assert.equal(await response.locator('pre.handoff-artifact').isVisible(), true);
  });
  await t.test('without Markdown plugin: source and install hint, full copy before loading more', async () => {
    const page = await open('obsidian-orbit', 390, false); t.after(() => page.close());
    const response = page.locator('[data-artifact="response"]');
    assert.equal(await response.getByRole('button', { name: 'Rendered', exact: true }).isDisabled(), true);
    assert.equal(await response.getByRole('button', { name: 'Source', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.match(await response.locator('[data-render-status]').innerText(), /Install and enable/);
    assert.equal(await response.locator('pre.handoff-artifact').textContent(), first);
    await response.getByRole('button', { name: 'Copy', exact: true }).click();
    await page.waitForFunction(() => window.copied.length === 1);
    assert.equal(await page.evaluate(() => window.copied[0]), first + last);
    assert.equal(await response.locator('pre.handoff-artifact').textContent(), first);
  });
});
