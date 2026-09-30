// Full-text search (alfrd.search through alfrd serve): logs, CSVs, alfrd.yaml, notes.
// A hit shows the lines around it; "Open log" opens the file full screen.
// Opened from the command palette (? or Tab); loaded with import() on first use.

import { $, $$, esc, icon, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";
import { openFileFull } from "./logview.js";

const words = (q) => (String(q).match(/[\w+.-]+/g) || []).filter((w) => w.replace(/[.-]/g, ""));
const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Escape `text`, then mark the query words. */
export function mark(text, q) {
  const ws = words(q);
  const safe = esc(text);
  if (!ws.length) return safe;
  return safe.replace(new RegExp(`(${ws.map((w) => reEsc(esc(w))).join("|")})`, "gi"), "<mark>$1</mark>");
}

function projectsOf(ctx) {
  const p = ctx.state.selectedProject;
  if (p && p !== "all") return [p];
  return [...new Set(ctx.state.targets.map((t) => t.project))].filter((x) => ctx.state.trees?.[x]?.provider === "server");
}

export function openSearch(ctx, initial = "") {
  loadCss("css/lazy.css");
  const projects = projectsOf(ctx);
  let timer = null;
  let seq = 0;
  ctx.modal(`<header class="modal-h"><h2>${icon("search")} Search file contents</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b srch">
      <input id="sr-q" class="input" type="search" placeholder="Words in logs, CSVs, alfrd.yaml, notes (all words, prefixes: fring j0742)" value="${esc(initial)}" aria-label="Search">
      <p class="muted small" id="sr-st" aria-live="polite"></p>
      <div class="srch-b"><ol id="sr-list" class="srch-list"></ol><div id="sr-ctx" class="srch-ctx"></div></div>
    </div>`, (root) => {
    const input = $("#sr-q", root);
    const run = async () => {
      const q = input.value.trim();
      const mine = ++seq;
      if (!q) { $("#sr-list", root).innerHTML = ""; $("#sr-st", root).textContent = ""; return; }
      $("#sr-st", root).textContent = "Searching…";
      try {
        const r = await server.search(projects, q);
        if (mine !== seq) return;
        const building = Object.values(r.index || {}).find((i) => i.state === "indexing" || i.state === "idle");
        $("#sr-st", root).textContent = `${r.hits.length} hit(s) in ${r.took_ms} ms${building ? ` · building the index (${building.files_done || 0}/${building.files_total || "?"} files): scanning meanwhile` : ""}${r.fts5 === false ? " · no FTS5 in this Python: scanning files" : ""}`;
        $("#sr-list", root).innerHTML = r.hits.map((h, i) => `<li><button class="srch-hit" data-i="${i}"><span class="mono small">${esc(h.rel)}<b>:${esc(h.line)}</b></span><span class="small">${mark(h.snippet, q)}</span></button></li>`).join("")
          || '<li class="muted small">No match.</li>';
        $$(".srch-hit", root).forEach((b) => b.addEventListener("click", () => showHit(ctx, root, r.hits[Number(b.dataset.i)], q)));
      } catch (error) {
        if (mine === seq) $("#sr-st", root).textContent = error.message;
      }
    };
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 250); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(timer); run(); } });
    if (initial) run();
  }, "wide");
}

async function showHit(ctx, root, hit, q) {
  const box = $("#sr-ctx", root);
  box.innerHTML = '<p class="muted small">Loading…</p>';
  try {
    const c = await server.searchContext(hit.project, hit.rel, hit.line);
    box.innerHTML = `<div class="row gap"><span class="mono small trunc">${esc(hit.rel)}</span><span class="grow"></span><button class="btn sm" id="sr-open">${icon("expand")} Open log</button></div>
      <pre class="log small srch-pre">${c.lines.map((l, i) => { const n = c.first + i; return `<span class="${n === hit.line ? "hit" : ""}" ${n === hit.line ? 'id="sr-hit"' : ""}><i>${n}</i>${mark(l, q)}</span>`; }).join("\n")}</pre>`;
    $("#sr-hit", box)?.scrollIntoView({ block: "center" });
    $("#sr-open", box).addEventListener("click", () => openFileFull(ctx, hit.project, hit.rel).catch((e) => ctx.toast(e.message, "warn")));
  } catch (error) {
    box.innerHTML = `<p class="bad small">${esc(error.message)}</p>`;
  }
}
