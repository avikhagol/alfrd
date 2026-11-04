// Source-markup guards extend the spinner fix: never compare browser-serialized SVG.
// Patch stable elements in place; defer a fragment while a native control or menu owns it.
const simpleSources = new WeakMap();
export function sourceHTML(root, html) {
  if (simpleSources.get(root)?.html === html && simpleSources.get(root)?.first === root.firstChild) return false;
  const top = root.scrollTop, left = root.scrollLeft;
  root.innerHTML = html; simpleSources.set(root, { html, first: root.firstChild });
  root.scrollTop = top; root.scrollLeft = left; return true;
}
const sources = new WeakMap(), pending = new WeakMap(), bound = new WeakSet();
export function refreshProtected(root) {
  const active = document.activeElement;
  return Boolean((root === active || root.contains(active)) && active?.matches?.("input,textarea,select,[contenteditable], [role=combobox]") || root.querySelector?.(".chat-session-menu[open], [role=menu], dialog[open]"));
}
function identity(node) {
  if (!node || node.nodeType !== 1) return "";
  return node.id || ["data-key", "data-sid", "data-row", "data-decision", "data-file-copy"].map((k) => node.hasAttribute(k) ? `${k}:${node.getAttribute(k)}` : "").find(Boolean) || "";
}
function patch(parent, next) {
  let cursor = parent.firstChild;
  for (const fresh of [...next.childNodes]) {
    const key = identity(fresh);
    let old = cursor;
    if (key && identity(old || {}) !== key) old = [...parent.childNodes].find((n) => identity(n) === key);
    if (!old || old.nodeType !== fresh.nodeType || old.nodeName !== fresh.nodeName || identity(old) !== key) {
      parent.insertBefore(fresh, cursor); continue;
    }
    if (old !== cursor) parent.insertBefore(old, cursor);
    if (old.nodeType === 1) {
      // Disclosures belong to the reader, not to the next server snapshot.
      for (const a of [...old.attributes]) if (!(old.tagName === "DETAILS" && a.name === "open") && !fresh.hasAttribute(a.name)) old.removeAttribute(a.name);
      for (const a of fresh.attributes) if (!(old.tagName === "DETAILS" && a.name === "open") && old.getAttribute(a.name) !== a.value) old.setAttribute(a.name, a.value);
      // Textarea contents are local drafts. Their owner applies explicit edits.
      if (old.tagName !== "TEXTAREA") patch(old, fresh);
    } else if (old.nodeValue !== fresh.nodeValue) old.nodeValue = fresh.nodeValue;
    cursor = old.nextSibling;
  }
  while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
}
export function refreshHTML(root, html) {
  if (!root) return false;
  // Always replace the pending value, including a reversion to the last rendered source.
  pending.set(root, html);
  if (!bound.has(root)) {
    bound.add(root);
    const flush = () => queueMicrotask(() => { if (root.isConnected && pending.has(root)) refreshHTML(root, pending.get(root)); });
    root.addEventListener("focusout", flush);
    root.addEventListener("toggle", flush, true);
    root.addEventListener("close", flush, true);
  }
  if (refreshProtected(root)) return false;
  pending.delete(root);
  if (sources.get(root)?.html === html && sources.get(root)?.first === root.firstChild) return false;
  const scrolls = [root, ...root.querySelectorAll("*")].filter((el) => el.scrollTop || el.scrollLeft).map((el) => [el, el.scrollTop, el.scrollLeft]);
  const template = document.createElement("template"); template.innerHTML = html;
  patch(root, template.content);
  sources.set(root, { html, first: root.firstChild });
  for (const [el, top, left] of scrolls) { if (el.isConnected) { el.scrollTop = top; el.scrollLeft = left; } }
  return true;
}
