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
  const menuShell = app.slice(app.indexOf("function menu(anchor"), app.indexOf("ctx.menu = menu;"));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") {
      const theme = url.searchParams.get("theme") || "obsidian-orbit";
      return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${theme}/theme.css"><link rel="stylesheet" href="/plugin/index.css"><body style="display:block;height:auto"><div id="home"></div><div id="modal-host" hidden></div></body>`);
    }
    if (url.pathname === "/shell.js") return send(`import { $, $$, esc, icon } from "/js/utils/dom.js"; let clearModalKeys; export ${shell} export ${menuShell}`, "text/javascript");
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
      const { modal, menu } = await import("/shell.js");
      window.calls = []; window.toasts = []; window.conflictOnce = false; window.createdHeaders = [];
      const mapping = { version: 1, enabled: true, spreadsheet_id: SID, worksheet: "Targets", header_row: 1, rows: { key_column: "TARGET_NAME" },
        outbound: [{ step: "calibrate", column: "calibrate", field: "status" }] };
      const evil = '<img src=x onerror="window.xss=1">';
      const state = () => ({ attached: window.isAttached, enabled: mapping.enabled, mapping: window.isAttached ? structuredClone(mapping) : null,
        mapping_sha256: window.isAttached ? "b".repeat(64) : "", intrinsic_errors: [], steps: ["calibrate", "image"],
        fields: [{ id: "status", label: "Status", takes_key: false }, { id: "usage.*", label: "Resource usage", takes_key: true, keys: ["peak_mem"] },
          { id: "template", label: "Template", takes_key: false }],
        formats: ["bytes", "raw"], statuses: ["done", "failed"], credentials: true, default_spreadsheet: false, dry_run: false,
        comments_notice: "Saving rewrites the file; comments are removed.", last_sync: {}, ...(window.stateOverrides || {}) });
      window.isAttached = attached; window.canWrite = true;
      const backend = {
        state: () => { if (window.stateFailure) throw new Error("Connection read failed"); return state(); },
        sheet_info: () => { if (window.metadataFailure) throw new Error("No access"); return { spreadsheet_id: SID, title: window.missingTitle ? "" : "Research targets", tabs: [{ title: "Other", gid: 0 }, { title: "Targets", gid: 5 }] }; },
        headers: (p) => ({ headers: [{ letter: "A", name: "TARGET_NAME" }, { letter: "B", name: evil }, { letter: "C", name: "calibrate" }, { letter: "D", name: "image" }, { letter: "E", name: "FILENAMES" }, ...window.createdHeaders.map((name, i) => ({ letter: ["AA", "AB"][i], name }))],
          sample_keys: ["M31", "M33", evil], matches: p.key_column === "FILENAMES" ? [{ value: "a.fits", status: window.ambiguousFiles ? "ambiguous" : "matched", target: window.ambiguousFiles ? "" : "M31" }, { value: "unknown.fits", status: "unmatched", target: "" }] : [{ value: "M31", status: "matched", target: "M31" }, { value: "M33", status: "matched", target: "M33" }, { value: evil, status: "unmatched", target: "" }], key_column: p.key_column || "TARGET_NAME", worksheet: p.worksheet || "Targets" }),
        validate: (p) => ({ errors: (p.mapping?.outbound || []).flatMap((r, i) => !r.column ? [{ path: `outbound[${i}].column`, message: "column is required" }] : r.column.includes("RAM") && !window.createdHeaders.length ? [{ path: `outbound[${i}].column`, message: "unknown sheet column" }] : []),
          missing_columns: (p.mapping?.outbound || []).some((r) => r.column.includes("RAM")) && !window.createdHeaders.length ? ["calibrate RAM", "image RAM"] : [], warnings: [] }),
        column_preview: () => ({ worksheet: "Targets", header_row: 1, columns: window.createdHeaders.length ? [] : [{ name: "calibrate RAM", letter: "AA" }, { name: "image RAM", letter: "AB" }], preview_sha256: "d".repeat(64), dry_run: !!window.columnDryRun }),
        create_columns: () => { if (window.columnFailOnce) { window.columnFailOnce = false; throw new Error("Sheet headers changed"); } window.createdHeaders = ["calibrate RAM", "image RAM"]; return { created: 2, dry_run: false }; },
        attach: () => { window.isAttached = true; return { mapping, mapping_sha256: "b".repeat(64), matched: [] }; },
        save: (p) => { if (!window.conflictOnce) { window.conflictOnce = true; throw new Error("The mapping changed on disk; reload."); } Object.assign(mapping, p.mapping); return { saved: true, mapping_sha256: "c".repeat(64), errors: [], warnings: [] }; },
        backfill: () => ({ dry_run: true, total_cells: 1, result: "dry run" }),
        preview: () => ({ cells: [{ a1: "C2", target: "M31", step: "calibrate", column: "calibrate", old: "", new: "done" }], total_cells: 1, truncated: false, conflicts: [], dry_run: true }),
      };
      const ctx = window.ctx = { modal, menu, projectName: () => "Research project", state: { source: "server", selectedProject: "p", targets: [{ project: "p", name: "M31" }, { project: "p", name: "M33" }] },
        log() {}, toast: (...a) => toasts.push(a), palette: { register: (fn) => { window.commands = fn; } } };
      const { api } = await import("/js/components/plugin_api.js");
      const host = api(ctx, { sanitize: (s) => s });
      host.action = async (project, pluginId, id, payload = {}) => { calls.push([id, payload]); await new Promise((r) => setTimeout(r, window.actionDelay || 5)); return backend[id](payload); };
      host.fetchJSON = async () => ({ plugin_actions: window.canWrite });
      (await import("/plugin/index.js")).activate(host);
      const sections = await import("/js/components/project_sections.js");
      window.sections = sections;
      sections.renderOverviewActions(document.querySelector("#home"), ctx);
      window.showConnection = async () => {
        document.querySelector('[data-plugin-action="gsheet"] button').click();
        [...document.querySelectorAll('.menu button')].find((b) => b.textContent.includes('Connect & validate')).click();
      };
      await window.showConnection();
    }, { attached, SID });
    return page;
  };

  await t.test("attach flow: gid preselects the tab, guessed key, plan matches, inert header text, opens editor", async () => {
    const page = await open(false); t.after(() => page.close());
    await page.getByRole("button", { name: "Connect sheet" }).click();
    await page.getByLabel("Sheet URL or ID").fill(`https://docs.google.com/spreadsheets/d/${SID}/edit#gid=5`);
    await page.getByRole("button", { name: "Load" }).click();
    await page.getByText("of 3 match plan targets").waitFor();
    assert.equal(await page.getByLabel("Tab", { exact: true }).inputValue(), "Targets");
    assert.equal(await page.getByLabel("Row-match column").inputValue(), "TARGET_NAME");
    assert.match(await page.locator(".gs-attach").innerText(), /2 of 3 match plan targets/);
    assert.equal(await page.locator('.gs-steps [aria-current="step"]').innerText(), "Columns");
    await page.getByRole("button", { name: "Attach", exact: true }).click();
    await page.getByRole("heading", { name: "Google Sheet mapping" }).waitFor();
    const attach = await page.evaluate(() => calls.find((c) => c[0] === "attach")[1]);
    assert.deepEqual(attach, { spreadsheet: `https://docs.google.com/spreadsheets/d/${SID}/edit#gid=5`, worksheet: "Targets", header_row: 1, key_column: "TARGET_NAME", match_against: "target" });
    assert.equal(await page.evaluate(() => !!window.xss || !!document.querySelector("img")), false);
    assert.deepEqual(await page.evaluate(() => window.commands().map((c) => c.label).sort()),
      ["Google Sheet: attach…", "Google Sheet: open mapping editor", "Google Sheet: validate mapping"]);
  });

  await t.test("filenames: explicit mode, backend samples, ambiguity gates attachment, mobile fits", async () => {
    const page = await open(false); t.after(() => page.close());
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Connect sheet" }).click();
    await page.getByLabel("Sheet URL or ID").fill(SID);
    await page.getByRole("button", { name: "Load" }).click();
    await page.getByText("2 of 3 match plan targets").waitFor();
    await page.getByLabel("Row-match column").selectOption("FILENAMES");
    await page.getByText("a.fits → M31").waitFor();
    assert.equal(await page.getByLabel("Match against").inputValue(), "files");
    await page.getByText("unknown.fits → No matching plan target").waitFor();
    assert.equal(await page.getByRole("button", { name: "Attach", exact: true }).isDisabled(), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= 390), true);
    await page.getByLabel("Match against").scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/gsheet-filenames-mobile.png" });
    await page.evaluate(() => { window.ambiguousFiles = true; });
    await page.getByLabel("Match against").selectOption("target");
    assert.equal(await page.getByRole("button", { name: "Attach", exact: true }).isDisabled(), true, "a pending resolver check gates Attach");
    await page.getByText("Matches multiple targets", { exact: false }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Attach", exact: true }).isDisabled(), true);
    await page.evaluate(() => { window.ambiguousFiles = false; });
    await page.getByLabel("Match against").selectOption("files");
    await page.getByText("a.fits → M31").waitFor();
    await page.getByRole("button", { name: "Attach", exact: true }).click();
    await page.getByRole("heading", { name: "Google Sheet mapping" }).waitFor();
    const payload = await page.evaluate(() => calls.find((c) => c[0] === "attach")[1]);
    assert.equal(payload.match_against, "files");
    assert.equal(payload.key_column, "FILENAMES");
  });

  await t.test("editor rows, errors by path, Save gating, reorder focus, conflict reload, unsaved guard", async () => {
    const page = await open(true); t.after(() => page.close());
    await page.evaluate(() => window.showConnection());
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

  await t.test("missing columns: review, failure/retry, draft preservation, separate Save, dry preview", async () => {
    const page = await open(true); t.after(() => page.close());
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => window.showConnection());
    await page.getByRole("button", { name: "Edit mapping" }).click();
    await page.getByLabel("Rule 1 step", { exact: true }).selectOption("*");
    await page.getByLabel("Rule 1 column", { exact: true }).selectOption("__custom__");
    await page.getByLabel("Rule 1 column letter or pattern").fill("{step} RAM");
    await page.getByText("2 destination columns are missing.").waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= 390), true);
    assert.equal(await page.locator(".modal-f .btn.primary").isDisabled(), true);
    await page.evaluate(() => { window.columnFailOnce = true; });
    await page.getByRole("button", { name: "Review column creation…" }).click();
    await page.getByText(/Targets, header row 1: AA · calibrate RAM, AB · image RAM/).waitFor();
    await page.screenshot({ path: "/tmp/gsheet-column-review.png" });
    assert.equal(await page.evaluate(() => calls.filter((c) => c[0] === "create_columns").length), 0);
    await page.getByRole("button", { name: "Create 2 columns", exact: true }).click();
    await page.getByText(/Your draft is kept. Review again/).waitFor();
    assert.equal(await page.getByLabel("Rule 1 column letter or pattern").inputValue(), "{step} RAM");
    await page.getByRole("button", { name: "Review column creation…" }).click();
    await page.getByRole("button", { name: "Create 2 columns", exact: true }).click();
    await page.getByText("Created 2 columns. Review your mapping, then Save.").waitFor();
    await page.waitForFunction(() => !document.querySelector(".modal-f .btn.primary").disabled);
    assert.equal(await page.getByLabel("Rule 1 column letter or pattern").inputValue(), "{step} RAM");
    assert.equal(await page.evaluate(() => calls.filter((c) => c[0] === "save").length), 0);
    assert.equal(await page.evaluate(() => calls.filter((c) => c[0] === "column_preview").length), 2);
    await page.keyboard.press("Escape");
    await page.getByText("Discard unsaved changes?").waitFor();
    await page.getByRole("button", { name: "Discard", exact: true }).click();

    await page.evaluate(() => window.showConnection());
    await page.getByRole("button", { name: "Edit mapping" }).click();
    await page.getByLabel("Rule 1 step", { exact: true }).selectOption("*");
    await page.getByLabel("Rule 1 column", { exact: true }).selectOption("__custom__");
    await page.getByLabel("Rule 1 column letter or pattern").fill("{step} RAM");
    await page.evaluate(() => { window.createdHeaders = []; window.columnDryRun = true; });
    await page.getByText("2 destination columns are missing.").waitFor();
    const before = await page.evaluate(() => calls.filter((c) => c[0] === "create_columns").length);
    await page.getByRole("button", { name: "Review column creation…" }).click();
    await page.getByText(/No columns will be written./).waitFor();
    assert.equal(await page.evaluate(() => calls.filter((c) => c[0] === "create_columns").length), before);
  });


  await t.test("toolbar menu: keyboard order, no card, stable refresh, scope and modal focus chain", async () => {
    const page = await open(true); t.after(() => page.close());
    await page.getByText("Research targets", { exact: true }).waitFor();
    assert.equal(await page.locator('[data-plugin-section="gsheet"]').count(), 0);
    await page.keyboard.press("Escape");
    const trigger = page.getByRole("button", { name: "Google Sheet", exact: true });
    assert.equal(await trigger.evaluate((b) => b === document.activeElement), true);
    await page.evaluate(() => { window.savedTrigger = document.activeElement; sections.renderOverviewActions(document.querySelector("#home"), ctx); });
    assert.equal(await trigger.evaluate((b) => b === window.savedTrigger && b === document.activeElement), true);
    await page.keyboard.press("ArrowDown");
    assert.deepEqual(await page.getByRole("menuitem").evaluateAll((items) => items.map((b) => b.querySelector("span").textContent)), ["Export to sheet…", "Sync", "Connect & validate…"]);
    assert.equal(await trigger.getAttribute("aria-expanded"), "true");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await page.getByRole("switch", { name: "Update this sheet during runs" }).waitFor();
    await page.waitForFunction(() => document.activeElement.getAttribute("role") === "switch");
    await page.keyboard.press("Escape");
    await page.evaluate(() => window.showConnection());
    await page.getByRole("button", { name: "Edit mapping…" }).click();
    await page.getByRole("heading", { name: "Google Sheet mapping" }).waitFor();
    await page.keyboard.press("Escape");
    assert.equal(await trigger.evaluate((b) => b === document.activeElement), true, "replacement dialog restores toolbar focus");
    await trigger.click();
    await page.evaluate(() => { ctx.state.selectedProject = "all"; sections.renderOverviewActions(document.querySelector("#home"), ctx); });
    assert.equal(await page.getByRole("menu").count(), 0);
    await trigger.click();
    await page.getByText("Select a project to use Google Sheet").waitFor();
    await page.evaluate(() => { ctx.state.source = "demo"; sections.renderOverviewActions(document.querySelector("#home"), ctx); });
    assert.equal(await trigger.isVisible(), false);
  });

  await t.test("connection failures, credentials, policy, title fallback, dry run, stale replies", async () => {
    const page = await open(true); t.after(() => page.close());
    await page.evaluate(() => { window.canWrite = false; window.missingTitle = true; window.stateOverrides = { enabled: false, dry_run: true, last_sync: { at: "2026-10-10T00:00:00Z", result: "error" } }; });
    await page.evaluate(() => window.showConnection());
    await page.getByText(`Sheet ID: ${SID}`).waitFor();
    await page.getByText("Dry run: no cells will be written").waitFor();
    assert.match(await page.locator(".gs-connection-dialog").innerText(), /Last sync failed/);
    assert.equal(await page.getByRole("switch").isDisabled(), true);
    await page.getByRole("button", { name: "Validate connection", exact: true }).click();
    await page.getByText("✓ Sheet access and mapping checked.").waitFor();
    await page.keyboard.press("Escape");
    await page.evaluate(() => { window.stateOverrides = { attached: false, mapping: null, credentials: false }; });
    await page.evaluate(() => window.showConnection());
    await page.getByRole("button", { name: "Set up credentials…" }).waitFor();
    await page.keyboard.press("Escape");
    await page.evaluate(() => { window.stateFailure = true; });
    await page.evaluate(() => window.showConnection());
    await page.getByRole("button", { name: "Reload connection" }).waitFor();
    await page.keyboard.press("Escape");
    await page.evaluate(() => { window.stateFailure = false; window.stateOverrides = {}; window.actionDelay = 100; window.showConnection(); ctx.state.selectedProject = "q"; sections.renderOverviewActions(document.querySelector("#home"), ctx); });
    await page.waitForTimeout(150);
    assert.equal(await page.getByRole("button", { name: "Validate connection", exact: true }).count(), 0, "old project replies cannot populate a new scope");
  });


  await t.test("actual Overview header: plugin order, refresh identity and narrow layout", async () => {
    const page = await open(true); t.after(() => page.close());
    await page.keyboard.press('Escape');
    await page.evaluate(async () => {
      document.querySelector('#home').hidden = true;
      const el = document.createElement('main'); el.id = 'overview-fixture'; document.body.prepend(el);
      Object.assign(ctx.state, { mode: 'demo', prefs: {}, notes: {}, targets: [], workflow: { name: 'Workflow', steps: [] }, trees: {} });
      Object.assign(ctx, { projects: () => [{ id: 'p', name: 'Research project' }], scopedTargets: () => [], steps: () => [], target: () => null, setFooterRight() {}, update() {} });
      window.overview = await import('/js/components/overview.js');
      overview.mount(el, ctx); overview.render(el, ctx);
    });
    const trigger = page.locator('#ov-plugin-actions button');
    await trigger.focus();
    await page.evaluate(() => { window.headerTrigger = document.activeElement; overview.render(document.querySelector('#overview-fixture'), ctx); });
    assert.equal(await trigger.evaluate(b => b === window.headerTrigger && b === document.activeElement), true);
    assert.deepEqual(await page.locator('#ov-title-actions button').allTextContents().then(items => items.map(x => x.trim().replace(/\s+/g, ' '))), ['Targets', 'Export', 'Google Sheet ▾']);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.locator('#ov-title-actions').evaluate(n => n.getBoundingClientRect().right <= 390));
    await page.screenshot({ path: '/tmp/gsheet-overview-header-390.png' });
    await trigger.click();
    await page.getByRole('menuitem', { name: 'Connect & validate…' }).click();
    await page.getByText('Research targets', { exact: true }).waitFor();
    await page.evaluate(() => overview.render(document.querySelector('#overview-fixture'), ctx));
    await page.keyboard.press('Escape');
    assert.equal(await trigger.evaluate(b => b === document.activeElement), true);
  });


  await t.test("sync saves preserve confirmation/revision; export reviews dry runs before action", async () => {
    const page = await open(true); t.after(() => page.close());
    const toggle = page.getByRole('switch', { name: 'Update this sheet during runs' });
    await toggle.uncheck();
    await page.getByText('comments are removed. Save anyway?').waitFor();
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.equal(await toggle.isChecked(), true);
    assert.equal(await page.evaluate(() => calls.filter(c => c[0] === 'save').length), 0);
    await page.evaluate(() => { window.conflictOnce = true; });
    await toggle.uncheck();
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByText('Automatic sync off', { exact: true }).waitFor();
    assert.equal(await toggle.isChecked(), false);
    const saved = await page.evaluate(() => calls.find(c => c[0] === 'save')[1]);
    assert.equal(saved.mapping.enabled, false);
    assert.equal(saved.base_sha256, 'b'.repeat(64));
    await page.keyboard.press('Escape');
    await page.evaluate(() => { window.stateOverrides = { dry_run: true }; window.showConnection(); });
    await page.getByText('Dry run: no cells will be written').waitFor();
    await page.keyboard.press('Escape');
    await page.evaluate(() => { ctx.state.selectedProject = 'q'; sections.renderOverviewActions(document.querySelector('#home'), ctx); ctx.state.selectedProject = 'p'; sections.renderOverviewActions(document.querySelector('#home'), ctx); });
    await page.waitForTimeout(25);
    await page.getByRole('button', { name: 'Google Sheet', exact: true }).click();
    await page.getByRole('menuitem', { name: /Export to sheet/ }).click();
    await page.getByText('1 cell would change in Targets').waitFor();
    await page.getByRole('button', { name: 'Review dry run…' }).click();
    await page.getByText('No cells will be written.', { exact: false }).waitFor();
    assert.equal(await page.evaluate(() => calls.filter(c => c[0] === 'backfill').length), 0);
    await page.getByRole('button', { name: 'Run dry run', exact: true }).click();
    await page.waitForFunction(() => calls.some(c => c[0] === 'backfill'));
  });

  for (const theme of ["obsidian-orbit", "daylight-orbit"]) {

    await t.test(`${theme}: connection dialog keyboard, mobile, contrast and Settings helpers`, async () => {
      const page = await open(true, theme); t.after(() => page.close());
      await page.getByText("Research targets", { exact: true }).waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      const info = await page.evaluate(() => {
        const color = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
        const lum = (c) => { const [r,g,b] = color(c); return .2126*r + .7152*g + .0722*b; };
        const ratio = (a,b) => (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
        const modal = document.querySelector('.modal'), helper = document.querySelector('.gs-facts dt');
        const bg = lum(getComputedStyle(modal).backgroundColor);
        const focus = getComputedStyle(modal.querySelector('[data-close]')).outlineColor;
        return { width: document.documentElement.scrollWidth, text: ratio(lum(getComputedStyle(helper).color), bg), focus: ratio(lum(focus), bg) };
      });
      assert.ok(info.width <= 390 && info.text >= 4.5 && info.focus >= 3, JSON.stringify(info));
      await page.locator('.modal-f .btn.primary').focus();
      await page.keyboard.press('Tab');
      assert.equal(await page.locator('.modal [data-close]').evaluate(b => b === document.activeElement), true);
      await page.keyboard.press('Shift+Tab');
      assert.equal(await page.locator('.modal-f .btn.primary').evaluate(b => b === document.activeElement), true);
      await page.screenshot({ path: `/tmp/gsheet-connection-${theme}-390.png` });
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Google Sheet', exact: true }).click();
      assert.ok(await page.getByRole('menu').evaluate(m => m.getBoundingClientRect().right <= 390));
      await page.keyboard.press('Escape');
      await page.evaluate(async () => {
        const { render } = await import('/js/components/plugin_config.js');
        document.querySelector('#home').innerHTML = render({ id: 'telegram', missing: [], services: [], can_check: true, settings: [
          { key: 'hold_minutes', label: 'Digest every (minutes)', kind: 'number', help: 'Empty or 0 sends notifications immediately. Keep the Bot service running for delivery on time.', placeholder: '0' },
          { key: 'mute_when_active', label: 'Mute while I use the Studio', kind: 'bool', help: 'Pending digests wait until you are inactive.' },
        ] }, true);
      });
      const input = page.getByLabel('Digest every (minutes)');
      assert.equal(await input.getAttribute('min'), '0');
      assert.equal(await input.getAttribute('step'), 'any');
      await input.fill('0.5');
      assert.equal(await input.evaluate(i => i.validity.stepMismatch), false);
      assert.equal(await input.getAttribute('aria-describedby'), 'plug-set-hold_minutes-help');
      assert.equal(await page.getByLabel('Mute while I use the Studio').getAttribute('aria-describedby'), 'plug-set-mute_when_active-help');
      await page.getByLabel('Mute while I use the Studio').check();
      assert.ok(await page.getByLabel('Mute while I use the Studio').evaluate(i => i.closest('label').getBoundingClientRect().height >= 44));
      await page.screenshot({ path: `/tmp/telegram-settings-${theme}-390.png` });
    });

    await t.test(`${theme}: editor and preview fit 1024 px, tokens give readable text`, async () => {
      const page = await open(true, theme); t.after(() => page.close());
      await page.evaluate(() => window.showConnection());
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
      await page.evaluate(() => window.showConnection());
      await page.getByRole("button", { name: "Preview changes…" }).click();
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
