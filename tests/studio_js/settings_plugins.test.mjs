import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

const source = ["settings_plugins_view.js", "settings_plugins.js"].map(name =>
  readFileSync(new URL(`../../src/alfrd/web/js/components/${name}`, import.meta.url), "utf8")
    .replace(/^(?:import|export \{).*;\n/gm, "").replace('import("./plugins_browse.js")', 'Promise.resolve({enhance: () => ({click() {}})})').replace(/export /g, "")
).join("\n");
const sample = () => ({ plugins: [
  { id: "markdown", title: "Markdown", version: "1.0", status: "ok", enabled: true, active: true, kinds: ["viewer"], web: { js: "index.js" }, description: "Read notes" },
  { id: "off", title: "Off plugin", status: "disabled", enabled: false, active: false, kinds: [] },
  { id: "later", title: "Later", status: "ok", enabled: true, active: false, kinds: ["converter"] },
], themes: [{ id: "obsidian", title: "Obsidian", source: "builtin" }, { id: "custom", title: "Custom", source: "plugin" }], theme: "obsidian", safe_mode: false });

function setup(list = sample()) {
  const live = {}, refresh = { setAttribute() {} }, inputs = [], toasts = [], calls = [], copied = [], swaps = [];
  const panel = { innerHTML: "", querySelector: (s) => s === "[data-plug-live]" ? live : s === "[data-plug-refresh]" ? refresh : null, querySelectorAll: () => inputs };
  const root = { querySelector: () => panel };
  const server = { session: { mutations_enabled: true }, authRequired: false, mutate: async (...args) => { calls.push(args); return {}; } };
  const ctx = { state: { mode: "server" }, toast: (...args) => toasts.push(args) };
  const sandbox = { server, pluginErrors: [], reportServerErrors() {}, fetchPlugins: async () => structuredClone(list),
    applyTheme: (_, next) => { swaps.push(next); return () => swaps.push("undo"); },
    $: (s, el) => el.querySelector(s), esc: (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"), icon: () => "<svg></svg>",
    window: { addEventListener() {}, removeEventListener() {} },
    copyText: async (s) => { copied.push(s); return true; }, location: { href: "/studio/" }, encodeURIComponent };
  runInNewContext(`${source};globalThis.api = { renderPlugins, mountPlugins };`, sandbox);
  return { ...sandbox.api, sandbox, ctx, root, panel, live, inputs, toasts, calls, copied, swaps, server };
}
const toggle = (id, checked) => ({ dataset: { plugToggle: id }, checked });

test("renders titles, kinds, all statuses, escaping and accessible switch descriptions", () => {
  const list = sample(); list.plugins.push({ id: "broken", title: '<img src=x onerror="bad">', status: "error", enabled: true, active: false, error: "boom", kinds: [] });
  const html = setup().renderPlugins(list, true);
  for (const text of ["Markdown", "Read notes", "viewer", "Active", "Off", "Restart needed", "Error", "Diagnostics", 'aria-describedby="plug-status-markdown"']) assert.ok(html.includes(text));
  assert.ok(!html.includes("<img")); assert.ok(html.includes("&lt;img"));
  assert.match(html, /role="status" aria-live="polite"/);
});

test("browser activation failures show Error; disabling the plugin shows Off and retains details", () => {
  const f = setup(), list = sample();
  const errors = [{ id: "markdown", title: "Markdown", message: "activation failed" }];
  assert.match(f.renderPlugins(list, true, errors), /id="plug-status-markdown" class="badge tone-fail">Error/);
  list.plugins[0].enabled = false;
  assert.match(f.renderPlugins(list, true, errors), /id="plug-status-markdown" class="badge tone-muted">Off/);
  assert.match(f.renderPlugins(list, true, errors), /browser: activation failed/);
});

test("diagnostics appears only for server and browser problems; includes traceback and missing binary hint", () => {
  const f = setup(), list = sample(); assert.doesNotMatch(f.renderPlugins(list, true), /class="plug-diag"/);
  list.plugins.push({ id: "gs", title: "GS", status: "missing_binary", missing_bin: ["gs"], traceback_tail: "trace", kinds: [] });
  const html = f.renderPlugins(list, true, [{ id: "bad-js", title: "Bad JS", message: "failed" }]);
  assert.match(html, /2 problems/); assert.match(html, /Install <code>gs/); assert.match(html, /Traceback/); assert.match(html, /browser: failed/);
});

test("failing toggle shows optimistic badge then restores state and announces error", async () => {
  const f = setup(); await f.mountPlugins(f.ctx, f.root);
  let reject;
  f.server.mutate = async (...args) => { f.calls.push(args); return new Promise((_, r) => reject = r); };
  const pending = f.panel.onchange({ target: toggle("markdown", false) });
  assert.match(f.panel.innerHTML, /plug-status-markdown" class="badge tone-muted">Off/);
  reject(new Error("Forbidden")); await pending;
  assert.match(f.panel.innerHTML, /plug-status-markdown" class="badge tone-ok">Active/);
  assert.equal(f.calls[0][0], "/studio/plugins/markdown/disable"); assert.equal(f.toasts.at(-1)[0], "Forbidden"); assert.equal(f.live.textContent, "Forbidden");
});

test("restart banner persists when Settings is opened again", async () => {
  const f = setup(); await f.mountPlugins(f.ctx, f.root);
  f.server.mutate = async () => ({ plugin: { enabled: false, active: false }, restart_required: true });
  await f.panel.onchange({ target: toggle("markdown", false) });
  assert.match(f.panel.innerHTML, /Restart <code>alfrd serve/);
  await f.mountPlugins(f.ctx, f.root); assert.match(f.panel.innerHTML, /Restart <code>alfrd serve/);
});

test("successful web toggle offers explicit Reload, never reloads automatically", async () => {
  const f = setup(); await f.mountPlugins(f.ctx, f.root);
  f.server.mutate = async () => ({ plugin: { enabled: false, active: false }, restart_required: false });
  await f.panel.onchange({ target: toggle("markdown", false) });
  assert.equal(f.toasts[0][2].linkLabel, "Reload"); assert.equal(f.toasts[0][2].link, "/studio/");
});

test("theme choice applies before POST; a failed save restores theme and stylesheet", async () => {
  const f = setup(); await f.mountPlugins(f.ctx, f.root);
  f.server.mutate = async (...args) => { f.calls.push(args); assert.deepEqual(f.swaps, ["custom"]); throw new Error("disk full"); };
  await f.panel.onchange({ target: { dataset: {}, name: "plug-theme", value: "custom" } });
  assert.equal(f.calls[0][0], "/studio/theme"); assert.equal(f.calls[0][1].id, "custom");
  assert.deepEqual(f.swaps, ["custom", "undo"]); assert.match(f.panel.innerHTML, /value="obsidian" checked/);
  assert.equal(f.toasts[0][0], "Theme not saved: disk full");
});

test("refresh retries failures and announces successful list fetch", async () => {
  const f = setup(); f.sandbox.fetchPlugins = async () => { throw new Error("offline"); };
  await f.mountPlugins(f.ctx, f.root); assert.match(f.panel.innerHTML, /Retry/);
  f.sandbox.fetchPlugins = async () => sample();
  await f.panel.onclick({ target: { closest: (sel) => sel === "[data-plug-refresh]" ? {} : null } });
  assert.match(f.panel.innerHTML, /Markdown/); assert.equal(f.live.textContent, "Plugins refreshed.");
});

test("successful theme save refreshes stylesheet from the persisted server state", async () => {
  const f = setup(); await f.mountPlugins(f.ctx, f.root);
  await f.panel.onchange({ target: { dataset: {}, name: "plug-theme", value: "custom" } });
  assert.deepEqual(f.swaps, ["custom", "custom"]);
  assert.equal(f.calls[0][0], "/studio/theme"); assert.equal(f.calls[0][1].id, "custom");
  assert.match(f.panel.innerHTML, /value="custom" checked/); assert.equal(f.live.textContent, "Theme saved.");
});

test("read-only and safe mode prevent mutations, preserve viewable diagnostics/themes", async () => {
  for (const safe of [true, false]) {
    const list = sample(); list.safe_mode = safe;
    const f = setup(list); if (!safe) f.server.session.mutations_enabled = false;
    await f.mountPlugins(f.ctx, f.root);
    await f.panel.onchange({ target: toggle("markdown", false) }); assert.equal(f.calls.length, 0);
    assert.match(f.panel.innerHTML, safe ? /Safe mode/ : /Change themes from a browser on the server host/);
  }
});

test("browser-only, demo and unauthenticated mode do not fetch plugins", async () => {
  for (const mode of ["folder", "demo", "auth", "session"]) {
    const f = setup(); f.sandbox.fetchPlugins = () => assert.fail("must not fetch");
    if (mode === "folder") f.ctx.state.mode = "folder";
    if (mode === "demo") f.ctx.state.demoEnabled = true;
    if (mode === "auth") f.server.authRequired = true;
    if (mode === "session") f.server.session = null;
    await f.mountPlugins(f.ctx, f.root); assert.match(f.panel.innerHTML, /No ALFRD server is connected/);
  }
});

test("copy details writes plain text diagnostics and reports success", async () => {
  const list = sample(); list.plugins.push({ id: "broken", version: "2", status: "error", error: "bad", traceback_tail: "trace", kinds: [] });
  const f = setup(list); await f.mountPlugins(f.ctx, f.root);
  await f.panel.onclick({ target: { closest: (s) => s === "[data-plug-copy]" ? { dataset: { plugCopy: "0" } } : null } });
  assert.equal(f.copied[0], "broken\n2\nerror\nbad\ntrace"); assert.equal(f.toasts[0][0], "Copied");
});

test("Configure appears only for configurable plugins and controls its own panel", () => {
  const list = sample(); list.plugins[0].configurable = true;
  const html = setup().renderPlugins(list, true);
  assert.equal(html.match(/data-plug-config="/g)?.length, 1);
  assert.match(html, /data-plug-config="markdown" aria-expanded="false" aria-controls="plug-config-markdown"/);
  assert.match(html, /id="plug-config-markdown" data-plug-config-body="markdown" hidden/);
});
