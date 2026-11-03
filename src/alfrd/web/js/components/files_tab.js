import { sourceHTML } from "../utils/refresh.js";
import { esc, copyText, icon } from "../utils/dom.js";
import { server } from "../data/server.js";
import { empty, loading, error } from "./state_view.js";

export function filesHtml(files) {
  if (!files.length) return empty("No files yet");
  return `<ul class="chat-arts">${files.map((f, i) => `<li>
    <strong>${esc(f.path)}</strong><p>Task: ${esc(f.producer_rows.join(", "))} · ${esc(f.state)}</p>
    <button type="button" class="btn sm" data-file-copy="${i}">Copy path</button>
    ${f.ref && f.state === "recorded" ? `<button type="button" class="btn sm" data-file-view="${i}">View</button>` : ""}
    ${f.state === "changed" ? `<p>File changed.</p><button type="button" class="btn sm" data-file-current="${i}">View current</button><button type="button" class="btn sm" data-file-keep="${i}">Keep recorded</button>` : ""}
  </li>`).join("")}</ul>`;
}

export async function blobAdapter(path, blob, registry, urls = URL) {
  const mime = blob.type || registry.mimeOf(path);
  let viewer = registry.viewerFor(path, mime), text = "";
  if (!["image", "pdf"].includes(viewer.id)) {
    if (mime.startsWith("application/") && !/json|javascript|xml/.test(mime)) {
      viewer = registry.viewerFor("", "application/octet-stream");
    } else {
      const bytes = new Uint8Array(await blob.arrayBuffer());
      try {
        if (bytes.includes(0)) throw new Error("binary");
        text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch { viewer = registry.viewerFor("", "application/octet-stream"); }
    }
  }
  const url = urls.createObjectURL(blob);
  return { file: { path, name: path.split("/").pop(), mime, url, text }, viewer, release: () => urls.revokeObjectURL(url) };
}

const modalHtml = (title, body) => `<header class="modal-h"><h2>${esc(title)}</h2><button type="button" class="icon-btn" data-close aria-label="Close file">${icon("close")}</button></header>${body}`;

export async function openBlob(ctx, path, blob, isCurrent = () => true, rawURL = null) {
  const registry = await import("./viewers.js");
  const adapted = await blobAdapter(path, blob, registry);
  if (!isCurrent()) { adapted.release(); return; }
  // Blob links are download-only; server links retain MIME and SVG sandbox headers.
  const action = `<a class="btn sm" href="${esc(rawURL || adapted.file.url)}" ${rawURL ? 'target="_blank" rel="noopener noreferrer"' : 'download'}>${rawURL ? "Open in new tab" : "Download"}</a>`;
  let observer;
  const release = () => { adapted.release(); observer?.disconnect(); };
  try {
    ctx.modal(modalHtml(path, `${action}<div class="modal-b"><div class="viewer viewer-full"></div></div>`), (root) => {
      root.addEventListener("beforeclose", release, { once: true });
      observer = new MutationObserver(() => { if (!root.isConnected) release(); });
      observer.observe(document.body, { childList: true, subtree: true });
      registry.renderViewer(adapted.viewer, adapted.file, root.querySelector(".viewer")).catch((err) => {
        if (root.isConnected) root.querySelector(".viewer").innerHTML = error(err);
      });
    }, "full");
  } catch (err) { release(); throw err; }
}

async function response(url) {
  const res = await server.request(url, { cache: "no-store" });
  if (!res.ok) throw Object.assign(new Error(`File: ${res.status}`), { status: res.status });
  return res;
}
export async function rawBlob(url, ref) {
  return (await response(`${url}/artifacts/raw?${new URLSearchParams({ ref })}`)).blob();
}

export function mountFiles(host, { getOverview, raw, isCurrent, view, rows, copy = copyText }) {
  let alive = true, files = [];
  const current = () => alive && host.isConnected && isCurrent();
  const status = () => host.querySelector("[data-file-status]");
  const draw = () => { sourceHTML(host, filesHtml(files) + '<div role="status" data-file-status></div>'); };
  const load = async () => {
    if (!host.innerHTML || !host.querySelector("[data-file-status]")) sourceHTML(host, loading("Loading files…"));
    try {
      const data = await getOverview();
      if (!current()) return;
      files = (data.files || []).filter((f) => !rows || f.producer_rows.some((r) => rows.includes(r)));
      draw();
    } catch (err) { if (current()) host.innerHTML = error(err, { onRetry: load }); }
  };
  host.onclick = async (event) => {
    const button = event.target.closest("button");
    if (!current() || !button || !host.contains(button)) return;
    const d = button.dataset;
    if (d.stateRetry != null) { load(); return; }
    const action = Object.keys(d).find((k) => k.startsWith("file"));
    const f = files[d[action]];
    if (!f) return;
    button.disabled = true;
    try {
      if (action === "fileCopy") {
        if (!f.absolute_path?.startsWith("/")) {
          status().textContent = "Absolute file path unavailable; refresh and retry.";
          return;
        }
        const ok = await copy(f.absolute_path);
        if (current()) status().textContent = ok ? "Copied path" : "Copy failed";
      } else if (action === "fileKeep") status().textContent = "Kept recorded; no file opened.";
      else if (action === "fileView" || action === "fileCurrent") {
        let ref = f.ref;
        if (action === "fileCurrent") {
          const fresh = await getOverview();
          ref = fresh.files?.find((x) => x.path === f.path && x.ref === f.ref)?.current_ref;
          if (!ref) throw { status: 404 };
        }
        if (!current()) return;
        const blob = await raw(ref);
        if (current()) await view(f.path, blob, current, ref);
      }
    } catch (err) {
      if (current()) {
        if (err.status === 409) { f.state = "changed"; draw(); }
        status().innerHTML = error(err);
      }
    } finally { if (current()) button.disabled = false; }
  };
  load();
  return { destroy() { alive = false; host.onclick = null; } };
}

let mounted = null;
export function drawFiles(host, { key, ...options }) {
  if (host === mounted?.host && key === mounted.key) return;
  mounted?.destroy();
  mounted = { host, key, ...mountFiles(host, options) };
}
export function leaveFiles() { mounted?.destroy(); mounted = null; }
