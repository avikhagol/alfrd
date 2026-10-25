// components/permission_ui.js (S4): the non-default permission badge and the risky-change confirmation.
// Presentation only; the server stays authoritative.
import test from "node:test";
import assert from "node:assert/strict";

globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const { confirmPermissions, permissionBadge } = await import("../../src/alfrd/web/js/components/permission_ui.js");
const { server } = await import("../../src/alfrd/web/js/data/server.js");

function withConfirm(answer, fn) {
  const asked = [];
  globalThis.confirm = (text) => { asked.push(text); return answer; };
  try { return [fn(), asked]; } finally { delete globalThis.confirm; }
}

test("enabling file edits or project deletion asks once and names the machine scope", () => {
  const [ok, asked] = withConfirm(true, () => confirmPermissions({ "lockdown.file_edits": true, "lockdown.project_delete": true }));
  assert.equal(ok, true);
  assert.equal(asked.length, 1);
  assert.match(asked[0], /project file edits and forgetting and deleting projects/);
  assert.match(asked[0], /every Studio on this machine/);
  assert.match(asked[0], /Server access checks/);
});

test("cancelling the confirmation returns false", () => {
  for (const key of ["lockdown.file_edits", "lockdown.project_delete"]) {
    const [ok, asked] = withConfirm(false, () => confirmPermissions({ [key]: true }));
    assert.equal(ok, false);
    assert.equal(asked.length, 1);
  }
});

test("disabling a permission, other flags and no changes never prompt", () => {
  for (const changes of [
    {}, { "lockdown.file_edits": false }, { "lockdown.project_delete": false },
    { "ui.theme": "x", "lockdown.runs": true, "lockdown.read_only": true }, { "lockdown.file_edits": "true" },
  ]) {
    const [ok, asked] = withConfirm(false, () => confirmPermissions(changes));
    assert.equal(ok, true, JSON.stringify(changes));
    assert.equal(asked.length, 0);
  }
});

const flag = (over) => ({ key: "lockdown.runs", label: "Allow workflow runs", dangerous: true, default: true, effective: true, ...over });

async function badge(respond) {
  const host = { isConnected: true, innerHTML: "", textContent: "" };
  const saved = server.request;
  const urls = [];
  server.request = async (url) => { urls.push(url); return respond(); };
  try { await permissionBadge(host, "a b/c"); } finally { server.request = saved; }
  return { host, urls };
}
const ok = (flags) => () => ({ ok: true, json: async () => ({ flags }) });

test("defaults and presentation-only changes show no badge", async () => {
  const { host, urls } = await badge(ok([flag(), flag({ key: "ui.theme", dangerous: false, default: "a", effective: "b" }), flag({ key: "ui.live_updates", dangerous: false, default: true, effective: false })]));
  assert.equal(host.innerHTML, "");
  assert.equal(urls[0], "/api/studio/ui-flags?project=a%20b%2Fc");
});

test("a non-default dangerous permission shows the badge with its value and where to change it", async () => {
  const { host } = await badge(ok([flag({ effective: false }), flag({ key: "lockdown.read_only", label: "Read-only Studio", default: false, effective: true }), flag({ key: "lockdown.chat", label: "Allow chat" })]));
  assert.match(host.innerHTML, /class="badge">Non-default permissions/);
  assert.match(host.innerHTML, /Allow workflow runs: off; Read-only Studio: on/);
  assert.doesNotMatch(host.innerHTML, /Allow chat/);
  assert.match(host.innerHTML, /across projects/);
  assert.match(host.innerHTML, /Preferences → Machine/);
});

test("registry labels are escaped in the badge", async () => {
  const { host } = await badge(ok([flag({ label: "<img src=x onerror=alert(1)>", effective: false })]));
  assert.doesNotMatch(host.innerHTML, /<img/);
  assert.match(host.innerHTML, /&lt;img/);
});

test("a failed or non-ok fetch reports unavailable, not default permissions", async () => {
  for (const respond of [() => ({ ok: false }), () => { throw new Error("offline"); }, () => ({ ok: true, json: async () => ({}) })]) {
    const { host } = await badge(respond);
    assert.match(host.textContent, /Permission status unavailable/);
    assert.equal(host.innerHTML, "");
  }
});

test("a host that left the page is not written to", async () => {
  const host = { isConnected: false, innerHTML: "", textContent: "" };
  const saved = server.request;
  server.request = async () => ({ ok: true, json: async () => ({ flags: [flag({ effective: false })] }) });
  try { await permissionBadge(host, "p"); } finally { server.request = saved; }
  assert.equal(host.innerHTML, "");
});
