import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

// The final spec freezes these default-layout fragments at a010e7f.
// Interaction coverage for both layouts lives in the companion browser test.
const baseline = {"default template": "46fa24b292931b471c57df90d49167e6a67dcef52b6dedffb0a557b966699413", "decision renderer": "30d07576d30130b5606d5b648633a79aaf6853136f9cfe20bf8a03f535c74705", "task renderer": "aae5cd8f14feb53004fd0929d95419bb4e59f8c1aabd1f58fa85c0765e6c7e05"};
test('harness opt-in preserves the default template and shared decision/task renderers verbatim', async () => {
  const source = await readFile(new URL('../../src/alfrd/web/js/components/chat_view.js', import.meta.url), 'utf8');
  const start = source.indexOf('`<div class="chat${folded');
  const fragments = {
    'default template': source.slice(start, source.indexOf('`;', start) + 1),
    'decision renderer': source.slice(source.indexOf('function drawDecisions()'), source.indexOf('function drawTask(ctx)')).trim(),
    'task renderer': source.slice(source.indexOf('function drawTask(ctx)'), source.indexOf('// --- actions')).trim(),
  };
  for (const [name, fragment] of Object.entries(fragments)) assert.equal(createHash('sha256').update(fragment).digest('hex'), baseline[name], name);
});
