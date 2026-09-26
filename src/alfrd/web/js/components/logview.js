// Shared log rendering: collapsible log files that load on open, scroll inside
// a fixed-height box, and a small expand button that opens any log (or any
// inline log snippet) full-screen in a modal.

import { esc, icon, bytes, when } from "../utils/dom.js";
import { groupLabel } from "../data/defs.js";

const MAX_INLINE = 400000;

/** Files for a project (step logs + log artifacts from alfrd.yaml). */
export function projectLogs(ctx, project) {
  return ctx.state.trees?.[project]?.logFiles || [];
}

/** Does a listed log belong to this target? (own target, its work dirs, or project-wide). */
export function logForTarget(ctx, f, t, codes = []) {
  if (!t) return true;
  if (f.target) return f.target === t.name;
  if (f.workdir) return codes.some((c) => f.workdir === c || String(f.workdir).startsWith(`${c}/`) || c.startsWith(`${f.workdir}/`));
  const crash = ctx.state.avica?.[t.project]?.logs?.find((l) => l.kind === "crash" && f.rel.endsWith(`/${l.name}`));
  if (crash?.crash?.target) return crash.crash.target === t.name;
  return true;
}

/** One collapsible log file. The body is filled on first open (see bindLogs). */
export function logItem(f, { project, open = false, showGroups = false, defs = null } = {}) {
  const dir = f.rel.includes("/") ? f.rel.slice(0, f.rel.lastIndexOf("/")) : "";
  const tags = [f.band && `<span class="code-chip">${esc(f.band)}</span>`, f.target && `<span class="code-chip auto">${esc(f.target)}</span>`].filter(Boolean).join("");
  const groups = showGroups && f.groups?.length ? `<span class="muted small">${esc(f.groups.map((g) => groupLabel(g, defs)).join(" · "))}</span>` : "";
  return `<details class="log-item" data-log-rel="${esc(f.rel)}" data-log-project="${esc(project)}" ${open ? "open" : ""}>
    <summary><span class="caret">${icon("caret")}</span><span class="mono log-name" title="${esc(f.rel)}">${esc(f.name || f.rel.split("/").pop())}</span>${tags}
      <span class="muted small mono trunc log-dir">${esc(dir)}</span>${groups}<span class="grow"></span>
      <span class="muted small tabular">${f.size ? bytes(f.size) : ""}${f.mtime ? ` · ${esc(when(new Date(f.mtime).toISOString()))}` : ""}</span>
      <button class="icon-btn xs" data-log-zoom="${esc(f.rel)}" data-log-project="${esc(project)}" title="Open full screen" aria-label="Open ${esc(f.name || f.rel)} full screen">${icon("expand")}</button></summary>
    <pre class="log log-box" data-log-body>${open ? "Loading…" : ""}</pre></details>`;
}

/** An inline snippet (not a file) with the same expand button. */
export function zoomablePre(text, { title = "Log", cls = "log small log-box", empty = "" } = {}) {
  const body = text ? esc(text) : empty;
  return `<div class="zoomable"><button class="icon-btn xs zoom-btn" data-zoom-pre data-title="${esc(title)}" title="Open full screen" aria-label="Open ${esc(title)} full screen">${icon("expand")}</button><pre class="${cls}">${body}</pre></div>`;
}

async function fill(ctx, d) {
  const pre = d.querySelector("[data-log-body]");
  if (!pre || pre.dataset.loaded) return;
  pre.dataset.loaded = "1";
  pre.textContent = "Loading…";
  try {
    const text = await ctx.readFile(d.dataset.logProject, d.dataset.logRel);
    pre.textContent = text.length > MAX_INLINE ? text.slice(-MAX_INLINE) : text || "(empty file)";
    pre.scrollTop = pre.scrollHeight;
  } catch (error) {
    pre.textContent = `(${error.message})`;
    pre.dataset.loaded = "error";
  }
}

export function openFull(ctx, title, text, sub = "") {
  ctx.modal(`<header class="modal-h"><h2 class="mono trunc">${esc(title)}</h2>${sub ? `<span class="muted small mono trunc">${esc(sub)}</span>` : ""}<span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><pre class="log log-full">${esc(text)}</pre></div>`, (root) => {
    const pre = root.querySelector("pre");
    pre.scrollTop = pre.scrollHeight;
  }, "full");
}

/** Install once: lazy loading on open, and the expand buttons (files and snippets). */
export function bindLogs(ctx, root = document) {
  root.addEventListener("toggle", (e) => {
    const d = e.target;
    if (d instanceof HTMLDetailsElement && d.classList.contains("log-item") && d.open) fill(ctx, d);
  }, true);
  root.addEventListener("click", async (e) => {
    const z = e.target.closest("[data-log-zoom]");
    if (z) {
      e.preventDefault();
      e.stopPropagation();
      try {
        const text = await ctx.readFile(z.dataset.logProject, z.dataset.logZoom);
        openFull(ctx, z.dataset.logZoom.split("/").pop(), text, z.dataset.logZoom);
      } catch (error) {
        ctx.toast(error.message, "warn");
      }
      return;
    }
    const p = e.target.closest("[data-zoom-pre]");
    if (p) {
      e.preventDefault();
      const pre = p.parentElement.querySelector("pre");
      openFull(ctx, p.dataset.title || "Log", pre ? pre.textContent : "");
    }
  });
  // Details rendered already open (e.g. first step log) need their body too.
  new MutationObserver(() => {
    root.querySelectorAll("details.log-item[open] [data-log-body]:not([data-loaded])").forEach((pre) => fill(ctx, pre.closest("details")));
  }).observe(root, { childList: true, subtree: true });
}
