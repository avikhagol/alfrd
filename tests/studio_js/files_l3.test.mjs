import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.document = { querySelector: () => ({}), head: { append() {} }, createElement: () => ({}) };
const { filesHtml, blobAdapter, mountFiles, openBlob, rawBlob } = await import('../../src/alfrd/web/js/components/files_tab.js');
const registry = await import('../../src/alfrd/web/js/components/viewers.js');
const { server } = await import('../../src/alfrd/web/js/data/server.js');
const tick = () => new Promise(r => setImmediate(r));
const entry = (state = 'recorded') => ({ path: 'out/<x>.txt', absolute_path: '/project/out/<x>.txt', ref: 'out/<x>.txt@original', current_ref: 'out/<x>.txt@current', producer_rows: ['r1.a', 'r1.b'], state });
function fixture(options = {}) {
  const status = {}, host = { isConnected: true, innerHTML: '', contains: () => true, querySelector: () => status };
  const seen = [];
  const controller = mountFiles(host, { getOverview: async () => ({ files: [entry()] }), isCurrent: () => true,
    raw: async ref => { seen.push(ref); return new Blob(['checked']); }, view: async (path, blob) => seen.push(await blob.text()), ...options });
  const click = async (action) => {
    const button = { dataset: { [action]: '0' }, disabled: false };
    await host.onclick({ target: { closest: () => button } });
  };
  return { host, status, seen, controller, click };
}

test('L3 entry fields, escaping, all artifact states and F2 empty', () => {
  const html = filesHtml([entry(), entry('changed'), entry('missing'), entry('expected')]);
  assert.match(html, /out\/&lt;x&gt;.txt/); assert.doesNotMatch(html, /<x>/);
  for (const text of ['r1.a, r1.b', 'recorded', 'changed', 'missing', 'expected', 'Copy path', 'View current', 'Keep recorded']) assert.ok(html.includes(text));
  assert.equal((html.match(/data-file-view=/g) || []).length, 1);
  assert.match(filesHtml([]), /state-empty/);
  assert.doesNotMatch(html, /Reveal in explorer|data-file-reveal/);
});

test('L3 checked blobs dispatch image, PDF and text through registry; cleanup', async () => {
  const revoked = [], urls = { createObjectURL: () => 'blob:checked', revokeObjectURL: url => revoked.push(url) };
  for (const [path, mime, id] of [['a.png', 'image/png', 'image'], ['a.svg', 'image/svg+xml', 'image'], ['a.ico', 'image/vnd.microsoft.icon', 'image'], ['a.pdf', 'application/pdf', 'pdf'], ['a.zip', 'application/zip', 'binary'], ['unknown', '', 'binary'], ['a.txt', 'text/plain', 'text']]) {
    const a = await blobAdapter(path, new Blob([path === 'unknown' ? new Uint8Array([0, 255]) : '<script>checked</script>'], { type: mime }), registry, urls);
    assert.equal(a.viewer.id, id); assert.equal(a.file.url, 'blob:checked');
    const host = { innerHTML: '', dataset: {} };
    await registry.renderViewer(a.viewer, a.file, host);
    if (id === 'text') { assert.match(host.innerHTML, /&lt;script&gt;/); assert.doesNotMatch(host.innerHTML, /<script>/); }
    else if (id !== 'binary') assert.match(host.innerHTML, /blob:checked/);
    else assert.equal(a.file.text, '');
    a.release();
  }
  assert.equal(revoked.length, 7);
});

test('L3 changed choice never reads or replaces recorded ref silently', async () => {
  const original = entry('changed');
  const f = fixture({ getOverview: async () => ({ files: [original] }) }); await tick();
  assert.deepEqual(f.seen, []);
  await f.click('fileKeep'); assert.deepEqual(f.seen, []); assert.match(f.status.textContent, /no file opened/);
  await f.click('fileCurrent'); assert.deepEqual(f.seen, [original.current_ref, 'checked']);
  assert.equal(original.ref, 'out/<x>.txt@original');
});

test('L3 raw 409 offers choice and does not retry with current bytes', async () => {
  let reads = 0;
  const f = fixture({ raw: async () => { reads++; throw Object.assign(new Error('changed'), { status: 409 }); } }); await tick();
  await f.click('fileView'); assert.equal(reads, 1); assert.deepEqual(f.seen, []);
  assert.match(f.host.innerHTML, /View current/); assert.match(f.status.innerHTML, /Changed elsewhere/);
  await f.click('fileKeep'); assert.equal(reads, 1);
});

test('L3 absolute copy, scope, retry and disposal guard installed handlers', async () => {
  const f = fixture({ copy: async p => p === entry().absolute_path }); await tick();
  await f.click('fileCopy'); assert.equal(f.status.textContent, 'Copied path');
  const scoped = fixture({ rows: ['other'] }); await tick(); assert.match(scoped.host.innerHTML, /No files yet/);
  let resolve;
  const stale = fixture({ getOverview: () => new Promise(r => { resolve = r; }) });
  assert.match(stale.host.innerHTML, /state-loading/); stale.controller.destroy();
  resolve({ files: [entry()] }); await tick(); assert.match(stale.host.innerHTML, /state-loading/); assert.equal(stale.host.onclick, null);
  let calls = 0;
  const retry = fixture({ getOverview: async () => { if (!calls++) throw { status: 403 }; return { files: [] }; } }); await tick();
  assert.match(retry.host.innerHTML, /state-error/); await retry.click('stateRetry'); await tick(); assert.match(retry.host.innerHTML, /state-empty/);
});

test('L3 raw adapter preserves status, authenticated wrapper and immutable ref', async () => {
  const old = server.request; let requested;
  try {
    server.request = async (url, init) => { requested = [url, init]; return { ok: false, status: 409 }; };
    await assert.rejects(rawBlob('/session', 'a@old'), { status: 409 });
    assert.match(requested[0], /ref=a%40old/); assert.equal(requested[1].cache, 'no-store');
  } finally { server.request = old; }
});

test('L3 modal blob URL revoked on close and stale open cannot show modal', async () => {
  const oldObserver = globalThis.MutationObserver;
  globalThis.MutationObserver = class { observe() {} disconnect() {} };
  let release, modal = 0;
  const ctx = { modal(html, setup) { modal++; setup({ isConnected: true, addEventListener(type, fn) { release = fn; }, querySelector: () => ({ dataset: {}, innerHTML: '' }) }); } };
  try {
    await openBlob(ctx, 'a.txt', new Blob(['hello']), () => false); assert.equal(modal, 0);
    await openBlob(ctx, 'a.txt', new Blob(['hello'])); assert.equal(modal, 1); release();
  } finally { globalThis.MutationObserver = oldObserver; }
});

test('L3 leaving Files twice preserves another tabs installed handler', async () => {
  const { drawFiles, leaveFiles } = await import('../../src/alfrd/web/js/components/files_tab.js');
  const host = { isConnected: true, innerHTML: '', querySelector: () => ({}) };
  drawFiles(host, { key: 'one', getOverview: async () => ({ files: [] }), isCurrent: () => true });
  await tick(); leaveFiles(); const goalsRetry = () => {}; host.onclick = goalsRetry;
  leaveFiles(); assert.equal(host.onclick, goalsRetry);
});

test('L3 pending raw bytes cannot open after session or tab disposal', async () => {
  let resolve;
  const f = fixture({ raw: () => new Promise(r => { resolve = r; }) }); await tick();
  const pending = f.click('fileView'); f.controller.destroy(); resolve(new Blob(['old session']));
  await pending; assert.deepEqual(f.seen, []);
});


test('binary and image blobs never request text decoding; raw modal action is isolated', async () => {
  const urls = { createObjectURL: () => 'blob:preview', revokeObjectURL() {} };
  for (const [path, type, id] of [['icon.svg', 'image/svg+xml', 'image'], ['file.zip', 'application/zip', 'binary']]) {
    const blob = { type, text() { throw Error('must not decode'); }, arrayBuffer() { throw Error('must not decode'); } };
    const adapted = await blobAdapter(path, blob, registry, urls);
    assert.equal(adapted.viewer.id, id); assert.equal(adapted.file.text, ''); adapted.release();
  }
  const oldObserver = globalThis.MutationObserver;
  globalThis.MutationObserver = class { observe() {} disconnect() {} };
  try {
    await openBlob({ modal(html, setup) {
      assert.match(html, /Open in new tab/); assert.match(html, /target="_blank" rel="noopener noreferrer"/);
      assert.match(html, /href="\/raw\?ref=icon.svg%40hash&amp;inline=1"/);
      setup({ isConnected: true, addEventListener() {}, querySelector: () => ({ dataset: {}, innerHTML: '' }) });
    } }, 'icon.svg', new Blob(['<svg/>'], { type: 'image/svg+xml' }), () => true, '/raw?ref=icon.svg%40hash&inline=1');
  } finally { globalThis.MutationObserver = oldObserver; }
});

test('copy refuses legacy relative paths rather than putting one on the clipboard', async () => {
  let copies = 0;
  const f = fixture({ getOverview: async () => ({ files: [{ ...entry(), absolute_path: undefined }] }), copy: () => { copies++; } });
  await tick(); await f.click('fileCopy'); assert.equal(copies, 0);
  assert.match(f.status.textContent, /Absolute file path unavailable/);
});
