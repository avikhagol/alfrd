// Agents & review in Chromium with the real Studio modal shell: Settings tab frame, validation and save.
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = resolve(fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url)));

test("Agents & review: tab frame, themes, mobile, validation and apply", { skip: !chromium && "Playwright not available" }, async (t) => {
  const app = await readFile(resolve(web, "js/app.js"), "utf8");
  const shell = app.slice(app.indexOf("function modal(html"), app.indexOf("ctx.modal = modal;"));
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (body, type = "text/html", code = 200) => { res.writeHead(code, { "Content-Type": type }); res.end(body); };
    if (url.pathname === "/") {
      const theme = url.searchParams.get("theme") || "obsidian-orbit";
      return send(`<!doctype html><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/lazy.css"><link rel="stylesheet" href="/css/themes/${theme}/theme.css"><body style="display:block;height:auto"><div id="home"></div><div id="modal-host" hidden></div></body>`);
    }
    if (url.pathname === "/shell.js") return send(`import { $ } from "/js/utils/dom.js"; let clearModalKeys; export ${shell}`, "text/javascript");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(web + sep)) return send("missing", "text/plain", 404);
    try { return send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { return send("missing", "text/plain", 404); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());

  const open = async (theme, width) => {
    const page = await browser.newPage({ viewport: { width, height: 844 } });
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async () => {
      const { modal } = await import("/shell.js");
      const { dumpYaml } = await import("/js/utils/yaml_parser.js");
      const text = dumpYaml({ name: "p", entrypoint: [
        { name: "claude", cmd: ["claude", "-p", "--model", "old"] },
        { name: "codex", cmd: ["codex", "exec", "-"] },
      ], project_settings: { personas: { dev: { label: "Senior Developer", instructions: "Write code." } } },
      workflows: [{ name: "loop", repeat: { iterations: 2 }, steps: [
        { id: "claude-turn", entrypoint: "claude", roles: ["dev"], handoff: { input: "a.md", output: "b.md" } },
        { id: "codex-turn", entrypoint: "codex", handoff: { input: "b.md", output: "a.md" } },
      ] }] });
      window.applied = [];
      const ctx = { modal, toast() {}, update() {} };
      await (await import("/js/components/agent_dialog.js")).openAgentSettings(ctx, "p", text, async (updated) => { window.applied.push(updated); });
    });
    return page;
  };
  for (const theme of ["obsidian-orbit", "daylight-orbit"]) for (const width of [1280, 390]) {
    await t.test(`${theme} at ${width}px`, async () => {
      const page = await open(theme, width); t.after(() => page.close());
      const errors = []; page.on("pageerror", (e) => errors.push(e.message));
      const dialog = page.locator(".modal.set-dlg.ag-dlg");
      const tabs = dialog.getByRole("tab");
      assert.deepEqual(await tabs.allInnerTexts(), ["Models", "Personalities", "Turn roles", "Folders & shell", "Human review"]);
      assert.equal(await dialog.getByRole("tab", { selected: true }).innerText(), "Models");
      assert.equal(await dialog.locator(".set-panel:visible").count(), 1);
      assert.equal(await dialog.locator("[data-model='0']").inputValue(), "old");
      // Keyboard: arrows move focus, Enter selects (same as Studio settings).
      await dialog.getByRole("tab", { name: "Models" }).focus();
      await page.keyboard.press("ArrowDown"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter");
      assert.equal(await dialog.getByRole("tab", { selected: true }).innerText(), "Turn roles");
      assert.equal(await dialog.locator("#ag-panel-roles [data-turn='0'][data-persona='0']").isVisible(), true);
      // Field ids are unchanged; one section scrolls, the page never does.
      for (const id of ["#agent-folders", "#agent-bash", "#agent-sandbox", "#review-enabled", "#persona-add", "#agent-settings-error"]) assert.equal(await dialog.locator(id).count(), 1, id);
      await dialog.getByRole("tab", { name: "Human review" }).click();
      assert.equal(await dialog.locator("[data-turn-review]").count(), 4);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "no page overflow");
      const box = await dialog.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width, "dialog fits the viewport");
      await page.screenshot({ path: `/tmp/agent-settings-${theme}-${width}.png` });
      // A blank required field in a hidden section opens that section instead of failing silently.
      await dialog.getByRole("tab", { name: "Personalities" }).click();
      await dialog.locator("#persona-add").click();
      await dialog.getByRole("tab", { name: "Folders & shell" }).click();
      await dialog.locator("#agent-folders").fill("docs");
      await dialog.getByRole("button", { name: "Apply settings" }).click();
      assert.equal(await dialog.getByRole("tab", { selected: true }).innerText(), "Personalities");
      assert.equal(await page.evaluate(() => window.applied.length), 0);
      await dialog.locator("[data-persona-key='1']").fill("ux");
      await dialog.locator("[data-persona-label='1']").fill("Designer");
      await dialog.getByRole("tab", { name: "Models" }).click();
      await dialog.locator("[data-model='1']").fill("gpt-5");
      await dialog.getByRole("button", { name: "Apply settings" }).click();
      await dialog.waitFor({ state: "detached" });
      const [updated] = await page.evaluate(() => window.applied);
      assert.match(updated, /gpt-5/);
      assert.match(updated, /docs/);
      assert.match(updated, /Designer/);
      assert.deepEqual(errors, []);
    });
  }
});
