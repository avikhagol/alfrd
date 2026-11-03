import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || 'playwright')); } catch {}
const web = resolve(fileURLToPath(new URL('../../src/alfrd/web/', import.meta.url)));
test('request scope: shared menu keyboard and theme at desktop and phone widths', { skip: !chromium && 'Playwright unavailable' }, async t => {
  const app = await readFile(resolve(web, 'js/app.js'), 'utf8');
  const menu = app.slice(app.indexOf('function menu(anchor, items)'), app.indexOf('ctx.menu = menu;'));
  const chat = await readFile(resolve(web, 'js/components/chat_view.js'), 'utf8');
  const button = chat.match(/<div class="chat-scope-bar">.*?<\/div>/)[0];
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const send = (s, type) => { res.setHeader('Content-Type', type); res.end(s); };
    if (url.pathname === '/') return send(`<link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/chat.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get('theme')}/theme.css"><div class="chat" style="display:block">${button}</div><button id="outside">Outside</button>`, 'text/html');
    if (url.pathname === '/menu.js') return send(`import { $, $$, esc, icon } from '/js/utils/dom.js'; export ${menu}`, 'text/javascript');
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web + sep)) { res.statusCode = 404; return res.end(); }
    try { send(await readFile(path), extname(path) === '.js' ? 'text/javascript' : 'text/css'); }
    catch { res.statusCode = 404; res.end(); }
  });
  await new Promise(done => service.listen(0, '127.0.0.1', done));
  t.after(() => new Promise(done => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE || undefined });
  t.after(() => browser.close());
  for (const theme of ['obsidian-orbit', 'daylight-orbit']) for (const width of [1280, 390]) await t.test(`${theme} ${width}`, async () => {
    const page = await browser.newPage({ viewport: { width, height: 844 } });
    t.after(() => page.close());
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async () => {
      const { menu } = await import('/menu.js');
      const { createRequestScope } = await import('/js/components/inspector.js');
      const scope = createRequestScope(), state = { requests: [{ id: 'r0001', text: 'First' }, { id: 'r0002', text: 'Latest' }] };
      const button = document.querySelector('[data-chat="request-scope"]');
      const draw = () => { button.textContent = `Request scope · ${scope.label(state)} ▾`; window.request = scope.request(state); };
      button.onclick = () => menu(button, scope.items(state, draw)); draw();
    });
    const trigger = page.locator('[data-chat="request-scope"]'), menuEl = page.getByRole('menu');
    assert.match(await trigger.innerText(), /Latest request.*r0002/);
    await trigger.focus(); await page.keyboard.press('Enter');
    assert.equal(await trigger.getAttribute('aria-expanded'), 'true');
    await page.keyboard.press('End'); await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.request), 'r0001');
    assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Space'); await page.keyboard.press('Home'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.request), null);
    await trigger.click(); await page.keyboard.press('End'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => window.request), 'r0002');
    await trigger.click();
    const box = await menuEl.boundingBox(); assert.ok(box.x >= 11 && box.x + box.width <= width - 11);
    await page.keyboard.press('Escape'); assert.equal(await menuEl.count(), 0);
    assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
    await trigger.click(); await page.locator('#outside').click(); assert.equal(await menuEl.count(), 0);
    assert.equal(await trigger.evaluate(el => getComputedStyle(el).borderTopWidth), '0px');
  });
});
