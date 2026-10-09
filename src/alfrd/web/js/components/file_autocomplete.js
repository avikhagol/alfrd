import { esc, loadCss } from "../utils/dom.js";
import { fuzzyRank } from "../data/fuzzy.js";

export function fileToken(value, cursor) {
  const left = value.slice(0, cursor);
  const start = left.search(/[^,;\s]*$/);
  const end = value.slice(cursor).search(/[,;\s]/);
  return { start, end: end < 0 ? value.length : cursor + end, query: value.slice(start, cursor).trim() };
}

export function replaceFileToken(value, cursor, name) {
  const token = fileToken(value, cursor);
  const parts = [value.slice(0, token.start), name, value.slice(token.end)].join("").split(/[,;\s]+/).filter(Boolean);
  const before = value.slice(0, token.start).split(/[,;\s]+/).filter(Boolean);
  return { value: parts.join(","), cursor: [...before, name].join(",").length };
}

let serial = 0;
export function mountFileAutocomplete(input, { files }) {
  loadCss("css/lazy.css");
  const list = document.createElement("div");
  list.id = `fits-list-${++serial}`;
  list.className = "fits-suggestions";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  input.after(list);
  input.setAttribute("role", "combobox");
  if (!input.labels?.length) input.setAttribute("aria-label", "FITS file names");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-expanded", "false");
  let matches = [], active = 0;
  const hide = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); };
  const paint = () => {
    list.innerHTML = matches.map((m, i) => `<div role="option" id="${list.id}-${i}" data-index="${i}" aria-selected="${i === active}">${esc(m.label)}</div>`).join("");
    list.hidden = !matches.length;
    input.setAttribute("aria-expanded", String(!list.hidden));
    if (matches.length) input.setAttribute("aria-activedescendant", `${list.id}-${active}`);
    else input.removeAttribute("aria-activedescendant");
  };
  const update = () => {
    const query = fileToken(input.value, input.selectionStart ?? input.value.length).query;
    matches = query ? fuzzyRank(query, files.map((label) => ({ label })), 12) : [];
    active = 0; paint();
  };
  const insert = (index) => {
    if (!matches[index]) return;
    const result = replaceFileToken(input.value, input.selectionStart ?? input.value.length, matches[index].label);
    input.value = result.value;
    input.focus(); input.setSelectionRange(result.cursor, result.cursor);
    input.dispatchEvent(new Event("input", { bubbles: true })); hide();
  };
  input.addEventListener("input", update);
  input.addEventListener("click", update);
  input.addEventListener("blur", hide);
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(e.key)) { e.preventDefault(); e.stopPropagation(); }
    if (e.key === "Escape") hide();
    else if (e.key === "Enter") insert(active);
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") { active = (active + (e.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length; paint(); }
  });
  list.addEventListener("mousedown", (e) => { const option = e.target.closest("[data-index]"); if (option) { e.preventDefault(); insert(Number(option.dataset.index)); } });
}
