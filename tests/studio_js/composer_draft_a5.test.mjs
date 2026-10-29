import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { sessionDraft } from "../../src/alfrd/web/js/data/chat_draft.js";
import { mountComposerDraft, imageWarning } from "../../src/alfrd/web/js/components/composer_draft.js";
import { server } from "../../src/alfrd/web/js/data/server.js";

const saved = new Map();
globalThis.sessionStorage = { getItem: (k) => saved.get(k), setItem: (k, v) => saved.set(k, v) };
class El {
  constructor() { this.nodes = new Map(); this.listeners = {}; this.style = {}; this.value = ""; this.scrollHeight = 30; }
  querySelector(s) { if (!this.nodes.has(s)) this.nodes.set(s, new El()); return this.nodes.get(s); }
  addEventListener(k, f) { (this.listeners[k] ||= new Set()).add(f); }
  removeEventListener(k, f) { this.listeners[k]?.delete(f); }
  setAttribute(k, v) { this[k] = v; }
}
const ref = "att_0123456789abcdef";
const settings = () => ({ orchestrator: "a" });
const tick = () => new Promise((r) => setTimeout(r, 0));
const mount = (id = "chat", extra = {}) => { const root = new El(); return { root, c: mountComposerDraft(root, id, { settings, ...extra }) }; };
server.session = { mutations_enabled: true, csrf_token: "csrf" };
server.request = async () => ({ ok: true, json: async () => ({ agents: { a: { images: false } } }) });

test("Chat and dock mount the shared session model and retain independent sessions/projects", () => {
  const a = mount(), b = mount("cd");
  a.c.select("shared", "s"); b.c.select("shared", "s");
  a.c.edit("hello");
  assert.equal(b.root.querySelector("#cd-input").value, "hello");
  const d = sessionDraft("shared", "s");
  d.chips.set(ref, { id: ref, ref, status: "done", name: "Screenshot" }); d.sync();
  assert.match(b.root.querySelector("#cd-attachments").querySelector("[data-attach-chips]").innerHTML, /Screenshot/);
  b.c.select("shared", "other"); b.c.edit("other session");
  assert.equal(a.root.querySelector("#chat-input").value, "hello");
  b.c.select("other-project", "s"); assert.equal(b.root.querySelector("#cd-input").value, "");
  b.c.select("shared", "s"); assert.equal(b.root.querySelector("#cd-input").value, "hello");
  a.c.destroy(); b.c.destroy();
});

test("reload restores accepted refs, text and the content-bound retry ID, not in-flight blobs", async () => {
  const d = sessionDraft("reload", "s"); d.edit("keep", []);
  d.chips.set(ref, { id: ref, ref, status: "done" }); d.sync();
  const payload = d.beginSend(); d.finish();
  d.chips.set("pending", { status: "uploading", file: new Blob(["image"]) }); d.sync();
  const fresh = await import(`../../src/alfrd/web/js/data/chat_draft.js?reload=${Date.now()}`);
  const restored = fresh.sessionDraft("reload", "s");
  assert.equal(restored.read().text, "keep"); assert.deepEqual(restored.read().refs, [ref]);
  assert.equal(restored.chips.size, 1); assert.equal(restored.beginSend().client_message_id, payload.client_message_id);
});

test("201 and a confirmed 200 replay clear the sent content; a later edit survives its response", () => {
  for (const status of [400, 409, 422, 500]) {
    const d = sessionDraft("status", String(status)); d.edit("draft", [ref]);
    const p = d.beginSend(); d.accepted(status, p); d.finish();
    assert.equal(d.read().text, "draft"); assert.deepEqual(d.read().refs, [ref]);
    assert.equal(d.beginSend().client_message_id, p.client_message_id); d.finish();
  }
  const replay = sessionDraft("status", "200"); replay.edit("recorded", [ref]); const replayPayload = replay.beginSend(); replay.accepted(200, replayPayload); replay.finish(); assert.equal(replay.read().text, ""); assert.deepEqual(replay.read().refs, []);
  const d = sessionDraft("status", "201"); d.edit("sent", []); const p = d.beginSend();
  d.accepted(201, p); d.finish(); assert.equal(d.read().text, "");
  d.edit("first", []); const q = d.beginSend(); d.edit("new edit", []);
  d.accepted(201, q); d.finish(); assert.equal(d.read().text, "new edit");
  assert.notEqual(d.beginSend().client_message_id, q.client_message_id); d.finish();
});

test("mounted sends allow image-only, lock both surfaces, preserve network failure and clear on confirmed replay", async () => {
  const a = mount(), b = mount("cd"), errors = [];
  a.c.destroy(); a.c = mountComposerDraft(a.root, "chat", { settings, onError: (s) => errors.push(s) });
  a.c.select("send / project", "s / one"); b.c.select("send / project", "s / one"); await tick();
  const d = sessionDraft("send / project", "s / one"); d.chips.set(ref, { id: ref, ref, status: "done" }); d.sync();
  assert.match(a.root.querySelector("#chat-image-warning").textContent, /not delivered/);
  let resolve, request, status = 200;
  server.request = (url, init) => { request = { url, init }; return new Promise((r) => { resolve = r; }); };
  const pending = a.c.send();
  assert.equal(b.root.querySelector("#cd-send").disabled, true); assert.equal(await b.c.send(), false);
  assert.match(request.url, /send%20%2F%20project\/chat\/sessions\/s%20%2F%20one\/messages/);
  assert.equal(request.init.headers["X-CSRF-Token"], "csrf");
  const payload = JSON.parse(request.init.body); assert.equal(payload.text, ""); assert.deepEqual(payload.attachments, [ref]);
  resolve({ ok: true, status, json: async () => ({ replayed: true }) }); await pending;
  assert.deepEqual(d.read().refs, []); assert.deepEqual(errors, []);
  d.chips.set(ref, { id: ref, ref, status: "done" }); d.sync();
  let retryId;
  server.request = async (_url, init) => { if (init?.body) retryId = JSON.parse(init.body).client_message_id; throw new Error("network lost"); };
  await assert.rejects(a.c.send(), /network lost/); assert.equal(d.busy(), false);
  server.request = async (_url, init) => {
    if (!init?.body) return { ok: true, json: async () => ({}) };
    assert.equal(JSON.parse(init.body).client_message_id, retryId);
    return { ok: true, status: 201, json: async () => ({}) };
  };
  await a.c.send(); assert.deepEqual(d.read().refs, []);
  assert.equal(b.root.querySelector("#cd-input").value, "");
  a.c.destroy(); b.c.destroy();
});

test("HTTP rejection retains the mounted draft and a changed retry receives a new ID", async () => {
  server.request = async () => ({ ok: true, json: async () => ({}) });
  const a = mount(); a.c.select("rejected", "s"); a.c.edit("old");
  let first;
  server.request = async (_url, init) => {
    first = JSON.parse(init.body);
    return { ok: false, status: 409, json: async () => ({ error: { message: "conflict" } }) };
  };
  await assert.rejects(a.c.send(), /conflict/);
  assert.equal(a.root.querySelector("#chat-input").value, "old");
  a.c.edit("changed");
  server.request = async (_url, init) => {
    assert.notEqual(JSON.parse(init.body).client_message_id, first.client_message_id);
    return { ok: true, status: 201, json: async () => ({}) };
  };
  await a.c.send(); assert.equal(a.root.querySelector("#chat-input").value, ""); a.c.destroy();
});

test("capability warning follows the mentioned target, is conservative when unknown and absent only for verified support", () => {
  const p = { agents: { a: { images: true }, b: { supports_images: false } } };
  assert.equal(imageWarning("hello", settings(), p), "");
  assert.match(imageWarning("@b look", settings(), p), /not delivered.*@b/);
  assert.match(imageWarning("@missing look", settings(), p), /unverified/);
  assert.match(imageWarning("hi", settings(), null), /unverified/);
});

test("uploads block form sends; navigation and removed pending chips cannot insert refs into another session", async () => {
  globalThis.File ||= class File extends Blob { constructor(parts, name, opts) { super(parts, opts); this.name = name; } };
  globalThis.createImageBitmap = async () => ({ width: 20, height: 20, close() {} });
  globalThis.document = { createElement: () => ({ getContext: () => ({ drawImage() {} }), toBlob: (cb) => cb(new Blob(["jpeg"])) }) };
  const a = mount(); let resolveUpload, uploaded;
  server.request = async (url, init) => {
    if (!init) return { ok: true, json: async () => ({}) };
    uploaded = { url, init }; return new Promise((r) => { resolveUpload = r; });
  };
  a.c.select("upload", "original");
  const file = a.root.querySelector("#chat-attachments").querySelector("#chat-file");
  file.files = [{ name: "photo.png", type: "image/png" }];
  [...file.listeners.change][0](); await tick();
  assert.equal(await a.c.send(), false); assert.match(uploaded.url, /original\/attachments$/);
  assert.equal(uploaded.init.headers["X-CSRF-Token"], "csrf");
  a.c.select("upload", "other");
  assert.equal(file.listeners.change.size, 1, "session switch removes the old picker listener");
  resolveUpload({ ok: true, json: async () => ({ ref }) }); await tick();
  assert.deepEqual(sessionDraft("upload", "original").read().refs, [ref]);
  assert.deepEqual(sessionDraft("upload", "other").read().refs, []);
  assert.equal(a.root.querySelector("#chat-attachments").querySelector("[data-attach-chips]").innerHTML, "");
  a.c.select("upload", "original");
  assert.match(a.root.querySelector("#chat-attachments").querySelector("[data-attach-chips]").innerHTML, /photo/);
  file.files = [{ name: "removed.png", type: "image/png" }]; [...file.listeners.change][0](); await tick();
  const d = sessionDraft("upload", "original"); const chip = [...d.chips.values()].find((c) => c.status === "uploading");
  d.chips.delete(chip.id); d.sync(); resolveUpload({ ok: true, json: async () => ({ ref: "att_1111111111111111" }) }); await tick();
  assert.deepEqual(d.read().refs, [ref]); a.c.destroy(); assert.equal(file.listeners.change.size, 0);
});

test("mutation gate rejects upload/send and both production surfaces opt into the binding", async () => {
  server.request = async () => ({ ok: true, json: async () => ({}) });
  const a = mount(); a.c.select("readonly", "s"); a.c.edit("keep");
  server.session.mutations_enabled = false; a.c.select("readonly", "s");
  assert.equal(await a.c.send(), false); assert.equal(sessionDraft("readonly", "s").read().text, "keep");
  a.c.destroy(); server.session.mutations_enabled = true;
  for (const name of ["chat_view", "chat_dock"]) {
    const source = readFileSync(new URL(`../../src/alfrd/web/js/components/${name}.js`, import.meta.url), "utf8");
    assert.match(source, /attachments: true/); assert.match(source, /mountComposerDraft/); assert.match(source, /await draft.send\(\)/);
    assert.doesNotMatch(source, /drafts: new Map/);
  }
});
