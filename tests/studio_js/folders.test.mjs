// Server folder browser helpers and docked-log (Log Stream tab) state (run with `node --test`).
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/alfrd/web/js");
globalThis.document ||= { hidden: false, addEventListener: () => {}, querySelectorAll: () => [] };

const { pathCrumbs, projectEntries } = await import(path.join(web, "data/paths.js"));
const logview = await import(path.join(web, "components/logview.js"));

test("pathCrumbs: POSIX, Windows drive and UNC paths", () => {
  assert.deepEqual(pathCrumbs("/home/avi/data_reductions"), [
    { name: "/", path: "/" },
    { name: "home", path: "/home" },
    { name: "avi", path: "/home/avi" },
    { name: "data_reductions", path: "/home/avi/data_reductions" },
  ]);
  assert.deepEqual(pathCrumbs("/"), [{ name: "/", path: "/" }]);
  assert.deepEqual(pathCrumbs("/a//b/"), [{ name: "/", path: "/" }, { name: "a", path: "/a" }, { name: "b", path: "/a/b" }]);
  assert.deepEqual(pathCrumbs("C:\\Users\\avi").map((c) => c.path), ["C:\\", "C:\\Users", "C:\\Users\\avi"]);
  assert.deepEqual(pathCrumbs("D:/x").map((c) => c.name), ["D:\\", "x"]);
  assert.deepEqual(pathCrumbs("\\\\srv\\share\\obs").map((c) => c.path), ["\\\\srv\\share\\", "\\\\srv\\share\\obs"]);
  assert.deepEqual(pathCrumbs(""), []);
});

test("projectEntries: project rows only, never MS folders", () => {
  const listing = { entries: [
    { name: "alma", path: "/d/alma", is_project: true, is_ms: false },
    { name: "obs.ms", path: "/d/obs.ms", is_project: true, is_ms: true },
    { name: "raw", path: "/d/raw", is_project: false, is_ms: false },
  ] };
  assert.deepEqual(projectEntries(listing).map((e) => e.name), ["alma"]);
  assert.deepEqual(projectEntries(null), []);
});

test("docked logs: tab order, de-duplication, cap and undock", () => {
  const shown = [];
  const ctx = { showDock: (key) => shown.push(key) };
  const { docked, MAX_DOCKED } = logview._internals;
  docked.length = 0;
  const k1 = logview.dockLog(ctx, "host.proj.a", "avica.logs/run.log");
  logview.dockLog(ctx, "host.proj.a", "avica.logs/run.log");
  assert.equal(k1, "host.proj.a::avica.logs/run.log");
  assert.deepEqual(logview.dockedLogs().map((d) => [d.project, d.rel, d.name]), [["host.proj.a", "avica.logs/run.log", "run.log"]]);
  assert.deepEqual(shown, [k1, k1]);
  // At the cap a followed log is never evicted silently: the user is asked to close a tab.
  const warned = [];
  ctx.toast = (text, tone) => warned.push([text, tone]);
  for (let i = 0; i < MAX_DOCKED + 2; i += 1) logview.dockLog(ctx, "p", `wd/l${i}.log`);
  assert.equal(logview.dockedLogs().length, MAX_DOCKED);
  assert.equal(logview.dockedLogs()[0].key, k1);
  assert.equal(logview.dockLog(ctx, "p", "wd/extra.log"), null);
  assert.ok(warned.length >= 1 && warned.every(([text, tone]) => tone === "warn" && /Close a tab/.test(text)));
  logview.undockLog(k1);
  assert.equal(logview.dockedLogs()[0].rel, "wd/l0.log");
  assert.equal(logview.dockLog(ctx, "p", "wd/extra.log"), "p::wd/extra.log");
  assert.deepEqual(logview.splitKey("a.b::c::d.log"), { project: "a.b", rel: "c::d.log" });
});

test("logview: an ANSI escape split across tail reads is stripped once complete", () => {
  const { stripAnsi, takeChunk } = logview._internals;
  const c = { pending: "" };
  const first = stripAnsi(takeChunk(c, "a\x1b[1"));
  const second = stripAnsi(takeChunk(c, "mb\x1b[0m"));
  assert.equal(first + second, "ab");
  assert.equal(c.pending, "");
  assert.equal(stripAnsi(takeChunk(c, "x\x1b")), "x");
  assert.equal(stripAnsi(takeChunk(c, "[32mgreen\x1b]0;title")), "green");
  assert.equal(stripAnsi(takeChunk(c, "\x07!")), "!");
});
