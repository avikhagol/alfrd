import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const web = new URL('../../src/alfrd/web/', import.meta.url);
const read = (name) => readFile(new URL(name, web), 'utf8');
const [tokens, baseline, studio, daylight, obsidian] = await Promise.all([
  read('css/chat-tokens.css'), read('css/chat.css'), read('css/studio.css'),
  read('css/themes/daylight-orbit/theme.css'), read('css/themes/obsidian-orbit/theme.css'),
]);
const imports = ['tokens', 'thread', 'composer', 'activity', 'dock'].map((s) => `chat-${s}.css`);
const declarations = (css) => Object.fromEntries([...css.matchAll(/(--[\w-]+)\s*:\s*([^;{}]+);/g)].map((m) => [m[1], m[2].trim()]));
const base = declarations(tokens.match(/\.chat, \.chat-dock\s*\{([^}]+)\}/)[1]);
const required = [
  'bg', 'surface', 'surface-2', 'code-bg', 'hover', 'border', 'border-strong', 'text', 'text-2', 'muted',
  'live', 'live-bg', 'action', 'on-action', 'agent-orchestrator', 'agent-purple', 'agent-sky',
  ...['success', 'warning', 'error'].flatMap((s) => [s, `${s}-bg`, `${s}-border`]),
  'focus', 'sans', 'mono', ...['title', 'heading', 'body', 'control', 'meta', 'code'].flatMap((s) => [`${s}-size`, `${s}-leading`]),
  ...Array.from({ length: 6 }, (_, i) => `space-${i + 1}`),
  ...['sm', 'control', 'card', 'composer', 'pill'].map((s) => `radius-${s}`),
].map((s) => `--chat-${s}`);

test('every specified token is defined on both chat hosts, with shell fallbacks', () => {
  assert.deepEqual(Object.keys(base).sort(), required.sort());
  assert.doesNotMatch(tokens, /:root|@font-face|https?:|@layer/);
  for (const [name, value] of Object.entries(base)) {
    if (value.startsWith('var(')) assert.match(value, /,/, `${name} has a fallback`);
  }
  for (const [name, value] of Object.entries({
    'body-size': '14px', 'body-leading': '20px', 'meta-size': '12px', 'meta-leading': '16px',
    'code-size': '12.5px', 'code-leading': '18px', 'title-size': '20px', 'title-leading': '28px',
    'heading-size': '16px', 'heading-leading': '22px', 'control-size': '13px', 'control-leading': '18px',
  })) assert.equal(base[`--chat-${name}`], value);
  assert.deepEqual(Array.from({ length: 6 }, (_, i) => base[`--chat-space-${i + 1}`]), ['4px', '6px', '8px', '12px', '16px', '24px']);
  assert.deepEqual(['sm', 'control', 'card', 'composer', 'pill'].map((s) => base[`--chat-radius-${s}`]), ['4px', '6px', '8px', '12px', '9999px']);
  for (const [alias, shell] of Object.entries({ bg: 'bg', surface: 'surface', 'surface-2': 'surface-2', 'code-bg': 'console-bg', text: 'text', muted: 'text-3', action: 'accent-control', 'on-action': 'on-accent', focus: 'focus', success: 'ok-text', warning: 'warn-text', error: 'fail-text' })) {
    assert.ok(base[`--chat-${alias}`].startsWith(`var(--${shell},`), alias);
  }
  for (const theme of ['obsidian-orbit', 'daylight-orbit']) assert.ok(tokens.includes(`[data-theme="${theme}"] :is(.chat, .chat-dock)`));
});

test('chat lazy entry point begins with the five ordered quoted imports', () => {
  assert.ok(baseline.startsWith(imports.map((s) => `@import "${s}";`).join('\n') + '\n'));
  assert.equal([...baseline.matchAll(/@import\b/g)].length, 5);
  assert.doesNotMatch(baseline, /@import\s+url\(/);
  assert.doesNotMatch(studio, /chat-(?:tokens|thread|composer|activity|dock)\.css|chat\.css/);
  assert.match(baseline, /\.su-agent/);
  assert.match(baseline, /\.cs-table/);
  assert.match(baseline, /\.gv-canvas/);
});

let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || 'playwright')); } catch { /* optional browser dependency */ }

test('computed tokens, composited contrast, custom themes and forced colors on page and dock', { skip: !chromium && 'Playwright not available' }, async (t) => {
  const browser = await chromium.launch(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {});
  t.after(() => browser.close());
  const page = await browser.newPage();
  for (const theme of ['obsidian-orbit', 'daylight-orbit']) {
    await page.setContent(`<style>${studio}\n${theme === 'daylight-orbit' ? daylight : obsidian}\n${tokens}\n${baseline.replace(/@import[^;]+;/g, '')}</style><main class="chat"><p class="chat-text">Body</p><span class="small">Metadata</span><pre>Code</pre><button>Focus</button></main><aside class="chat-dock"><p class="chat-text">Body</p><span class="small">Metadata</span><pre>Code</pre><button>Focus</button></aside><div id="outside"></div>`);
    await page.evaluate((theme) => { document.documentElement.dataset.theme = theme; }, theme);
    for (const host of ['.chat', '.chat-dock']) {
      const report = await page.locator(host).evaluate((el, required) => {
        const styles = getComputedStyle(el);
        const values = Object.fromEntries(required.map((key) => [key, styles.getPropertyValue(key).trim()]));
        const probe = document.createElement('span'); el.append(probe);
        const color = (value) => { probe.style.color = value; return getComputedStyle(probe).color.match(/[\d.]+/g).map(Number); };
        const colors = Object.fromEntries(Object.entries(values).filter(([key]) => !/size|leading|space|radius|sans|mono/.test(key)).map(([key, value]) => [key, color(value)]));
        probe.remove();
        const type = (selector) => { const s = getComputedStyle(el.querySelector(selector)); return [s.fontSize, s.lineHeight, s.fontFamily]; };
        return { values, colors, body: type('.chat-text'), meta: type('.small'), code: type('pre') };
      }, required);
      assert.deepEqual(report.body.slice(0, 2), ['14px', '20px']);
      assert.deepEqual(report.meta.slice(0, 2), ['12px', '16px']);
      assert.deepEqual(report.code.slice(0, 2), ['12.5px', '18px']);
      assert.match(report.code[2], /JetBrains Mono/);
      const rgb = (name) => report.colors[`--chat-${name}`];
      const blend = (fg, bg) => fg.slice(0, 3).map((v, i) => v * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
      const lum = (c) => c.slice(0, 3).map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
      const ratio = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
      const check = (fg, bg, min) => assert.ok(ratio(fg, bg) >= min, `${theme} ${host}: ${ratio(fg, bg).toFixed(2)} < ${min}`);
      for (const surface of ['bg', 'surface', 'surface-2', 'code-bg', 'hover']) {
        for (const text of ['text', 'text-2', 'muted', 'agent-orchestrator', 'agent-purple', 'agent-sky', 'live']) check(rgb(text), rgb(surface), 4.5);
        check(rgb('focus'), rgb(surface), 3);
        for (const status of ['success', 'warning', 'error']) {
          check(rgb(status), rgb(surface), 4.5);
          // Shell status tints are tested on their card/canvas substrates.
          // In Daylight the success tint over nested gray falls below 4.5:1.
          if (['bg', 'surface'].includes(surface)) check(rgb(status), blend(rgb(`${status}-bg`), rgb(surface)), 4.5);
        }
        if (['bg', 'surface'].includes(surface)) for (const identity of ['agent-orchestrator', 'agent-purple', 'agent-sky']) check(rgb(identity), blend([...rgb(identity).slice(0, 3), .10], rgb(surface)), 4.5);
      }
      check(rgb('on-action'), rgb('action'), 4.5);
      check(rgb('action'), rgb('surface'), 3);
      check(rgb('live'), rgb('live-bg'), 4.5);
      assert.equal(await page.locator('#outside').evaluate((el) => getComputedStyle(el).getPropertyValue('--chat-bg')), '');
      t.diagnostic(`${theme} ${host} ${JSON.stringify({ body: report.body, meta: report.meta, code: report.code, shapes: Object.fromEntries(Object.entries(report.values).filter(([k]) => k.includes('radius'))), surfaces: ['bg', 'surface', 'surface-2', 'code-bg'].map((k) => [k, report.values[`--chat-${k}`]]) })}`);
    }
    await page.emulateMedia({ forcedColors: 'active' });
    assert.equal(await page.locator('.chat').evaluate((el) => getComputedStyle(el).getPropertyValue('--chat-live').trim()), 'CanvasText');
    await page.locator('.chat button').focus();
    assert.equal(await page.locator('.chat button').evaluate((el) => getComputedStyle(el).outlineStyle), 'solid');
    await page.emulateMedia({ forcedColors: 'none' });
  }
  await page.evaluate(() => {
    document.documentElement.dataset.theme = 'custom';
    document.documentElement.style.cssText = '--bg:#123456;--surface:#234567;--accent-text:#abcdef;--accent-control:#345678;--focus:#fedcba;--console-bg:#112233;--ok-text:#aabbcc';
  });
  for (const host of ['.chat', '.chat-dock']) {
    const values = await page.locator(host).evaluate((el) => ['bg', 'surface', 'live', 'agent-purple', 'agent-sky', 'action', 'focus', 'code-bg', 'success'].map((s) => getComputedStyle(el).getPropertyValue(`--chat-${s}`).trim()));
    assert.deepEqual(values, ['#123456', '#234567', '#abcdef', '#abcdef', '#abcdef', '#345678', '#fedcba', '#112233', '#aabbcc']);
  }
});
