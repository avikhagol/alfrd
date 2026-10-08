// Compact header picker: a button showing the current name (shortened in the
// middle to its max-width) that opens a listbox with the full names. Replaces
// native <select>s whose closed state cannot be shortened differently from the list.
import { esc, icon } from "../utils/dom.js";
import { fitMiddle } from "../utils/text_fit.js";

let openPop = null; // only one picker list at a time

/** The items matching a filter (case-insensitive, on label and detail). */
export function filterItems(items, query) {
  const q = String(query || "").trim().toLowerCase();
  return q ? items.filter((it) => `${it.label} ${it.detail || ""}`.toLowerCase().includes(q)) : items;
}

/** Next index for a key in a list of ``count`` (null: the key does not move). */
export function moveIndex(key, index, count) {
  if (!count) return null;
  if (key === "ArrowDown") return Math.min(count - 1, index + 1);
  if (key === "ArrowUp") return Math.max(0, index - 1);
  if (key === "Home" || key === "PageUp") return 0;
  if (key === "End" || key === "PageDown") return count - 1;
  return null;
}

/** Index of the next label starting with ``typed`` (a new single letter moves past ``index``; it wraps). */
export function typeahead(items, index, typed) {
  const start = typed.length > 1 ? index : index + 1;
  for (let k = 0; k < items.length; k++) {
    const i = (start + k) % items.length;
    if (items[i].label.toLowerCase().startsWith(typed)) return i;
  }
  return null;
}

/**
 * trigger: <button> containing a `.picker-val` span. opts: {label, onChange(value), filterAt = 8, empty}.
 * Returns {set(items, value), open(), close()}; items: [{value, label, detail?, badge?}].
 */
export function mountPicker(trigger, { label, onChange, filterAt = 8, empty = "Nothing to choose" }) {
  const valEl = trigger.querySelector(".picker-val");
  let items = [], value = null, typed = "", typedAt = 0;
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");

  const current = () => items.find((it) => it.value === value);
  let fitted = "";
  const fit = () => {
    const it = current();
    const text = it ? it.label : empty;
    const key = `${text}\u0000${window.innerWidth}`;
    if (key === fitted) return;
    fitted = key;
    fitMiddle(valEl, text);
    valEl.removeAttribute("title");
    trigger.title = `${label}: ${text}${it?.detail ? `\n${it.detail}` : ""}`;
    trigger.setAttribute("aria-label", `${label}: ${text}`);
  };

  function close(focus = false) {
    if (!openPop || openPop.trigger !== trigger) return;
    openPop.el.remove();
    document.removeEventListener("pointerdown", openPop.outside, true);
    window.removeEventListener("resize", openPop.dismiss);
    openPop = null;
    trigger.setAttribute("aria-expanded", "false");
    if (focus) trigger.focus();
  }

  function open() {
    if (openPop) { const same = openPop.trigger === trigger; openPop.close(); if (same) return; }
    if (!items.length) return;
    const el = document.createElement("div");
    el.className = "menu picker-pop";
    const id = `pk-${Math.random().toString(36).slice(2, 8)}`;
    const withFilter = items.length > filterAt;
    el.innerHTML = `${withFilter ? `<input class="input sm picker-filter" type="search" placeholder="Filter ${esc(label.toLowerCase())}s" aria-label="Filter ${esc(label.toLowerCase())}s" aria-controls="${id}" autocomplete="off" spellcheck="false">` : ""}
      <div class="picker-list" id="${id}" role="listbox" tabindex="0" aria-label="${esc(label)}"></div>`;
    document.body.appendChild(el);
    const list = el.querySelector(".picker-list");
    const filter = el.querySelector(".picker-filter");
    let shown = items, active = Math.max(0, items.findIndex((it) => it.value === value));

    const paint = () => {
      list.innerHTML = shown.length ? shown.map((it, i) => `<div role="option" id="${id}-${i}" data-i="${i}" aria-selected="${it.value === value}" class="${i === active ? "active" : ""}">
          <span class="picker-opt-l">${esc(it.label)}${it.badge ? ` <small class="picker-badge">${esc(it.badge)}</small>` : ""}</span>${it.detail ? `<small class="picker-opt-d">${esc(it.detail)}</small>` : ""}${it.value === value ? icon("check", "picker-check") : ""}</div>`).join("")
        : `<p class="muted small picker-none">No matches</p>`;
      list.querySelectorAll(".picker-opt-d").forEach((d) => fitMiddle(d, d.textContent, d.clientWidth));
      const target = filter || list;
      if (shown[active]) target.setAttribute("aria-activedescendant", `${id}-${active}`); else target.removeAttribute("aria-activedescendant");
      list.querySelector(".active")?.scrollIntoView({ block: "nearest" });
    };
    const choose = (it) => {
      close(true);
      if (it && it.value !== value) { value = it.value; fit(); onChange(it.value); }
    };
    const onKey = (e) => {
      const next = moveIndex(e.key, active, shown.length);
      if (next !== null) { e.preventDefault(); active = next; paint(); return; }
      if (e.key === "Enter") { e.preventDefault(); choose(shown[active]); return; }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); return; }
      if (e.key === "Tab") { close(false); return; }
      if (!filter && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        typed = Date.now() - typedAt > 700 ? e.key.toLowerCase() : typed + e.key.toLowerCase();
        typedAt = Date.now();
        const hit = typeahead(shown, active, typed);
        if (hit !== null) { active = hit; paint(); }
      }
    };
    list.addEventListener("keydown", onKey);
    filter?.addEventListener("keydown", onKey);
    filter?.addEventListener("input", () => { shown = filterItems(items, filter.value); active = 0; paint(); });
    list.addEventListener("click", (e) => { const o = e.target.closest("[data-i]"); if (o) choose(shown[Number(o.dataset.i)]); });
    list.addEventListener("mousemove", (e) => {
      const o = e.target.closest("[data-i]");
      if (o && Number(o.dataset.i) !== active) { active = Number(o.dataset.i); list.querySelector(".active")?.classList.remove("active"); o.classList.add("active"); }
    });

    const r = trigger.getBoundingClientRect();
    el.style.top = `${r.bottom + 6}px`;
    el.style.minWidth = `${Math.max(240, r.width)}px`;
    el.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - el.offsetWidth - 8))}px`;
    paint();
    (filter || list).focus();
    trigger.setAttribute("aria-expanded", "true");
    const outside = (e) => { if (!el.contains(e.target) && !trigger.contains(e.target)) close(false); };
    const dismiss = () => close(false);
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("resize", dismiss);
    openPop = { el, trigger, outside, dismiss, close: () => close(false) };
  }

  trigger.addEventListener("click", open);
  trigger.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(); }
  });
  let frame = 0;
  window.addEventListener("resize", () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(fit); });

  return {
    set(next, selected) {
      items = next;
      value = selected;
      trigger.disabled = !items.length;
      fit();
    },
    open,
    close: () => close(false),
    get value() { return value; },
  };
}
