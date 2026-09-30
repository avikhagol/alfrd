// Shared notes (alfrd.notes.jsonl next to alfrd.yaml): list, filter by status and
// tag, add, edit, resolve, delete. Anchors are entity paths (target, code, work
// dir, step, file, line). Writes go through alfrd serve (loopback + CSRF);
// elsewhere the notes from the scan are shown read-only. Loaded with import().

import { $, $$, esc, icon, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";
import { projectNotes } from "../data/notes.js";
import { entityLabel } from "../data/entities.js";

const FIELDS = [["target", "Target"], ["project_code", "Code"], ["workdir", "Work dir"], ["step", "Step"], ["file", "File"], ["line", "Line"]];
const when = (iso) => { try { return new Date(iso).toLocaleString(); } catch { return iso || ""; } };
const matches = (n, where) => Object.entries(where).every(([k, v]) => v == null || v === "" || String(n.anchor?.[k] ?? "") === String(v));

export async function openNotes(ctx, project, where = {}, { focus = null } = {}) {
  loadCss("css/lazy.css");
  const live = ctx.state.mode === "server" && ctx.state.trees?.[project]?.provider === "server";
  const canWrite = live && Boolean(server.session?.mutations_enabled);
  const ui = { status: "open", tag: "", here: Object.values(where).some(Boolean), editing: null };
  let data = { notes: [], tags: [] };
  const load = async () => {
    if (live) data = await server.notes(project);
    else {
      const notes = projectNotes(ctx, project);
      data = { notes, tags: [...new Set(notes.flatMap((n) => n.tags))].sort() };
    }
    if (focus) { const f = data.notes.find((n) => n.id === focus); if (f) ui.status = f.status; ui.here = false; }
  };
  try { await load(); } catch (error) { ctx.toast(`Notes: ${error.message}`, "fail"); return; }
  const whereText = entityLabel(where) || "whole project";
  ctx.modal(`<header class="modal-h"><h2>${icon("file")} Notes · <span class="mono">${esc(ctx.projectName(project))}</span></h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b notes"><div id="nt-bar" class="row gap wrap small"></div><ol id="nt-list" class="nt-list"></ol>
      ${canWrite ? `<details class="nt-add" ${data.notes.some((n) => matches(n, where)) ? "" : "open"}><summary><b>${icon("plus")} Add a note</b> <span class="muted small">${esc(whereText)}</span></summary>
        <div class="row gap wrap">${FIELDS.map(([k, label]) => `<label class="field"><span>${label}</span><input class="input sm mono" data-anchor="${k}" value="${esc(where[k] ?? "")}" size="${k === "file" ? 28 : 10}"></label>`).join("")}</div>
        <textarea id="nt-text" class="input" rows="3" placeholder="EF flagged 03:10–03:40, RFI"></textarea>
        <div class="row gap"><input id="nt-tags" class="input sm grow" placeholder="tags: flagged, rfi"><button class="btn primary sm" id="nt-save">${icon("save")} Add note</button></div>
        <p class="muted small">Saved to <code>alfrd.notes.jsonl</code> next to alfrd.yaml (it travels with the data); the author is the user running alfrd serve.</p></details>`
        : `<p class="muted small">${live ? "Adding notes needs a browser on the same machine as alfrd serve." : "Read-only here: notes are written through alfrd serve."}</p>`}
    </div>`, (root) => {
    const draw = () => {
      const all = data.notes || [];
      const shown = all.filter((n) => (ui.status === "all" || (ui.status === "orphaned" ? n.orphaned : n.status === ui.status))
        && (!ui.tag || (n.tags || []).includes(ui.tag)) && (!ui.here || matches(n, where)));
      $("#nt-bar", root).innerHTML = `<div class="seg" role="group" aria-label="Status">${["open", "resolved", "orphaned", "all"].map((s) => `<button data-st="${s}" class="${ui.status === s ? "on" : ""}">${s} <span class="muted">${s === "all" ? all.length : all.filter((n) => (s === "orphaned" ? n.orphaned : n.status === s)).length}</span></button>`).join("")}</div>
        ${Object.values(where).some(Boolean) ? `<label class="check"><input type="checkbox" id="nt-here" ${ui.here ? "checked" : ""}> only ${esc(whereText)}</label>` : ""}
        ${data.tags?.length ? `<span class="muted">tags:</span>${data.tags.map((t) => `<button class="chip ${ui.tag === t ? "on" : ""}" data-tag="${esc(t)}">#${esc(t)}</button>`).join("")}` : ""}`;
      $("#nt-list", root).innerHTML = shown.map((n) => `<li class="nt ${n.id === focus ? "focus" : ""}" data-id="${esc(n.id)}">
        <div class="row gap small"><span class="mono">${esc(entityLabel(n.anchor) || "project")}</span>${n.status === "resolved" ? '<span class="badge tone-ok">resolved</span>' : ""}${n.orphaned ? '<span class="badge tone-warn" title="Its target or file no longer exists">orphaned</span>' : ""}${(n.tags || []).map((t) => `<span class="tag">#${esc(t)}</span>`).join("")}<span class="grow"></span><span class="muted">${esc(n.author || "")} · ${esc(when(n.updated || n.created))}</span></div>
        ${ui.editing === n.id ? `<textarea class="input" rows="3" data-edit-text>${esc(n.text)}</textarea><div class="row gap"><input class="input sm grow" data-edit-tags value="${esc((n.tags || []).join(", "))}"><button class="btn sm primary" data-op="save">Save</button><button class="btn sm" data-op="cancel">Cancel</button></div>`
          : `<p class="nt-text">${esc(n.text)}</p>`}
        ${canWrite && ui.editing !== n.id ? `<div class="row gap small"><button class="link-btn" data-op="edit">edit</button><button class="link-btn" data-op="${n.status === "open" ? "resolve" : "reopen"}">${n.status === "open" ? "resolve" : "reopen"}</button><button class="link-btn bad" data-op="delete">delete</button></div>` : ""}</li>`).join("")
        || `<li class="muted small">No ${ui.status === "all" ? "" : `${ui.status} `}notes${ui.here ? ` on ${esc(whereText)}` : ""}.</li>`;
      $$("[data-st]", root).forEach((b) => b.addEventListener("click", () => { ui.status = b.dataset.st; draw(); }));
      $$("[data-tag]", root).forEach((b) => b.addEventListener("click", () => { ui.tag = ui.tag === b.dataset.tag ? "" : b.dataset.tag; draw(); }));
      $("#nt-here", root)?.addEventListener("change", (e) => { ui.here = e.target.checked; draw(); });
      $$("[data-op]", root).forEach((b) => b.addEventListener("click", () => act(b.closest("li").dataset.id, b.dataset.op, b.closest("li"))));
      $("li.focus", root)?.scrollIntoView({ block: "nearest" });
    };
    const after = async (message) => {
      ctx.toast(message, "ok");
      await load();
      draw();
      ctx.refreshProject(project).catch(() => {});
    };
    const act = async (id, op, li) => {
      if (op === "edit") { ui.editing = id; draw(); return; }
      if (op === "cancel") { ui.editing = null; draw(); return; }
      if (op === "delete" && !window.confirm("Delete this note? (The file keeps the history.)")) return;
      try {
        const body = op === "save" ? { op: "edit", text: $("[data-edit-text]", li).value, tags: $("[data-edit-tags]", li).value } : { op };
        await server.noteChange(project, id, body);
        ui.editing = null;
        await after({ save: "Note saved", resolve: "Note resolved", reopen: "Note reopened", delete: "Note deleted" }[op] || "Done");
      } catch (error) { ctx.toast(error.message, "fail"); }
    };
    $("#nt-save", root)?.addEventListener("click", async () => {
      const anchor = {};
      $$("[data-anchor]", root).forEach((i) => { if (i.value.trim()) anchor[i.dataset.anchor] = i.dataset.anchor === "line" ? Number(i.value) : i.value.trim(); });
      try {
        await server.noteCreate(project, { anchor, text: $("#nt-text", root).value, tags: $("#nt-tags", root).value });
        $("#nt-text", root).value = "";
        ui.status = "open";
        await after("Note added");
      } catch (error) { ctx.toast(error.message, "fail"); }
    });
    draw();
  }, "wide");
}
