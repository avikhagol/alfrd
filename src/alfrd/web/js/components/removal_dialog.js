// Remove project dialog (Projects → Remove). Loaded on first use.
//
// Forget: database rows only. Delete (name typed): also ALFRD's files; with
// "Delete all files and folders" the whole folder (server refuses unsafe ones).

import { $, esc, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

const size = (b) => (b >= 1 << 30 ? `${(b / (1 << 30)).toFixed(1)} GB` : b >= 1 << 20 ? `${(b / (1 << 20)).toFixed(1)} MB` : `${Math.ceil(b / 1024)} KB`);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function countsText(c = {}) {
  return `${plural(c.runs || 0, "run")}, ${plural(c.datasets || 0, "dataset")}, ${plural(c.workflows || 0, "workflow")}`;
}

/**
 * Open the removal dialog for one project.
 * @param {object} ctx  Studio context (needs ctx.modal; ctx.toast / ctx.log are used when present).
 * @param {{key: string, label?: string, root?: string, onDone?: (result: object) => void}} opts
 *   key: project identifier (or unique name) used in API calls; label: display name;
 *   root: project folder (shown until the preview arrives); onDone: called after a successful Forget.
 */
export async function openRemoval(ctx, { key, label, root, onDone } = {}) {
  if (!key) return;
  loadCss("css/lazy.css");
  let preview;
  try {
    preview = await server.removalPreview(key);
  } catch (error) {
    ctx.toast?.(`Cannot remove ${label || key}: ${error.message}`, "fail");
    return;
  }
  const name = preview.name || label || key;
  const where = preview.root_path || root || "";
  const counts = countsText(preview.counts);
  const blocked = !preview.can_remove;
  const reason = preview.reason || "";
  const scope = preview.delete_scope || { alfrd_files: [], all_files_allowed: false };
  const listed = scope.alfrd_files || [];
  const alfrdText = !scope.exists ? "The folder no longer exists; only the database records are removed."
    : listed.length ? `Deletes ALFRD's files: ${listed.slice(0, 8).join(", ")}${listed.length > 8 ? ` and ${listed.length - 8} more` : ""}. Your other files and folders stay.`
      : "No ALFRD files are left in the folder; only the database records are removed.";
  const allText = !scope.all_files_allowed ? scope.all_files_reason || "Not available for this folder."
    : `Deletes the folder ${scope.path} with ${scope.truncated ? "more than " : ""}${plural(scope.files || 0, "file")} (${size(scope.bytes || 0)}). This cannot be undone.`;
  const deleteReason = "";

  ctx.modal(`
    <header class="modal-h rm-h"><div>
      <h2 id="rm-title" tabindex="-1">Remove “${esc(name)}”?</h2>
      ${where ? `<p class="rm-root mono small">${esc(where)}</p>` : ""}
    </div></header>
    <div class="modal-b rm-b">
      ${blocked ? `<p class="rm-reason" id="rm-reason">${esc(reason)}</p>` : ""}
      <section class="rm-sec" aria-labelledby="rm-forget-h">
        <h3 id="rm-forget-h">Forget</h3>
        <p>Remove this project and ${esc(counts)} from the runtime database. Files stay on disk. You can rediscover the project later.</p>
      </section>
      <section class="rm-sec rm-danger" aria-labelledby="rm-delete-h">
        <h3 id="rm-delete-h">Delete permanently</h3>
        <p>Removes ${esc(counts)} from the runtime database.</p>
        <p class="rm-note" id="rm-delete-what">${esc(alfrdText)}</p>
        ${scope.exists ? `<label class="rm-all"><input type="checkbox" id="rm-all" ${scope.all_files_allowed ? "" : "disabled"} aria-describedby="rm-all-what"> Delete all files and folders</label>
        <p class="rm-note" id="rm-all-what">${esc(allText)}</p>
        ${scope.git_repository ? `<p class="rm-note" id="rm-all-git" hidden><b>The folder is a git repository: its whole history is deleted too.</b></p>` : ""}` : ""}
        ${deleteReason ? `<p class="rm-note" id="rm-delete-reason">${esc(deleteReason)}</p>` : ""}
        <label class="rm-confirm">Type <b class="mono">${esc(name)}</b> to confirm
          <input id="rm-name" class="input" type="text" autocomplete="off" spellcheck="false" aria-describedby="${deleteReason ? "rm-delete-reason" : "rm-delete-what"}" ${blocked || deleteReason ? "disabled" : ""}>
        </label>
      </section>
      <p id="rm-status" class="rm-status" role="status" aria-live="polite"></p>
      <p id="rm-alert" class="rm-alert" role="alert"></p>
    </div>
    <footer class="modal-f rm-f">
      <button class="btn primary" id="rm-forget" ${blocked ? "disabled" : ""} ${blocked ? 'aria-describedby="rm-reason"' : ""}>Forget</button>
      <button class="btn danger" id="rm-delete" disabled aria-describedby="${blocked ? "rm-reason" : deleteReason ? "rm-delete-reason" : "rm-delete-what"}">Delete permanently</button>
      <button class="btn" id="rm-cancel" data-close>Cancel</button>
    </footer>`, (dlg, close) => {
    const forgetBtn = $("#rm-forget", dlg);
    const deleteBtn = $("#rm-delete", dlg);
    const input = $("#rm-name", dlg);
    const status = $("#rm-status", dlg);
    const alert = $("#rm-alert", dlg);
    let busy = false;
    const all = $("#rm-all", dlg);
    const deleteAllowed = () => !blocked && !deleteReason && input.value === name; // case-sensitive
    all?.addEventListener("change", () => {
      const git = $("#rm-all-git", dlg);
      if (git) git.hidden = !all.checked;
      deleteBtn.textContent = all.checked ? "Delete everything" : "Delete permanently";
    });
    const sync = () => {
      forgetBtn.disabled = busy || blocked;
      deleteBtn.disabled = busy || !deleteAllowed();
    };
    // Enter in the confirmation field must never submit anything.
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
    input.addEventListener("input", sync);
    // Do not let the dialog close mid-request (Escape / backdrop / Cancel stay inert while busy).
    dlg.addEventListener("beforeclose", (e) => { if (busy) e.preventDefault(); });

    forgetBtn.addEventListener("click", async () => {
      if (busy || blocked) return;
      busy = true;
      sync();
      alert.textContent = "";
      forgetBtn.textContent = "Forgetting…";
      status.textContent = `Forgetting ${name}…`;
      try {
        const res = await server.forgetProject(key);
        status.textContent = `Forgot ${name}: ${countsText(res)} removed from the runtime database. Files stay on disk.`;
        ctx.log?.("info", `Project ${key} forgotten (runtime database only).`, "server");
        busy = false;
        close();
        ctx.toast?.(`Forgot ${name} (${countsText(res)})`, "ok");
        onDone?.(res);
      } catch (error) {
        busy = false;
        forgetBtn.textContent = "Forget";
        status.textContent = "";
        alert.textContent = `Forget failed: ${error.message}`;
        sync();
      }
    });

    deleteBtn.addEventListener("click", async () => {
      if (busy || !deleteAllowed()) return;
      busy = true;
      sync();
      alert.textContent = "";
      status.textContent = `Deleting ${name}…`;
      try {
        const everything = Boolean(all?.checked);
        if (everything && !confirm(`Delete the whole folder ${scope.path}? This cannot be undone.`)) { busy = false; status.textContent = ""; sync(); return; }
        const res = await server.deleteProject(key, { confirm: input.value, all_files: everything });
        status.textContent = `Deleted ${name}.`;
        const detail = res.all_files ? `the folder ${res.path}` : `${plural(res.removed?.length || 0, "ALFRD file")}`;
        ctx.log?.("info", `Project ${key} deleted: ${detail} removed and the project forgotten.`, "server");
        busy = false;
        close();
        ctx.toast?.(`Deleted ${name} (${detail})`, "ok");
        onDone?.({ ...res, deleted: key });
      } catch (error) {
        busy = false;
        status.textContent = "";
        alert.textContent = `Delete failed: ${error.message}`;
        sync();
      }
    });

    // modal() focuses the first control after setup; move focus to the heading instead.
    setTimeout(() => $("#rm-title", dlg)?.focus(), 0);
  }, "rm-dlg");
}
