// Files previews in Chromium with the real Studio modal shell and Markdown plugin.
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

test("Files preview: markdown layout, images and raw actions", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") {
      const theme = url.searchParams.get("theme") || "obsidian-orbit";
      return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/themes/${theme}/theme.css"><link rel="stylesheet" href="/plugin/index.css"><body style="display:block;height:auto"><button id="home">Files</button><div id="modal-host" hidden></div></body>`);
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



  for (const theme of ['obsidian-orbit', 'daylight-orbit']) for (const width of [1280, 390]) {
    await t.test(`${theme} at ${width}px`, async () => {
      const page = await browser.newPage({ viewport: { width, height: 844 } });
      t.after(() => page.close());
      await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
      const mountList = async () => page.evaluate(async () => {
        const host = document.createElement('div'); host.id = 'files';
        host.style.cssText = 'width:100%;max-width:420px'; document.body.append(host);
        const path = 'src/' + 'long-directory/'.repeat(8) + 'components/chat_view.js';
        window.filePath = '/project/' + path;
        window.filesController = (await import('/js/components/files_tab.js')).mountFiles(host, {
          getOverview: async () => ({ files: [{ path, absolute_path: window.filePath, producer_rows: ['task-12'], state: 'recorded', ref: 'recorded' }] }),
          isCurrent: () => true, copy: async value => { window.copied = value; return true; },
          raw: async () => new Blob(['preview']), view: async path => { window.previewed = path; },
        });
      });
      await mountList();
      await page.locator('.chat-files-list').waitFor();
      assert.equal(await page.getByRole('button', { name: 'List view', exact: true }).getAttribute('aria-pressed'), 'true');
      assert.match(await page.locator('.chat-file-dir').innerText(), /…/);
      for (const selector of ['.chat-file-name', '.chat-file-dir']) {
        assert.equal(await page.locator(selector).evaluate(el => getComputedStyle(el).whiteSpace), 'nowrap');
      }
      assert.equal(await page.locator('.chat-file-open').getAttribute('title'), await page.evaluate(() => window.filePath));
      assert.equal(await page.locator('.chat-file-task').innerText(), 'task-12');
      await page.getByRole('button', { name: 'Grid view', exact: true }).click();
      assert.equal(await page.locator('.chat-files-grid').evaluate(el => getComputedStyle(el).display), 'grid');
      assert.equal(await page.locator('.chat-file-format').innerText(), 'JS');
      assert.ok(await page.locator('.chat-file').evaluate(el => el.getBoundingClientRect().right <= innerWidth));
      const copy = page.getByRole('button', { name: 'Copy path', exact: true });
      assert.equal(await copy.innerText(), ''); await copy.click();
      assert.equal(await page.evaluate(() => window.copied), await page.evaluate(() => window.filePath));
      assert.match(await page.locator('[data-file-status]').innerText(), /Copied/);
      await page.locator('.chat-file-open').click();
      assert.equal(await page.evaluate(() => '/project/' + window.previewed), await page.evaluate(() => window.filePath));
      await page.reload(); await mountList();
      await page.locator('.chat-files-grid').waitFor();
      assert.equal(await page.getByRole('button', { name: 'Grid view', exact: true }).getAttribute('aria-pressed'), 'true');
      await page.getByRole('button', { name: 'List view', exact: true }).click();
      await page.evaluate(() => { window.filesController.destroy(); document.querySelector('#files').remove(); });
      const source = '# Heading\n\nParagraph\n\n> Quoted\n\n- first\n- second\n\n| Name | Result |\n| --- | --- |\n| A | B |\n\n```py\n' + 'x'.repeat(300) + '\n  print(1)\n```';
      await page.locator('#home').focus();
      await page.evaluate(async ({ source }) => {
        const { modal } = await import('/shell.js');
        const { api } = await import('/js/components/plugin_api.js');
        const ctx = { modal, state: {}, toast() {}, update() {} };
        (await import('/plugin/index.js')).activate(api(ctx, (await import('/js/vendor/purify.es.mjs')).default));
        window.openFile = async (path, data, type) => (await import('/js/components/files_tab.js')).openBlob(ctx, path, new Blob([data], { type }), () => true, '/raw?inline=1');
        await window.openFile('report.md', source, 'text/markdown');
      }, { source });
      await page.locator('.file-markdown h1').waitFor();
      assert.equal(await page.locator('.file-markdown li').count(), 2);
      assert.equal(await page.locator('.file-markdown blockquote').innerText(), 'Quoted');
      assert.equal(await page.locator('.file-table-wrap table').count(), 1);
      const metrics = await page.locator('.file-markdown pre').evaluate(el => ({
        whiteSpace: getComputedStyle(el).whiteSpace, overflow: getComputedStyle(el).overflowX,
        scroll: el.scrollWidth, width: el.clientWidth, right: el.getBoundingClientRect().right,
        font: getComputedStyle(el.querySelector('code')).fontFamily, text: el.textContent,
        hostDisplay: getComputedStyle(el.closest('.viewer')).display,
      }));
      assert.equal(metrics.whiteSpace, 'pre'); assert.equal(metrics.overflow, 'auto');
      assert.equal(metrics.hostDisplay, 'block'); assert.ok(metrics.scroll > metrics.width);
      assert.ok(metrics.right <= width); assert.match(metrics.font, /mono/i);
      assert.ok(metrics.text.includes('\n  print(1)\n'));
      assert.equal(await page.getByRole('link', { name: 'Open in new tab' }).getAttribute('target'), '_blank');
      const close = page.getByRole('button', { name: 'Close file', exact: true });
      assert.equal(await close.getAttribute('class'), 'icon-btn');
      assert.equal(await close.locator('svg').count(), 1);
      await close.click();
      assert.equal(await page.getByRole('dialog').count(), 0);
      assert.equal(await page.locator('#home').evaluate(el => el === document.activeElement), true);
      await page.evaluate(() => window.openFile('report.md', '# Reopened', 'text/markdown'));
      await page.keyboard.press('Escape');
      assert.equal(await page.getByRole('dialog').count(), 0);
      assert.equal(await page.locator('#home').evaluate(el => el === document.activeElement), true);
      for (const [path, data, type] of [
        ['icon.svg', '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/><script>window.xss=1</script></svg>', 'image/svg+xml'],
        ['pixel.png', null, 'image/png'],
      ]) {
        await page.evaluate(async ({ path, data, type }) => {
          const bytes = data ?? Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII='), c => c.charCodeAt(0));
          await window.openFile(path, bytes, type);
        }, { path, data, type });
        await page.waitForFunction(() => document.querySelector('.viewer-img')?.naturalWidth > 0);
        assert.equal(await page.locator('.viewer pre').count(), 0);
        assert.equal(await page.evaluate(() => !!window.xss), false);
        assert.equal(await page.getByRole('link', { name: 'Open in new tab' }).getAttribute('href'), '/raw?inline=1');
      }
    });
  }
});
