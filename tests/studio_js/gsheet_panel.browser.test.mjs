// Optional real-browser checks. All inputs are local files and fake models; no sheet API.
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const read = path => readFile(new URL(`../../${path}`, import.meta.url), "utf8");
const moduleURL = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;

test("Google Sheet panel: Chromium keyboard, sanitization, AA text and narrow layout", { skip: !chromium }, async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const projectUI = moduleURL(await read("examples/plugins/alfrd-gsheet/alfrd_gsheet/web/project_ui.js"));
    const source = moduleURL((await read("examples/plugins/alfrd-gsheet/alfrd_gsheet/web/index.js")).replace('"./project_ui.js"', `"${projectUI}"`));
    const purify = moduleURL(await read("src/alfrd/web/js/vendor/purify.es.mjs"));
    const baseCSS = await read("src/alfrd/web/css/studio.css");
    const pluginCSS = await read("examples/plugins/alfrd-gsheet/alfrd_gsheet/web/index.css");
    for (const theme of ["obsidian-orbit", "daylight-orbit"]) {
      const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
      await page.setContent('<!doctype html><html><head></head><body><main class="card" style="padding:24px; max-width:900px; margin:24px auto"></main></body></html>');
      await page.addStyleTag({ content: baseCSS + await read(`src/alfrd/web/css/themes/${theme}/theme.css`) + pluginCSS + "body{display:block;padding:16px;background:var(--surface);height:auto;} main{min-width:0;}" });
      await page.evaluate(async ({ source, purify }) => {
        const module = await import(source);
        const purifier = (await import(purify)).default;
        window.sheetCalls = 0;
        const api = { sanitize: html => purifier.sanitize(html), registerPanel: (_, render) => { window.draw = render; },
          registerCommand: c => { window.command = c; }, project: () => "fake", toast() {}, fetchJSON: async () => { window.sheetCalls++; } };
        module.activate(api);
        const rows = Array.from({ length: 12 }, (_, i) => ({ step: `calibrate-${i}`, at: new Date(Date.now() - i * 60000).toISOString(), cells_written: i,
          conflicts: i === 2 ? 1 : 0, result: ["ok", "no changes", "skipped (conflict)", "dry run", "error"][i % 5], error: "Sync failed. Validate mapping." }));
        const sid = "a".repeat(40);
        window.model = { state: "syncing", label: "Syncing", rows: rows.slice(0, 10), more_rows: rows.slice(10), total: 12,
          sheet: { id: sid, worksheet: "Targets", url: `https://docs.google.com/spreadsheets/d/${sid}/edit#gid=42` },
          note: "State reflects the mapping and last recorded sync; validate to check current settings." };
        document.querySelector("main").innerHTML = window.draw(window.model);
      }, { source, purify });
      assert.equal(await page.evaluate(() => window.sheetCalls), 0);
      assert.equal(await page.locator("tbody tr:visible").count(), 10);
      assert.equal(await page.locator('th[scope="col"]').count(), 5);
      const button = page.locator("button[data-gsheet-toggle]");
      await button.focus(); await page.keyboard.press("Enter");
      assert.equal(await button.getAttribute("aria-expanded"), "true");
      assert.equal(await page.locator("tbody tr:visible").count(), 12);
      assert.equal(await button.evaluate(el => el === document.activeElement), true);
      await page.keyboard.press("Space");
      assert.equal(await page.locator("tbody tr:visible").count(), 10);
      // Contrast for all rendered text, compositing transparent backgrounds up to the body.
      const contrastFailures = await page.evaluate(() => {
        const rgb = color => color.match(/[\d.]+/g).map(Number);
        const luminance = color => rgb(color).slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
          .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
        return [...document.querySelectorAll(".gsheet-summary *")].filter(el => el.checkVisibility() && [...el.childNodes].some(node => node.nodeType === 3 && node.textContent.trim()))
          .map(el => {
            let background = el;
            while (background.parentElement && rgb(getComputedStyle(background).backgroundColor)[3] === 0) background = background.parentElement;
            const fg = luminance(getComputedStyle(el).color), bg = luminance(getComputedStyle(background).backgroundColor);
            const ratio = (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
            return { text: el.textContent.slice(0, 30), ratio };
          }).filter(item => item.ratio < 4.5);
      });
      assert.deepEqual(contrastFailures, [], theme);
      await page.screenshot({ path: `/tmp/gsheet-panel-${theme}.png`, fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, theme);
      const scroll = page.locator(".gsheet-scroll");
      await scroll.focus(); await page.keyboard.press("ArrowRight");
      await page.waitForFunction(() => document.querySelector(".gsheet-scroll").scrollLeft > 0);
      assert.equal(await page.locator(".gsheet-scroll-hint").isVisible(), true);
      await scroll.evaluate(el => { el.scrollLeft = 0; });
      await page.screenshot({ path: `/tmp/gsheet-panel-${theme}-narrow.png`, fullPage: true });
      await page.evaluate(() => {
        document.querySelector("main").innerHTML = window.draw({ state: "invalid", label: "Mapping invalid", error: '<img src=x onerror="window.injected=true">',
          command: "alfrd gsheet init <project> --spreadsheet <id>" });
      });
      assert.equal(await page.locator(".gsheet-summary img").count(), 0);
      assert.match(await page.locator(".gsheet-error").innerText(), /<img/);
      assert.equal(await page.evaluate(() => window.injected || false), false);
      await page.close();
    }
  } finally { await browser.close(); }
});
