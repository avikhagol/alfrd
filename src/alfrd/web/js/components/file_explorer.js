// Project file explorer for plugin pages (api.fileExplorer). Lists one folder at a time from
// GET /studio/projects/<p>/files?path=; the server confines it to the project root. `.ms` folders are leaves.
import { esc, icon, loadCss } from "../utils/dom.js";

const KIND = { dir: "Folder", ms: "Measurement Set", file: "File", link: "Link outside the project (not followed)", broken: "Broken link" };

/**
 * mountExplorer(box, {list(path) → Promise<listing>, select(entry, listing), selectable(entry), selectLabel, start, label})
 * Returns {load(path), destroy()}. `selectable` defaults to Measurement Sets only.
 */
export function mountExplorer(box, { list, select, selectable = (e) => e.kind === "ms", selectLabel = "Open", start = "", label = "Project files" }) {
  loadCss("css/lazy.css");
  let gen = 0, data = null, alive = true;
  box.innerHTML = `<div class="fx" role="region" aria-label="${esc(label)}"><nav class="fx-crumbs" aria-label="Folder path"></nav><div class="fx-body"></div><p class="sr-only" role="status" aria-live="polite"></p></div>`;
  const crumbs = box.querySelector(".fx-crumbs"), body = box.querySelector(".fx-body"), live = box.querySelector("[role=status]");

  const crumbHtml = (path) => {
    const parts = path ? path.split("/") : [];
    const items = [["", "Project folder"], ...parts.map((name, i) => [parts.slice(0, i + 1).join("/"), name])];
    return `<ol>${items.map(([p, name], i) => i === items.length - 1
      ? `<li><span aria-current="location">${esc(name)}</span></li>`
      : `<li><button type="button" class="link-btn" data-fx-go="${esc(p)}">${esc(name)}</button></li>`).join("")}</ol>`;
  };

  const row = (e, i) => {
    const note = e.kind === "file" && e.size != null ? `${e.size.toLocaleString()} B` : KIND[e.kind] || e.kind;
    const action = e.browsable
      ? `<button type="button" class="btn sm" data-fx-go="${esc(e.path)}" aria-label="Open folder ${esc(e.name)}">${icon("folder")} Open folder</button>`
      : selectable(e) ? `<button type="button" class="btn sm primary" data-fx-pick="${i}" aria-label="${esc(selectLabel)}: ${esc(e.name)}">${esc(selectLabel)}</button>`
        : `<span class="muted small">${e.kind === "file" ? "No preview here" : e.kind === "link" || e.kind === "broken" ? "Not followed" : "Not selectable"}</span>`;
    return `<li class="fx-row" data-kind="${esc(e.kind)}"><span class="fx-name mono${e.kind === "link" || e.kind === "broken" ? " muted" : ""}">${icon(e.kind === "dir" ? "folder" : e.kind === "ms" ? "database" : e.kind === "link" ? "link" : "file")} ${esc(e.name)}</span><span class="fx-kind muted small">${esc(note)}</span>${action}</li>`;
  };

  async function load(path = "") {
    const mine = ++gen;
    body.setAttribute("aria-busy", "true");
    body.innerHTML = `<p class="muted">Loading ${esc(path || "the project folder")}…</p>`;
    crumbs.innerHTML = crumbHtml(path);
    try {
      const reply = await list(path);
      if (!alive || mine !== gen) return;
      data = reply;
      crumbs.innerHTML = crumbHtml(reply.path);
      const entries = reply.entries || [];
      body.innerHTML = (reply.parent != null ? `<p><button type="button" class="btn sm" data-fx-go="${esc(reply.parent)}">${icon("chevron")} Up</button></p>` : "")
        + (entries.length ? `<ul class="fx-list">${entries.map(row).join("")}</ul>` : `<p class="empty">This folder is empty.</p>`)
        + (reply.truncated ? `<p class="muted small">Only the first ${entries.length} entries are shown.</p>` : "");
      live.textContent = `${reply.path || "Project folder"}: ${entries.length} item${entries.length === 1 ? "" : "s"}.`;
    } catch (error) {
      if (!alive || mine !== gen) return;
      body.innerHTML = `<div class="callout fail" role="alert"><p>${esc(error.message || String(error))}</p>${path ? '<button type="button" class="btn sm" data-fx-go="">Back to the project folder</button>' : ""}</div>`;
      live.textContent = error.message || String(error);
    } finally {
      if (mine === gen) body.removeAttribute("aria-busy");
    }
  }

  box.addEventListener("click", (event) => {
    const go = event.target.closest("[data-fx-go]");
    if (go) {
      load(go.dataset.fxGo).then(() => (box.querySelector(".fx-list button, .fx-body button") || crumbs.querySelector("[aria-current]"))?.focus?.());
      return;
    }
    const pick = event.target.closest("[data-fx-pick]");
    if (pick && data) select?.(data.entries[Number(pick.dataset.fxPick)], data);
  });
  load(start);
  return { load, destroy() { alive = false; box.replaceChildren(); } };
}
