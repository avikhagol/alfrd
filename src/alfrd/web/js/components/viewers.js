// File viewers: how the Studio shows a file (full-file view, `text` panel).
// registerViewer({id, match, render(file, el)}): `match` lists lower-case
// extensions (".pdf"), exact mime types ("application/pdf") or "type/*".
// viewerFor(path, mime) tries extensions first, then the exact mime, then
// "type/*"; later registrations win; plain text is the fallback.
// Loaded with import() (tails.js, panels.js).

import { esc, icon } from "../utils/dom.js";
import { server } from "../data/server.js";

const MIME = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
  ".svg": "image/svg+xml", ".ico": "image/vnd.microsoft.icon", ".webp": "image/webp", ".bmp": "image/bmp", ".pdf": "application/pdf",
};

const viewers = []; // newest first
let converters = [];
let revision = 0;
let tabSequence = 0;
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
  if (/\.(md|markdown)$/i.test(file.path || file.name || "") && viewer.id !== "text") {
    el.classList.add("file-markdown");
    for (const table of el.querySelectorAll?.("table") || []) {
      if (table.parentElement?.classList.contains("file-table-wrap")) continue;
      const wrap = document.createElement("div");
      wrap.className = "file-table-wrap";
      table.replaceWith(wrap);
      wrap.append(table);
    }
    prepareMarkdownLinks(el, file);
  }
}

/** Local text links only. The existing project file endpoint remains the authority
 * on which paths are readable; never turn a local link into a raw browser URL. */
export function textLink(href, currentPath) {
  if (!href || href.startsWith("#") || /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(href)) return null;
  const [pathname, hash = ""] = href.split("#", 2);
  if (pathname.includes("?")) return null;
  let path;
  try { path = decodeURIComponent(pathname); } catch { return null; }
  if (!/\.(?:md|markdown|txt|text|log|json|jsonl|csv|tsv|yaml|yml|toml|ini|cfg|conf|xml|html|css|js|mjs|ts|tsx|jsx|py|sh|sql|rst)$/i.test(path) || /[\0\\]/.test(path)) return null;
  // Preserve absolute paths and leading .. components for the endpoint to check.
  if (!path.startsWith("/")) path = `${currentPath.slice(0, currentPath.lastIndexOf("/") + 1)}${path}`;
  const parts = [];
  for (const part of path.split("/")) {
    if (part === ".") continue;
    if (part === ".." && parts.length && parts.at(-1) !== ".." && parts.at(-1) !== "") parts.pop();
    else parts.push(part);
  }
  return { path: parts.join("/"), hash };
}

function scrollToAnchor(el, hash) {
  let id;
  try { id = decodeURIComponent(hash.replace(/^#/, "")); } catch { return; }
  const target = [...el.querySelectorAll("[id], a[name]")].find(node => node.id === id || node.getAttribute("name") === id);
  const heading = target || [...el.querySelectorAll("h1,h2,h3,h4,h5,h6")].find(node => node.textContent.trim() === id);
  if (!id) el.scrollTop = 0;
  else if (heading) el.scrollTop += heading.getBoundingClientRect().top - el.getBoundingClientRect().top;
}

function prepareMarkdownLinks(el, file) {
  const used = new Set([...el.querySelectorAll?.("[id]") || []].map(node => node.id));
  for (const heading of el.querySelectorAll?.("h1,h2,h3,h4,h5,h6") || []) {
    if (heading.id) continue;
    const slug = heading.textContent.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s+/g, "-") || "section";
    let id = slug, suffix = 1;
    while (used.has(id)) id = `${slug}-${suffix++}`;
    used.add(id); heading.id = id;
  }
  for (const link of el.querySelectorAll?.("a[href]") || []) {
    if (/^(?:https?:)?\/\//i.test(link.getAttribute("href"))) {
      link.target = "_blank"; link.rel = "noopener noreferrer";
    }
  }
  el.onclick = (event) => {
    const link = event.target.closest("a[href]");
    if (!link || !el.contains(link) || event.button > 0) return;
    const href = link.getAttribute("href");
    if (href.startsWith("#")) { event.preventDefault(); scrollToAnchor(el, href); return; }
    const local = textLink(href, file.path || "");
    if (local && file.openTextLink) { event.preventDefault(); file.openTextLink(local); }
    // A relative link that is not readable text must never navigate the Studio tab away.
    else if (!/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(href)) event.preventDefault();
  };
}

/** A small tab set owned by this modal. The original artifact keeps its verified
 * bytes and paging controls; linked tabs fetch through projectFile as usual. */
export function mountViewerTabs(root, project, initial, path) {
  root.classList.add("file-viewer-modal");
  const body = initial.parentElement;
  const bar = document.createElement("div");
  bar.className = "file-viewer-tabs"; bar.setAttribute("role", "tablist"); bar.setAttribute("aria-label", "Open files");
  body.before(bar);
  const tabs = [];
  let active = null;
  const activate = (tab, focus = false) => {
    if (active?.el === initial) {
      for (const node of body.children) if (!tabs.some(item => item.el === node)) node.dataset.viewerWasHidden = node.hidden ? "1" : "0";
    }
    active = tab;
    for (const item of tabs) {
      const selected = item === tab;
      item.button.setAttribute("aria-selected", String(selected)); item.button.tabIndex = selected ? 0 : -1;
      item.el.hidden = !selected;
    }
    // Paging belongs to the original artifact, never the linked file.
    for (const node of body.children) if (!tabs.some(item => item.el === node)) node.hidden = tab.el !== initial || node.dataset.viewerWasHidden === "1";
    if (tab.hash && tab.ready) { scrollToAnchor(tab.el, tab.hash); tab.hash = ""; }
    if (focus) tab.button.focus();
  };
  for (const node of body.children) if (node !== initial) node.dataset.viewerWasHidden = node.hidden ? "1" : "0";
  const add = (el, rel) => {
    const button = document.createElement("button");
    button.type = "button"; button.setAttribute("role", "tab"); button.textContent = rel.split("/").pop(); button.title = rel;
    const id = `file-viewer-tab-${++tabSequence}`;
    button.id = id; el.id ||= `${id}-panel`; el.tabIndex = 0;
    button.setAttribute("aria-controls", el.id); el.setAttribute("role", "tabpanel"); el.setAttribute("aria-labelledby", id);
    const tab = { el, button }; tabs.push(tab); bar.append(button);
    button.onclick = () => activate(tab);
    button.onkeydown = event => {
      let index = tabs.indexOf(tab);
      if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") index = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = tabs.length - 1;
      else return;
      event.preventDefault(); activate(tabs[index], true);
    };
    return tab;
  };
  activate(add(initial, path));
  const openTextLink = async ({ path: rel, hash }) => {
    const el = document.createElement("div"); el.className = "viewer viewer-full";
    body.append(el);
    const tab = add(el, rel); tab.hash = hash; activate(tab, true);
    el.textContent = "Loading…"; el.setAttribute("aria-busy", "true");
    try {
      await renderViewer(viewerFor(rel), fileObject(project, rel, { openTextLink }), el);
      tab.ready = true;
      if (active === tab && tab.hash) { scrollToAnchor(el, tab.hash); tab.hash = ""; }
    } catch (error) { el.textContent = `Unable to open ${rel}: ${error.message}`; }
    finally { el.removeAttribute("aria-busy"); }
  };
  return openTextLink;
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

/** Full-screen view of a file with a non-text viewer (same header as tails's openFull, no Follow). */
export function openViewer(ctx, project, rel, viewer = viewerFor(rel), extra = {}) {
  ctx.modal(`<header class="modal-h"><h2 class="mono trunc">${esc(rel.split("/").pop())}</h2><span class="muted small mono trunc">${esc(rel)}</span><span class="grow"></span>${conversionButton(rel, viewer)}
      <button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p data-convert-status role="status" aria-live="polite" hidden></p><div class="viewer viewer-full" data-viewer="${esc(viewer.id)}"></div></div>`, (root) => {
    const el = root.querySelector(".viewer-full");
    const openTextLink = mountViewerTabs(root, project, el, rel);
    const file = fileObject(project, rel, { ...extra, openTextLink });
    renderViewer(viewer, file, el).catch((error) => { if (el.dataset.converted !== "1") el.textContent = error.message; });
    mountConversion(root.querySelector("[data-convert-rel]"), el, root.querySelector("[data-convert-status]"), file, viewer);
  }, "full");
}

export { findSpans, attemptLabel, textToolsHtml, textFindHtml, bindTextTools } from "../utils/text_find.js";

// Built-ins (registered first, so anything registered later wins).
registerViewer({
  id: "text", match: ["text/*"],
  render(file, el) {
    el.classList.add("file-text");
    el.innerHTML = `<pre class="code small">${esc(file.text ?? "")}</pre>`;
  },
});
registerViewer({
  id: "binary", match: ["application/octet-stream"],
  render(file, el) { el.innerHTML = `<p class="muted">Binary file. Use the open or download action to view it.</p>`; },
});
registerViewer({
  id: "image", match: [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", "image/*"],
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
