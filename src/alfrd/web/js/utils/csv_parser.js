// CSV/TSV reader, writer and legacy schema alias resolver.
//
// parseCsv handles RFC 4180 quoting (embedded commas, quotes and newlines),
// a UTF-8 BOM, and auto-detects comma vs tab vs semicolon delimiters.

export function detectDelimiter(text) {
  const head = text.split(/\r?\n/, 1)[0] || "";
  const counts = [",", "\t", ";"].map((d) => [d, head.split(d).length - 1]);
  counts.sort((a, b) => b[1] - a[1]);
  return counts[0][1] > 0 ? counts[0][0] : ",";
}

/** Parse delimited text into { header: string[], rows: object[] }. */
export function parseCsv(text, delimiter) {
  const src = String(text ?? "").replace(/^﻿/, "");
  const d = delimiter || detectDelimiter(src);
  const records = [];
  let field = "";
  let record = [];
  let quoted = false;
  for (let i = 0; i < src.length; i += 1) {
    const c = src[i];
    if (quoted) {
      if (c === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i += 1;
        } else quoted = false;
      } else field += c;
    } else if (c === '"' && field === "") {
      quoted = true;
    } else if (c === d) {
      record.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && src[i + 1] === "\n") i += 1;
      record.push(field);
      records.push(record);
      record = [];
      field = "";
    } else field += c;
  }
  if (field !== "" || record.length) {
    record.push(field);
    records.push(record);
  }
  const nonEmpty = records.filter((r) => r.some((v) => v.trim() !== ""));
  if (!nonEmpty.length) return { header: [], rows: [], delimiter: d };
  const header = nonEmpty[0].map((h) => h.trim());
  const rows = nonEmpty.slice(1).map((r) => {
    const row = {};
    header.forEach((h, idx) => {
      row[h] = r[idx] ?? "";
    });
    return row;
  });
  return { header, rows, delimiter: d };
}

function cell(value) {
  if (value === null || value === undefined) return "";
  const s = typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Serialize rows (array of arrays) to CSV text. */
export function toCsv(rows) {
  return `${rows.map((r) => r.map(cell).join(",")).join("\n")}\n`;
}

/** Parse a JSON-encoded CSV cell (AVICA stores lists/dicts this way). */
export function jsonCell(value, fallback = null) {
  if (value === null || value === undefined || value === "") return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

// ---------------------------------------------------------------------------
// Field aliases (alfrd.yaml `project_settings.field_aliases`): older names →
// names used now. A key ending in "*" rewrites a prefix ("old*": "new*").
// Nothing is built in; the rules come from the loaded alfrd.yaml.

let RULES = [];
let PREFIXES = [];

/** Install alias rules from a `{from: to}` mapping (or [{from, to}] list). */
export function setAliasRules(rules) {
  const list = Array.isArray(rules)
    ? rules.filter((r) => r && r.from && r.to).map((r) => ({ from: String(r.from), to: String(r.to) }))
    : Object.entries(rules || {}).map(([from, to]) => ({ from: String(from), to: String(to) }));
  RULES = list.filter((r) => !r.from.endsWith("*"));
  PREFIXES = list.filter((r) => r.from.endsWith("*")).map((r) => ({ from: r.from.slice(0, -1), to: r.to.replace(/\*$/, "") }));
  return list;
}

export function aliasRules() {
  return [...RULES, ...PREFIXES.map((p) => ({ from: `${p.from}*`, to: `${p.to}*` }))];
}

/**
 * Resolve an identifier through the configured field aliases.
 * Returns { name, alias } where alias is the original value or null.
 */
export function resolveAlias(name) {
  const raw = String(name ?? "").trim();
  const hit = RULES.find((r) => r.from === raw);
  if (hit) return { name: hit.to, alias: raw };
  const pre = PREFIXES.find((p) => p.from && raw.toLowerCase().startsWith(p.from.toLowerCase()));
  if (pre) {
    const head = raw.slice(0, pre.from.length);
    const to = head === head.toUpperCase() && head !== head.toLowerCase() ? pre.to.toUpperCase() : pre.to;
    return { name: to + raw.slice(pre.from.length), alias: raw };
  }
  return { name: raw, alias: null };
}

/** Collects unique alias resolutions while importing (shown in Project settings). */
export class AliasLog {
  constructor() {
    this.map = new Map();
  }
  resolve(name, source) {
    const result = resolveAlias(name);
    if (result.alias) {
      const key = `${result.alias}→${result.name}`;
      const entry = this.map.get(key) || { from: result.alias, to: result.name, sources: new Set() };
      if (source) entry.sources.add(source);
      this.map.set(key, entry);
    }
    return result.name;
  }
  list() {
    return Array.from(this.map.values()).map((e) => ({ from: e.from, to: e.to, sources: Array.from(e.sources) }));
  }
}
