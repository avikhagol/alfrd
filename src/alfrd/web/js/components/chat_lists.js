import { esc } from "../utils/dom.js";

// Small plain-text fallback; richer Markdown stays with the installed viewer.
export function listItem(line) {
  const m = /^([ \t]*)([-*]|[0-9]+\.) (.*)$/.exec(line);
  return m ? { indent: m[1], marker: m[2], text: m[3], ordered: m[2].endsWith(".") } : null;
}

/** Newline edit; selections and ordinary text keep native textarea behavior. */
export function listNewline(text, start, end = start) {
  if (start !== end) return null;
  const lineStart = start ? text.lastIndexOf("\n", start - 1) + 1 : 0;
  const nextBreak = text.indexOf("\n", start), lineEnd = nextBreak < 0 ? text.length : nextBreak;
  const item = listItem(text.slice(lineStart, lineEnd));
  if (!item || start < lineStart + item.indent.length + item.marker.length + 1) return null;
  if (!item.text.trim()) return { text: text.slice(0, lineStart) + text.slice(lineEnd), caret: lineStart };
  const marker = item.ordered ? `${BigInt(item.marker.slice(0, -1)) + 1n}.` : item.marker;
  const insert = `\n${item.indent}${marker} `;
  return { text: text.slice(0, start) + insert + text.slice(end), caret: start + insert.length };
}

/** All source fragments are escaped; consecutive list lines become semantic lists. */
export function listHtml(text) {
  let tag = "", html = "", fence = null;
  const close = () => { if (tag) html += `</${tag}>`; tag = ""; };
  const lines = String(text).split("\n");
  lines.forEach((line, i) => {
    const delimiter = /^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (delimiter && (!fence || (delimiter[0] === fence[0] && delimiter.length >= fence.length))) fence = fence ? null : delimiter;
    const item = !fence && !delimiter ? listItem(line) : null;
    if (item) {
      const nextTag = item.ordered ? "ol" : "ul";
      if (tag !== nextTag) {
        close(); tag = nextTag;
        html += item.ordered ? `<ol start="${esc(item.marker.slice(0, -1))}">` : "<ul>";
      }
      html += `<li>${esc(item.text)}</li>`;
    } else {
      close(); html += esc(line) + (i < lines.length - 1 ? "\n" : "");
    }
  });
  close(); return html;
}
