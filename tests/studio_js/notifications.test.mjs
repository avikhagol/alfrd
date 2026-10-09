import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";
const saved = new Map();
globalThis.localStorage = { getItem: (k) => saved.get(k) ?? null, setItem: (k, v) => saved.set(k, v) };
globalThis.document = { hidden: false, addEventListener() {} };
globalThis.window = { addEventListener() {}, focus() { this.focused = true; }, location: {} };
globalThis.isSecureContext = true;
globalThis.navigator ??= {}; // global only from Node 21; CI runs Node 20
let requests = 0;
class FakeNotification {
  static permission = "default";
  static sent = [];
  static async requestPermission() { requests++; return this.permission === "denied" ? "denied" : (this.permission = "granted"); } // a blocked site stays blocked
  constructor(title, options) { Object.assign(this, { title, options }); FakeNotification.sent.push(this); }
  close() { this.closed = true; }
}
globalThis.Notification = FakeNotification;
const { server } = await import("../../src/alfrd/web/js/data/server.js");
const { browserState, browserEnabled, setBrowserNotifications, handleNotifications, mountNotifications } = await import("../../src/alfrd/web/js/components/notifications.js");
const { parseRoute } = await import("../../src/alfrd/web/js/utils/dom.js");
const { loadPlan, openLinkedRun, forgetPlan } = await import("../../src/alfrd/web/js/components/plans.js");
const flush = () => new Promise((r) => setTimeout(r, 180));

test("mounting the toggle never requests permission; enabling does, denial stays off", async () => {
  const handlers = {}, toggle = { addEventListener: (kind, fn) => handlers[kind] = fn }, hint = { addEventListener() {} };
  const root = { querySelector: (sel) => ({ "[data-notify-browser]": toggle, "[data-notify-hint]": hint })[sel] };
  mountNotifications({ toast() {} }, root);
  assert.equal(requests, 0); assert.equal(toggle.checked, false);
  toggle.checked = true; await handlers.change(); await flush();
  assert.equal(requests, 1); assert.equal(browserEnabled(), true);
  FakeNotification.permission = "denied";
  assert.equal(await setBrowserNotifications(true), false); await flush();
  assert.equal(browserEnabled(), false); assert.equal(requests, 1);
  globalThis.isSecureContext = false; assert.equal(browserState(), "unsupported"); globalThis.isSecureContext = true;
});
test("blocked: the hint names the site and Ask again asks the browser once more", async () => {
  FakeNotification.permission = "denied";
  const toggle = { addEventListener() {} }, hintHandlers = {}, toasts = [];
  const hint = { addEventListener: (kind, fn) => hintHandlers[kind] = fn };
  const root = { querySelector: (sel) => ({ "[data-notify-browser]": toggle, "[data-notify-hint]": hint })[sel] };
  mountNotifications({ toast: (...a) => toasts.push(a) }, root);
  assert.match(hint.innerHTML, /Blocked for <span class="mono">this site/);
  assert.match(hint.innerHTML, /data-notify-ask/);
  const before = requests;
  await hintHandlers.click({ target: { closest: (sel) => (sel === "[data-notify-ask]" ? {} : null) } });
  assert.equal(requests, before + 1);
  assert.equal(toasts.length, 1, "still blocked: says so");
  FakeNotification.permission = "default";
});
test("route matching, sticky toasts and browser click preserve the exact run/unit link", async () => {
  const original = server.notificationRoutes, toasts = [];
  FakeNotification.permission = "granted";
  await setBrowserNotifications(true); await flush();
  server.notificationRoutes = async () => ({ routes: [] });
  const event = { kind: "review.pending", target: "t-task", plan: "run", turn: 2, turns: 4, link: "/studio/#/workflow?project=p&plan=run&unit=u" };
  try {
    await handleNotifications({ toast: (...args) => toasts.push(args) }, "p", [event, { ...event, kind: "turn.started" }]);
    assert.equal(toasts.length, 1); assert.equal(toasts[0][2].sticky, true);
    const n = FakeNotification.sent.at(-1); assert.equal(n.options.tag, "run:review.pending"); n.onclick();
    assert.equal(window.focused, true); assert.equal(window.location.hash, "#/workflow?project=p&plan=run&unit=u"); assert.equal(n.closed, true);
    server.notificationRoutes = async () => ({ routes: [{ on: ["turn.*"] }] });
    await handleNotifications({ toast: (...args) => toasts.push(args) }, "p", [{ ...event, kind: "turn.started" }, event]);
    assert.equal(toasts.length, 2); assert.equal(toasts[1][2].sticky, false);
  } finally { server.notificationRoutes = original; }
});
test("D5 parse and in-flight deep link load wait for the explicit run", async () => {
  assert.deepEqual(parseRoute("#/workflow?project=p&plan=r&unit=u"), { view: "workflow", project: "p", target: null, plan: "r", unit: "u" });
  const ctx = { state: { mode: "server", trees: { p: { provider: "server" } } }, update() {}, log() {}, toast() { assert.fail("valid linked run must not show a missing-run toast"); } };
  const original = server.planStatus; let finish; const asked = [];
  server.planStatus = async (_, id) => { asked.push(id); if (!id) return new Promise((resolve) => { finish = () => resolve({ plan: { id: "latest", status: "finished" } }); }); return { plan: { id, status: "finished" } }; };
  try {
    const first = loadPlan(ctx, "p");
    const linked = openLinkedRun(ctx, "p", "r"); finish();
    await Promise.all([first, linked]); assert.deepEqual(asked, [null, "r"]);
  } finally { server.planStatus = original; forgetPlan("p"); }
});


test("app route selects the linked project before opening its run and deduplicates rendering", async () => {
  const app = readFileSync(new URL("../../src/alfrd/web/js/app.js", import.meta.url), "utf8");
  const source = app.slice(app.indexOf("function applyRunLink("), app.indexOf("async function boot()"));
  const state = { selectedProject: "old", view: "overview" }, calls = [];
  const sandbox = { state, parseRoute, linkedRoute: "", location: { hash: "#/workflow?project=p&plan=r&unit=u" },
    ctx: { projects: () => [{ id: "p" }], scopedTargets: () => [], toast() {} },
    switchProject: (p) => { calls.push(["project", p]); state.selectedProject = p; },
    chooseTarget: () => null, storage: { get: () => ({}) }, rememberTarget() {},
    VIEWS: [{ id: "overview", mod: {} }, { id: "workflow", mod: {} }], scheduleRender() {}, plansAvailable: () => true,
    openLinkedRun: async (_, ...args) => { assert.equal(state.selectedProject, "p"); calls.push(["run", ...args]); },
  };
  runInNewContext(`${source}; route(); route();`, sandbox);
  assert.deepEqual(calls, [["project", "p"], ["run", "p", "r", "u"]]);
  assert.equal(state.view, "workflow");
});


test("missing deep-linked runs show the existing message and refresh can return to default", async () => {
  const original = server.planStatus, asked = [], toasts = [];
  const ctx = { state: { mode: "server", trees: { missing: { provider: "server" } } }, update() {}, log() {}, toast: (m) => toasts.push(m) };
  server.planStatus = async (_, id) => {
    asked.push(id);
    if (id) throw Object.assign(new Error("missing"), { status: 404 });
    return { plan: { id: "default", status: "finished" } };
  };
  try {
    await openLinkedRun(ctx, "missing", "gone"); assert.match(toasts[0], /This run is no longer available/);
    await loadPlan(ctx, "missing"); assert.deepEqual(asked, ["gone", null]);
  } finally { server.planStatus = original; forgetPlan("missing"); }
});
