// Shared notes replay (data/notes.js), same rules as alfrd.notes.replay.
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const N = await import(path.join(path.resolve(here, "../../src/alfrd/web/js"), "data/notes.js"));

test("notes: replay create/edit/resolve/delete, broken lines skipped", () => {
  const lines = [
    { id: "n_1", op: "create", anchor: { target: "A", step: "rpicard" }, text: "RFI", tags: ["rfi"], at: "t1" },
    { id: "n_2", op: "create", anchor: { target: "B" }, text: "x", at: "t1" },
    { id: "n_1", op: "edit", text: "RFI 03:10", at: "t2" },
    { id: "n_1", op: "resolve", at: "t3" },
    { id: "n_2", op: "delete", at: "t3" },
    { id: "n_9", op: "edit", text: "orphan event", at: "t4" },
  ].map((e) => JSON.stringify(e)).join("\n") + '\n{"id": "n_3", "op": "cre';
  const notes = N.replayNotes(lines);
  assert.deepEqual(notes.map((n) => [n.id, n.text, n.status, n.updated]), [["n_1", "RFI 03:10", "resolved", "t3"]]);
  const ctx = { state: { trees: { p: { notesText: lines } } } };
  assert.equal(N.notesAt(ctx, "p", { target: "A" }).length, 0, "resolved notes are not markers");
  assert.equal(N.notesAt(ctx, "p", { target: "A" }, { all: true }).length, 1);
  assert.match(N.noteMark("p", { target: "A" }, 2), /data-notes="\{&quot;target&quot;:&quot;A&quot;\}"/);
});
