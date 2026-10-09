// Google Sheet card + dialogs in Chromium with the real Studio modal shell and a fake action backend.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));
const plugin = resolve(fileURLToPath(new URL("../../examples/plugins/alfrd-gsheet/alfrd_gsheet/web/", import.meta.url)));
const SID = "a".repeat(40);

test("Google Sheet project UI: attach, edit, validate, conflict, guard, themes", { skip: !chromium && "Playwright not available" }, async (t) => {
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

  const open = async (attached, theme) => {
    const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme || "obsidian-orbit"}`);
    await page.evaluate(async ({ attached, SID }) => {
      const { modal } = await import("/shell.js");
      window.calls = []; window.toasts = []; window.conflictOnce = false;
      const mapping = { version: 1, enabled: true, spreadsheet_id: SID, worksheet: "Targets", header_row: 1, rows: { key_column: "TARGET_NAME" },
        outbound: [{ step: "calibrate", column: "calibrate", field: "status" }] };
      const evil = '<img src=x onerror="window.xss=1">';
      const state = () => ({ attached: window.isAttached, enabled: true, mapping: window.isAttached ? structuredClone(mapping) : null,
        mapping_sha256: window.isAttached ? "b".repeat(64) : "", intrinsic_errors: [], steps: ["calibrate", "image"],
        fields: [{ id: "status", label: "Status", takes_key: false }, { id: "usage.*", label: "Resource usage", takes_key: true, keys: ["peak_mem"] },
          { id: "template", label: "Template", takes_key: false }],
        formats: ["bytes", "raw"], statuses: ["done", "failed"], credentials: true, default_spreadsheet: false, dry_run: false,
        comments_notice: "Saving rewrites the file; comments are removed.", last_sync: {} });
      window.isAttached = attached;
      const backend = {
        state, sheet_info: () => ({ spreadsheet_id: SID, tabs: [{ title: "Other", gid: 0 }, { title: "Targets", gid: 5 }] }),
        headers: (p) => ({ headers: [{ letter: "A", name: "TARGET_NAME" }, { letter: "B", name: evil }, { letter: "C", name: "calibrate" }, { letter: "D", name: "image" }],
          sample_keys: ["M31", "M33", evil], key_column: p.key_column || "TARGET_NAME", worksheet: p.worksheet || "Targets" }),
        validate: (p) => ({ errors: (p.mapping?.outbound || []).flatMap((r, i) => r.column ? [] : [{ path: `outbound[${i}].column`, message: "column is required" }]), warnings: [] }),
        attach: () => { window.isAttached = true; return { mapping, mapping_sha256: "b".repeat(64), matched: [] }; },
        save: () => { if (!window.conflictOnce) { window.conflictOnce = true; throw new Error("The mapping changed on disk; reload."); } return { saved: true, mapping_sha256: "c".repeat(64), errors: [], warnings: [] }; },
        preview: () => ({ cells: [{ a1: "C2", target: "M31", step: "calibrate", column: "calibrate", old: "", new: "done" }], total_cells: 1, truncated: false, conflicts: [], dry_run: true }),
      };
      const ctx = { modal, state: { selectedProject: "p", targets: [{ project: "p", name: "M31" }, { project: "p", name: "M33" }] },
        log() {}, toast: (...a) => toasts.push(a), palette: { register: (fn) => { window.commands = fn; } } };
      const { api } = await import("/js/components/plugin_api.js");
      const host = api(ctx, { sanitize: (s) => s });
      host.action = async (project, pluginId, id, payload = {}) => { calls.push([id, payload]); await new Promise((r) => setTimeout(r, 5)); return backend[id](payload); };
      host.fetchJSON = async () => ({ plugin_actions: true });
      (await import("/plugin/index.js")).activate(host);
      const sections = await import("/js/components/project_sections.js");
      sections.renderProjectSections(document.querySelector("#home"), "p", ctx);
    }, { attached, SID });
    return page;
  };

  await t.test("attach flow: gid preselects the tab, guessed key, plan matches, inert header text, opens editor", async () => {
    const page = await open(false); t.after(() => page.close());
    await page.getByRole("button", { name: "Attach Google Sheet" }).click();
    await page.getByLabel("Sheet URL or ID").fill(`https://docs.google.com/spreadsheets/d/${SID}/edit#gid=5`);
    await page.getByRole("button", { name: "Load" }).click();
    await page.getByText("of 3 match plan targets").waitFor();
    assert.equal(await page.getByLabel("Tab", { exact: true }).inputValue(), "Targets");
    assert.equal(await page.getByLabel("Target-name column").inputValue(), "TARGET_NAME");
    assert.match(await page.locator(".gs-attach").innerText(), /✓ 2 of 3 match plan targets/);
    assert.equal(await page.locator('.gs-steps [aria-current="step"]').innerText(), "Columns");
    await page.getByRole("button", { name: "Attach", exact: true }).click();
    await page.getByRole("heading", { name: "Google Sheet mapping" }).waitFor();
    const attach = await page.evaluate(() => calls.find((c) => c[0] === "attach")[1]);
    assert.deepEqual(attach, { spreadsheet: `https://docs.google.com/spreadsheets/d/${SID}/edit#gid=5`, worksheet: "Targets", header_row: 1, key_column: "TARGET_NAME" });
    assert.equal(await page.evaluate(() => !!window.xss || !!document.querySelector("img")), false);
    assert.deepEqual(await page.evaluate(() => window.commands().map((c) => c.label).sort()),
      ["Google Sheet: attach…", "Google Sheet: open mapping editor", "Google Sheet: validate mapping"]);
  });

  await t.test("editor rows, errors by path, Save gating, reorder focus, conflict reload, unsaved guard", async () => {
    const page = await open(true); t.after(() => page.close());
    await page.getByRole("button", { name: "Edit mapping" }).click();
    await page.getByRole("heading", { name: "Google Sheet mapping" }).waitFor();
    const save = page.locator(".modal-f .btn.primary");
    await page.getByRole("button", { name: "Add rule" }).click();
    assert.equal(await page.locator(".gs-rules tbody tr").count(), 2);
    await page.locator('.gs-err').first().waitFor();
    assert.equal(await page.getByLabel("Rule 2 column", { exact: true }).getAttribute("aria-invalid"), "true");
    assert.match(await page.locator(".gs-summary").innerText(), /1 problem/);
    assert.equal(await save.isDisabled(), true);
    assert.match(await save.getAttribute("title"), /Fix 1 problem/);
    await page.getByRole("button", { name: "Move rule 2 up" }).click();
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "Move rule 1 down");
    await page.getByLabel("Rule 1 column", { exact: true }).selectOption("image");
    await page.waitForFunction(() => !document.querySelector(".gs-err"));
    assert.equal(await save.isDisabled(), false);
    await page.getByRole("button", { name: "Delete rule 2" }).click();
    assert.equal(await page.locator(".gs-rules tbody tr").count(), 1);
    await page.getByRole("button", { name: "Add status columns for all steps" }).click();
    assert.equal(await page.locator(".gs-rules tbody tr").count(), 2);
    // Unsaved guard
    await page.keyboard.press("Escape");
    await page.getByText("Discard unsaved changes?").waitFor();
    await page.getByRole("button", { name: "Cancel" }).click();
    assert.equal(await page.locator(".modal").count(), 1);
    // Comment notice, then the SHA conflict → reload discards the draft
    await save.click();
    await page.getByText("comments are removed. Save anyway?").waitFor();
    await page.locator(".plug-confirm button").last().click();
    await page.getByText("The file changed on disk. Reload it and discard your edits?").waitFor();
    await page.getByRole("button", { name: "Reload" }).click();
    await page.waitForFunction(() => document.querySelectorAll(".gs-rules tbody tr").length === 1);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator(".modal").count(), 0, "a reloaded draft is clean");
  });

  for (const theme of ["obsidian-orbit", "daylight-orbit"]) {
    await t.test(`${theme}: editor and preview fit 1024 px, tokens give readable text`, async () => {
      const page = await open(true, theme); t.after(() => page.close());
      await page.getByRole("button", { name: "Edit mapping" }).click();
      await page.getByRole("heading", { name: "Google Sheet mapping" }).waitFor();
      const fit = await page.evaluate(() => {
        const modal = document.querySelector(".modal").getBoundingClientRect();
        const save = document.querySelector(".modal-f .btn.primary").getBoundingClientRect();
        const region = document.querySelector(".gs-scroll");
        return { modalRight: modal.right, saveRight: save.right, page: document.documentElement.scrollWidth, region: region.getAttribute("role") };
      });
      assert.ok(fit.modalRight <= 1024 && fit.saveRight <= 1024 && fit.page <= 1024, JSON.stringify(fit));
      assert.equal(fit.region, "region");
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Preview" }).click();
      await page.getByText("1 cell would change in Targets").waitFor();
      const contrast = await page.evaluate(() => {
        const rgb = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
        const lum = (c) => { const [r, g, b] = rgb(c); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
        const cell = document.querySelector(".gs-cells tbody td");
        let node = cell, bg = "rgba(0, 0, 0, 0)";
        while (node && /rgba\(0, 0, 0, 0\)|transparent/.test(bg)) { bg = getComputedStyle(node).backgroundColor; node = node.parentElement; }
        const [a, b] = [lum(getComputedStyle(cell).color), lum(bg)].sort((x, y) => y - x);
        return (a + 0.05) / (b + 0.05);
      });
      assert.ok(contrast >= 4.5, `contrast ${contrast}`);
      await page.screenshot({ path: `/tmp/gsheet-preview-${theme}.png` });
    });
  }
});
