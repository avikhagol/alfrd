// Real canvas + editor + Studio modal; manifest writes are captured, no commands execute.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));
const MINIMAL = "# unrelated comment\nname: review\ntemplate: avica\nentrypoint: []\nproject_settings:\n  unknown: keep\n";

test("Workflow editor: minimal AVICA, graph parity, blank creation and loop Save", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme") || "obsidian-orbit"}/theme.css"><body style="display:block;height:100vh"><main id="home" style="height:100%"></main><div id="modal-host" hidden></div></body>`);
    if (url.pathname === "/shell.js") return send(`import { $ } from "/js/utils/dom.js"; let clearModalKeys; export ${shell}`, "text/javascript");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web + sep)) return send("missing", "text/plain", 404);
    try { return send(await readFile(path), extname(path) === ".js" ? "text/javascript" : extname(path) === ".css" ? "text/css" : "text/plain"); }
    catch { return send("missing", "text/plain", 404); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const open = async (text, theme = "obsidian-orbit", width = 1440, extra = {}) => {
    const page = await browser.newPage({ viewport: { width, height: 1000 }, ...extra });
    page.setDefaultTimeout(5000);
    t.after(() => page.close());
    page.errors = [];
    page.on("pageerror", (e) => page.errors.push(e.message));
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await initialize(page, text);
    return page;
  };
  const initialize = async (page, text) => {
    await page.evaluate(async (text) => {
      const { modal } = await import("/shell.js");
      const canvas = await import("/js/components/canvas.js");
      const { parseYaml } = await import("/js/utils/yaml_parser.js");
      const { manifestToWorkflows } = await import("/js/data/model.js");
      const { loadTemplate, templateName } = await import("/js/data/defs.js");
      const name = templateName(parseYaml(text));
      if (name) await loadTemplate(name, parseYaml);
      const resolve = (text) => manifestToWorkflows(parseYaml(text), "alfrd.yaml", { aliases: false });
      const info = resolve(text);
      const el = document.querySelector("#home");
      window.saved = [];
      const state = { mode: "static", selectedProject: "review", targets: [], trees: { review: { manifestText: text, files: [] } }, workflow: info.workflows[0] || { name: "main", steps: [], stages: [] }, workflowFile: { name: "alfrd.yaml", errors: info.errors, warnings: info.warnings, validated: !info.errors.length } };
      window.ctx = { state, modal, target: () => null, projectName: () => "review", canWrite: () => true,
        toast() {}, setFooterRight() {}, log() {}, goTo() {}, update: () => canvas.render(el, window.ctx),
        saveManifest: async (_, text) => { window.saved.push(text); state.trees.review.manifestText = text; state.workflow = resolve(text).workflows[0]; },
      };
      canvas.mount(el, window.ctx); canvas.render(el, window.ctx);
    }, text);
  };
  const start = async (page) => { await page.locator('[data-wfe="start"]').click(); await page.locator(".wfe-bar").waitFor(); };
  const live = (page, text) => page.waitForFunction((text) => document.querySelector("#wfe-live").textContent === text, text);
  const focus = (page) => page.evaluate(() => ({ action: document.activeElement.dataset.wfe, id: document.activeElement.dataset.id, tag: document.activeElement.tagName }));

  for (const theme of ["obsidian-orbit", "daylight-orbit"]) await t.test(`minimal AVICA in ${theme}`, async () => {
    const page = await open(MINIMAL, theme);
    await start(page);
    assert.equal(await page.locator("[data-wfe-row]").count(), 9);
    assert.equal(await page.locator('[data-wfe="save"]').isDisabled(), true);
    await page.locator('[data-wfe="discard"]').click();
    assert.equal(await page.evaluate(() => ctx.state.trees.review.manifestText), MINIMAL);
    await start(page);
    const run = page.locator('[data-wfe-skip="phaseshift"]');
    assert.equal(await run.isChecked(), false);
    await run.check(); assert.equal(await run.isChecked(), true);
    await run.uncheck(); assert.equal(await run.isChecked(), false);
    await page.locator('[data-act="graph"]').click();
    assert.equal(await page.locator('.node.skipped[data-key="phaseshift"]').count(), 1);
    assert.equal(await page.locator(".wfe-node-tools").count(), 9);
    const contrast = await page.evaluate(() => {
      const node = document.querySelector('.node.skipped');
      const luminance = (color) => color.match(/[\d.]+/g).slice(0, 3).map(Number).map((v) => {
        v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
      }).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0);
      const fg = luminance(getComputedStyle(node.querySelector('.node-t span:last-child')).color);
      const bg = luminance(getComputedStyle(node).backgroundColor);
      return (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
    });
    assert.ok(contrast >= 4.5, `skipped label contrast ${contrast}`);
    t.diagnostic(`${theme}: skipped label contrast ${contrast.toFixed(2)}:1`);
    await page.screenshot({ path: `/tmp/workflow-editor-skipped-${theme}.png` });
    // Select through the keyboard; all actions remain at screen size under fit/zoom.
    await page.locator('.node[data-key="phaseshift"]').focus();
    await page.keyboard.press("Enter");
    const actions = page.getByRole("group", { name: "Step actions for phaseshift" });
    await page.waitForFunction(() => document.activeElement.dataset.wfe === "unskip");
    assert.equal((await focus(page)).action, "unskip");
    for (const box of await actions.locator("button").evaluateAll((bs) => bs.map((b) => ({ w: b.getBoundingClientRect().width, h: b.getBoundingClientRect().height })))) assert.ok(box.w >= 24 && box.h >= 24);
    await actions.getByRole("button", { name: "Run", exact: true }).click();
    await page.locator('.node.skipped[data-key="phaseshift"]').waitFor({ state: "detached" });
    assert.equal(await page.locator('.node.skipped[data-key="phaseshift"]').count(), 0);
    await actions.getByRole("button", { name: "Skip", exact: true }).click();
    await actions.getByRole("button", { name: "Run", exact: true }).click();
    await actions.getByRole("button", { name: "Edit", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Edit step phaseshift" });
    await dialog.locator('[name="label"]').fill("Phase shift reviewed");
    await dialog.getByRole("button", { name: "Apply", exact: true }).click();
    await page.waitForFunction(() => document.activeElement.dataset.wfe === "edit");
    assert.equal((await focus(page)).id, "phaseshift");
    await actions.getByRole("button", { name: "Move earlier" }).click();
    await actions.getByRole("button", { name: "Delete step" }).click();
    await page.getByRole("dialog", { name: "Delete step phaseshift?" }).getByRole("button", { name: "Delete step" }).click();
    await page.locator('.node[data-key="phaseshift"]').waitFor({ state: "detached" });
    assert.equal(await page.locator('.node[data-key="phaseshift"]').count(), 0);
    await page.locator('[data-wfe="undo"]').click();
    await page.locator('.node[data-key="phaseshift"]').waitFor();
    assert.equal(await page.locator('.node[data-key="phaseshift"]').count(), 1);
    await page.locator('[data-wfe="save"]').click();
    await page.waitForFunction(() => saved.length === 1);
    await page.waitForFunction(() => document.activeElement.dataset.wfe === "start");
    const [saved] = await page.evaluate(() => window.saved);
    assert.ok(saved.startsWith(MINIMAL));
    const resolved = await page.evaluate(async () => {
      const { parseYaml } = await import("/js/utils/yaml_parser.js");
      const { manifestToWorkflows } = await import("/js/data/model.js");
      const info = manifestToWorkflows(parseYaml(saved[0]), "alfrd.yaml", { aliases: false });
      return { errors: info.errors, steps: info.workflows[0].steps.map((s) => s.key) };
    });
    assert.deepEqual(resolved.errors, []); assert.equal(resolved.steps[1], "phaseshift", "graph reorder survives Save and Undo of deletion");
    await page.reload();
    await initialize(page, saved);
    await start(page);
    await page.locator('[data-act="list"]').click();
    assert.equal(await run.isChecked(), true);
    assert.equal(await page.locator('[data-wfe="save"]').isDisabled(), true);
    assert.deepEqual(page.errors, []);
    await page.screenshot({ path: `/tmp/workflow-editor-${theme}.png` });
  });

  await t.test("blank creation at 390px", async () => {
    const page = await open("# keep\nname: blank\nentrypoint: []\nsteps: {}\nworkflows: []\n", "daylight-orbit", 390);
    await page.locator('[data-wfe="first"]').click();
    const dialog = page.getByRole("dialog", { name: "Add a step" });
    await dialog.locator('[name="label"]').fill("Prepare data");
    await dialog.locator('[name="cmd"]').fill("python prep.py {target}");
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await dialog.getByRole("button", { name: "Add step", exact: true }).click();
    await page.waitForFunction(() => document.activeElement.dataset.id === "prepare-data");
    await page.locator('[data-wfe="save"]').click();
    await page.waitForFunction(() => saved.length === 1);
    assert.match((await page.evaluate(() => saved))[0], /^# keep/);
    assert.deepEqual(page.errors, []);
  });

  await t.test("agent-loop turn add, reorder and total turns", async () => {
    const page = await open('name: loop\nentrypoint:\n  - name: claude\n    cmd: [claude]\n  - name: codex\n    cmd: [codex]\nworkflows:\n  - name: loop\n    repeat:\n      iterations: 4\n      sequence: [claude, codex]\n');
    await start(page);
    await page.locator("#wfe-turn-agent").selectOption("claude");
    await page.locator('[data-wfe="turn-add"]').click();
    await page.locator('[data-wfe="turn-left"][data-i="2"]').click();
    await live(page, "claude moved to turn 2 of 3; Save to keep changes");
    await page.locator("#wfe-total").fill("12"); await page.locator("#wfe-total").press("Tab");
    assert.deepEqual(await page.locator(".wfe-chip b").allTextContents(), ["claude", "claude", "codex"]);
    await page.locator('[data-wfe="save"]').click();
    await page.waitForFunction(() => saved.length === 1);
    assert.match((await page.evaluate(() => saved))[0], /iterations: 12/);
    assert.deepEqual(page.errors, []);
  });

  await t.test("delete a chosen dependency without changing another workflow", async () => {
    const page = await open('name: deps\nsteps:\n  a:\n    cmd: [echo, a]\n  b:\n    cmd: [echo, b]\n    depends_on: [a]\nworkflows:\n  - name: first\n    steps: [a, b]\n  - name: other\n    steps: [a, b]\n');
    await start(page);
    await page.locator('[data-act="graph"]').click();
    await page.getByRole("group", { name: "Step actions for a" }).getByRole("button", { name: "Delete step" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete step a?" });
    await dialog.waitFor();
    assert.equal(await dialog.getByText("b: no step dependencies", { exact: true }).count(), 1);
    await dialog.getByRole("button", { name: "Delete step" }).click();
    await page.locator('.node[data-key="a"]').waitFor({ state: "detached" });
    await page.locator('[data-wfe="save"]').click();
    await page.waitForFunction(() => saved.length === 1);
    const result = await page.evaluate(async () => {
      const { parseYaml } = await import("/js/utils/yaml_parser.js");
      const { manifestToWorkflows } = await import("/js/data/model.js");
      const data = parseYaml(saved[0]);
      const info = manifestToWorkflows(data, "alfrd.yaml", { aliases: false });
      return { errors: info.errors, other: data.workflows[1].steps, originalDependency: data.steps.b.depends_on, depends: info.workflows[0].steps[0].depends };
    });
    assert.deepEqual(result, { errors: [], other: ["a", "b"], originalDependency: ["a"], depends: [] });
    assert.deepEqual(page.errors, []);
  });

  const BLANK = "# keep\nname: blank\nentrypoint: []\nsteps: {}\nworkflows: []\nnotify:\n  routes: []\n";

  await t.test("template cards create a draft: Discard keeps the file, Save keeps unrelated keys", async () => {
    const page = await open(BLANK);
    page.on("dialog", (dialog) => dialog.accept());
    await page.locator('[data-wfe="template"][data-template="avica"]').click();
    await page.locator(".wfe-bar .badge", { hasText: "Unsaved changes" }).waitFor();
    await live(page, "Draft created from the AVICA pipeline template; Save to keep it");
    assert.equal(await page.evaluate(() => saved.length), 0);
    await page.locator('[data-wfe="yaml"]').click();
    assert.match(await page.locator(".wfe-yaml").textContent(), /template: avica/);
    await page.keyboard.press("Escape");
    await page.locator('[data-wfe="discard"]').click();
    await page.locator(".wfe-start").waitFor();
    await live(page, "Changes discarded");
    assert.equal(await page.evaluate(() => ctx.state.trees.review.manifestText), BLANK);
    await page.locator('[data-wfe="template"][data-template="agent-loop"]').click();
    await page.locator(".wfe-chip b").first().waitFor();
    await page.locator('[data-wfe="save"]').click();
    await page.waitForFunction(() => saved.length === 1);
    const out = await page.evaluate(async () => {
      const { parseYaml } = await import("/js/utils/yaml_parser.js");
      const data = parseYaml(saved[0]);
      return { head: saved[0].split("\n")[0], template: data.template, notify: data.notify, name: data.name };
    });
    assert.deepEqual(out, { head: "# keep", template: "agent-loop", notify: { routes: [] }, name: "blank" });
    assert.deepEqual(page.errors, []);
  });

  await t.test("settings and stages apply to the draft, undo, and save only their sections", async () => {
    const text = "# top\nname: s\nentrypoint:\n  - name: run\n    cmd: [echo]\nstages:\n  - {id: s1, title: One, color: red}\n  - {id: s2, title: Two}\nsteps:\n  a:\n    stage: s1\n  b:\n    stage: s2\n    depends_on: [a]\nworkflows:\n  - name: main\n    steps: [a, b]\n";
    const page = await open(text);
    await start(page);
    assert.match(await page.locator(".wfe-bar b").first().textContent(), /Editing workflow · main/);
    await page.locator('[data-wfe="settings"]').click();
    let dialog = page.getByRole("dialog", { name: "Workflow settings · main" });
    await dialog.getByLabel(/Targets at once/).fill("99");
    await dialog.getByRole("button", { name: "Apply to draft" }).click();
    assert.equal(await dialog.locator("#wfs-concurrency").getAttribute("aria-invalid"), "true");
    assert.equal(await page.evaluate(() => document.activeElement.id), "wfs-concurrency");
    // Apply without changes marks nothing dirty.
    await dialog.getByLabel(/Targets at once/).fill("1");
    await dialog.getByRole("button", { name: "Apply to draft" }).click();
    assert.equal(await page.locator('[data-wfe="save"]').isDisabled(), true);
    await page.locator('[data-wfe="settings"]').click();
    dialog = page.getByRole("dialog", { name: "Workflow settings · main" });
    await dialog.getByLabel(/Default command/).first().selectOption("run");
    await dialog.getByLabel(/Targets at once/).fill("3");
    await dialog.getByLabel(/On failure/).selectOption("continue");
    await dialog.getByLabel("No default limit").check();
    await dialog.getByRole("button", { name: "Apply to draft" }).click();
    await live(page, "Workflow settings updated; Save to keep changes");
    await page.locator('[data-wfe="undo"]').click();
    await live(page, "Last change undone");
    assert.equal(await page.locator('[data-wfe="save"]').isDisabled(), true);
    await page.locator('[data-wfe="settings"]').click();
    dialog = page.getByRole("dialog", { name: "Workflow settings · main" });
    await dialog.getByLabel(/Targets at once/).fill("3");
    await dialog.getByLabel("No default limit").check();
    await dialog.getByRole("button", { name: "Apply to draft" }).click();
    await page.locator('[data-wfe="stages"]').click();
    dialog = page.getByRole("dialog", { name: "Stages" });
    await dialog.getByRole("button", { name: "Move later" }).first().click();
    assert.equal(await dialog.locator("[data-stage-live]").textContent(), "One moved to stage 2 of 2");
    assert.equal(await page.evaluate(() => document.activeElement.textContent), "Move earlier");
    await dialog.getByLabel("Stage 2 title").fill("Calibration");
    await dialog.getByRole("button", { name: "Apply to draft" }).click();
    await live(page, "Stages updated; Save to keep changes");
    await page.locator('[data-act="list"]').click();
    await page.locator('[data-wfe-skip="a"]').uncheck();
    await live(page, "a skipped; Save to keep changes");
    await page.locator('[data-wfe-skip="a"]').check();
    await live(page, "a will run; Save to keep changes");
    await page.getByRole("button", { name: "Move b earlier" }).click();
    await live(page, "b moved to position 1 of 2; Save to keep changes");
    await page.locator('[data-wfe="save"]').click();
    await page.waitForFunction(() => saved.length === 1);
    await live(page, "Saved alfrd.yaml");
    const data = await page.evaluate(async () => (await import("/js/utils/yaml_parser.js")).parseYaml(saved[0]));
    assert.deepEqual(data.execution, { concurrency: 3, timeout: null });
    assert.deepEqual(data.stages, [{ id: "s2", title: "Two" }, { id: "s1", title: "Calibration", color: "red" }]);
    assert.deepEqual(data.steps, { a: { stage: "s1" }, b: { stage: "s2", depends_on: ["a"] } });
    assert.deepEqual(data.entrypoint, [{ name: "run", cmd: ["echo"] }]);
    assert.ok((await page.evaluate(() => saved[0])).startsWith("# top\nname: s\nentrypoint:\n  - name: run\n    cmd: [echo]\n"));
    assert.deepEqual(page.errors, []);
  });

  await t.test("coarse-pointer targets at 390px", async () => {
    const page = await open('name: loop\nentrypoint:\n  - name: claude\n    cmd: [claude]\nworkflows:\n  - name: loop\n    repeat:\n      iterations: 4\n      sequence: [claude, claude]\n', "obsidian-orbit", 390, { hasTouch: true, isMobile: true });
    assert.ok(await page.evaluate(() => matchMedia("(pointer: coarse)").matches));
    await start(page);
    for (const box of await page.locator(".wfe-chip .icon-btn").evaluateAll((bs) => bs.map((b) => b.getBoundingClientRect()))) assert.ok(box.width >= 44 && box.height >= 44);
    await page.locator('[data-wfe="turn-right"][data-i="0"]').click();
    await live(page, "claude moved to turn 2 of 2; Save to keep changes");
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const list = await open("name: l\nsteps:\n  a:\n    cmd: [echo]\nworkflows:\n  - name: m\n    steps: [a]\n", "obsidian-orbit", 390, { hasTouch: true, isMobile: true });
    await start(list);
    await list.locator('[data-act="list"]').click();
    for (const box of await list.locator(".wfe-tools .icon-btn").evaluateAll((bs) => bs.map((b) => b.getBoundingClientRect()))) assert.ok(box.width >= 44 && box.height >= 44);
    assert.deepEqual([...page.errors, ...list.errors], []);
  });
});
