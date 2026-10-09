// Tests the actual Studio modal shell and plugin helpers in Chromium.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));

test("project sections and guarded plugin dialogs in the Studio shell", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") return send('<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><button id="opener">Open</button><div id="ov-home"></div><div id="modal-host" hidden></div>');
    if (url.pathname === "/shell.js") return send(`import { $ } from "/js/utils/dom.js"; let clearModalKeys; export ${shell}`, "text/javascript");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web + sep)) return send("missing", "text/plain", 404);
    try { return send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { return send("missing", "text/plain", 404); }
  });
  await new Promise((resolve) => service.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => { service.close(resolve); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const initialize = async () => {
    const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
    await page.goto(`http://127.0.0.1:${service.address().port}`);
    await page.evaluate(async () => {
      const { modal } = await import("/shell.js");
      window.logs = []; window.toasts = [];
      window.ctx = { modal, state: { selectedProject: "one" }, log: (...args) => logs.push(args), toast: (...args) => toasts.push(args) };
      window.api = (await import("/js/components/plugin_api.js")).api(ctx, {});
      window.sections = await import("/js/components/project_sections.js");
      window.openDialog = (actions = []) => {
        const body = document.createElement("div");
        body.innerHTML = '<label>Name <input id="name"></label>';
        window.body = body;
        window.handle = api.dialog({ title: '<img src=x onerror="window.xss=true">', body, actions, wide: true });
      };
    });
    return page;
  };

  await t.test("sections order, persisted collapse, project changes, isolated async failure", async () => {
    const page = await initialize();
    t.after(() => page.close());
    const result = await page.evaluate(async () => {
      window.renders = [];
      api.registerProjectSection({ id: "good", title: "Google Sheet", order: 10, render(project, host) { renders.push(project); host.textContent = project; } });
      api.registerProjectSection({ id: "bad", title: "Bad", order: 20, async render() { throw new Error('<img src=x onerror="window.xss=true">'); } });
      const box = document.querySelector("#ov-home");
      sections.renderProjectSections(box, "one", ctx);
      await new Promise((r) => setTimeout(r, 0));
      const first = box.firstElementChild;
      box.innerHTML = "";
      sections.renderProjectSections(box, "one", ctx);
      const same = first === box.firstElementChild;
      box.querySelector("button").click();
      const collapsed = box.querySelector("button").getAttribute("aria-expanded");
      ctx.state.selectedProject = "two"; box.innerHTML = "";
      sections.renderProjectSections(box, "two", ctx);
      await new Promise((r) => setTimeout(r, 0));
      const nextCollapsed = box.querySelector(".plug-section-body").hidden;
      const order = [...box.children].map((n) => n.dataset.pluginSection);
      const warnings = box.querySelectorAll(".callout.warn").length;
      ctx.state.selectedProject = "all"; box.innerHTML = "";
      sections.renderProjectSections(box, "all", ctx);
      return { same, collapsed, nextCollapsed, order, warnings, renders, logs, all: box.childElementCount, xss: !!window.xss, errors: (await import("/js/components/plugin_api.js")).pluginErrors };
    });
    assert.deepEqual(result.renders, ["one", "two"]);
    assert.equal(result.same, true);
    assert.equal(result.collapsed, "false");
    assert.equal(result.nextCollapsed, true);
    assert.deepEqual(result.order, ["good", "bad"]);
    assert.equal(result.warnings, 1);
    assert.equal(result.logs.length, 2);
    assert.equal(result.all, 0);
    assert.equal(result.xss, false);
    assert.match(result.errors[0].message, /project section/);
  });

  await t.test("Overview mounts sections for regular and loop projects, preserves focused controls and clears All", async () => {
    const page = await initialize(); t.after(() => page.close());
    const result = await page.evaluate(async () => {
      const { renderHome } = await import("/js/components/overview.js");
      let renders = 0;
      api.registerProjectSection({ id: "integration", title: "Integration", render(project, host) {
        renders++;
        const input = document.createElement("input"); input.id = "plugin-input"; host.append(input);
      } });
      Object.assign(ctx, { projectName: (id) => id });
      Object.assign(ctx.state, { mode: "demo", workflow: { steps: [] }, targets: [], trees: {} });
      renderHome(document.body, ctx);
      const input = document.querySelector("#plugin-input"); input.focus(); input.value = "draft";
      renderHome(document.body, ctx);
      const same = document.querySelector("#plugin-input") === input;
      const focus = document.activeElement === input;
      const cardTitles = [...document.querySelectorAll("#ov-home h2")].map((node) => node.textContent);
      ctx.state.selectedProject = "loop"; ctx.state.trees.loop = { defs: { template: "agent-loop" } };
      renderHome(document.body, ctx);
      const loopCount = document.querySelectorAll("[data-plugin-section]").length;
      ctx.state.selectedProject = "all"; renderHome(document.body, ctx);
      const allCount = document.querySelectorAll("[data-plugin-section]").length;
      ctx.state.selectedProject = "loop"; renderHome(document.body, ctx);
      return { same, focus, cardTitles, loopCount, allCount, renders };
    });
    assert.equal(result.same, true);
    assert.equal(result.focus, true);
    assert.deepEqual(result.cardTitles, ["Get started", "Integration"]);
    assert.equal(result.loopCount, 1);
    assert.equal(result.allCount, 0);
    assert.equal(result.renders, 3);
  });

  await t.test("clean Escape and backdrop close and restore focus; dirty closes require discard", async () => {
    const page = await initialize(); t.after(() => page.close());
    await page.focus("#opener");
    await page.evaluate(() => openDialog());
    assert.equal(await page.locator(".modal-b > div").evaluate((node) => node === window.body), true);
    assert.equal(await page.locator(".modal-h img").count(), 0);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#opener").evaluate((n) => n === document.activeElement), true);
    await page.evaluate(() => { openDialog(); handle.setDirty(true); });
    await page.keyboard.press("Escape");
    assert.equal(await page.locator(".plug-confirm").count(), 1);
    assert.equal(await page.locator(".plug-confirm button").first().evaluate((n) => n === document.activeElement), true);
    await page.keyboard.press("Shift+Tab");
    assert.equal(await page.locator(".plug-confirm button").last().evaluate((n) => n === document.activeElement), true);
    await page.keyboard.press("Escape"); // cancels choice, keeps draft
    assert.equal(await page.locator(".plug-confirm").count(), 0);
    assert.equal(await page.locator(".modal").count(), 1);
    await page.locator(".modal-back").click({ position: { x: 2, y: 2 } });
    await page.getByRole("button", { name: "Discard", exact: true }).click();
    await page.waitForSelector(".modal", { state: "detached" });
    await page.evaluate(() => openDialog());
    await page.locator(".modal-back").click({ position: { x: 2, y: 2 } });
    assert.equal(await page.locator(".modal").count(), 0);
  });

  await t.test("async action blocks closure, confirms in its footer, restores disabled states and handles errors", async () => {
    const page = await initialize(); t.after(() => page.close());
    await page.evaluate(() => {
      openDialog([{ label: "Save", tone: "primary", async run() { window.answer = await api.confirm("Write cells?", { confirmLabel: "Write" }); await new Promise((r) => window.finish = r); } },
        { label: "Cancel", run: (h) => h.close() }, { label: "Unavailable" }]);
      handle.footer.querySelectorAll("button")[1].disabled = true;
    });
    await page.getByRole("button", { name: "Save", exact: true }).click();
    assert.equal(await page.locator(".modal").count(), 1);
    await page.getByRole("button", { name: "Write", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "Save …", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "Cancel", exact: true }).isDisabled(), true);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator(".modal").count(), 1);
    assert.match(await page.evaluate(() => toasts[0][0]), /Wait/);
    await page.evaluate(() => finish());
    await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'));
    assert.equal(await page.evaluate(() => answer), true);
    assert.equal(await page.getByRole("button", { name: "Save", exact: true }).isDisabled(), false);
    assert.equal(await page.getByRole("button", { name: "Unavailable", exact: true }).isDisabled(), true);
    await page.evaluate(() => { handle.close(); openDialog([{ label: "Fail", run: async () => { throw new Error("safe error"); } }]); });
    await page.getByRole("button", { name: "Fail", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "Fail", exact: true }).isDisabled(), false);
    assert.equal(await page.evaluate(() => toasts.at(-1)[0]), "safe error");
  });

  await t.test("a late async action cannot close a replacement modal", async () => {
    const page = await initialize(); t.after(() => page.close());
    await page.evaluate(() => openDialog([{ label: "Run", async run(h) { await new Promise((r) => window.finish = r); h.close(); } }]));
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await page.evaluate(() => { ctx.modal("Replacement"); finish(); });
    await page.waitForFunction(() => document.querySelector(".modal")?.textContent === "Replacement");
    await page.evaluate(() => new Promise((r) => setTimeout(r, 20)));
    assert.equal(await page.locator(".modal").textContent(), "Replacement");
  });

  await t.test("standalone confirmations resolve on accept, Escape and replacement", async () => {
    const page = await initialize(); t.after(() => page.close());
    await page.evaluate(() => { window.result = null; api.confirm("Delete mapping?", { tone: "danger" }).then((v) => result = v); });
    await page.getByRole("button", { name: "Confirm", exact: true }).click();
    await page.waitForFunction(() => result === true);
    assert.equal(await page.locator(".modal").count(), 0);
    await page.evaluate(() => { result = null; api.confirm("Again?").then((v) => result = v); });
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => result === false);
    await page.evaluate(() => { result = null; api.confirm("Again?").then((v) => result = v); ctx.modal("Replacement"); });
    await page.waitForFunction(() => result === false);
  });
});
