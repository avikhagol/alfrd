// File viewers: how the Studio shows a file (full-file view, `text` panel).
// registerViewer({id, match, render(file, el)}): `match` lists lower-case
// extensions (".pdf"), exact mime types ("application/pdf") or "type/*".
// viewerFor(path, mime) tries extensions first, then the exact mime, then
// "type/*"; later registrations win; plain text is the fallback.
// Loaded with import() (logview.js, panels.js).

import { esc, icon, copyText } from "../utils/dom.js";
import { server } from "../data/server.js";

const MIME = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
  ".webp": "image/webp", ".bmp": "image/bmp", ".pdf": "application/pdf",
};

const viewers = []; // newest first
let converters = [];
let revision = 0;
export const viewerGeneration = () => revision;

const extOf = (path) => { const m = /\.[^./]+$/.exec(String(path || "")); return m ? m[0].toLowerCase() : ""; };

/** The mime type of a path, from its extension ("text/plain" when unknown). */
export const mimeOf = (path) => MIME[extOf(path)] || "text/plain";

/** URL of the file's bytes (images and PDF only; the server refuses other types). */
export function rawUrl(project, path) {
  return `/api/studio/projects/${encodeURIComponent(project)}/file?${new URLSearchParams({ path, raw: "1" })}`;
}

/** The `file` object a viewer's render() gets. */
export function fileObject(project, path, extra = {}) {
  return { project, path, name: String(path).split("/").pop(), mime: mimeOf(path), url: rawUrl(project, path), ...extra };
}

export function registerViewer(viewer) {
  const { id, match, render } = viewer || {};
  if (typeof id !== "string" || !id || !Array.isArray(match) || typeof render !== "function") {
    throw new TypeError("registerViewer({id, match: [...], render(file, el)})");
  }
  const at = viewers.findIndex((v) => v.id === id);
  if (at >= 0) viewers.splice(at, 1);
  viewers.unshift({ id, match: match.map((m) => String(m).toLowerCase()), render });
  revision++;
}

export function viewerFor(path, mime) {
  const ext = extOf(path);
  const type = String(mime || mimeOf(path)).toLowerCase();
  const wild = `${type.split("/")[0]}/*`;
  for (const want of [ext, type, wild]) {
    const hit = want && viewers.find((v) => v.match.includes(want));
    if (hit) return hit;
  }
  return viewers.find((v) => v.id === "text");
}

export function setConverters(list) {
  list = Array.isArray(list) ? list : [];
  if (JSON.stringify(list) !== JSON.stringify(converters)) revision++;
  converters = list;
}

/** PDF conversion is offered only when the file has no dedicated viewer. */
export function converterFor(path, viewer = viewerFor(path)) {
  if (viewer && viewer.id !== "text") return null;
  return converters.find((c) => c.to.replace(/^\./, "").toLowerCase() === "pdf" &&
    c.src.some((s) => s.toLowerCase() === extOf(path))) || null;
}

export function conversionUrl(project, path, to = "pdf") {
  if (!project || project === "all") throw new Error("Select a project to convert a file.");
  return `/api/studio/projects/${encodeURIComponent(project)}/convert?${new URLSearchParams({ path, to })}`;
}

export async function convertFile(project, path, to = "pdf") {
  const response = await server.request(conversionUrl(project, path, to));
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).error?.message || message; } catch { /* non-JSON error */ }
    throw new Error(message);
  }
  return response.blob();
}

/** Custom text viewers receive the entire allowed file, not an empty raw-file URL. */
export async function renderViewer(viewer, file, el) {
  if (file.text == null && !["image", "pdf"].includes(viewer.id) && file.mime.startsWith("text/")) {
    file = { ...file, text: await server.projectFile(file.project, file.path) };
  }
  if (el.dataset?.converted === "1") return;
  await viewer.render(file, el);
}

export function conversionButton(path, viewer = viewerFor(path)) {
  return converterFor(path, viewer) ? `<button type="button" class="btn sm" data-convert-rel="${esc(path)}">${icon("file")} Convert to PDF</button>` : "";
}

/** Keep text visible until a conversion succeeds; retry errors without losing the file. */
export function mountConversion(button, host, status, file, viewer = viewerFor(file.path)) {
  const converter = converterFor(file.path, viewer);
  if (!button || !converter) return;
  button.onclick = async () => {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = `${icon("sync")} Converting…`;
    status.hidden = true;
    let url;
    try {
      url = URL.createObjectURL(await convertFile(file.project, file.path));
      // A closed modal / replaced panel must not retain a large converted Blob.
      if (!host.isConnected) { URL.revokeObjectURL(url); return; }
      host.dataset.converted = "1";
      await viewerFor("converted.pdf", "application/pdf").render({ ...file, mime: "application/pdf", url }, host);
      status.className = "small muted";
      status.textContent = `Converted by ${converter.title || converter.plugin} · cached`;
      status.hidden = false;
      button.hidden = true;
      const observer = new MutationObserver(() => {
        if (!host.isConnected) { URL.revokeObjectURL(url); observer.disconnect(); }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    } catch (error) {
      delete host.dataset.converted;
      if (url) URL.revokeObjectURL(url);
      status.className = "callout warn small";
      status.textContent = error.message;
      status.hidden = false;
      button.innerHTML = `${icon("file")} Try again`;
    } finally {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  };
}

/** Full-screen view of a file with a non-text viewer (same header as logview's openFull, no Follow). */
export function openViewer(ctx, project, rel, viewer = viewerFor(rel), extra = {}) {
  ctx.modal(`<header class="modal-h"><h2 class="mono trunc">${esc(rel.split("/").pop())}</h2><span class="muted small mono trunc">${esc(rel)}</span><span class="grow"></span>${conversionButton(rel, viewer)}
      <button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p data-convert-status role="status" aria-live="polite" hidden></p><div class="viewer viewer-full" data-viewer="${esc(viewer.id)}"></div></div>`, (root) => {
    const el = root.querySelector(".viewer-full");
    const file = fileObject(project, rel, extra);
    renderViewer(viewer, file, el).catch((error) => { if (el.dataset.converted !== "1") el.textContent = error.message; });
    mountConversion(root.querySelector("[data-convert-rel]"), el, root.querySelector("[data-convert-status]"), file, viewer);
  }, "full");
}

/** Every [start, end) span where `query` occurs in `text`, ignoring case (none for an empty query). */
export function findSpans(text, query) {
  const hay = String(text ?? "").toLowerCase(), q = String(query ?? "").toLowerCase();
  const spans = [];
  if (!q) return spans;
  for (let at = hay.indexOf(q); at >= 0; at = hay.indexOf(q, at + q.length)) spans.push([at, at + q.length]);
  return spans;
}

/** "build · attempt 3 (latest)": the unit and attempt a text view shows. Empty when neither is known. */
export function attemptLabel({ unit, attempt, latest } = {}) {
  return [unit, attempt != null && attempt !== "" ? `attempt ${attempt}${latest ? " (latest)" : ""}` : ""].filter(Boolean).join(" · ");
}

/** Copy and Find buttons for a text view's header, with the optional unit/attempt label. */
export function textToolsHtml(label = "") {
  return `${label ? `<span class="small muted mono trunc" data-viewer-label>${esc(label)}</span>` : ""}<button type="button" class="btn sm" data-text-copy>${icon("copy")} Copy</button><button type="button" class="btn sm" data-text-find-open aria-expanded="false">${icon("search")} Find</button>`;
}

/** The find bar under a text view's header; hidden until Find is pressed. */
export function textFindHtml() {
  return `<div class="row gap wrap small" role="search" data-text-find hidden><input type="search" class="input sm" data-text-query aria-label="Find in text" placeholder="Find in text"><span class="muted tabular" data-text-count role="status" aria-live="polite"></span><button type="button" class="btn sm" data-text-prev aria-label="Previous match">↑</button><button type="button" class="btn sm" data-text-next aria-label="Next match">↓</button><button type="button" class="icon-btn xs" data-text-find-close aria-label="Close find">${icon("close")}</button></div>`;
}

/** A DOM Range over characters [start, end) of `el`'s text, which may span several text nodes. */
function rangeAt(el, start, end) {
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let base = 0, from = false;
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    const next = base + node.data.length;
    if (!from && start < next) { range.setStart(node, start - base); from = true; }
    if (from && end <= next) { range.setEnd(node, end - base); return range; }
    base = next;
  }
  return null;
}

/**
 * Wire Copy and Find in `root` to `getText()`, the text `pre` shows. Enter and Shift+Enter step
 * through the matches; the current one is selected and scrolled into view. Escape closes the bar.
 */
export function bindTextTools(root, { getText, pre, toast }) {
  root.querySelector("[data-text-copy]")?.addEventListener("click", async () => {
    const ok = await copyText(getText());
    toast?.(ok ? "Copied" : "Copy failed", ok ? "ok" : "fail");
  });
  const bar = root.querySelector("[data-text-find]");
  const input = root.querySelector("[data-text-query]");
  const count = root.querySelector("[data-text-count]");
  let spans = [], at = -1;
  const show = () => {
    count.textContent = !input.value ? "" : spans.length ? `${at + 1} of ${spans.length}` : "No matches";
    const range = spans.length ? rangeAt(pre, ...spans[at]) : null;
    if (!range) return;
    const sel = window.getSelection?.();
    sel?.removeAllRanges();
    sel?.addRange(range);
    const r = range.getBoundingClientRect(), b = pre.getBoundingClientRect();
    if (r.top < b.top || r.bottom > b.bottom) pre.scrollTop += r.top - b.top - b.height / 3;
  };
  const search = () => { spans = findSpans(getText(), input.value); at = spans.length ? 0 : -1; show(); };
  const step = (n) => { if (spans.length) { at = (at + n + spans.length) % spans.length; show(); } };
  root.querySelector("[data-text-find-open]")?.addEventListener("click", () => {
    bar.hidden = false;
    input.focus();
    input.select();
    search();
  });
  input.addEventListener("input", search);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); bar.hidden = true; }
  });
  root.querySelector("[data-text-prev]")?.addEventListener("click", () => step(-1));
  root.querySelector("[data-text-next]")?.addEventListener("click", () => step(1));
  root.querySelector("[data-text-find-close]")?.addEventListener("click", () => { bar.hidden = true; });
  return { search };
}

// Built-ins (registered first, so anything registered later wins).
registerViewer({
  id: "text", match: ["text/*"],
  render(file, el) {
    el.innerHTML = `<pre class="code small">${esc(file.text ?? "")}</pre>`;
  },
});
registerViewer({
  id: "image", match: [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"],
  render(file, el) {
    el.innerHTML = `<img class="viewer-img" src="${esc(file.url)}" alt="${esc(file.name)}">`;
  },
});
registerViewer({
  id: "pdf", match: [".pdf", "application/pdf"],
  render(file, el) {
    el.innerHTML = `<iframe class="viewer-pdf" src="${esc(file.url)}" title="${esc(file.name)}"></iframe>`;
  },
});
revision = 0; // Built-ins are the baseline; only later contributions invalidate panel HTML.
