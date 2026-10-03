// Docked logs keep streaming while minimized or while another project is open (run with `node --test`).
import test from "node:test";
import assert from "node:assert/strict";

globalThis.document ||= { hidden: false, addEventListener() {}, querySelectorAll: () => [], getElementById: () => null };
const logview = await import("../../src/alfrd/web/js/components/logview.js");
const { cache, docked, tick, dockFollow } = logview._internals;

function reader(chunks) {
  let offset = 0;
  return async (project, rel, from) => {
    if (from == null) return { text: "line 1\n", offset: (offset = 7), live: true };
    const text = chunks.shift() || "";
    offset += text.length;
    return { text, offset, live: true };
  };
}

async function settle(ctx) {
  for (let i = 0; i < 3; i += 1) {
    await tick(ctx, { querySelectorAll: () => [] });
    for (const c of cache.values()) c.nextAt = 0;
    await new Promise((r) => setTimeout(r, 0));
  }
}

test("a docked log keeps following with the panel hidden (no <pre> on screen)", async () => {
  docked.length = 0; cache.clear(); dockFollow.clear();
  const ctx = { readLogRange: reader(["line 2\n", "line 3\n"]), showDock() {} };
  const key = logview.dockLog(ctx, "p1", ".alfrd/plans/r1/turn.log");
  let changes = 0;
  logview.onDock(() => { changes += 1; });
  await settle(ctx);
  const info = logview.dockInfo(key);
  assert.equal(info.loaded, true);
  assert.equal(info.last, "line 3", "the minimized preview shows the newest line");
  assert.equal(info.follow, true);
  assert.ok(changes >= 1);
});

test("pause keeps receiving output and counts new lines; resume clears the counter", async () => {
  docked.length = 0; cache.clear(); dockFollow.clear();
  const ctx = { readLogRange: reader(["a\nb\n", "c\n"]), showDock() {} };
  const key = logview.dockLog(ctx, "p2", "run.log");
  logview.setDockFollow(key, false); // paused before any new output arrives
  await settle(ctx);
  await settle(ctx);
  const paused = logview.dockInfo(key);
  assert.equal(paused.follow, false);
  assert.equal(paused.last, "c", "output still accumulates while paused");
  assert.equal(paused.unread, 3);
  logview.setDockFollow(key, true);
  assert.equal(logview.dockInfo(key).unread, 0);
});

test("a disconnect shows Reconnecting and keeps the received output", async () => {
  docked.length = 0; cache.clear(); dockFollow.clear();
  let fail = false;
  const base = reader(["more\n"]);
  const ctx = { showDock() {}, readLogRange: async (...a) => { if (fail) throw Object.assign(new Error("offline"), { status: 0 }); return base(...a); } };
  const key = logview.dockLog(ctx, "p1", "x.log");
  await settle(ctx);
  fail = true;
  await settle(ctx);
  const info = logview.dockInfo(key);
  assert.equal(info.reconnecting, true);
  assert.equal(info.last, "more");
  fail = false;
  for (const c of cache.values()) c.nextAt = 0;
  await settle(ctx);
  assert.equal(logview.dockInfo(key).reconnecting, false);
});

test("closing a dock tab forgets its follow state but never touches other projects' tabs", () => {
  docked.length = 0; dockFollow.clear();
  const ctx = { showDock() {} };
  const a = logview.dockLog(ctx, "p1", "a.log");
  const b = logview.dockLog(ctx, "p2", "b.log");
  logview.setDockFollow(a, false);
  logview.undockLog(a);
  assert.deepEqual(logview.dockedLogs().map((d) => d.key), [b]);
  assert.equal(logview.dockInfo(a).follow, true, "a re-docked log starts following again");
});
