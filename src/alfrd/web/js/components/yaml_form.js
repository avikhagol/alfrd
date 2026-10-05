// VIEW — Settings → All settings: every alfrd.yaml key as a form field.
// The tree and the edits live in data/yaml_form.js; this file draws and binds them.

import { esc, icon, loadCss } from "../utils/dom.js";
import { parseYaml } from "../utils/yaml_parser.js";
import { formTree, coerce, applyFormEdit, hintFor, newItem } from "../data/yaml_form.js";

const opened = new Set(); // group paths the user opened; kept across rebuilds
const id = (path) => esc(JSON.stringify(path));
const human = (key) => (typeof key === "number" ? key : String(key).replace(/[_-]+/g, " ").replace(/^./, (c) => c.toUpperCase()));
const shown = (v) => (v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));

function field(f) {
  const h = f.hint || {};
  const def = h.default !== undefined ? `default: ${shown(h.default)}` : "";
  const name = typeof f.key === "number" ? `<span class="mono">${esc(f.label)}</span>` : `${esc(human(f.key))} <span class="mono muted">${esc(f.key)}</span>`;
  const attrs = `data-yf="${id(f.path)}" data-yf-type="${f.type}" aria-label="${esc(f.path.join("."))}"`;
  const reset = typeof f.key === "number" ? `<button type="button" class="icon-btn xs" data-yf-remove="${id(f.path)}" title="Remove this item" aria-label="Remove ${esc(f.path.join("."))}">${icon("trash")}</button>`
    : !f.unset && f.hint && !h.required ? `<button type="button" class="icon-btn xs" data-yf-reset="${id(f.path)}" title="Remove from alfrd.yaml${def ? ` (back to ${esc(def)})` : ""}" aria-label="Reset ${esc(f.path.join("."))}">${icon("reset")}</button>` : "";
  const note = [f.unset ? "not set" : "", def, h.help || ""].filter(Boolean).join(" · ");
  const tail = `${note ? `<small class="muted">${esc(note)}</small>` : ""}<small class="qs-err" data-yf-err="${id(f.path)}" role="alert"></small>`;
  if (f.empty) return `<div class="field yf-field" data-yf-key="${esc(f.path.join("."))}"><span>${name}</span><small class="muted">Empty.</small>${addButton(f.path, false)}</div>`;
  if (f.type === "bool") {
    const on = f.unset ? h.default === true : f.value === true;
    return `<div class="field yf-field" data-yf-key="${esc(f.path.join("."))}"><div class="row gap"><label class="check grow"><input type="checkbox" ${attrs} ${on ? "checked" : ""}> ${name}</label>${reset}</div>${tail}</div>`;
  }
  let input;
  if (f.type === "select") {
    const options = [...(h.options || [])];
    if (f.value != null && !options.includes(f.value)) options.unshift(f.value);
    input = `<select class="input" ${attrs}><option value="" ${f.value == null ? "selected" : ""}>${esc(def ? `(${def})` : "(not set)")}</option>${options.map((o) => `<option ${String(o) === String(f.value) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  } else if (f.type === "list" || f.type === "long") {
    const text = f.type === "list" ? (f.value || []).map(shown).join("\n") : shown(f.value);
    const rows = Math.min(12, Math.max(f.type === "list" ? 2 : 3, text.split("\n").length + (f.type === "long" ? Math.ceil(text.length / 90) : 0)));
    input = `<textarea class="input${f.type === "list" ? " mono" : ""}" rows="${rows}" ${attrs} placeholder="${esc(f.type === "list" ? "One item per line" : def)}">${esc(text)}</textarea>`;
  } else {
    const num = f.type === "number" ? `type="number" step="any"${h.min != null ? ` min="${h.min}"` : ""}${h.max != null ? ` max="${h.max}"` : ""}` : 'type="text"';
    input = `<input class="input" ${num} ${attrs} value="${esc(shown(f.value))}" placeholder="${esc(def || (f.value === null ? "null" : ""))}" ${h.required ? "required" : ""}>`;
  }
  return `<label class="field yf-field" data-yf-key="${esc(f.path.join("."))}"><span class="row gap"><span class="grow">${name}${f.type === "list" ? ' <span class="muted">(one per line)</span>' : ""}</span>${reset}</span>${input}${tail}</label>`;
}

function group(g, depth) {
  const key = JSON.stringify(g.path);
  const count = g.children.length;
  const title = typeof g.key === "number" ? `<span class="mono">${esc(g.label)}</span>` : `${esc(human(g.key))} <span class="mono muted small">${esc(g.key)}</span>`;
  return `<details class="yf-group${depth ? "" : " top"}" data-yf-group="${esc(key)}" data-yf-key="${esc(g.path.join("."))}" ${opened.has(key) ? "open" : ""}>
    <summary>${title}<span class="muted small">${g.unset ? "not set · defaults" : g.list ? `${count} item${count === 1 ? "" : "s"}` : `${count} key${count === 1 ? "" : "s"}`}</span>${typeof g.key === "number" ? `<button type="button" class="icon-btn xs" data-yf-remove="${id(g.path)}" title="Remove this item" aria-label="Remove ${esc(g.label)}">${icon("trash")}</button>` : ""}</summary>
    <div class="yf-body">${body(g.children, depth + 1)}${addButton(g.path, g.list)}</div></details>`;
}

const addButton = (path, list) => `<div class="yf-add"><button type="button" class="btn sm" ${list ? `data-yf-add-item="${id(path)}"` : `data-yf-add-key="${id(path)}"`}>${icon("plus")} ${list ? "Add item" : "Add key"}</button></div>`;

const body = (nodes, depth) => nodes.map((n) => (n.kind === "group" ? group(n, depth) : field(n))).join("");

/** The form for alfrd.yaml text, or an error card when the YAML does not parse. */
export function renderYamlForm(text) {
  loadCss("css/lazy.css");
  let tree;
  try {
    const data = parseYaml(text);
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("alfrd.yaml must be a YAML mapping.");
    tree = formTree(data);
  } catch (error) { return `<p class="lvl-error">Fix the YAML first: ${esc(error.message)}</p>`; }
  const rank = (n) => { const i = ["name", "description"].indexOf(n.key); return i < 0 ? 9 : i; };
  const top = tree.filter((n) => n.kind === "field").sort((a, b) => rank(a) - rank(b)), groups = tree.filter((n) => n.kind === "group");
  return `<div id="ps-form" class="yf">
    <div class="row gap wrap"><h4 class="grow">All settings</h4><input class="input sm yf-filter" type="search" placeholder="Filter keys…" aria-label="Filter settings" data-yf-filter></div>
    <p class="muted small">Every key in alfrd.yaml, with defaults for known keys that are not set. A change rewrites that top-level section (its comments are dropped). Save when ready.</p>
    ${body(top, 0)}${body(groups, 0)}${addButton([], false).replace("Add key", "Add setting")}</div>`;
}

/**
 * Bind the form inside `el`. opts: { getText(), setText(text) — after a value change,
 * rebuild() — when the form's shape changed (a key was reset) }.
 */
export function bindYamlForm(el, { getText, setText, rebuild }) {
  const err = (path, text) => { const e = el.querySelector(`[data-yf-err="${CSS.escape(path)}"]`); if (e) e.textContent = text; };
  const edit = (input) => {
    const path = JSON.parse(input.dataset.yf);
    try {
      const data = parseYaml(getText());
      let original = data;
      for (const p of path) original = original?.[p];
      const type = input.dataset.yfType;
      const hint = hintFor(path);
      let value = coerce(type, type === "bool" ? input.checked : input.value, { original, hint });
      if (value === undefined && original === undefined) { err(input.dataset.yf, ""); return; }
      // Clearing a key without a known default keeps it (as "") instead of dropping it mid-typing.
      if (value === undefined && (!hint || typeof path.at(-1) === "number")) {
        if (type === "number") throw new Error("enter a number (remove keys in Edit YAML file)");
        value = "";
      }
      setText(applyFormEdit(getText(), path, value));
      input.setCustomValidity(""); err(input.dataset.yf, "");
    } catch (error) { input.setCustomValidity(error.message); err(input.dataset.yf, error.message); }
  };
  el.addEventListener("input", (e) => { if (e.target.matches?.("[data-yf]") && e.target.type !== "checkbox" && e.target.tagName !== "SELECT") edit(e.target); });
  el.addEventListener("change", (e) => { if (e.target.matches?.("[data-yf]") && (e.target.type === "checkbox" || e.target.tagName === "SELECT")) edit(e.target); });
  el.addEventListener("click", (e) => {
    const b = e.target.closest?.("[data-yf-reset], [data-yf-remove], [data-yf-add-key], [data-yf-add-item]");
    if (!b || !el.contains(b)) return;
    e.preventDefault();
    const at = (attr) => JSON.parse(b.getAttribute(attr));
    try {
      if (b.dataset.yfReset) setText(applyFormEdit(getText(), at("data-yf-reset"), undefined));
      if (b.dataset.yfRemove) {
        const path = at("data-yf-remove");
        if (!window.confirm(`Remove ${path.join(".")} from alfrd.yaml?`)) return;
        setText(applyFormEdit(getText(), path, undefined));
      }
      if (b.dataset.yfAddKey) {
        const path = at("data-yf-add-key");
        const name = window.prompt(`New key${path.length ? ` under ${path.join(".")}` : ""} (its value is filled in next):`, "")?.trim();
        if (!name) return;
        let node = parseYaml(getText());
        for (const p of path) node = node?.[p];
        if (node && typeof node === "object" && name in node) throw new Error(`${name} is already there`);
        setText(applyFormEdit(getText(), [...path, name], ""));
        opened.add(JSON.stringify(path));
      }
      if (b.dataset.yfAddItem) {
        const path = at("data-yf-add-item");
        let list = parseYaml(getText());
        for (const p of path) list = list?.[p];
        list = Array.isArray(list) ? list : [];
        setText(applyFormEdit(getText(), [...path, list.length], newItem(list)));
        opened.add(JSON.stringify(path)).add(JSON.stringify([...path, list.length]));
      }
      rebuild();
    } catch (error) { window.alert(error.message); }
  });
  el.addEventListener("toggle", (e) => {
    const g = e.target.dataset?.yfGroup;
    if (g && !el.querySelector("[data-yf-filter]")?.value.trim()) e.target.open ? opened.add(g) : opened.delete(g);
  }, true);
  el.addEventListener("input", (e) => {
    if (!e.target.matches?.("[data-yf-filter]")) return;
    const q = e.target.value.trim().toLowerCase();
    el.querySelectorAll(".yf-field").forEach((f) => { f.hidden = Boolean(q) && !f.dataset.yfKey.toLowerCase().includes(q) && !f.textContent.toLowerCase().includes(q); });
    el.querySelectorAll(".yf-group").forEach((g) => {
      const hit = !q || [...g.querySelectorAll(".yf-field")].some((f) => !f.hidden) || g.dataset.yfKey.toLowerCase().includes(q);
      g.hidden = !hit;
      if (q && hit) g.open = true;
      else if (!q) g.open = opened.has(g.dataset.yfGroup);
    });
  });
}

const drafts = new WeakMap(); // el → { text, hooks }: the draft the bound listeners edit

/** Draw the form for `text` into `host` inside `el` (listeners bound once). hooks: { setText(text), rebuild() } */
export function showYamlForm(el, host, text, hooks) {
  if (!drafts.has(el)) {
    bindYamlForm(el, {
      getText: () => drafts.get(el).text,
      setText: (next) => { const d = drafts.get(el); if (next !== d.text) { d.text = next; d.hooks.setText(next); } },
      rebuild: () => drafts.get(el).hooks.rebuild(),
    });
  }
  drafts.set(el, { text, hooks });
  if (host.isConnected) host.innerHTML = renderYamlForm(text);
}
