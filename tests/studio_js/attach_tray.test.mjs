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
  assert.match(button, /title="Attach images or text files" aria-label="Attach images or text files"/);
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
  assert.match(reason.textContent || reason.innerHTML, /Wait for attachments/);
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

const T = await import("../../src/alfrd/web/js/data/text_attachments.js");
test("long paste boundaries, CRLF lines and Shift paste escape hatch", () => {
  assert.deepEqual(T.LONG_PASTE_THRESHOLD, { characters: 2000, lines: 40 });
  assert.equal(T.longPaste("x".repeat(2000)), false);
  assert.equal(T.longPaste("x".repeat(2001)), true);
  assert.equal(T.longPaste(Array(40).fill("x").join("\r\n")), false);
  assert.equal(T.longPaste(Array(41).fill("x").join("\r\n")), true);
  const handlers = {}, added = [];
  const input = { value: "unchanged", addEventListener: (k,f) => handlers[k] = f, removeEventListener() {} };
  A.bindAttachmentPaste(input, { addText: text => added.push(text) });
  let prevented = 0;
  const paste = text => handlers.paste({ clipboardData: { getData: () => text }, preventDefault: () => prevented++ });
  paste("small"); assert.equal(prevented, 0);
  paste("x".repeat(2001)); assert.equal(prevented, 1); assert.equal(input.value, "unchanged");
  for (const modifier of ["ctrlKey", "metaKey"]) {
    handlers.keydown({ key: "V", shiftKey: true, [modifier]: true }); paste("x".repeat(2001));
    assert.equal(prevented, 1); handlers.keyup();
  }
  assert.equal(added.length, 1);
});

test("text chips create, preview, restore, remove and reject oversize; picker and drop sniff UTF-8", async () => {
  const { root, tray, chips, input } = trayDom(); let restored, preview, error;
  const mounted = A.mountAttachmentTray(root, { onRestore: t => restored = t, onPreview: c => preview = c, onError: e => error = e.message });
  const first = mounted.addText("hello\nworld");
  assert.equal(first.name, "pasted-text-1.txt"); assert.equal(first.type, "text/plain");
  assert.match(chips.innerHTML, /11 B · 2 lines/);
  assert.equal(mounted.addText("again").name, "pasted-text-2.txt");
  const click = (attr, id) => tray.listeners.click[0]({ target: { closest: s => s === `[data-attach-${attr}]` ? { dataset: { ["attach" + attr[0].toUpperCase() + attr.slice(1)]: id } } : null } });
  click("preview", first.id); assert.equal(preview.text, "hello\nworld");
  click("restore", first.id); assert.equal(restored, "hello\nworld"); assert.equal(mounted.getState().length, 1);
  mounted.remove(mounted.getState()[0].id); assert.equal(mounted.getState().length, 0);
  assert.equal(mounted.addText("x".repeat(16385)), null); assert.match(error, /too big/);
  for (const name of ["a.txt", "a.md", "a.csv", "a.tsv", "a.json", "a.yaml", "a.yml", "a.log", "a.xml", "a.html", "a.py", "a.js", "a.ts", "a.sh", "unknown.dat"]) {
    const file = new Blob(["UTF-8 λ\ntext"]); file.name = name;
    input.files = [file]; input.listeners.change[0]();
    await new Promise(r => setTimeout(r, 0));
    assert.equal(mounted.getState().at(-1).name, name);
  }
  const file = new Blob(["dropped"]); file.name = "drop.md";
  tray.listeners.drop[0]({ dataTransfer: { files: [file] }, preventDefault() {} });
  await new Promise(r => setTimeout(r, 0)); assert.equal(mounted.getState().at(-1).text, "dropped");
  await mounted.add(new Blob([new Uint8Array([0xff, 0, 0x80])])); assert.match(error, /encoded|UTF-8|encoding/i);
  mounted.destroy();
});

test("draft cap and pending text reads cannot silently bypass Send or resurrect removed chips", async () => {
  const {root} = trayDom(); const errors = [];
  const send = new El('button');
  const mounted = A.mountAttachmentTray(root, { sendButton: send, onError: e => errors.push(e.message) });
  for (let n = 0; n < 4; n++) assert.ok(mounted.addText('x'.repeat(16384)));
  assert.equal(mounted.addText('x'), null); assert.match(errors.at(-1), /64 KiB/);
  for (const chip of mounted.getState()) mounted.remove(chip.id);
  let finish;
  const pending = mounted.add({ name: 'slow.txt', size: 4, arrayBuffer: () => new Promise(r => finish = r) });
  assert.equal(send.disabled, true);
  mounted.remove(mounted.getState()[0].id);
  finish(new TextEncoder().encode('text').buffer); await pending;
  assert.equal(mounted.getState().length, 0); assert.equal(send.disabled, false);
  mounted.destroy();
});
