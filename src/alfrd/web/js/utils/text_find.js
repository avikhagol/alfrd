import { esc, icon, copyText } from "./dom.js";

/** Every [start, end) span where `query` occurs in `text`, ignoring case (none for an empty query). */
export function findSpans(text, query) {
  const hay = String(text ?? "").toLowerCase(), q = String(query ?? "").toLowerCase();
  const spans = [];
  if (!q) return spans;
  for (let at = hay.indexOf(q); at >= 0; at = hay.indexOf(q, at + q.length)) spans.push([at, at + q.length]);
  return spans;
}

/** "build · attempt 3 (latest)": the unit and attempt a text view shows. Empty when neither is known. */
export function attemptLabel({ unit, attempt, latest } = {}) {
  return [unit, attempt != null && attempt !== "" ? `attempt ${attempt}${latest ? " (latest)" : ""}` : ""].filter(Boolean).join(" · ");
}

/** Copy and Find buttons for a text view's header, with the optional unit/attempt label. */
export function textToolsHtml(label = "") {
  return `${label ? `<span class="small muted mono trunc" data-viewer-label>${esc(label)}</span>` : ""}<button type="button" class="btn sm" data-text-copy>${icon("copy")} Copy</button><button type="button" class="btn sm" data-text-find-open aria-expanded="false">${icon("search")} Find</button>`;
}

/** The find bar under a text view's header; hidden until Find is pressed. */
export function textFindHtml() {
  return `<div class="text-find small" role="search" data-text-find hidden><input type="search" class="input sm" data-text-query aria-label="Find in text" placeholder="Find in text"><span class="muted tabular" data-text-count role="status" aria-live="polite"></span><button type="button" class="btn sm" data-text-prev aria-label="Previous match">↑</button><button type="button" class="btn sm" data-text-next aria-label="Next match">↓</button><button type="button" class="btn sm" data-text-find-close aria-label="Close find">${icon("close")}</button></div>`;
}

/** A DOM Range over characters [start, end) of `el`'s text, which may span several text nodes. */
function rangeAt(el, start, end) {
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let base = 0, from = false;
  for (let node = walk.nextNode(); node; node = walk.nextNode()) {
    const next = base + node.data.length;
    if (!from && start < next) { range.setStart(node, start - base); from = true; }
    if (from && end <= next) { range.setEnd(node, end - base); return range; }
    base = next;
  }
  return null;
}

/**
 * Wire Copy and Find in `root` to `getText()`, the text `pre` shows. Enter and Shift+Enter step
 * through the matches; the current one is highlighted and scrolled into view. Escape closes the bar.
 */
export function bindTextTools(root, { getText, pre, toast }) {
  root.querySelector("[data-text-copy]")?.addEventListener("click", async () => {
    const ok = await copyText(getText());
    toast?.(ok ? "Copied" : "Copy failed", ok ? "ok" : "fail");
  });
  const bar = root.querySelector("[data-text-find]");
  const input = root.querySelector("[data-text-query]");
  const count = root.querySelector("[data-text-count]");
  const open = root.querySelector("[data-text-find-open]");
  const prev = root.querySelector("[data-text-prev]"), next = root.querySelector("[data-text-next]");
  let spans = [], at = -1;
  const clear = () => CSS.highlights?.delete("text-find");
  const close = () => { bar.hidden = true; open.setAttribute("aria-expanded", "false"); clear(); open.focus(); };
  root.addEventListener("beforeclose", clear);
  const show = () => {
    count.textContent = `${at + 1}/${spans.length}`;
    prev.disabled = next.disabled = !spans.length;
    clear();
    const range = spans.length ? rangeAt(pre, ...spans[at]) : null;
    if (!range) return;
    if (CSS.highlights) CSS.highlights.set("text-find", new Highlight(range));
    else { const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); }
    const r = range.getBoundingClientRect(), b = pre.getBoundingClientRect();
    if (r.top < b.top || r.bottom > b.bottom) pre.scrollTop += r.top - b.top - b.height / 3;
  };
  const search = () => { spans = findSpans(getText(), input.value); at = spans.length ? 0 : -1; show(); };
  const step = (n) => { if (spans.length) { at = (at + n + spans.length) % spans.length; show(); } };
  root.querySelector("[data-text-find-open]")?.addEventListener("click", () => {
    bar.hidden = false;
    open.setAttribute("aria-expanded", "true");
    input.focus();
    input.select();
    search();
  });
  input.addEventListener("input", search);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
  });
  bar.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
  });
  root.querySelector("[data-text-prev]")?.addEventListener("click", () => step(-1));
  root.querySelector("[data-text-next]")?.addEventListener("click", () => step(1));
  root.querySelector("[data-text-find-close]")?.addEventListener("click", close);
  return { search };
}

