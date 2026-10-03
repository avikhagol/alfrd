// Remove project dialog (Projects → Remove). Loaded on first use.
//
// Forget removes the project's rows (runs, datasets, workflows) from the
// runtime database only; files stay on disk. Permanent deletion stays
// disabled until a deletion scope is configured on the server (it is not), so
// this dialog never deletes anything on disk.

import { $, esc, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

const DELETE_SCOPE_MISSING = "Deletion scope has not been configured.";
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
  const deleteReason = preview.delete_scope ? "" : DELETE_SCOPE_MISSING;

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
        <p>Also removes ${esc(counts)}.</p>
        ${deleteReason ? `<p class="rm-note" id="rm-delete-reason">${esc(deleteReason)}</p>` : ""}
        <label class="rm-confirm">Type <b class="mono">${esc(name)}</b> to confirm
          <input id="rm-name" class="input" type="text" autocomplete="off" spellcheck="false" aria-describedby="rm-delete-reason" ${blocked || deleteReason ? "disabled" : ""}>
        </label>
      </section>
      <p id="rm-status" class="rm-status" role="status" aria-live="polite"></p>
      <p id="rm-alert" class="rm-alert" role="alert"></p>
    </div>
    <footer class="modal-f rm-f">
      <button class="btn primary" id="rm-forget" ${blocked ? "disabled" : ""} ${blocked ? 'aria-describedby="rm-reason"' : ""}>Forget</button>
      <button class="btn danger" id="rm-delete" disabled aria-describedby="${blocked ? "rm-reason" : "rm-delete-reason"}">Delete permanently</button>
      <button class="btn" id="rm-cancel" data-close>Cancel</button>
    </footer>`, (dlg, close) => {
    const forgetBtn = $("#rm-forget", dlg);
    const deleteBtn = $("#rm-delete", dlg);
    const input = $("#rm-name", dlg);
    const status = $("#rm-status", dlg);
    const alert = $("#rm-alert", dlg);
    let busy = false;
    const deleteAllowed = () => !blocked && !deleteReason && input.value === name; // case-sensitive
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
        await server.deleteProject(key, { confirm: input.value });
        status.textContent = `Deleted ${name}.`;
        busy = false;
        close();
        onDone?.({ deleted: key });
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
