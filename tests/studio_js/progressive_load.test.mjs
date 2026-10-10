import test from "node:test";
import assert from "node:assert/strict";
import { server, createProjectLoader, projectLoadInfo } from "../../src/alfrd/web/js/data/server.js";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
// Drain promise continuations after releasing a response; no duration threshold.
const drain = () => new Promise(setImmediate);
const projects = (...names) => names.map((name) => ({ name, title: name }));

test.beforeEach(() => { server.authRequired = false; server.session = null; });

test("catalog loads without runtime requests and preserves Settings' raw hidden projects", async (t) => {
  const all = [{ identifier: "a", name: "same", display_name: "A", root_path: "/a" }, { identifier: "b", name: "same" }, { name: "legacy" }];
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => { calls.push(url); return Response.json({ projects: all }); });
  server.session = { projects: ["a", "legacy"] };
  assert.deepEqual(await server.listProjects(), all);
  const expected = [
    { ...all[0], manifest_name: "same", title: "A", name: "a" },
    { ...all[2], manifest_name: "legacy", title: "legacy" },
  ];
  assert.deepEqual(await server.listProjects({ scoped: true }), expected);
  assert.deepEqual(calls, ["/api/projects", "/api/projects"]);
  t.mock.method(server, "loadProjectRuntime", async (p) => ({ targets: [p.name], workflows: [p.name], messages: [p.name] }));
  assert.deepEqual(await server.loadAll(), { targets: ["a", "legacy"], workflows: ["a", "legacy"], messages: ["a", "legacy"], projects: expected, aliases: [] });
  server.session = { projects: [] };
  assert.deepEqual(await server.listProjects({ scoped: true }), []);
  server.session = null;
  assert.deepEqual((await server.listProjects({ scoped: true })).map((p) => p.name), ["a", "b", "legacy"]);
});

test("failed workflow list is distinct from a successful empty list", async (t) => {
  t.mock.method(globalThis, "fetch", async () => Response.json({ error: { message: "offline" } }, { status: 503 }));
  assert.deepEqual(await server.loadProjectRuntime({ name: "p", title: "P" }), {
    targets: [], workflows: [], messages: [{ level: "warn", text: "P: offline" }], ok: false, error: "offline", failed: [],
  });
  globalThis.fetch = async () => Response.json({ workflows: [] });
  assert.deepEqual(await server.loadProjectRuntime({ name: "p", title: "P" }), {
    targets: [], workflows: [], messages: [{ level: "info", text: "P: connected, but its manifest declares no runtime workflows." }], ok: true, failed: [],
  });
});

test("matrices use two slots and keep workflow, row and message order on reverse completion", async (t) => {
  const names = ["first", "second", "third", "fourth"];
  const pending = new Map(), calls = [];
  let active = 0, peak = 0;
  t.mock.method(globalThis, "fetch", (url) => {
    if (url.endsWith("/workflows")) return Promise.resolve(Response.json({ workflows: names.map((name) => ({ name, sequence: ["run"] })) }));
    const name = url.split("/").at(-2);
    const response = deferred();
    pending.set(name, response);
    calls.push(name);
    peak = Math.max(peak, ++active);
    return response.promise.finally(() => active--);
  });
  const done = server.loadProjectRuntime({ name: "p", title: "P" });
  await drain();
  assert.deepEqual(calls, ["first", "second"]);
  pending.get("second").resolve(Response.json({ error: { message: "broken" } }, { status: 503 }));
  await drain();
  assert.deepEqual(calls, ["first", "second", "third"]);
  pending.get("third").resolve(Response.json({ rows: [] }));
  await drain();
  assert.deepEqual(calls, names);
  pending.get("fourth").resolve(Response.json({ rows: [{ dataset_id: "4", cells: { run: { status: "queued", attempt: 2 } } }] }));
  pending.get("first").resolve(Response.json({ rows: [{ dataset_id: "1" }, { dataset_name: "two" }] }));
  const result = await done;
  assert.equal(peak, 2);
  assert.deepEqual(result.workflows.map((w) => w.name), names);
  assert.deepEqual(result.targets.map((row) => row.id), ["p/1", "p/two", "p/4"]);
  assert.equal(result.targets[2].steps.run.status, "queued");
  assert.equal(result.targets[2].steps.run.attempt, 2);
  assert.deepEqual(result.messages, [
    { level: "warn", text: "P/second: broken" },
    { level: "info", text: "P/third: no datasets yet — import results with `alfrd import avica-run` or start a run." },
  ]);
  assert.equal(result.ok, false);
  assert.deepEqual(result.failed, ["second"]);
});

function harness(onSettle) {
  const calls = [], pending = new Map();
  const request = (source) => (p) => {
    const d = deferred(), key = `${p.name}/${source}`;
    calls.push(key);
    if (!pending.has(key)) pending.set(key, []);
    pending.get(key).push(d);
    return d.promise;
  };
  const loader = createProjectLoader({ scan: request("scan"), runtime: request("runtime"), onSettle });
  const resolve = (name, source, value = { ok: true, targets: [name] }) => pending.get(`${name}/${source}`).shift().resolve(value);
  const reject = (name, source) => pending.get(`${name}/${source}`).shift().reject(new Error(`${name} unavailable`));
  const ready = (name) => { resolve(name, "scan"); resolve(name, "runtime"); };
  return { loader, calls, resolve, reject, ready };
}

test("five projects: selected first, two jobs, independent settlement, stable catalog result", async () => {
  const settlements = [];
  const h = harness((job, key) => settlements.push(`${job.project.name}/${key}`));
  const done = h.loader.reloadAll(projects("a", "b", "c", "d", "e"), "c");
  assert.deepEqual(h.calls, ["c/scan", "c/runtime", "a/scan", "a/runtime"]);
  assert.deepEqual(h.loader.jobs.map((j) => j.scan.status), ["loading", "queued", "loading", "queued", "queued"]);
  h.resolve("a", "scan");
  await drain();
  assert.deepEqual(settlements, ["a/scan"]);
  assert.equal(h.calls.length, 4); // runtime still holds the project slot
  h.ready("c");
  await drain();
  assert.deepEqual(h.calls.slice(4), ["b/scan", "b/runtime"]);
  h.ready("b");
  await drain();
  h.ready("d");
  await drain();
  h.ready("e");
  h.resolve("a", "runtime");
  const result = await done;
  assert.equal(result.cancelled, false);
  assert.deepEqual(result.jobs.map((j) => j.project.name), ["a", "b", "c", "d", "e"]);
  assert.deepEqual(result.jobs.flatMap((j) => j.scan.value.targets), ["a", "b", "c", "d", "e"]);
});

test("promotion uses the next free slot and never duplicates queued or running work", async () => {
  const h = harness();
  const done = h.loader.reloadAll(projects("a", "b", "c", "d", "e"), "a");
  h.loader.promote("e"); h.loader.promote("e"); h.loader.promote("a");
  h.ready("b");
  await drain();
  assert.deepEqual(h.calls.slice(4), ["e/scan", "e/runtime"]);
  h.loader.promote("e");
  h.ready("a");
  await drain();
  h.ready("c");
  await drain();
  h.ready("d"); h.ready("e");
  await done;
  assert.equal(h.calls.length, 10);
});

test("reload drops old results and retains the global two-job bound across generations", async () => {
  const seen = [];
  const h = harness((job, key, gen) => seen.push([job.project.name, key, gen]));
  const old = h.loader.reloadAll(projects("a", "b", "c"), "a");
  const oldGen = h.loader.generation;
  const done = h.loader.reloadAll(projects("d"), "d");
  assert.equal((await old).cancelled, true);
  assert.equal(h.loader.isCurrent(oldGen), false);
  assert.equal(h.calls.length, 4);
  h.ready("a");
  await drain();
  assert.deepEqual(h.calls.slice(4), ["d/scan", "d/runtime"]);
  h.ready("b"); h.ready("d");
  const result = await done;
  assert.deepEqual(seen, [["d", "scan", result.generation], ["d", "runtime", result.generation]]);
  assert.deepEqual(result.jobs.map((j) => j.project.name), ["d"]);
});

for (const source of ["scan", "runtime"]) {
  test(`retry runs only failed ${source}, preserving the successful source and preventing duplicate retry`, async () => {
    const h = harness();
    const done = h.loader.reloadAll(projects("a", "b"), "a");
    h.reject("a", source); h.resolve("a", source === "scan" ? "runtime" : "scan");
    h.ready("b");
    await done;
    const other = h.loader.jobs[0][source === "scan" ? "runtime" : "scan"].value;
    assert.equal(h.loader.retry("missing"), false);
    assert.equal(h.loader.retry("b"), false);
    assert.equal(h.loader.retry("a"), true);
    assert.equal(h.loader.retry("a"), false);
    assert.deepEqual(h.calls.slice(4), [`a/${source}`]);
    h.resolve("a", source);
    await drain();
    assert.equal(h.loader.jobs[0][source].status, "ready");
    assert.equal(h.loader.jobs[0][source === "scan" ? "runtime" : "scan"].value, other);
  });
}

test("partial runtime retains rows, settles failed, and can retry alongside queued initial projects", async () => {
  const h = harness();
  const done = h.loader.reloadAll(projects("a", "b", "c", "d"), "a");
  h.resolve("a", "scan");
  h.resolve("a", "runtime", { ok: false, failed: ["broken"], targets: ["usable"] });
  await drain(); // c now runs with b
  assert.equal(h.loader.jobs[0].runtime.status, "failed");
  assert.deepEqual(h.loader.jobs[0].runtime.value.targets, ["usable"]);
  assert.equal(h.loader.retry("a"), true); // queued retry ahead of d
  assert.equal(h.loader.retry("a"), false);
  h.ready("b");
  await drain();
  assert.deepEqual(h.calls.slice(6), ["a/runtime"]);
  h.resolve("a", "runtime");
  await drain();
  h.ready("c"); h.ready("d");
  await done;
});

test("invalidation for removal/auth drops responses, clears queue and resolves cancellation", async () => {
  const seen = [];
  const h = harness((job) => seen.push(job.project.name));
  const done = h.loader.reloadAll(projects("a", "b", "c"), "a");
  h.loader.invalidate();
  assert.equal((await done).cancelled, true);
  h.ready("a"); h.ready("b");
  await drain();
  assert.deepEqual(h.loader.jobs, []);
  assert.deepEqual(seen, []);
  assert.equal(h.calls.length, 4);
});

test("a rejected retry keeps previously received partial runtime data", async () => {
  const h = harness();
  const done = h.loader.reloadAll(projects("a"), "a");
  const partial = { ok: false, failed: ["broken"], targets: ["usable"] };
  h.resolve("a", "scan"); h.resolve("a", "runtime", partial);
  await done;
  assert.equal(h.loader.retry("a"), true);
  h.reject("a", "runtime");
  await drain();
  assert.equal(h.loader.jobs[0].runtime.status, "failed");
  assert.equal(h.loader.jobs[0].runtime.value, partial);
});

test("empty catalog settles immediately; async application holds a slot and hook errors settle failed", async () => {
  const applied = deferred();
  const h = harness(async (job, key) => {
    if (job.project.name === "a" && key === "scan") await applied.promise;
    if (job.project.name === "b" && key === "runtime") throw new Error("application failed");
  });
  assert.deepEqual((await h.loader.reloadAll([], "all")).jobs, []);
  const done = h.loader.reloadAll(projects("a", "b", "c"), "a");
  h.ready("a");
  await drain();
  assert.equal(h.calls.length, 4);
  assert.equal(projectLoadInfo(h.loader.jobs[0]).pending, true, "async application remains visibly pending");
  assert.equal(projectLoadInfo(h.loader.jobs[0]).badge, "Loading files");
  applied.resolve();
  await drain();
  assert.deepEqual(h.calls.slice(4), ["c/scan", "c/runtime"]);
  h.ready("b"); h.ready("c");
  const result = await done;
  assert.equal(result.jobs[1].runtime.status, "failed");
  assert.equal(result.jobs[1].runtime.error.message, "application failed");
});

test("async application can guard a generation that changes while its hook awaits", async () => {
  const gate = deferred(), writes = [];
  const h = harness(async (job, key, gen) => {
    if (job.project.name === "a" && key === "scan") await gate.promise;
    if (h.loader.isCurrent(gen)) writes.push(`${job.project.name}/${key}`);
  });
  const old = h.loader.reloadAll(projects("a", "b"), "a");
  h.resolve("a", "scan");
  await drain();
  const done = h.loader.reloadAll(projects("c"), "c");
  assert.equal((await old).cancelled, true);
  h.resolve("a", "runtime"); gate.resolve();
  await drain();
  h.ready("b"); h.ready("c");
  await done;
  assert.deepEqual(writes, ["c/scan", "c/runtime"]);
});

test("loading presentation distinguishes queued, partial, failed and retained data", () => {
  const job = {project: {name: "p"}, scan: {status: "queued"}, runtime: {status: "queued"}};
  assert.equal(projectLoadInfo(job).badge, "Waiting");
  job.scan.status = job.runtime.status = "loading";
  assert.equal(projectLoadInfo(job).badge, "Loading");
  job.scan = {status: "ready", value: {}};
  assert.equal(projectLoadInfo(job).badge, "Loading runtime");
  job.scan = {status: "loading"}; job.runtime = {status: "ready", value: {}};
  assert.equal(projectLoadInfo(job).badge, "Loading files");
  job.scan = {status: "failed", error: new Error("disk offline")};
  assert.equal(projectLoadInfo(job).badge, "Partly loaded");
  assert.match(projectLoadInfo(job).errors, /Project files: disk offline/);
  job.runtime = {status: "failed", error: "runtime offline"};
  assert.equal(projectLoadInfo(job).badge, "Could not load");
  job.retained = true;
  assert.equal(projectLoadInfo(job).badge, "Update failed");
  job.scan.status = "queued"; job.retrying = true;
  assert.equal(projectLoadInfo(job).badge, "Updating");
  assert.equal(projectLoadInfo(job).retrying, true);
  job.scan.status = job.runtime.status = "ready";
  assert.equal(projectLoadInfo(job).badge, "");
  assert.equal(projectLoadInfo(job).retrying, false);
});

test("status observers see job starts and completion without response-order assumptions", async () => {
  const scan = deferred(), runtime = deferred(), states = [];
  const loader = createProjectLoader({ scan: () => scan.promise, runtime: () => runtime.promise,
    onChange: () => states.push(loader.jobs.map((j) => [j.running, j.scan.status, j.runtime.status])) });
  const done = loader.reloadAll(projects("a"), "a");
  assert.deepEqual(states[0], [[true, "loading", "loading"]]);
  scan.resolve({}); runtime.resolve({}); await done;
  assert.deepEqual(states.at(-1), [[false, "ready", "ready"]]);
});
