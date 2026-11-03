// Exercise the real inspector tab rendering/navigation functions and delegated handlers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || 'playwright')); } catch {}
const web = new URL('../../src/alfrd/web/', import.meta.url);
test('Files X/Escape restore the prior tab and selection stays purple in both themes', { skip: !chromium && 'Playwright unavailable' }, async t => {
  const source = await readFile(new URL('js/components/chat_view.js', web), 'utf8');
  const functions = source.slice(source.indexOf('function onFilesKeydown(e)'), source.indexOf('let filesModule ='));
  const keys = source.slice(source.lastIndexOf('  el.addEventListener("keydown",'), source.indexOf('\nasync function delegateDecision')).replace(/\n}\s*$/, '');
  const clicks = source.match(/else if \(a === "close-files"\)[\s\S]*?(?=    else if \(b.dataset.mailJump\))/)[0];
  const browser = await chromium.launch({ executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE || undefined });
  t.after(() => browser.close());
  for (const theme of ['obsidian-orbit', 'daylight-orbit']) for (const width of [1440, 390]) await t.test(`${theme} ${width}`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 844 } });
    t.after(() => page.close());
    await page.route('http://alfrd.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html data-theme="${theme}"><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/themes/${theme}/theme.css"><link rel="stylesheet" href="/css/chat.css"><style>.chat .chat-sessions { display:flex !important; position:static !important; width:100%; }</style><main class="chat" style="display:block"><aside class="chat-sessions"><div class="chat-session-item"><a class="chat-session on" href="#"><b>Selected chat</b></a></div><a class="chat-session" href="#">Other chat</a></aside><aside id="chat-inspector"><div id="chat-insp-tabs"></div></aside></main>` });
      if (path === '/harness.js') return route.fulfill({ contentType: 'text/javascript', body: `import { chatSheet } from '/js/data/chat_sessions.js'; import { $ } from '/js/utils/dom.js'; import { tabsHtml, createInspectorTabs } from '/js/components/inspector.js'; const inspectorTabs = createInspectorTabs(), host = document, el = document.querySelector('.chat'), ctxRef = {}, insp = { onKeydown() { return false; } }; function draw() {} ${functions} document.querySelector("#chat-inspector").addEventListener("keydown", onFilesKeydown); window.sheetClosed = false; window.releaseSheet = chatSheet(document.querySelector("#chat-inspector"), () => { window.sheetClosed = true; }, { focus: false }); ${keys} el.addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; const a = b.dataset.chat; if (false) {} ${clicks} }); renderInspectorTab();` });
      try { await route.fulfill({ contentType: path.endsWith('.css') ? 'text/css' : 'text/javascript', body: await readFile(new URL(path.slice(1), web)) }); }
      catch { await route.fulfill({ status: 404, body: '' }); }
    });
    await page.goto('http://alfrd.test/');
    await page.evaluate(() => import('/harness.js'));
    for (const previous of ['Graph', 'Goals']) for (const how of ['click', 'Escape']) {
      await page.getByRole('tab', { name: previous, exact: true }).click();
      await page.getByRole('tab', { name: 'Files', exact: true }).click();
      const x = page.getByRole('button', { name: 'Close Files', exact: true });
      assert.equal(await x.locator('svg').count(), 1);
      if (how === 'click') await x.click(); else await page.keyboard.press('Escape');
      const restored = page.getByRole('tab', { name: previous, exact: true });
      assert.equal(await restored.getAttribute('aria-selected'), 'true');
      assert.equal(await restored.evaluate(el => el === document.activeElement), true);
      assert.equal(await x.count(), 0);
      assert.equal(await page.evaluate(() => window.sheetClosed), false);
    }
    await page.evaluate(() => window.releaseSheet());
    const selected = page.locator('.chat-session.on'), other = page.locator('.chat-session:not(.on)');
    const color = await selected.evaluate(el => {
      const s = getComputedStyle(el), probe = document.createElement('i'); el.append(probe);
      probe.style.color = 'var(--accent)'; const accent = getComputedStyle(probe).color;
      probe.style.color = 'var(--accent-bg)'; const tint = getComputedStyle(probe).color;
      probe.style.color = "var(--chat-bg)"; const base = getComputedStyle(probe).color;
      probe.remove();
      return { shadow: s.boxShadow, bg: s.backgroundColor, text: getComputedStyle(el.querySelector('b')).color, base, accent, tint };
    });
    assert.ok(color.shadow.includes(color.accent), color.shadow);
    assert.equal(color.bg, color.tint);
    await other.hover();
    assert.notEqual(await other.evaluate(el => getComputedStyle(el).backgroundColor), color.bg);
    await selected.hover();
    assert.equal(await selected.evaluate(el => getComputedStyle(el).backgroundColor), color.bg);
    const rgb = c => c.match(/[\d.]+/g).map(Number), fg = rgb(color.bg), base = rgb(color.base);
    const bg = fg.slice(0, 3).map((v, i) => v * (fg[3] ?? 1) + base[i] * (1 - (fg[3] ?? 1)));
    const lum = c => c.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s,v,i) => s + v * [.2126,.7152,.0722][i], 0);
    const textLum = lum(rgb(color.text)), bgLum = lum(bg);
    const ratio = (Math.max(textLum, bgLum) + .05) / (Math.min(textLum, bgLum) + .05);
    assert.ok(ratio >= 4.5, `selected text contrast ${ratio}`);
    t.diagnostic(`${theme} ${width}: selected text contrast ${ratio.toFixed(2)}:1; ${color.shadow}; ${color.bg}`);
  });
});
