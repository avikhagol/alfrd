// Shared annotations (alfrd.notes.jsonl, see alfrd/notes.py): replay the events
// the scan delivered; markers in Overview, the plan grid, Results and logs use this.

import { esc } from "../utils/dom.js";

const cache = new Map(); // project -> {text, notes}

/** Events (one JSON object per line) → notes, same rules as alfrd.notes.replay. */
export function replayNotes(text) {
  const notes = new Map();
  String(text || "").split("\n").forEach((line) => {
    let ev;
    try { ev = JSON.parse(line); } catch { return; }
    if (!ev || typeof ev.id !== "string") return;
    if (ev.op === "create") {
      notes.set(ev.id, { id: ev.id, anchor: ev.anchor || {}, text: String(ev.text || ""), tags: ev.tags || [], author: ev.author, created: ev.at, updated: ev.at, status: "open" });
      return;
    }
    const n = notes.get(ev.id);
    if (!n) return;
    n.updated = ev.at;
    if (ev.op === "edit") {
      if ("text" in ev) n.text = String(ev.text || "");
      if ("tags" in ev) n.tags = ev.tags || [];
      if ("anchor" in ev) n.anchor = ev.anchor || {};
    } else if (ev.op === "resolve") n.status = "resolved";
    else if (ev.op === "reopen") n.status = "open";
    else if (ev.op === "delete") notes.delete(ev.id);
  });
  return [...notes.values()];
}

export function projectNotes(ctx, project) {
  const text = ctx.state.trees?.[project]?.notesText || "";
  const hit = cache.get(project);
  if (hit && hit.text === text) return hit.notes;
  const notes = replayNotes(text);
  cache.set(project, { text, notes });
  return notes;
}

/** Open notes of a project whose anchor has every key of `where` (e.g. {target, step}). */
export function notesAt(ctx, project, where, { all = false } = {}) {
  return projectNotes(ctx, project).filter((n) => (all || n.status === "open") && Object.entries(where).every(([k, v]) => v == null || n.anchor[k] === v));
}

/** Small "notes" marker; opens the notes panel (data-notes-* read by app.js). */
export function noteMark(project, where, n) {
  if (!n) return "";
  return `<button class="note-mark" data-notes="${esc(JSON.stringify(where))}" data-notes-project="${esc(project)}" title="${n} open note(s)" aria-label="${n} open note(s)">✎${n > 1 ? n : ""}</button>`;
}
