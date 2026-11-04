// New project wizard in Chromium with the real Studio modal shell (server calls stubbed; nothing is created),
// then Settings shows the Entrypoints card for the created manifest.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));

const OPTIONS = [
  { id: "claude", label: "Claude Code", help: "Runs the claude CLI headless; edits files in this project.", group: "common", available: true, status: "found at /usr/bin/claude", default: true, shown: true, hidden_reason: null, loop: true, cmd: ["claude", "-p"] },
  { id: "codex", label: "Codex", help: "Runs codex in a workspace-write sandbox.", group: "common", available: false, status: "not installed — you can still add it", default: true, shown: true, hidden_reason: null, loop: true, cmd: ["codex", "exec", "-"] },
  { id: "agy", label: "Antigravity (agy)", help: "Runs agy.", group: "common", available: false, status: "agy is not on PATH", default: false, shown: false, hidden_reason: "agy is not on PATH", loop: true, cmd: ["agy"] },
  { id: "custom", label: "Custom command", help: "Your own program or script.", group: "custom", available: true, status: "", default: false, shown: true, hidden_reason: null, loop: true, cmd: null },
];

test("New project wizard: steps, checkboxes, turn order, review, create; Settings Entrypoints card", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/obsidian-orbit/theme.css"><body style="display:block;height:auto"><div id="home"></div><div id="modal-host" hidden></div></body>`);
    if (url.pathname === "/shell.js") return send(`import { $ } from "/js/utils/dom.js"; let clearModalKeys; export ${shell}`, "text/javascript");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web + sep)) return send("missing", "text/plain", 404);
    try { return send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : extname(path) === ".css" ? "text/css" : "text/plain"); }
    catch { return send("missing", "text/plain", 404); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(5000);
  const errors = []; page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${service.address().port}/`);
  await page.evaluate(async (options) => {
    const { modal } = await import("/shell.js");
    const { server } = await import("/js/data/server.js");
    window.created = []; window.toasts = [];
    Object.assign(server, {
      canBrowse: () => false,
      projectTemplates: async () => ({ templates: [{ name: "basic", description: "A new configurable ALFRD project." }, { name: "agent-loop", description: "Claude and Codex." }] }),
      entrypointOptions: async (template) => ({ options: options.map((o) => ({ ...o, default: template === "agent-loop" && o.default })), experimental: false, experimental_templates: [] }),
      entrypointCheck: async (_path, custom) => (custom[0].cmd.startsWith("/mnt/") ? Promise.reject(new Error(`custom command ${custom[0].name}: ${custom[0].cmd} points into another project's .alfrd/bin. Copy the script into this project and use a relative path such as .alfrd/bin/x.py.`))
        : { entries: [{ name: custom[0].name, cmd: ["{project_dir}/" + custom[0].cmd.replace(/^\.\//, "")] }], warnings: [] }),
      createProject: async (payload) => { window.created.push(payload); return { name: "wizard-2", identifier: "wizard-2", warnings: ["custom command mine: a warning"] }; },
    });
    const ctx = { modal, toast: (text, tone, opts) => window.toasts.push({ text, tone, link: opts?.link }), browseFolder() {} };
    await (await import("/js/components/agent_dialog.js")).openCreateProject(ctx, async () => {}, "");
  }, OPTIONS);
  const dialog = page.locator(".modal");
  const next = dialog.locator("#cw-next");
  const current = () => dialog.locator('.cw-steps [aria-current="step"]').innerText();

  // 1 Folder: an empty folder is refused with the field and the fix
  assert.deepEqual(await dialog.locator(".cw-steps li").allInnerTexts(), ["Folder", "Agent commands", "Review & create"], "Basic has no Agents step");
  await next.click();
  assert.match(await dialog.locator("#cw-problems").innerText(), /Folder: is empty\. Enter or browse/);
  await dialog.locator('[name="path"]').fill("/tmp/wizard-2");
  await dialog.locator('[name="template"]').selectOption("agent-loop");
  assert.deepEqual(await dialog.locator(".cw-steps li").allInnerTexts(), ["Folder", "Agent commands", "Agents", "Review & create"]);
  await dialog.locator('[name="path"]').press("Enter"); // Enter moves on
  assert.equal(await current(), "Agent commands");
  assert.equal(await page.evaluate(() => document.activeElement.tagName), "H3", "focus moves to the step heading");

  // 2 Agent commands: real checkboxes with help text and status; hidden ones explained
  const claude = dialog.getByRole("checkbox", { name: /Claude Code/ });
  assert.equal(await claude.isChecked(), true);
  assert.equal(await dialog.getByRole("checkbox", { name: /Codex/ }).isChecked(), true);
  assert.match(await dialog.locator("#cw-options").innerText(), /not installed — you can still add it/);
  assert.match(await dialog.locator(".cw-why summary").innerText(), /Why isn't Antigravity \(agy\) listed\?/);
  assert.equal(await claude.getAttribute("aria-describedby"), "cw-help-claude");
  await dialog.getByRole("checkbox", { name: /Custom command/ }).check();
  assert.equal(await dialog.locator("#cw-custom").isVisible(), true);
  await dialog.locator("#cw-custom-name").fill("mine");
  await dialog.locator("#cw-custom-cmd").fill("./scripts/agent.sh");
  await next.click();

  // 3 Agents: the turn order holds only ticked agents; unticking an agent removes its turns
  assert.equal(await current(), "Agents");
  assert.deepEqual(await dialog.locator("#cw-order .chip").allInnerTexts(), ["1. claude", "2. codex", "3. mine"]);
  await dialog.locator('[data-order-add="claude"]').click();
  assert.equal(await dialog.locator("#cw-order .chip").count(), 4);
  await dialog.locator('[data-order-rm="1"]').click(); // drop codex's turn
  assert.deepEqual(await dialog.locator("#cw-order .chip").allInnerTexts(), ["1. claude", "2. mine", "3. claude"]);
  await next.click();
  assert.match(await dialog.locator("#cw-problems").innerText(), /Initial task: is empty/);
  await dialog.locator('[name="task"]').fill("Verify the wizard.");
  await dialog.locator('[name="iterations"]').fill("3");
  await next.click();

  // 4 Review: exact commands, the custom one checked by the server; Create sends agents and custom
  assert.equal(await current(), "Review & create");
  const review = await dialog.locator("#cw-review").innerText();
  assert.match(review, /claude -p/); assert.match(review, /\{project_dir\}\/scripts\/agent\.sh/);
  assert.match(review, /3 turns: claude → mine → claude/);
  assert.match(review, /Codex: not installed/);
  assert.equal(await next.innerText(), "Create project");
  await dialog.locator("#cw-back").click(); await dialog.locator("#cw-back").click();
  await dialog.locator("#cw-custom-cmd").fill("/mnt/x/.alfrd/bin/agy_prompt.py");
  await next.click(); await next.click();
  assert.match(await dialog.locator("#cw-problems").innerText(), /^custom command mine: .* points into another project's \.alfrd\/bin\. Copy the script/);
  assert.equal(await next.isDisabled(), true, "a refused custom command blocks Create");
  await dialog.locator("#cw-back").click(); await dialog.locator("#cw-back").click();
  await dialog.locator("#cw-custom-cmd").fill("./scripts/agent.sh");
  await next.click(); await next.click(); await next.click();
  const created = await page.evaluate(() => window.created);
  assert.deepEqual(created, [{ path: "/tmp/wizard-2", template: "agent-loop", manifest: ".alfrd/alfrd.yaml", agents: ["claude", "codex"], custom: [{ name: "mine", cmd: "./scripts/agent.sh" }], task: "Verify the wizard.", iterations: 3, sequence: ["claude", "mine", "claude"] }]);
  const toasts = await page.evaluate(() => window.toasts);
  assert.equal(toasts[0].link, "#/config", "the toast links to Settings to review the agent commands");
  assert.equal(toasts[1].tone, "warn");

  // Settings: the Entrypoints card is visible without opening Advanced…
  await page.evaluate(async () => {
    const config = await import("/js/components/alfrd_config.js");
    const { dumpYaml } = await import("/js/utils/yaml_parser.js");
    const text = dumpYaml({ version: 1, name: "wizard-2", entrypoint: [{ name: "claude", cmd: ["claude", "-p"] }, { name: "mine", cmd: ["{project_dir}/scripts/agent.sh"] }] });
    const el = document.querySelector("#home");
    const ctx = { state: { mode: "static", selectedProject: "wizard-2", trees: { "wizard-2": { manifestText: text } }, workflowFile: { name: "alfrd.yaml" }, aliases: [] },
      canWrite: () => true, projectName: () => "wizard-2", setFooterRight() {}, toast() {}, modal() {}, update() {} };
    config.mount(el, ctx); config.render(el, ctx);
  });
  const card = page.locator("#ps-entrypoints-card");
  await card.waitFor();
  assert.match(await card.locator("h4").innerText(), /Entrypoints \(agent commands\)/);
  assert.deepEqual(await card.locator("tbody td.mono:first-child").allInnerTexts(), ["claude", "mine"]);
  assert.equal(await card.getByRole("button", { name: "Edit commands" }).isVisible(), true);
  assert.equal(await card.getByRole("button", { name: "Choose chat agents" }).isVisible(), true);
  await card.scrollIntoViewIfNeeded();
  assert.equal(await page.locator('[data-act="entrypoints-goto"]').count(), 0);

  // Agent chat: @mine's found chip is the saved command's; changing its entrypoint clears it
  await page.evaluate(async () => {
    const { modal } = await import("/shell.js");
    const { server } = await import("/js/data/server.js");
    const { dumpYaml } = await import("/js/utils/yaml_parser.js");
    Object.assign(server, { request: async () => ({ ok: false, json: async () => null }),
      chatProbe: async () => ({ agents: { mine: { available: true, detail: "found", entrypoint: "mine", cmd: ["{project_dir}/scripts/agent.sh"] } } }) });
    const text = dumpYaml({ version: 1, name: "wizard-2", entrypoint: [{ name: "mine", cmd: ["{project_dir}/scripts/agent.sh"] }],
      chat: { agents: { mine: { label: "Mine", entrypoint: "mine", roles: [] } } } });
    (await import("/js/components/chat_settings.js")).openChatSettings({ modal, toast() {} }, "wizard-2", text, () => {});
  });
  const chip = page.locator('[id="cs-ep-mine"]');
  await page.locator('[id="cs-ep-mine"].tone-ok').waitFor();
  assert.equal(await chip.innerText(), "found");
  const field = page.getByRole("combobox", { name: "Entrypoint of @mine" });
  await field.fill("claude");
  assert.equal(await chip.innerText(), "", "another entrypoint: not the saved command's result");
  await field.fill("mine");
  assert.equal(await chip.innerText(), "found");
  assert.deepEqual(errors, []);
});
