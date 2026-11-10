import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve, extname, sep } from "node:path";
import { fileURLToPath } from "node:url";
let chromium;
try { ({ chromium } = await import(process.env.ALFRD_PLAYWRIGHT || "playwright")); } catch { /* optional */ }
const web = fileURLToPath(new URL("../../src/alfrd/web/", import.meta.url));

const DEFAULTS = { version: 1, features: { experimental: false }, studio: { hidden_sections: [], hidden_templates: [], live_interval: null },
  lockdown: { read_only: false, plugins: true, plugin_actions: true, project_create: true, project_delete: true, runs: true, chat: true, file_edits: true },
  limits: { chat_max_cost_usd: null, chat_max_replans: null, chat_allowed_agents: [] } };

test("superuser page: settings form, ops panel, both themes and phone width", { skip: !chromium && "Playwright not available" }, async (t) => {
  const service = createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (data, type) => { res.writeHead(200, { "Content-Type": type }); res.end(data); };
    if (url.pathname === "/") return send(`<!doctype html><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/css/studio.css"><link rel="stylesheet" href="/css/themes/${url.searchParams.get("theme")}/theme.css"><body style="display:block;height:auto;margin:0"><main id="home"></main></body>`, "text/html");
    const path = resolve(web, url.pathname.slice(1));
    if (!path.startsWith(resolve(web) + sep)) { res.writeHead(404); return res.end(); }
    try { send(await readFile(path), [".js", ".mjs"].includes(extname(path)) ? "text/javascript" : "text/css"); }
    catch { res.writeHead(404); res.end(); }
  });
  await new Promise((done) => service.listen(0, "127.0.0.1", done));
  t.after(() => new Promise((done) => { service.close(done); service.closeAllConnections(); }));
  const browser = await chromium.launch({ ...(process.env.ALFRD_CHROMIUM_EXECUTABLE ? { executablePath: process.env.ALFRD_CHROMIUM_EXECUTABLE } : {}) });
  t.after(() => browser.close());

  const open = async (theme, width) => {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    page.setDefaultTimeout(7000);
    const errors = []; page.on("pageerror", (e) => errors.push(e.message));
    page.on("dialog", (d) => d.accept());
    await page.goto(`http://127.0.0.1:${service.address().port}/?theme=${theme}`);
    await page.evaluate(async (defaults) => {
      const { server } = await import("/js/data/server.js");
      const view = (settings) => ({ settings, defaults, labels: { "lockdown.runs": "Start and resume workflow runs" }, file: "/home/u/.config/alfrd/superuser.yaml", exists: true, error: null,
        flags: { experimental_features: false, no_gui_install: false, no_plugin_actions: false, live_interval: 2, demo: false, projects_scope: null },
        effective: { experimental: settings.features.experimental, live_interval: 2, read_only: settings.lockdown.read_only, ...settings.lockdown },
        choices: { sections: ["workflow", "metadata", "results", "logs", "chat", "config"], templates: ["basic", "avica", "agent-loop"],
          locks: ["plugins", "plugin_actions", "project_create", "project_delete", "runs", "chat", "file_edits"] } });
      window.settings = JSON.parse(JSON.stringify(defaults)); window.mutations = [];
      const ops = { stats: { projects: 2, plans: 3, chat_sessions: 1, active_plans: 1, runners_alive: 1, running_units: 1, servers_alive: 2 },
        runs: [{ project: "p", project_name: "Demo project", root: "/data/a/very/long/path/to/the/demo/project", plan: "20261024-0001", kind: "workflow", title: "main", status: "running",
          runner_alive: true, runner_pid: 41, units: [{ id: "0001-alpha-wait", steps: ["wait"], row: "alpha", pid: 42 }] }],
        servers: [{ port: 5000, url: "http://127.0.0.1:5000/studio/", pid: 7, alive: true, this: true }, { port: 5001, url: "http://127.0.0.1:5001/studio/", pid: 8, alive: false }],
        system: { version: "0.4.0.0", python: "3.12.1", platform: "Linux", host: "khagol", pid: 7, uptime_s: 4000, cpu: 12, cores: 8, mem_used: 2e9, mem_total: 16e9, load: [0.5, 0.4, 0.3], runtime_db: "/home/u/.alfrd/runtime.sqlite", runtime_db_bytes: 3e6 } };
      Object.assign(server, { session: { mutations_enabled: true, can_superuser: true, csrf_token: "t" },
        request: async (url) => new Response(JSON.stringify(url.endsWith("/ops") ? ops : view(window.settings)), { status: 200, headers: { "Content-Type": "application/json" } }),
        mutate: async (url, data) => {
          window.mutations.push({ url, data });
          if (url === "/studio/superuser") {
            if (data.replace) window.settings = data.settings;
            else for (const [k, v] of Object.entries(data.settings)) { const [a, b] = k.split("."); window.settings[a][b] = v; }
            return view(window.settings);
          }
          return { ok: true };
        } });
      const host = document.getElementById("home"), mod = await import("/js/components/superuser.js");
      const ctx = { state: { mode: "server" }, toast(m) { window.lastToast = m; }, refreshSession: async () => { window.refreshed = (window.refreshed || 0) + 1; } };
      mod.render(host, ctx);
    }, DEFAULTS);
    await page.locator('#su-form [data-path="features.experimental"]').waitFor();
    await page.locator(".su-table tbody tr").first().waitFor();
    return { page, errors };
  };

  for (const theme of ["obsidian-orbit", "daylight-orbit"]) for (const width of [1360, 390]) {
    await t.test(`${theme} ${width}px: no horizontal scroll, statistics shown`, async () => {
      const { page, errors } = await open(theme, width); t.after(() => page.close());
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.locator("#su-stats > div").count(), 7);
      assert.match(await page.locator("#su-servers").innerText(), /this server/);
      assert.deepEqual(errors, []);
    });
  }

  await t.test("save sends the edited settings; read-only greys out the switches", async () => {
    const { page, errors } = await open("obsidian-orbit", 1360); t.after(() => page.close());
    assert.equal(await page.locator("#su-save").isDisabled(), true);
    await page.locator('[data-path="studio.hidden_sections:results"]').uncheck();
    await page.locator('[data-path="lockdown.runs"]').uncheck();
    await page.locator('[data-path="limits.chat_max_cost_usd"]').fill("2.5");
    await page.locator('[data-path="limits.chat_allowed_agents"]').fill("claude, sonnet");
    assert.equal(await page.locator("#su-state").innerText(), "Unsaved changes");
    await page.locator("#su-save").click();
    await page.locator("#su-state", { hasText: "Saved" }).waitFor();
    const sent = await page.evaluate(() => window.mutations[0]);
    assert.equal(sent.url, "/studio/superuser");
    // only the changed keys travel, so settings saved elsewhere meanwhile are kept
    assert.deepEqual(sent.data.settings, { "studio.hidden_sections": ["results"], "lockdown.runs": false,
      "limits.chat_max_cost_usd": 2.5, "limits.chat_allowed_agents": ["claude", "sonnet"] });
    assert.equal(await page.evaluate(() => window.refreshed), 1);
    await page.locator('[data-path="lockdown.read_only"]').check();
    assert.equal(await page.locator('[data-path="lockdown.chat"]').isDisabled(), true);
    await page.locator('[data-su="discard"]').click();
    assert.equal(await page.locator('[data-path="lockdown.chat"]').isDisabled(), false);
    assert.deepEqual(errors, []);
  });

  await t.test("ops: stop one agent, pause and cancel a run", async () => {
    const { page, errors } = await open("daylight-orbit", 1360); t.after(() => page.close());
    await page.locator('[data-op="stop-unit"]').click();
    await page.waitForFunction(() => window.mutations.length === 1);
    assert.deepEqual(await page.evaluate(() => window.mutations[0]), { url: "/studio/superuser/ops", data: { project: "p", plan: "20261024-0001", action: "stop-unit", unit: "0001-alpha-wait" } });
    await page.locator('[data-op="pause"]').click();
    await page.waitForFunction(() => window.mutations.length === 2);
    await page.locator('[data-op="cancel"]').click();
    await page.waitForFunction(() => window.mutations.length === 3);
    assert.deepEqual(await page.evaluate(() => window.mutations.map((m) => m.data.action)), ["stop-unit", "pause", "cancel"]);
    assert.deepEqual(errors, []);
  });
});
