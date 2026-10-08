// T9 Open vs Create: folder browser actions per mode (run with `node --test`).
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/alfrd/web/js");
globalThis.document ||= { hidden: false, addEventListener: () => {}, querySelectorAll: () => [] };
const { mountFolderBrowser } = await import(path.join(web, "components/folder_browser.js"));

const listing = (here = false) => ({
  path: "/d", parent: "/", writable: false, is_project: here, manifest_name: here ? "d" : null,
  entries: [
    { name: "alma", path: "/d/alma", is_project: true, manifest_name: "alma", is_ms: false },
    { name: "plain", path: "/d/plain", is_project: false, is_ms: false },
  ],
});

async function mount(opts, here = false) {
  const handlers = {};
  const host = {
    hidden: true, innerHTML: "", isConnected: true, classList: { add() {} },
    setAttribute() {}, removeAttribute() {}, querySelector: () => null, querySelectorAll: () => [],
    addEventListener: (type, fn) => { handlers[type] = fn; }, removeEventListener() {},
    contains: () => true,
  };
  mountFolderBrowser(host, { list: async () => listing(here), use() {}, close() {}, ...opts });
  await new Promise((r) => setTimeout(r, 0));
  // Click a button by data attribute (the browser dispatches on data-fsb-*).
  const click = (attr, value = "") => handlers.click({ target: { closest: () => ({ dataset: { fsbConnect: value }, matches: (s) => s === `[${attr}]` }) } });
  return { host, click };
}

test("Open project mode: projects show Open; a plain folder offers Create project here, never the default manifest", async () => {
  const created = [], opened = [];
  const { host, click } = await mount({ create: (p) => created.push(p), connect: async (p) => opened.push(...p), allowDefault: true });
  assert.match(host.innerHTML, /data-fsb-connect="0">Open</);
  assert.match(host.innerHTML, /data-fsb-create>.*Create project here/s);
  assert.doesNotMatch(host.innerHTML, /default alfrd\.yaml|data-fsb-use|data-fsb-all/);
  click("data-fsb-create");
  assert.deepEqual(created, ["/d"]);
  click("data-fsb-connect", "0");
  assert.deepEqual(opened, ["/d/alma"]);
});

test("Open project mode: a project folder itself offers Open this project", async () => {
  const { host } = await mount({ create() {}, connect: async () => {} }, true);
  assert.match(host.innerHTML, /data-fsb-connect-here>Open this project/);
  assert.doesNotMatch(host.innerHTML, /data-fsb-create/);
});

test("Create's folder picker marks ALFRD projects: Already an ALFRD project, Open instead", async () => {
  const { host } = await mount({ select: true, connect: async () => {} }, true);
  assert.match(host.innerHTML, /Already an ALFRD project:<\/span><button[^>]*data-fsb-connect="0">Open instead/);
  assert.match(host.innerHTML, /itself an ALFRD project.*data-fsb-connect-here>Open instead/s);
  assert.doesNotMatch(host.innerHTML.match(/<li class="fsb-row">.*?<\/li>/s)[0], /Open instead/);
});

test("Plain select pickers (no connect) and Import → Connect keep their old actions", async () => {
  const picker = await mount({ select: true });
  assert.doesNotMatch(picker.host.innerHTML, /Open instead|data-fsb-connect/);
  const imp = await mount({ connect: async () => {}, allowDefault: true });
  assert.match(imp.host.innerHTML, /data-fsb-connect="0">Connect</);
  assert.match(imp.host.innerHTML, /default alfrd\.yaml/);
  assert.match(imp.host.innerHTML, /data-fsb-use/);
});
