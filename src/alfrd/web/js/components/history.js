// History of alfrd.yaml (and other tracked files): versions, diff between any two, Restore.
// Also the save-conflict dialog (the file changed on disk since it was loaded).
// Loaded with import() from Project settings; the server keeps the versions (alfrd.history).

import { $, $$, esc, icon, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

/** Unified diff text → coloured lines. */
export function diffHtml(text) {
  if (!text) return '<p class="muted small">No differences.</p>';
  return `<pre class="diff">${text.split("\n").map((l) => {
    const c = l.startsWith("@@") ? "d-h" : l.startsWith("+++") || l.startsWith("---") ? "d-f" : l[0] === "+" ? "d-add" : l[0] === "-" ? "d-del" : "";
    return `<span class="${c}">${esc(l)}</span>`;
  }).join("\n")}</pre>`;
}

const when = (iso) => { try { return new Date(iso).toLocaleString(); } catch { return iso; } };

function conflictDialog(ctx, project, { conflict, overwrite, loadDisk }) {
  ctx.modal(`<header class="modal-h"><h2>${icon("alert")} alfrd.yaml changed on disk</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b"><p class="small">Someone (another Studio, an editor, a script) saved alfrd.yaml after you loaded it. Nothing was overwritten. Differences from the file on disk to your version:</p>
      ${diffHtml(conflict.diff)}</div>
    <footer class="modal-f row gap right"><button class="btn" data-close>Cancel</button><button class="btn" id="hc-load">${icon("reset")} Load the disk version</button><button class="btn danger" id="hc-over">${icon("save")} Overwrite with mine</button></footer>`,
  (root, close) => {
    $("#hc-over", root).addEventListener("click", async () => {
      try { await overwrite(); close(); } catch (error) { ctx.toast(`Not saved: ${error.message}`, "fail"); }
    });
    $("#hc-load", root).addEventListener("click", () => { close(); loadDisk?.(); });
  }, "wide");
}

export async function openHistory(ctx, project, opts = {}) {
  loadCss("css/lazy.css");
  if (opts.conflict) return conflictDialog(ctx, project, opts);
  let file = opts.file || "alfrd.yaml";
  const load = () => server.history(project, file);
  let data;
  try { data = await load(); } catch (error) { ctx.toast(`History: ${error.message}`, "fail"); return; }
  const canWrite = Boolean(server.session?.mutations_enabled);
  ctx.modal(`<header class="modal-h"><h2>${icon("clock")} History · <span class="mono" id="hi-file"></span></h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b hist"><div id="hi-body"></div><div id="hi-diff"></div></div>`, (root, close) => {
    const draw = () => {
      $("#hi-file", root).textContent = file;
      const vs = data.versions || [];
      $("#hi-body", root).innerHTML = `
        <div class="row gap wrap small">${(data.tracked || []).length > 1 ? `<label>File <select class="input sm" id="hi-pick">${data.tracked.map((f) => `<option ${f === file ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>` : ""}
          <span class="muted">${vs.length} version(s), newest first · keeps ${esc(data.keep)} · tracked files: alfrd.yaml <code>history.files</code></span></div>
        ${vs.length ? `<table class="tbl small hist-t"><thead><tr><th title="From">A</th><th title="To">B</th><th>Saved</th><th>By</th><th>Source</th><th>Note</th><th></th></tr></thead><tbody>
          <tr><td><input type="radio" name="ha" value="current"></td><td><input type="radio" name="hb" value="current" checked></td><td colspan="4"><b>current file</b></td><td></td></tr>
          ${vs.map((v, i) => `<tr><td><input type="radio" name="ha" value="${esc(v.version)}" ${i === 0 ? "checked" : ""}></td><td><input type="radio" name="hb" value="${esc(v.version)}"></td>
            <td class="nowrap">${esc(when(v.at))}</td><td class="mono">${esc(v.who || "")}</td><td><span class="badge tone-${v.source === "external" ? "warn" : v.source === "restore" ? "run" : "muted"}">${esc(v.source)}</span></td><td class="muted">${esc(v.message || "")}</td>
            <td><button class="link-btn small" data-view="${esc(v.version)}">view</button>${canWrite && !(data.current_hash || "").startsWith(v.hash) ? ` <button class="link-btn small" data-restore="${esc(v.version)}">restore</button>` : ""}</td></tr>`).join("")}
          </tbody></table>
          <div class="row gap"><button class="btn sm" id="hi-diff-go">${icon("columns")} Diff A → B</button></div>` : '<p class="muted small">No versions yet: one is kept at every save (Studio, CLI) and when the file changes on disk while <code>alfrd serve</code> watches it.</p>'}
        ${data.git ? `<details class="small"><summary>git log (${data.git.length})</summary><ul class="mono">${data.git.map((g) => `<li>${esc(g.commit.slice(0, 8))} ${esc(g.at)} ${esc(g.author)}: ${esc(g.subject)}</li>`).join("") || "<li>not committed yet</li>"}</ul></details>` : ""}`;
      $("#hi-pick", root)?.addEventListener("change", async (e) => { file = e.target.value; data = await load(); $("#hi-diff", root).innerHTML = ""; draw(); });
      $("#hi-diff-go", root)?.addEventListener("click", async () => {
        const a = $("input[name=ha]:checked", root)?.value;
        const b = $("input[name=hb]:checked", root)?.value;
        if (!a || !b || a === b) { $("#hi-diff", root).innerHTML = '<p class="muted small">Pick two different versions.</p>'; return; }
        try { $("#hi-diff", root).innerHTML = diffHtml((await server.historyDiff(project, a, b, file)).diff); } catch (error) { ctx.toast(error.message, "fail"); }
      });
      $$("[data-view]", root).forEach((btn) => btn.addEventListener("click", async () => {
        try { $("#hi-diff", root).innerHTML = `<pre class="diff">${esc((await server.historyVersion(project, btn.dataset.view, file)).text)}</pre>`; } catch (error) { ctx.toast(error.message, "fail"); }
      }));
      $$("[data-restore]", root).forEach((btn) => btn.addEventListener("click", async () => {
        if (!window.confirm(`Restore ${file} to the version of ${when(vs.find((v) => v.version === btn.dataset.restore)?.at)}? The current file is kept as a version.`)) return;
        try {
          await server.historyRestore(project, btn.dataset.restore, file, { base_hash: data.current_hash });
          ctx.toast(`${file} restored`, "ok");
          await ctx.refreshProject(project);
          opts.onRestored?.();
          data = await load();
          draw();
        } catch (error) {
          ctx.toast(`Not restored: ${error.message}`, "fail");
        }
      }));
    };
    draw();
  }, "wide");
}
