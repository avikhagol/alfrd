// File viewers: how the Studio shows a file (full-file view, `text` panel).
// registerViewer({id, match, render(file, el)}): `match` lists lower-case
// extensions (".pdf"), exact mime types ("application/pdf") or "type/*".
// viewerFor(path, mime) tries extensions first, then the exact mime, then
// "type/*"; later registrations win; plain text is the fallback.
// Loaded with import() (logview.js, panels.js).

import { esc, icon } from "../utils/dom.js";

const MIME = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
  ".webp": "image/webp", ".bmp": "image/bmp", ".pdf": "application/pdf",
};

const viewers = []; // newest first

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

/** Full-screen view of a file with a non-text viewer (same header as logview's openFull, no Follow). */
export function openViewer(ctx, project, rel, viewer = viewerFor(rel)) {
  ctx.modal(`<header class="modal-h"><h2 class="mono trunc">${esc(rel.split("/").pop())}</h2><span class="muted small mono trunc">${esc(rel)}</span><span class="grow"></span>
      <button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><div class="viewer viewer-full" data-viewer="${esc(viewer.id)}"></div></div>`, (root) => {
    const el = root.querySelector(".viewer-full");
    try { viewer.render(fileObject(project, rel), el); } catch (error) { el.textContent = error.message; }
  }, "full");
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
