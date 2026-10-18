// Project settings: where alfrd.yaml lives (active path, ignored root file) and the "Move to .alfrd/" dialog.

import { $, esc, icon, copyText, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";
import { diffHtml } from "./history.js";

const NESTED = ".alfrd/alfrd.yaml";
/** {preview} → plan; {keep: "rename"|"shadow", force} → move alfrd.yaml to .alfrd/alfrd.yaml. */
const migrate = (project, payload) => server.mutate(`/studio/projects/${encodeURIComponent(project)}/manifest/migrate`, payload);
const cache = new Map(); // project -> GET .../manifest/location (dropped after a change)

/** Fill `host` with the active configuration path, plus a notice when another file is ignored or legacy. */
export async function showLocation(host, ctx, project, onChange) {
  let info = cache.get(project);
  if (!info) {
    try { info = await (await server.request(`/api/studio/projects/${encodeURIComponent(project)}/manifest/location`)).json(); } catch { return; }
    cache.set(project, info);
  }
  if (!host.isConnected) return;
  const { active, files } = info.manifest_location;
  if (!active) { host.innerHTML = ""; return; }
  const ignored = files.filter((f) => f.shadowed).map((f) => f.rel);
  const legacy = active !== NESTED;
  const notice = ignored.length
    ? `<code>${esc(ignored.join("</code>, <code>"))}</code> also exists and is ignored: <code>${NESTED}</code> takes precedence, so it is never read or written.`
    : legacy ? `Using <code>${esc(active)}</code> in the project root (older location). <code>${NESTED}</code> is where new configurations are kept.` : "";
  host.innerHTML = `<p class="row gap wrap small"><span class="muted">Configuration file</span><code class="mono" id="ps-active">${esc(active)}</code>
      <button class="icon-btn" data-loc="copy" aria-label="Copy path ${esc(active)}" title="Copy path">${icon("copy")}</button></p>
    ${notice ? `<p class="callout info small" role="status">${icon("info")}<span>${notice}</span>
      <button class="btn sm" data-loc="move">${ignored.length ? "Review…" : "Move to .alfrd/…"}</button></p>` : ""}`;
  host.onclick = (e) => {
    const act = e.target.closest("[data-loc]")?.dataset.loc;
    if (act === "copy") copyText(active).then(() => ctx.toast("Path copied", "ok"));
    else if (act === "move") migrateDialog(ctx, project, () => { cache.delete(project); onChange?.(); });
  };
}

async function migrateDialog(ctx, project, done) {
  loadCss("css/lazy.css");
  let plan;
  try { plan = await migrate(project, { preview: true }); } catch (error) { ctx.toast(`Cannot preview: ${error.message}`, "fail"); return; }
  const conflict = plan.status === "conflict";
  const title = conflict ? "Two different configurations" : plan.status === "cleanup" ? "Retire the ignored root file" : "Move configuration to .alfrd/";
  const lead = conflict
    ? `<code>${NESTED}</code> is in use and differs from <code>${esc(plan.source)}</code>. Moving replaces it with the root file; the current one is kept as <code>${NESTED}.bak</code> and in History.`
    : plan.status === "cleanup" ? `<code>${esc(plan.source)}</code> is identical to <code>${NESTED}</code> and no longer read.`
    : `<code>${esc(plan.source)}</code> is copied unchanged to <code>${NESTED}</code>. Paths inside it still resolve against the project folder.`;
  const label = conflict ? `Replace ${NESTED}` : plan.status === "cleanup" ? "Rename old file" : "Move";
  ctx.modal(`<header class="modal-h"><h2>${icon("folder")} ${title}</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p>${lead}</p>${conflict ? diffHtml(plan.diff) : ""}
      ${plan.valid ? "" : `<p class="callout fail" role="alert">${esc(plan.error)}</p>`}
      <fieldset class="small"><legend>The old file</legend>
        <label><input type="radio" name="keep" value="rename" checked> Rename it to <code>${esc(plan.rename_to || plan.source + ".migrated")}</code> (recommended; nothing is deleted)</label><br>
        <label><input type="radio" name="keep" value="shadow"> Leave it in place (ignored, lower precedence)</label></fieldset>
      <p id="loc-msg" class="small" role="status"></p></div>
    <footer class="modal-f row gap"><span class="grow"></span><button class="btn" data-close id="loc-cancel">Cancel</button>
      <button class="btn ${conflict ? "danger" : "primary"}" id="loc-go" ${plan.valid ? "" : "disabled"}>${label}</button></footer>`, (root, close) => {
    if (conflict) $("#loc-cancel", root).focus(); // the safe choice first when something is replaced
    $("#loc-go", root).onclick = async (e) => {
      e.target.disabled = true;
      try {
        await migrate(project, { keep: root.querySelector("[name=keep]:checked").value, force: conflict });
        close();
        ctx.toast(`Configuration is now ${NESTED}`, "ok");
        await ctx.refreshProject(project);
        done();
      } catch (error) {
        e.target.disabled = false;
        $("#loc-msg", root).textContent = `Not moved: ${error.message}`;
      }
    };
  });
}
