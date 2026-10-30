import test from "node:test";
import assert from "node:assert/strict";
import { server } from "../../src/alfrd/web/js/data/server.js";
const response = (body, ok = true) => ({ ok, json: async () => body });

test("capability probes share pending and fresh results by project, expire, and retry errors", async () => {
  const request = server.request, session = server.session, now = Date.now;
  let calls = 0, finish, clock = 0;
  Date.now = () => clock;
  server.session = { csrf_token: "probe-test" };
  server.request = () => { calls++; return new Promise((resolve) => { finish = resolve; }); };
  try {
    const first = server.chatProbe("a / b");
    assert.equal(server.chatProbe("a / b"), first); assert.equal(calls, 1);
    finish(response({ agents: { a: { images: true } } }));
    const body = await first; assert.equal(await server.chatProbe("a / b"), body);
    clock = 30000;
    const expired = server.chatProbe("a / b"); assert.equal(calls, 2);
    finish(response({}, false)); await assert.rejects(expired, /unavailable/);
    const retry = server.chatProbe("a / b"); assert.equal(calls, 3);
    finish(response({ agents: {} })); await retry;
    const other = server.chatProbe("other"); assert.equal(calls, 4);
    finish(response({})); await other;
  } finally { server.request = request; server.session = session; Date.now = now; server.invalidateChatProbes(); }
});

test("config invalidation and connection changes reject old in-flight capability replies", async () => {
  const request = server.request, session = server.session;
  const pending = [];
  server.session = { csrf_token: "old" };
  server.request = () => new Promise((resolve) => pending.push(resolve));
  try {
    const old = server.chatProbe("p"); server.invalidateChatProbes();
    const current = server.chatProbe("p");
    pending.shift()(response({ old: true })); await assert.rejects(old, /changed/);
    pending.shift()(response({ current: true })); assert.deepEqual(await current, { current: true });
    server.session = { csrf_token: "new" };
    const next = server.chatProbe("p");
    server.session = { csrf_token: "replacement" };
    pending.shift()(response({ leaked: true })); await assert.rejects(next, /changed/);
  } finally { server.request = request; server.session = session; server.invalidateChatProbes(); }
});

test("successful configuration mutations invalidate capabilities, failed writes retain the cache", async () => {
  const request = server.request, session = server.session, fetchImpl = globalThis.fetch;
  let calls = 0;
  server.session = { csrf_token: "mutation-test", mutations_enabled: true };
  server.request = async () => { calls++; return response({}); };
  globalThis.fetch = async () => ({ ok: true, headers: new Headers({ "content-type": "application/json" }), json: async () => ({}) });
  try {
    await server.chatProbe("p");
    await server.mutate("/studio/projects/p/chat/settings", {});
    await server.chatProbe("p"); assert.equal(calls, 2);
    globalThis.fetch = async () => { throw new Error("network"); };
    await assert.rejects(server.mutate("/studio/projects/p/chat/settings", {}), /network/);
    await server.chatProbe("p"); assert.equal(calls, 2);
  } finally { server.request = request; server.session = session; globalThis.fetch = fetchImpl; server.invalidateChatProbes(); }
});
