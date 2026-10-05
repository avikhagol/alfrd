// Setup wizard (`quickstart:` forms; see alfrd/quickstart.py). Loaded on first use.

import { esc, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

const shown = (v) => (Array.isArray(v) ? v.join(", ") : v ?? "");

function control(f, i, canWrite) {
  const value = f.value ?? f.default;
  const id = `qs-${i}`, dis = canWrite ? "" : "disabled";
  const help = f.help ? `<small class="muted" id="${id}-h">${esc(f.help)}</small>` : "";
  const desc = `aria-label="${esc(f.label)}"${f.help ? ` aria-describedby="${id}-h"` : ""}`;
  const req = f.required ? " *" : "";
  if (f.type === "toggle") {
    return `<div class="field"><label><input type="checkbox" id="${id}" data-qs="${i}" ${value === true || value === "True" ? "checked" : ""} ${dis} ${desc}> ${esc(f.label)}</label>${help}</div>`;
  }
  let input;
  if (f.type === "select") {
    input = `<select class="input" id="${id}" data-qs="${i}" ${dis} ${desc}>${f.options.map((o) => `<option ${String(o) === String(value) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  } else if (f.type === "textarea") {
    input = `<textarea class="input" id="${id}" data-qs="${i}" rows="4" ${dis} ${desc}>${esc(shown(value))}</textarea>`;
  } else {
    const type = f.type === "number" ? `type="number" step="any"${f.min != null ? ` min="${esc(f.min)}"` : ""}${f.max != null ? ` max="${esc(f.max)}"` : ""}` : 'type="text"';
    input = `<input class="input${f.type === "path" || f.type === "list" ? " mono" : ""}" ${type} id="${id}" data-qs="${i}" value="${esc(shown(value))}" placeholder="${esc(f.placeholder ?? (f.type === "list" ? "a, b, c" : ""))}" ${f.required ? "required" : ""} ${dis} ${desc}>`;
    if (f.type === "path" && f.pick !== "program") input = `<div class="row gap">${input.replace('class="input', 'class="input grow')}<button type="button" class="btn" data-qs-browse="${i}" ${dis}>Browse…</button></div><div data-qs-folders="${i}" hidden></div>`;
  }
  return `<label class="field" for="${id}"><span>${esc(f.label)}${req}</span>${input}${help}<small class="qs-err" data-qs-err="${i}" role="alert"></small></label>`;
}

/** Open the setup form(s) of a project. `formId` picks one; `onSaved` runs after a successful write. */
export async function openQuickstart(ctx, project, { formId = null, onSaved = null } = {}) {
  loadCss("css/lazy.css");
  const { forms } = await server.quickstart(project);
  const usable = forms.filter((f) => !f.error && (!formId || f.id === formId));
  if (!usable.length) {
    ctx.toast(forms.find((f) => f.error)?.error || "This project declares no setup wizard.", forms.length ? "fail" : "info");
    return;
  }
  let form = usable[0];
  const canWrite = Boolean(server.session?.mutations_enabled);
  ctx.modal(`<header class="modal-h"><h2 id="qs-title">Setup wizard</h2><button class="btn" data-close>Close</button></header>
    <form id="qs-form" novalidate><div class="modal-b" id="qs-body"></div>
    <footer class="modal-f"><p class="muted small grow" id="qs-status" role="status"></p><button class="btn primary" type="submit" ${canWrite ? "" : "disabled"}>Save</button></footer></form>`, (root, close) => {
    const body = root.querySelector("#qs-body");
    const browsers = [];
    const render = () => {
      browsers.splice(0).forEach((b) => b.destroy());
      root.querySelector("#qs-title").textContent = `Setup wizard — ${form.title}`;
      body.innerHTML = `${usable.length > 1 ? `<label class="field"><span>Form</span><select class="input" id="qs-pick">${usable.map((f) => `<option value="${esc(f.id)}" ${f.id === form.id ? "selected" : ""}>${esc(f.title)}</option>`).join("")}</select></label>` : ""}
        ${form.description ? `<p class="muted small">${esc(form.description)}</p>` : ""}
        <p class="muted small">Writes <code>${esc(form.target.file)}</code> in the project folder. * required.${canWrite ? "" : " Read-only session: changes cannot be saved."}</p>
        ${form.fields.map((f, i) => control(f, i, canWrite)).join("")}`;
      body.querySelector("#qs-pick")?.addEventListener("change", (e) => { form = usable.find((f) => f.id === e.target.value); render(); });
      body.querySelectorAll("[data-qs-browse]").forEach((b) => b.addEventListener("click", () => {
        const i = b.dataset.qsBrowse;
        browsers.push(ctx.browseFolder(body.querySelector(`[data-qs-folders="${i}"]`), body.querySelector(`[data-qs="${i}"]`)));
      }));
    };
    render();
    root.addEventListener("beforeclose", () => browsers.forEach((b) => b.destroy()));
    root.querySelector("#qs-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = root.querySelector("button[type=submit]"), status = root.querySelector("#qs-status");
      body.querySelectorAll("[data-qs-err]").forEach((e) => { e.textContent = ""; });
      const values = {};
      form.fields.forEach((f, i) => {
        const el = body.querySelector(`[data-qs="${i}"]`);
        values[f.key] = f.type === "toggle" ? el.checked : el.value;
      });
      button.disabled = true; status.textContent = "Saving…";
      try {
        const res = await server.quickstartApply(project, form.id, values);
        const written = Object.keys(res.written || {});
        const warnings = Object.entries(res.warnings || {});
        status.textContent = written.length ? `Saved ${written.length} value${written.length === 1 ? "" : "s"} to ${res.file}.` : "Nothing changed.";
        warnings.forEach(([key, text]) => { const i = form.fields.findIndex((f) => f.key === key); const e = body.querySelector(`[data-qs-err="${i}"]`); if (e) e.textContent = `Note: ${text}`; });
        form.fields.forEach((f) => { if (f.key in (res.written || {})) f.value = res.written[f.key]; });
        if (written.length) { ctx.toast(`Setup saved to ${res.file}`, "ok"); await onSaved?.(res); }
        if (!warnings.length && written.length) close();
      } catch (error) {
        const errors = error.body?.field_errors || {};
        Object.entries(errors).forEach(([key, text]) => { const i = form.fields.findIndex((f) => f.key === key); const e = body.querySelector(`[data-qs-err="${i}"]`); if (e) e.textContent = text; });
        status.textContent = error.message;
      } finally { button.disabled = !canWrite; }
    });
  });
}

// Dotted paths, kept in step with alfrd/quickstart.py (tests/studio_js/quickstart_paths.test.mjs).

/** Dotted path into alfrd.yaml data; a list segment is an index or an item's `name`. Mirrors alfrd/quickstart.py. */
function step(node, part) {
  if (Array.isArray(node)) {
    const i = /^\d+$/.test(part) ? Number(part) : node.findIndex((x) => x && typeof x === "object" && String(x.name) === part);
    return i >= 0 && i < node.length ? [i, node[i]] : null;
  }
  return node && typeof node === "object" && part in node ? [part, node[part]] : null;
}
export function getPath(data, key) {
  let node = data;
  for (const part of key.split(".")) { const s = step(node, part); if (!s) return undefined; node = s[1]; }
  return node;
}
export function setPath(data, key, value) {
  const parts = key.split(".");
  let node = data;
  for (const part of parts.slice(0, -1)) {
    let s = step(node, part);
    if (!s) {
      if (Array.isArray(node) || !node || typeof node !== "object") throw new Error(`${key}: “${part}” is not in alfrd.yaml`);
      if (value === undefined) return;
      node[part] = {}; s = [part, node[part]];
    }
    node = s[1];
  }
  const last = parts.at(-1);
  if (Array.isArray(node)) {
    if (!/^\d+$/.test(last)) throw new Error(`${key}: list position “${last}” does not exist`);
    if (value === undefined) node.splice(Number(last), 1); else node[Number(last)] = value;
  } else if (value === undefined) delete node[last];
  else node[last] = value;
}
