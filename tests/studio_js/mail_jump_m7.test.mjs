import test from "node:test";
import assert from "node:assert/strict";
import { server } from "../../src/alfrd/web/js/data/server.js";
class El {
  constructor() { this.children = []; this.dataset = {}; this.selectors = new Map(); this.attrs = {}; this.hidden = false; this.focuses = 0; this.scrolls = 0; this.className = ""; }
  get parentElement() { return this.parent; }
  append(...kids) { for (const n of kids) { n.parent = this; this.children.push(n); } }
  insertBefore(n, before) { if (n.parent) n.parent.children = n.parent.children.filter((x) => x !== n); n.parent = this; const i = this.children.indexOf(before); if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); }
  querySelector(q) { if (!this.selectors.has(q)) { const n = new El(); n.parent = this; this.selectors.set(q, n); } return this.selectors.get(q); }
  contains(el) { return this === el || this.children.some((n) => n.contains(el)); }
  addEventListener() {}
  setAttribute(k, v) { this.attrs[k] = v; }
  focus() { this.focuses++; }
  scrollIntoView() { this.scrolls++; }
  closest() { return null; }
  get isConnected() { return true; }
}
globalThis.document = { hidden: false, activeElement: null, createElement: () => new El(), addEventListener() {}, removeEventListener() {} };
const { mountMailbox } = await import("../../src/alfrd/web/js/components/chat_mailbox.js");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("M7 async mail jump drains pages to user/peer targets and refresh never repeats focus", async () => {
  const original = server.request;
  for (const recipient of ["user", "task"]) {
    const box = new El(), teamBox = new El(), details = new El(); details.tagName = "DETAILS"; details.open = false; details.append(teamBox);
    const message = { id: "exact-id", seq: 2, sender: { kind: "agent" }, recipient: { kind: recipient }, kind: "info", state: "pending", subject: "subject" };
    server.request = async (url) => ({ ok: true, status: 200, json: async () => url.endsWith("/pending") ? { messages: [] }
      : url.includes("after=page1") ? { messages: [message], next_cursor: "page2", has_more: false }
      : url.includes("after=page2") ? { messages: [], next_cursor: "page2", has_more: false }
      : { messages: [], next_cursor: "page1", has_more: true } });
    const api = mountMailbox(box, { project: () => "p", sid: () => `s-${recipient}`, request: () => "r", agentName: (id) => id, teamBox });
    try {
      api.jump("exact-id"); await sleep(180);
      const list = recipient === "user" ? box.querySelector("[data-mail-cards]") : teamBox.children[0].querySelector("div");
      const card = list.children[0]; assert.ok(card); assert.equal(card.dataset.mailId, "exact-id");
      assert.equal(card.scrolls, 1); assert.equal(card.querySelector("button,textarea").focuses, 1);
      assert.equal(card.querySelector("[data-mail-full]").hidden, false);
      if (recipient === "task") assert.equal(details.open, true);
      api.kick(); await sleep(180);
      assert.equal(card.scrolls, 1); assert.equal(card.querySelector("button,textarea").focuses, 1);
    } finally { api.stop(); }
  }
  server.request = original;
});
