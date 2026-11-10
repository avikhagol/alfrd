// Project removal, loaded on demand.

import { $, esc, loadCss, bytes } from "../utils/dom.js";
import { server } from "../data/server.js";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function countsText(c = {}) {
  return `${plural(c.runs || 0, "run")}, ${plural(c.datasets || 0, "dataset")}, ${plural(c.workflows || 0, "workflow")}`;
}

export function sizeText(m) {
  if (!m || !m.measured || m.files == null) return null;
  const prefix = m.truncated ? "at least " : "";
  return `${prefix}${plural(m.files, "file")} (${prefix}${bytes(m.bytes || 0)})`;
}

// Opens before either preview or size request resolves.
export function openRemoval(ctx, { key, label, root, onDone } = {}) {
  if (!key) return;
  loadCss("css/lazy.css");
  let preview = null;
  let measured = null;
  let name = label || key;

  ctx.modal(`
    <div id="rm-main">
      <header class="modal-h rm-h"><div>
        <h2 id="rm-title" tabindex="-1">Remove “${esc(name)}”?</h2>
        <p class="rm-root mono small" id="rm-root" ${root ? "" : "hidden"}>${esc(root || "")}</p>
      </div></header>
      <div class="modal-b rm-b" id="rm-body"><p class="rm-note">Loading what would be removed…</p></div>
      <div class="modal-b rm-b rm-msgs">
        <p id="rm-status" class="rm-status" role="status" aria-live="polite"></p>
        <p id="rm-alert" class="rm-alert" role="alert"></p>
      </div>
      <footer class="modal-f rm-f">
        <button class="btn primary" id="rm-forget" disabled>Forget</button>
        <button class="btn danger" id="rm-delete" disabled>Delete permanently</button>
        <button class="btn" id="rm-cancel" data-close>Cancel</button>
      </footer>
    </div>
    <div id="rm-sure" hidden>
      <header class="modal-h rm-h"><h2 id="rm-sure-title" class="rm-sure-title" tabindex="-1">Delete the entire folder?</h2></header>
      <div class="modal-b rm-b">
        <section class="rm-sec rm-danger">
          <p id="rm-sure-what"></p>
          <p class="rm-note" id="rm-sure-git" hidden><b>The folder is a Git repository; uncommitted work and history will be lost too.</b></p>
        </section>
        <p id="rm-sure-status" class="rm-status" role="status" aria-live="polite"></p>
        <p id="rm-sure-alert" class="rm-alert" role="alert"></p>
      </div>
      <footer class="modal-f rm-f">
        <button class="btn" id="rm-sure-cancel">Cancel</button>
        <button class="btn danger" id="rm-sure-yes">Yes, delete everything</button>
      </footer>
    </div>`, (dlg, close) => {
    dlg.setAttribute("aria-labelledby", "rm-title");
    const forgetBtn = $("#rm-forget", dlg);
    const deleteBtn = $("#rm-delete", dlg);
    const status = $("#rm-status", dlg);
    const alert = $("#rm-alert", dlg);
    const main = $("#rm-main", dlg);
    const sure = $("#rm-sure", dlg);
    const sureCancel = $("#rm-sure-cancel", dlg);
    const sureYes = $("#rm-sure-yes", dlg);
    let busy = false;
    const blocked = () => !preview || !preview.can_remove;
    const scope = () => preview?.delete_scope || { alfrd_files: [], all_files_allowed: false };
    const input = () => $("#rm-name", dlg);
    const deleteAllowed = () => !blocked() && input()?.value === name; // case-sensitive
    const sync = () => {
      forgetBtn.disabled = busy || blocked();
      deleteBtn.disabled = busy || !deleteAllowed();
      sureYes.disabled = sureCancel.disabled = busy;
    };

    const sureText = () => {
      const n = sizeText(measured);
      return `This permanently deletes the folder ${scope().path} and everything inside it${n ? `: ${n}` : measured?.error ? " (size unknown)" : " (size still being calculated)"}. This cannot be undone.`;
    };
    const showSize = () => {
      const what = $("#rm-all-what", dlg);
      if (what) what.textContent = sureText();
      $("#rm-sure-what", dlg).textContent = sureText();
    };

    const fill = () => {
      name = preview.name || name;
      const where = preview.root_path || root || "";
      $("#rm-title", dlg).textContent = `Remove “${name}”?`;
      Object.assign($("#rm-root", dlg), { textContent: where, hidden: !where });
      const counts = countsText(preview.counts);
      const sc = scope();
      const listed = sc.alfrd_files || [];
      const alfrdText = !sc.exists ? "The folder no longer exists; only the database records are removed."
        : listed.length ? `Deletes ALFRD's files: ${listed.slice(0, 8).join(", ")}${listed.length > 8 ? ` and ${listed.length - 8} more` : ""}. Your other files and folders stay.`
          : "No ALFRD files are left in the folder; only the database records are removed.";
      $("#rm-body", dlg).innerHTML = `
        ${blocked() ? `<p class="rm-reason" id="rm-reason">${esc(preview.reason || "")}</p>` : ""}
        <section class="rm-sec" aria-labelledby="rm-forget-h">
          <h3 id="rm-forget-h">Forget</h3>
          <p>Remove this project and ${esc(counts)} from the runtime database. Files stay on disk. You can rediscover the project later.</p>
        </section>
        <section class="rm-sec rm-danger" aria-labelledby="rm-delete-h">
          <h3 id="rm-delete-h">Delete permanently</h3>
          <p>Removes ${esc(counts)} from the runtime database.</p>
          <p class="rm-note" id="rm-delete-what">${esc(alfrdText)}</p>
          ${sc.exists && sc.all_files_allowed ? `<label class="rm-all"><input type="checkbox" id="rm-all" ${sc.all_files_allowed ? "" : "disabled"} aria-describedby="rm-all-what"> Delete all files and folders</label>
          <p class="rm-note" id="rm-all-what">${esc(sureText())}</p>
          ${sc.git_repository ? `<p class="rm-note" id="rm-all-git" hidden><b>The folder is a git repository: its whole history is deleted too.</b></p>` : ""}` : ""}
          <label class="rm-confirm">Type <b class="mono">${esc(name)}</b> to confirm
            <input id="rm-name" class="input" type="text" autocomplete="off" spellcheck="false" aria-describedby="rm-delete-what" ${blocked() ? "disabled" : ""}>
          </label>
        </section>`;
      const describedBy = blocked() ? "rm-reason" : "rm-delete-what";
      deleteBtn.setAttribute("aria-describedby", describedBy);
      if (blocked()) forgetBtn.setAttribute("aria-describedby", "rm-reason");
      $("#rm-sure-git", dlg).hidden = !sc.git_repository;
      showSize();
      sync();
    };

    dlg.addEventListener("keydown", (e) => { if (e.target.id === "rm-name" && e.key === "Enter") e.preventDefault(); }); // never submits
    dlg.addEventListener("input", (e) => { if (e.target.id === "rm-name") sync(); });
    dlg.addEventListener("change", (e) => {
      if (e.target.id !== "rm-all") return;
      const git = $("#rm-all-git", dlg);
      if (git) git.hidden = !e.target.checked;
      deleteBtn.textContent = e.target.checked ? "Delete everything" : "Delete permanently";
    });

    const confirmStep = (on) => {
      dlg.setAttribute("aria-labelledby", on ? "rm-sure-title" : "rm-title");
      main.hidden = on;
      sure.hidden = !on;
      if (on) {
        $("#rm-sure-status", dlg).textContent = $("#rm-sure-alert", dlg).textContent = "";
        showSize();
        sureCancel.focus(); // the safe choice is the default
      } else deleteBtn.focus();
    };
    dlg.addEventListener("beforeclose", (e) => {
      if (busy) e.preventDefault();
      else if (!sure.hidden) { e.preventDefault(); confirmStep(false); }
    });
    sureCancel.addEventListener("click", () => { if (!busy) confirmStep(false); });

    const finish = (res, text) => {
      busy = false;
      sure.hidden = true; // let beforeclose through
      close();
      ctx.toast?.(text, "ok");
      onDone?.(res);
    };

    forgetBtn.addEventListener("click", async () => {
      if (busy || blocked()) return;
      busy = true;
      sync();
      alert.textContent = "";
      forgetBtn.textContent = "Forgetting…";
      status.textContent = `Forgetting ${name}…`;
      try {
        const res = await server.forgetProject(key);
        ctx.log?.("info", `Project ${key} forgotten (runtime database only).`, "server");
        finish(res, `Forgot ${name} (${countsText(res)})`);
      } catch (error) {
        busy = false;
        forgetBtn.textContent = "Forget";
        status.textContent = "";
        alert.textContent = `Forget failed: ${error.message}`;
        sync();
      }
    });

    const remove = async (everything) => {
      const out = everything ? { status: $("#rm-sure-status", dlg), alert: $("#rm-sure-alert", dlg) } : { status, alert };
      busy = true;
      sync();
      out.alert.textContent = "";
      out.status.textContent = `Deleting ${name}…`;
      try {
        const res = await server.deleteProject(key, { confirm: input().value, all_files: everything });
        const detail = res.all_files ? `the folder ${res.path}` : `${plural(res.removed?.length || 0, "ALFRD file")}`;
        ctx.log?.("info", `Project ${key} deleted: ${detail} removed and the project forgotten.`, "server");
        finish({ ...res, deleted: key }, `Deleted ${name} (${detail})`);
      } catch (error) {
        busy = false;
        out.status.textContent = "";
        out.alert.textContent = `Delete failed: ${error.message}`;
        sync();
      }
    };
    deleteBtn.addEventListener("click", () => {
      if (busy || !deleteAllowed()) return;
      if ($("#rm-all", dlg)?.checked) confirmStep(true); // last step before the whole folder goes
      else remove(false);
    });
    sureYes.addEventListener("click", () => { if (!busy && deleteAllowed()) remove(true); });

    server.removalPreview(key).then((res) => {
      if (!dlg.isConnected) return;
      preview = res;
      fill();
      const sc = scope();
      if (!sc.exists || !sc.all_files_allowed) return;
      server.removalSize(key)
        .then((m) => { measured = m; })
        .catch((error) => { measured = { error: error.message }; })
        .finally(() => { if (dlg.isConnected) showSize(); });
    }).catch((error) => {
      if (!dlg.isConnected) return;
      $("#rm-body", dlg).innerHTML = "";
      alert.textContent = `Cannot remove ${name}: ${error.message}`;
    });

    // Focus the heading after modal setup.
    setTimeout(() => $("#rm-title", dlg)?.focus(), 0);
  }, "rm-dlg");
}
