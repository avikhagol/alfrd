import test from "node:test";
import assert from "node:assert/strict";

const A = await import("../../src/alfrd/web/js/components/attach_tray.js");

test("downscaleImage limits the long edge and re-encodes as JPEG", async () => {
  const oldDocument = globalThis.document;
  const oldBitmap = globalThis.createImageBitmap;
  const oldFile = globalThis.File;
  globalThis.File ||= class File extends Blob { constructor(parts, name, opts) { super(parts, opts); this.name = name; } };
  globalThis.createImageBitmap = async () => ({ width: 4000, height: 1000, close() {} });
  let drawn;
  globalThis.document = { createElement: () => ({ width: 0, height: 0, getContext: () => ({ drawImage: (...args) => { drawn = args; } }), toBlob: (done, type) => done(new Blob(["jpeg"], { type })) }) };
  const out = await A.downscaleImage({ type: "image/png", name: "photo.png" });
  assert.equal(out.type, "image/jpeg");
  assert.equal(out.name, "photo.jpg");
  assert.deepEqual(drawn.slice(1), [0, 0, 2000, 500]);
  globalThis.document = oldDocument; globalThis.createImageBitmap = oldBitmap;
  if (oldFile) globalThis.File = oldFile;
});

test("downscaleImage rejects unsupported formats", async () => {
  await assert.rejects(() => A.downscaleImage({ type: "image/gif", name: "x.gif" }), /PNG, JPEG, or WebP/);
});

test("tray HTML exposes hidden picker and state hooks without an extra helper row", () => {
  const html = A.attachmentTrayHtml("chat");
  assert.match(html, /type="file"/);
  assert.match(html, /data-attach-chips/);
  assert.match(html, /multiple hidden/);
  assert.doesNotMatch(html, /data-attach-hint|attach-actions/);
  assert.doesNotMatch(html, /2000 px/);
  const button = A.attachmentButtonHtml("chat");
  assert.match(button, /<button type="button"/);
  assert.match(button, /title="Attach images" aria-label="Attach images"/);
});

test("mixed text and image paste keeps native text and queues the image", async () => {
  let prevented = false; const added = [];
  const input = { addEventListener: (kind, fn) => { input.handler = fn; }, removeEventListener() {} };
  A.bindAttachmentPaste(input, { add: (file) => added.push(file) });
  const image = { type: "image/png", name: "clip.png" };
  input.handler({ clipboardData: { items: [
    { kind: "string", getAsFile: () => null },
    { kind: "file", getAsFile: () => image },
  ] }, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, false);
  assert.deepEqual(added, [image]);
});

test("image-only paste prevents the browser's default and queues the image", () => {
  let prevented = false; const added = [];
  const input = { addEventListener: (kind, fn) => { input.handler = fn; }, removeEventListener() {} };
  A.bindAttachmentPaste(input, { add: (file) => added.push(file) });
  const image = { type: "image/jpeg", name: "clip.jpg" };
  input.handler({ clipboardData: { items: [{ kind: "file", getAsFile: () => image }] }, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(added[0], image);
});

class El {
  constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {}; this.dataset = {}; this.attrs = {}; this._html = ""; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((x) => x !== fn); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  querySelector(selector) { return this.children.find((child) => child.matches?.(selector)) || null; }
  matches(selector) { return selector === "[data-attach-chips]" ? this.dataset.attachChips !== undefined : selector === "[data-attach-reason]" ? this.dataset.attachReason !== undefined : selector === "#chat-file" ? this.id === "chat-file" : selector === "#chat-attachments" ? this.id === "chat-attachments" : false; }
  set innerHTML(value) { this._html = value; }
  get innerHTML() { return this._html; }
}

function trayDom() {
  const tray = new El(); tray.id = "chat-attachments";
  const chips = new El(); chips.dataset.attachChips = "";
  const reason = new El(); reason.dataset.attachReason = "";
  const input = new El("input"); input.id = "chat-file";
  tray.children = [chips, reason, input];
  return { root: { querySelector: (selector) => selector === "#chat-attachments" ? tray : null }, tray, chips, reason, input };
}

test("upload disables Send with a reason, then exposes Retry and Remove", async () => {
  const oldDocument = globalThis.document;
  globalThis.document = { createElement: () => ({ width: 2000, height: 1000, getContext: () => ({ drawImage() {} }), toBlob: (done, type) => done(new Blob(["jpeg"], { type })) }) };
  globalThis.createImageBitmap = async () => ({ width: 2000, height: 1000, close() {} });
  const { root, tray, chips, reason, input } = trayDom();
  const send = new El("button");
  let resolveFetch, request;
  const fetchImpl = (url, init) => new Promise((resolve) => { request = { url, init }; resolveFetch = resolve; });
  const mounted = A.mountAttachmentTray(root, { sendButton: send, fetchImpl, csrfToken: "csrf" });
  const file = { type: "image/png", name: "photo.png" };
  const pending = mounted.add(file);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(request.url, "S/attachments");
  assert.equal(request.init.method, "POST");
  assert.equal(request.init.headers["X-CSRF-Token"], "csrf");
  assert.equal(send.disabled, true);
  assert.match(reason.textContent || reason.innerHTML, /Wait for image uploads/);
  assert.match(chips.innerHTML, /Uploading/);
  resolveFetch({ ok: false, status: 503, statusText: "Unavailable", json: async () => ({ error: "offline" }) });
  await pending;
  assert.equal(send.disabled, false);
  assert.match(chips.innerHTML, /Retry/);
  assert.match(chips.innerHTML, /Upload failed/);
  const id = mounted.getState()[0].id;
  tray.listeners.click[0]({ target: { closest: (selector) => selector === "[data-attach-remove]" ? { dataset: { attachRemove: id } } : null } });
  assert.equal(mounted.getState().length, 0);
  mounted.destroy();
  globalThis.document = oldDocument;
});
