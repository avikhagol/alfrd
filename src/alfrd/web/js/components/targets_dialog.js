// Targets CSV dialog (Overview → Targets): import a list of sources with their
// FITS file names into the project's targets file (alfrd.yaml `targets.csv`,
// default alfrd.targets.csv), or download it. Loaded on first use.
//
// Server mode previews and saves through the server (alfrd.targets_csv);
// browser mode writes into the opened folder, or downloads the file.

import { $, on, esc, icon, download, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";
import { targetsSpec, parseTargets, mergeTargets, dumpTargets, dropTargets } from "../data/targets.js";

const MAX_BYTES = 8 * 1024 * 1024;
const PREVIEW_ROWS = 50;

function projectOf(ctx) {
  const p = ctx.state.selectedProject;
  if (p && p !== "all") return p;
  return ctx.projects()[0]?.id || Object.keys(ctx.state.trees || {})[0] || null;
}

const onServer = (ctx, project) => ctx.state.mode === "server" && ctx.state.trees?.[project]?.provider === "server";

/** Rows of the current targets file: from the server, or from the imported tables. */
async function currentRows(ctx, project, spec) {
  if (onServer(ctx, project)) {
    const data = await server.targets(project);
    return { rows: data.rows || [], spec: data.spec };
  }
  // Browser mode: the file on disk first (so a merge never drops rows), else the imported copy.
  const onDisk = ctx.canWrite(project) ? await ctx.readProjectText(project, spec.csv).catch(() => null) : null;
  const text = onDisk ?? ctx.state.trees?.[project]?.targetsFile?.text;
  return { rows: text ? parseTargets(text, spec).rows : [], spec: null };
}

export async function openTargets(ctx, action = "import", only = null, names = []) {
  const project = only || projectOf(ctx);
  if (!project) { ctx.toast("Open a project first", "warn"); return; }
  const defs = ctx.state.trees?.[project]?.defs || {};
  const spec = targetsSpec(defs, defs.execution || {});
  let existing = [];
  let serverSpec = null;
  try {
    ({ rows: existing, spec: serverSpec } = await currentRows(ctx, project, spec));
  } catch (error) {
    ctx.toast(`Targets file: ${error.message}`, "warn");
  }
  const rel = serverSpec?.csv || spec.csv;
  if (serverSpec) Object.assign(spec.written, { key: serverSpec.key_column, files: serverSpec.files_column, code: serverSpec.code_column });
  if (action === "remove") return removeTargets(ctx, project, spec, rel, existing, names);
  if (action === "download") {
    if (!existing.length) { ctx.toast(`${rel} has no rows yet`, "warn"); return; }
    download(rel.split("/").pop(), dumpTargets(spec, existing), "text/csv");
    return;
  }
  openImportDialog(ctx, project, spec, rel, existing);
}

function openImportDialog(ctx, project, spec, rel, existing) {
  loadCss("css/lazy.css");
  const server_ = onServer(ctx, project);
  const canSave = server_ ? Boolean(server.session?.mutations_enabled) : ctx.canWrite(project);
  const st = { text: "", name: "", mapping: {}, mode: "merge", parsed: null, merged: null };

  ctx.modal(`
    <header class="modal-h"><h2>${icon("upload")} Import targets · <span class="mono">${esc(rel)}</span></h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b tg-dlg">
      <p class="muted small">A CSV/TSV with a target (source) column and its FITS file names, optionally a project code. Kept across plans; the Run dialog fills <code>${esc(spec.written.files)}</code> from it. ${existing.length ? `Now: <b>${existing.length}</b> target(s).` : "The file does not exist yet."}</p>
      <label class="drop" id="tg-drop" tabindex="0">${icon("upload", "big")}<b>Drop a CSV/TSV here</b><span class="muted">or <label class="link-btn">choose a file<input type="file" id="tg-file" accept=".csv,.tsv,.txt,text/csv" hidden></label>, or paste below</span></label>
      <textarea id="tg-text" class="input mono" rows="5" placeholder="${esc(spec.aliases.key[1] || "source")},${esc(spec.aliases.files[1] || "fitsfilenames")}&#10;J0804+1012,&quot;BW106A.idifits,BW106B.idifits&quot;" spellcheck="false"></textarea>
      <div id="tg-map"></div>
      <div id="tg-preview"></div>
    </div>
    <footer class="modal-f row gap">
      <div class="seg" role="group" aria-label="Import mode"><button class="on" data-mode="merge" aria-pressed="true" title="Keep existing rows, add new ones, update FITS names">Merge</button><button data-mode="replace" aria-pressed="false" title="The file becomes exactly the imported rows">Replace</button></div>
      <span class="grow"></span>
      ${canSave ? "" : `<span class="muted small">${server_ ? "Only from a browser on the same machine as alfrd serve." : "No writable folder: the file is downloaded."}</span>`}
      <button class="btn primary" id="tg-go" disabled>${icon("check")} ${canSave ? "Save" : "Download"}</button>
    </footer>`, (root, close) => {
    const setText = (text, name = "") => {
      if (text.length > MAX_BYTES) { ctx.toast("File is larger than 8 MB", "warn"); return; }
      st.text = text; st.name = name; st.mapping = {};
      $("#tg-text", root).value = text;
      update();
    };
    const update = () => {
      const box = $("#tg-preview", root);
      const map = $("#tg-map", root);
      if (!st.text.trim()) { box.innerHTML = map.innerHTML = ""; $("#tg-go", root).disabled = true; return; }
      st.parsed = parseTargets(st.text, spec, st.mapping);
      st.merged = mergeTargets(existing, st.parsed.rows, st.mode);
      const { header, columns, rows, problems } = st.parsed;
      const opt = (role) => `<label>${esc({ key: "Target", files: "FITS files", code: "Project code" }[role])}<select class="input sm" data-role="${role}"><option value="">(none)</option>${header.map((h) => `<option ${columns[role] === h ? "selected" : ""}>${esc(h)}</option>`).join("")}</select></label>`;
      map.innerHTML = header.length ? `<div class="row gap wrap tg-map">${["key", "files", "code"].map(opt).join("")}</div>` : "";
      const m = st.merged;
      const status = (r) => {
        const key = r.code ? `${r.target}@${r.code}` : r.target;
        return m.added.includes(key) ? "new" : m.updated.includes(key) ? "updated" : "same";
      };
      const shown = rows.slice(0, PREVIEW_ROWS);
      box.innerHTML = `
        <p class="small">${[
          `<b>${m.added.length}</b> new`, `<b>${m.updated.length}</b> updated`, `${m.unchanged.length} unchanged`,
          m.removed.length ? `<b class="st-failed">${m.removed.length}</b> removed` : "",
          problems.length ? `<b class="st-failed">${problems.length}</b> problem(s)` : "",
        ].filter(Boolean).join(" · ")} → ${m.rows.length} target(s)</p>
        ${problems.length ? `<ul class="msgs">${problems.slice(0, 8).map((p) => `<li class="lvl-error">${p.line ? `line ${p.line}: ` : ""}${esc(p.message)}</li>`).join("")}</ul>` : ""}
        ${rows.length ? `<div class="tg-table"><table class="tbl sm"><thead><tr><th></th><th>${esc(spec.written.key)}</th><th>${esc(spec.written.files)}</th><th>${esc(spec.written.code)}</th></tr></thead><tbody>
          ${shown.map((r) => `<tr><td><span class="tag tg-${status(r)}">${status(r)}</span></td><td class="mono">${esc(r.target)}</td><td class="mono small">${esc(r.files || "-")}</td><td class="mono">${esc(r.code || "")}</td></tr>`).join("")}
        </tbody></table>${rows.length > shown.length ? `<p class="muted small">… ${rows.length - shown.length} more</p>` : ""}</div>` : ""}`;
      $("#tg-go", root).disabled = Boolean(problems.length) || !rows.length;
    };
    const go = async () => {
      const btn = $("#tg-go", root);
      btn.disabled = true;
      try {
        if (server_ && canSave) {
          const res = await server.targetsSave(project, { text: st.text, mode: st.mode, columns: st.mapping });
          ctx.toast(`${rel}: ${res.added.length} new, ${res.updated.length} updated`, "ok");
          await ctx.refreshProject(project);
        } else {
          const text = dumpTargets(spec, st.merged.rows);
          if (canSave && (await ctx.writeProjectFile(project, rel, text))) {
            ctx.toast(`${rel} written`, "ok");
            await ctx.refreshProject(project);
          } else {
            download(rel.split("/").pop(), text, "text/csv");
          }
        }
        ctx.log("info", `Targets imported${st.name ? ` from ${st.name}` : ""} into ${rel} (${st.mode}).`, "targets");
        close();
      } catch (error) {
        ctx.toast(error.message, "fail");
        btn.disabled = false;
      }
    };
    $("#tg-file", root).addEventListener("change", async (e) => {
      const f = e.target.files?.[0];
      if (f) setText(await f.text(), f.name);
    });
    const drop = $("#tg-drop", root);
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      const f = e.dataTransfer?.files?.[0];
      if (f) setText(await f.text(), f.name);
    });
    let timer = null;
    $("#tg-text", root).addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { st.text = e.target.value; update(); }, 200);
    });
    on(root, "change", "select[data-role]", (e, s) => { st.mapping[s.dataset.role] = s.value; update(); });
    on(root, "click", "[data-mode]", (e, b) => {
      st.mode = b.dataset.mode;
      root.querySelectorAll("[data-mode]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
      update();
    });
    $("#tg-go", root).addEventListener("click", go);
  }, "wide");
}

/** Remove targets from the targets file (after confirming). Result CSVs and work dirs are not touched. */
async function removeTargets(ctx, project, spec, rel, existing, names) {
  const local = dropTargets(existing, names);
  if (!local.removed.length) {
    ctx.toast(`None of the selected targets is in ${rel}`, "warn");
    return null;
  }
  const list = local.removed.slice(0, 12).join(", ") + (local.removed.length > 12 ? ", …" : "");
  if (!window.confirm(`Remove ${local.removed.length} row(s) from ${rel}?\n\n${list}\n\nResult CSVs, work dirs and the plan CSV are not changed.`)) return null;
  let result = local;
  if (onServer(ctx, project)) {
    if (!server.session?.mutations_enabled) { ctx.toast("Only from a browser on the same machine as alfrd serve", "warn"); return null; }
    result = await server.targetsRemove(project, names);
  } else if (!(await ctx.writeProjectFile(project, rel, dumpTargets(spec, local.rows)))) {
    ctx.toast("No writable project folder: open the project folder to edit its targets file", "warn");
    return null;
  }
  ctx.log("info", `Removed ${result.removed.length} row(s) from ${rel}: ${result.removed.join(", ")}.`, "targets");
  await ctx.refreshProject(project);
  return result;
}
