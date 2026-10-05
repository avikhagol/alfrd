// Folder browser → New folder (lazy). One inline row creates a sub-folder of the
// browsed folder (POST /api/studio/fs/mkdir); it never connects a project.
// folder_browser.js asks it first about clicks and keys.

import { esc, icon, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

export const FOLDER_NAME_COPY = {
  empty: "Enter a folder name.",
  invalid: "Use a folder name, without / or a parent-folder reference.",
  unavailable: "New folder is unavailable here. Choose another folder or cancel.",
};
const duplicateCopy = (name) => `A folder named ‘${name}’ already exists here.`;

/** "empty" | "invalid" | "duplicate" (in the visible listing) | "". Early feedback only: the server decides. */
export function folderNameProblem(name, entries = []) {
  if (name === "") return "empty";
  if (!name.trim() || name === "." || name === ".." || /[/\\\0]/.test(name)) return "invalid";
  return (entries || []).some((e) => e.name === name) ? "duplicate" : "";
}

const problemCopy = (problem, name) => (problem === "duplicate" ? duplicateCopy(name) : FOLDER_NAME_COPY[problem] || "");
const reasonOf = (error) => error?.reason || error?.body?.error?.reason;

/** Inline copy for a failed mkdir (`server.mkdir` errors carry `status` and `reason`). */
export function mkdirErrorCopy(error, name) {
  if (error?.status === 403 || reasonOf(error) === "session") {
    return reasonOf(error) === "permission" ? "You don’t have permission to create a folder here."
      : "Folder creation isn’t allowed in this session. Reload Studio and try again.";
  }
  if (error?.status === 409) return duplicateCopy(name);
  return `Couldn’t create the folder. ${error?.message || error || ""}`.trim();
}

let seq = 0;

/** api: { listing, go(path, {focus}), render(), canCreate(), mkdir? }. click/key return true when handled. */
export function folderCreate(host, api) {
  let entry = null; // {parent, name, error, busy}
  let created = null; // {parent, name, path} of a new folder the listing does not show (hidden or truncated)
  let keep = {};
  let shown = api.listing?.path;
  const id = `fsb-new${++seq}`;
  const mkdir = api.mkdir || ((parent, name) => server.mkdir(parent, name));
  // Kept across renders so screen readers announce "Folder … created."
  const status = document.createElement("p");
  status.className = "fsb-status hint";
  status.setAttribute("role", "status");

  const field = () => host.querySelector("[data-fsb-new-name]");
  const focusField = () => {
    const f = field();
    if (!f || f.disabled) return;
    f.focus();
    f.setSelectionRange(f.value.length, f.value.length);
  };
  const describedBy = () => [`${id}-where`, !api.canCreate() && `${id}-why`, entry?.error && `${id}-err`].filter(Boolean).join(" ");
  const errorHtml = () => `<p class="callout fail fsb-new-error" id="${id}-err" role="alert">${icon("alert")}<span>${esc(entry.error)}</span></p>`;
  const blocked = () => entry.busy || !entry.name || !api.canCreate();

  // Validation feedback while typing: patch the row in place (replacing the input would break IME input).
  const sync = () => {
    const f = field();
    const row = host.querySelector("[data-fsb-new-row]");
    if (!f || !row) return;
    row.querySelector(".fsb-new-error")?.remove();
    if (entry.error) row.insertAdjacentHTML("beforeend", errorHtml());
    if (entry.error) f.setAttribute("aria-invalid", "true");
    else f.removeAttribute("aria-invalid");
    f.setAttribute("aria-describedby", describedBy());
    row.querySelector("[data-fsb-new-create]").disabled = blocked();
  };

  const close = () => {
    entry = null;
    api.render();
    (host.querySelector("[data-fsb-new]") || host.querySelector("[data-fsb-open], [data-fsb-up]:not([disabled])"))?.focus();
  };

  const submit = async () => {
    if (!entry || entry.busy) return;
    const name = entry.name;
    const problem = folderNameProblem(name, api.listing?.entries);
    if (problem || !api.canCreate()) {
      // Re-inserting the alert announces it again for this attempt.
      entry.error = problemCopy(problem, name) || entry.error;
      sync();
      focusField();
      return;
    }
    entry.busy = true;
    entry.error = "";
    api.render();
    let made;
    try {
      made = await mkdir(entry.parent, name);
    } catch (error) {
      if (!entry) return;
      entry.busy = false;
      entry.error = mkdirErrorCopy(error, name);
      // A refused or duplicate name: the listing (and its writability) is stale.
      if (error?.status === 409 || (error?.status === 403 && reasonOf(error) === "permission")) await api.go(entry.parent, { focus: false });
      else api.render();
      focusField();
      return;
    }
    const parent = entry.parent;
    const label = made?.name || name;
    entry = null;
    await api.go(parent, { focus: false });
    status.textContent = `Folder ‘${label}’ created.`;
    const i = (api.listing?.entries || []).findIndex((e) => e.path === made?.path || e.name === label);
    const open = i >= 0 ? host.querySelector(`[data-fsb-open="${i}"]`) : null;
    if (open) {
      open.focus();
    } else if (made?.path) {
      created = { parent, name: label, path: made.path };
      api.render();
      host.querySelector("[data-fsb-created-open]")?.focus();
    }
  };

  const onInput = (e) => {
    if (!entry || !e.target.matches("[data-fsb-new-name]")) return;
    entry.name = e.target.value;
    // No "Enter a folder name." while typing: that one waits for a submit attempt.
    const problem = folderNameProblem(entry.name, api.listing?.entries);
    const message = problem === "empty" ? "" : problemCopy(problem, entry.name);
    // Only a changed message is re-inserted, so the alert is not re-announced on every keystroke.
    if (message !== entry.error) {
      entry.error = message;
      sync();
    } else host.querySelector("[data-fsb-new-create]").disabled = blocked();
  };
  host.addEventListener("input", onInput);

  return {
    open() {
      if (!api.listing || !api.canCreate()) return;
      loadCss("css/lazy.css");
      entry ||= { parent: api.listing.path, name: "", error: "", busy: false };
      created = null;
      status.textContent = "";
      api.render();
      focusField();
    },

    /** The row and the created-folder offer, rendered right under the browser's toolbar. */
    html() {
      const path = api.listing?.path;
      // Both belong to the folder they were opened in.
      if (entry?.parent !== path) entry = null;
      if (created?.parent !== path) created = null;
      if (shown !== path) status.textContent = "";
      // Background re-renders keep the caret in the name field and the list's scroll position.
      const f = field();
      const list = host.querySelector(".fsb-list");
      keep = { caret: f && document.activeElement === f ? [f.selectionStart, f.selectionEnd] : null, top: list && shown === path ? list.scrollTop : 0 };
      const offer = created ? `<p class="hint fsb-created">${icon("checkCircle")}<span>‘${esc(created.name)}’ is not shown in this listing.</span> <button type="button" class="btn sm" data-fsb-created-open>Open created folder</button></p>` : "";
      if (!entry) return offer;
      const lock = entry.busy ? " disabled" : "";
      // Not a <form>: the browser sits inside the New project form, so Enter is handled on the input.
      return `
        <div class="field fsb-new" role="group" aria-labelledby="${id}-label" data-fsb-new-row${entry.busy ? ' aria-busy="true"' : ""}>
          <label id="${id}-label" for="${id}-name">Folder name</label>
          <div class="fsb-new-row row gap wrap">
            <input id="${id}-name" class="input grow" type="text" data-fsb-new-name value="${esc(entry.name)}" placeholder="e.g. analysis" autocomplete="off" spellcheck="false" aria-describedby="${describedBy()}"${entry.error ? ' aria-invalid="true"' : ""}${lock}>
            <button type="button" class="btn primary" data-fsb-new-create${blocked() ? " disabled" : ""}>${entry.busy ? "Creating…" : "Create folder"}</button>
            <button type="button" class="btn" data-fsb-new-cancel${lock}>Cancel</button>
          </div>
          <p class="hint fsb-new-where" id="${id}-where">Create inside <span class="mono">${esc(entry.parent)}</span></p>
          ${api.canCreate() ? "" : `<p class="hint warn" id="${id}-why">${FOLDER_NAME_COPY.unavailable}</p>`}
          ${entry.error ? errorHtml() : ""}
        </div>${offer}`;
    },

    after() {
      host.append(status);
      shown = api.listing?.path;
      const list = host.querySelector(".fsb-list");
      if (keep.top && list) list.scrollTop = keep.top;
      const f = field();
      if (keep.caret && f && !f.disabled) {
        f.focus();
        f.setSelectionRange(...keep.caret);
      }
      // While a folder is being created the path cannot change under it (Close stays available).
      if (entry?.busy) host.querySelectorAll("button, input").forEach((el) => { el.disabled ||= !el.matches("[data-fsb-close]"); });
    },

    click(t) {
      if (entry?.busy) return !t.matches("[data-fsb-close]");
      if (t.matches("[data-fsb-new-create]")) submit();
      else if (t.matches("[data-fsb-new-cancel]")) close();
      else if (t.matches("[data-fsb-created-open]")) api.go(created.path);
      else return false;
      return true;
    },

    key(e) {
      const inName = e.target.matches("[data-fsb-new-name]");
      if (inName && e.key === "Enter") {
        // Never let Enter reach (and submit) the New project form around the browser.
        e.preventDefault();
        e.stopPropagation();
        if (!e.isComposing) submit();
        return true;
      }
      if (e.key === "Escape" && entry) {
        // The first Esc cancels New folder (not while it is being created); the next closes the browser.
        e.stopPropagation();
        if (!entry.busy) close();
        return true;
      }
      if (entry?.busy && e.key === "Backspace") e.preventDefault();
      return inName || !!entry?.busy;
    },

    destroy() {
      host.removeEventListener("input", onInput);
    },
  };
}
