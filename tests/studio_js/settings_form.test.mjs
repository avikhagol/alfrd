// data/prefs.js, the settings form model: Browser, Project and User sections, each saved alone with its own
// expected_revision. A Read-only override greys a toggle out with a reason; a missing Chat mode says what it needs.
import test from "node:test";
import assert from "node:assert/strict";

const items = new Map();
globalThis.localStorage = {
  getItem: (k) => (items.has(k) ? items.get(k) : null),
  setItem: (k, v) => items.set(k, String(v)),
  removeItem: (k) => items.delete(k),
};
const { prefsRevision, readPrefs } = await import("../../src/alfrd/web/js/data/prefs.js");
const { fieldState, reasonText, saveSection, SECTIONS, visibleSections, rowShown } = await import("../../src/alfrd/web/js/components/settings_dialog.js");

const flag = (over) => ({ key: "ui.live_updates", scope: "browser", locked: false, disabled: false, reason: null, ...over });

test("the form has Browser, Project and User sections", () => {
  assert.deepEqual(SECTIONS.map((s) => s.scope), ["browser", "project", "machine"]);
  assert.deepEqual(visibleSections(false).map((s) => s.scope), ["browser", "machine"], "Project (chat settings) only with Chat mode on");
  assert.deepEqual(visibleSections(true).map((s) => s.scope), ["browser", "project", "machine"]);
  assert.equal(SECTIONS.at(-1).title, "User");
  for (const key of ["ui.experimental_chat", "lockdown.chat"]) {
    assert.equal(rowShown({ key }, false), false, `${key} hidden without experimental features`);
    assert.equal(rowShown({ key }, true), true);
  }
  assert.equal(rowShown({ key: "lockdown.runs" }, false), true);
});

test("Read-only greys a browser or project toggle out and says why", () => {
  const locked = flag({ locked: true, reason: "Read-only Studio" });
  assert.deepEqual(fieldState(locked, { readOnly: true }), { disabled: true, reason: "Read-only Studio" });
  assert.deepEqual(fieldState(flag({ scope: "project", key: "chat.final_summary" }), { readOnly: true }),
    { disabled: true, reason: "Read-only Studio overrides this" });
  // a machine toggle stays editable, so Read-only can be turned off again
  assert.deepEqual(fieldState(flag({ scope: "machine", key: "lockdown.read_only" }), { readOnly: true }), { disabled: false, reason: "" });
  assert.deepEqual(fieldState(flag(), { readOnly: false }), { disabled: false, reason: "" });
});

test("a missing Chat mode says it requires Experimental features", () => {
  const chat = flag({ key: "ui.experimental_chat", disabled: true, reason: "Requires features.experimental" });
  assert.equal(reasonText(chat), "Requires Experimental features");
  assert.deepEqual(fieldState(chat), { disabled: true, reason: "Requires Experimental features" });
  // behind a remote reason the requirement stays readable
  assert.match(reasonText({ reason: "Changes are allowed only from a browser on the server machine. Requires features.experimental" }), /Requires Experimental features$/);
});

test("each section saves alone with its own expected_revision", async () => {
  const calls = [];
  const post = async (path, payload) => {
    calls.push([path, payload]);
    if (path === "/studio/superuser") return { revision: 22, settings: {} };
    return payload.scope === "browser" ? { values: payload.changes } : { revisions: { project: 12 }, flags: [] };
  };
  const browser = await saveSection("browser", { changes: { "ui.rows_per_page": 50 }, expected: 0, post });
  assert.equal(readPrefs()["ui.rows_per_page"], 50);
  assert.equal(browser.revision, 1);
  assert.equal(prefsRevision(), 1);
  const project = await saveSection("project", { changes: { "chat.final_summary": true }, expected: 11, project: "p", post });
  assert.equal(project.revision, 12);
  const machine = await saveSection("machine", { changes: { "lockdown.runs": false }, expected: 21, post });
  assert.equal(machine.revision, 22);
  assert.deepEqual(calls.map(([path, p]) => [path, p.scope ?? "machine", p.expected_revision]),
    [["/studio/ui-flags", "browser", 0], ["/studio/ui-flags", "project", 11], ["/studio/superuser", "machine", 21]]);
  assert.equal(calls[0][1].current_revision, 0); // the revision the page holds when it saves
  assert.equal(calls[1][1].project, "p");
});

test("a stale form (409) is marked stale and stores nothing", async () => {
  const before = readPrefs();
  const post = async () => { throw Object.assign(new Error("stale expected_revision 1"), { status: 409 }); };
  for (const scope of ["browser", "project", "machine"]) {
    await assert.rejects(saveSection(scope, { changes: { "ui.rows_per_page": 100 }, expected: 1, project: "p", post }), (error) => error.stale === true);
  }
  assert.deepEqual(readPrefs(), before);
  await assert.rejects(saveSection("browser", { changes: {}, expected: 1, post: async () => { throw Object.assign(new Error("no"), { status: 403 }); } }),
    (error) => error.stale === undefined);
});
