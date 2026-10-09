// Live updates: event sequencing, folder fingerprints, log text helpers (run with `node --test`).
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src/alfrd/web/js");

// Minimal browser globals: live.js listens for visibility changes; EventSource is faked.
const listeners = {};
globalThis.document = { hidden: false, addEventListener: (t, fn) => { (listeners[t] ||= []).push(fn); } };
globalThis.window = { addEventListener: () => {} };
class FakeES {
  static last = null;
  static CLOSED = 2;
  constructor(url) { this.url = url; this.handlers = {}; this.readyState = 1; FakeES.last = this; }
  addEventListener(t, fn) { this.handlers[t] = fn; }
  emit(t, data) { this.handlers[t]?.({ data: JSON.stringify(data) }); }
  close() { this.readyState = 2; this.closed = true; }
}
globalThis.EventSource = FakeES;

const { createLive } = await import(path.join(web, "data/live.js"));
const { diffFingerprints } = await import(path.join(web, "data/folder_scan.js"));
const { _internals } = await import(path.join(web, "components/logview.js"));

function harness(scanState = { ts: 100 }) {
  const calls = [];
  const live = createLive({
    mode: () => "server",
    projects: () => ["p"],
    folders: () => [],
    scanState: () => scanState,
    onTree: (p, d) => calls.push(["tree", p, d.changed, d.removed]),
    onLogs: (p, logs) => calls.push(["logs", p, Object.keys(logs)]),
    onRuntime: () => calls.push(["runtime"]),
    onAlfrd: (p, events) => calls.push(["alfrd", p, events]),
    onResync: (p) => calls.push(["resync", p]),
    onStatus: () => {},
  });
  live.start();
  return { live, calls, es: FakeES.last };
}

test("live: hello, in-order events, gaps and duplicates", () => {
  const { calls, es, live } = harness({ ts: 100 });
  assert.match(es.url, /\/api\/studio\/events\?projects=p$/);
  es.emit("hello", { state: { p: { epoch: "e1", version: 0, baseline_ts: 99 }, "@runtime": { epoch: "r", version: 0 } }, interval: 2, idle: 5 });
  assert.deepEqual(calls, []); // our scan is newer than the baseline: nothing to re-read
  assert.equal(live.status.state, "live");
  es.emit("tree", { type: "tree", key: "p", epoch: "e1", version: 1, changed: ["a.avica"], removed: [], logs: { "x.log": [10, 1] } });
  es.emit("tree", { type: "tree", key: "p", epoch: "e1", version: 1, changed: ["a.avica"], removed: [], logs: {} }); // duplicate
  es.emit("tree", { type: "tree", key: "p", epoch: "e1", version: 3, changed: ["b"], removed: [], logs: {} }); // missed 2
  es.emit("runtime", { type: "runtime", key: "@runtime", epoch: "r", version: 1 });
  assert.deepEqual(calls, [["tree", "p", ["a.avica"], []], ["logs", "p", ["x.log"]], ["resync", "p"], ["runtime"]]);
  // Reconnect: hello with a newer version → re-read; same version → nothing.
  calls.length = 0;
  es.emit("hello", { state: { p: { epoch: "e1", version: 5, baseline_ts: 99 }, "@runtime": { epoch: "r", version: 1 } } });
  es.emit("hello", { state: { p: { epoch: "e1", version: 5, baseline_ts: 99 }, "@runtime": { epoch: "r", version: 1 } } });
  assert.deepEqual(calls, [["resync", "p"]]);
});

test("live: stale first scan re-reads, hidden tab closes the stream", () => {
  const { calls, es, live } = harness({ ts: 50 });
  es.emit("hello", { state: { p: { epoch: "e", version: 0, baseline_ts: 60 } } });
  assert.deepEqual(calls, [["resync", "p"]]);
  document.hidden = true;
  listeners.visibilitychange.forEach((fn) => fn());
  assert.ok(es.closed);
  assert.equal(live.status.state, "paused");
  document.hidden = false;
  live.stop();
  assert.equal(live.status.state, "off");
});

test("live: a scan that carries the watcher version re-reads only if the watcher moved on", () => {
  let h = harness({ ts: 1, epoch: "e", version: 4 });
  h.es.emit("hello", { state: { p: { epoch: "e", version: 4, baseline_ts: 999 } } });
  assert.deepEqual(h.calls, []);
  h.live.stop();
  h = harness({ ts: 1, epoch: "e", version: 4 });
  h.es.emit("hello", { state: { p: { epoch: "e", version: 5, baseline_ts: 0 } } });
  assert.deepEqual(h.calls, [["resync", "p"]]);
  h.live.stop();
});

test("live: stream errors fall back to polling", () => {
  const { es, live } = harness();
  globalThis.fetch = async () => ({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ state: {}, events: [], reset: [] }) });
  es.onerror();
  assert.ok(es.closed, "close automatic reconnects so polling can inspect the HTTP status");
  assert.equal(live.status.state, "polling");
  live.stop();
});

test("folder fingerprints: content changes vs log growth", () => {
  const old = { "alfrd.yaml": "content:10:1", "a.log": "log:5:1", "gone.avica": "content:1:1", "wd/wd_S/.dir": "marker" };
  const next = { "alfrd.yaml": "content:11:2", "a.log": "log:9:2000", "new.log": "log:1:1", "wd/wd_S/.dir": "marker" };
  assert.deepEqual(diffFingerprints(old, next), { changed: ["alfrd.yaml", "new.log"], removed: ["gone.avica"], logs: { "a.log": [9, 2] } });
});

test("log text: carriage-return progress lines collapse, long text trims at a line", () => {
  assert.equal(_internals.clean("a\r\nstep 1%\rstep 50%\rstep 100%\nb"), "a\nstep 100%\nb");
  assert.equal(_internals.clean("plain\n"), "plain\n");
  const long = Array.from({ length: 80000 }, (_, i) => `line ${i}`).join("\n");
  const cut = _internals.trim(long);
  assert.ok(cut.length <= 400000 && cut.startsWith("line ") && cut.endsWith("line 79999"));
});


test("live: alfrd SSE shares the project cursor and duplicates do not toast twice", () => {
  const { calls, es, live } = harness();
  es.emit("hello", { state: { p: { epoch: "e", version: 0, baseline_ts: 99 } } });
  const event = { type: "alfrd", key: "p", epoch: "e", version: 1, events: [{ kind: "review.pending" }] };
  es.emit("alfrd", event); es.emit("alfrd", event);
  assert.deepEqual(calls, [["alfrd", "p", event.events]]);
  live.stop();
});


test("live: enabled browser notifications can keep the server stream in a hidden tab", () => {
  document.hidden = true;
  const live = createLive({ mode: () => "server", projects: () => ["p"], watchHidden: () => true });
  live.start(); assert.ok(live.es); assert.equal(live.es.closed, undefined);
  const stream = live.es;
  listeners.visibilitychange.at(-1)(); assert.equal(live.es, stream); assert.equal(stream.closed, undefined);
  live.stop(); document.hidden = false;
});

test("plugin jobs arrive without project subscriptions and reach the hook and Studio event", () => {
  const received = [], dispatched = [];
  globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  window.dispatchEvent = e => { dispatched.push(e); };
  const live = createLive({ mode: () => "server", projects: () => [], onPluginJob: job => received.push(job) });
  live.start();
  const job = { id: "plugin-1", status: "running" };
  FakeES.last.emit("plugin_job", { type: "plugin_job", job });
  assert.deepEqual(received, [job]);
  assert.equal(dispatched[0].type, "plugin-job"); assert.deepEqual(dispatched[0].detail, job);
  live.stop();
});
