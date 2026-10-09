import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("../../src/alfrd/web/js/components/plugin_config.js", import.meta.url), "utf8")
  .replace(/^import .*;\n/gm, "").replace(/export /g, "");
const sandbox = { esc: (s) => String(s ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"), Date };
runInNewContext(`${source};globalThis.render = render;`, sandbox);
const config = (over = {}) => ({ id: "telegram", missing: [], can_check: true, settings: [
  { key: "token", label: "Bot token", kind: "secret", required: true, set: true, help: "From @BotFather", placeholder: "123:AAH" },
  { key: "chat_id", label: "Allowed chat id", kind: "text", required: true, value: '42"><b>', help: "", placeholder: "" },
], services: [{ id: "bot", title: "Bot", description: "Answers commands", command: ["alfrd", "telegram", "run"],
  status: "running", pid: 7, started: 1, exit_code: null, autostart: true }], ...over });

test("a saved secret is never put back in the form; values are escaped", () => {
  const html = sandbox.render(config(), true);
  assert.match(html, /type="password" value="" placeholder="Saved · leave empty to keep it"/);
  assert.match(html, /tone-ok">set</);
  assert.ok(html.includes('value="42&quot;>&lt;b>"') && !html.includes('42"><b>'));
  assert.match(html, /data-plug-clear="token"/);
  assert.match(html, /data-plug-check/);
});

test("services show state and the matching actions", () => {
  let html = sandbox.render(config(), true);
  assert.match(html, /tone-ok">running</);
  assert.match(html, /data-plug-svc-do="stop"/); assert.ok(!html.includes('data-plug-svc-do="start"'));
  assert.match(html, /data-plug-auto checked/);
  assert.ok(html.includes("alfrd telegram run") && html.includes("pid 7"));
  const stopped = config(); Object.assign(stopped.services[0], { status: "failed", exit_code: 1, autostart: false });
  html = sandbox.render(stopped, true);
  assert.match(html, /tone-fail">failed</); assert.match(html, /data-plug-svc-do="start"/);
  assert.ok(html.includes("last exit code 1"));
});

test("read-only browsers and unconfigured plugins", () => {
  const html = sandbox.render(config({ missing: ["Bot token"] }), false);
  assert.ok(!html.includes('type="submit"') && html.includes("from a browser on the server host"));
  assert.ok(!html.includes("data-plug-clear"));
  assert.ok(html.includes("Not configured: Bot token."));
  assert.match(html, /data-plug-svc-do="stop" disabled/);
});
