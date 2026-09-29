// The project's target list (alfrd.yaml `targets:`, default alfrd.targets.csv):
// parse an imported CSV/TSV, merge it into the current list, write it back.
// Mirrors alfrd/targets_csv.py so the browser-only mode (no server) behaves the
// same. Loaded on first use (not part of the Studio's startup payload).

import { parseCsv, toCsv } from "../utils/csv_parser.js";

export const DEFAULT_TARGETS = {
  csv: "alfrd.targets.csv",
  columns: {
    key: ["TARGET_NAME", "source", "source_name", "target", "name"],
    files: ["FILENAMES", "fitsfilenames", "fits", "fitsidi", "files"],
    code: ["PROJECT_CODE", "project_code", "code"],
  },
};
const ROLES = ["key", "files", "code"];

const list = (v) => (Array.isArray(v) ? v.map(String).filter((x) => x.trim()) : v ? [String(v)] : []);

/** The targets block with written column names (the plan CSV's, from `execution:`). */
export function targetsSpec(defs = {}, execution = {}) {
  const block = defs.targets || {};
  const written = { key: execution.key_column || "TARGET_NAME", files: execution.files_column || "FILENAMES", code: execution.code_column || "PROJECT_CODE" };
  const aliases = {};
  ROLES.forEach((r) => {
    const accepted = list(block.columns?.[r]);
    aliases[r] = [...new Set([written[r], ...(accepted.length ? accepted : DEFAULT_TARGETS.columns[r])])];
  });
  return { csv: String(block.csv || DEFAULT_TARGETS.csv).replace(/^\.\//, ""), written, aliases };
}

export const rowKey = (target, code = "") => (code ? `${target}@${code}` : target);
export const joinFiles = (v) => String(v || "").split(/[,;\s]+/).filter(Boolean).join(",");

export function checkTarget(name) {
  const n = String(name || "").trim();
  if (!n) throw new Error("target is required");
  if (n.startsWith("#")) throw new Error(`target '${n}' starts with '#', which marks a comment row`);
  if (n.includes("@")) throw new Error(`target '${n}' contains '@', which separates target and project code`);
  return n;
}

/** Which header column holds key / files / code (explicit mapping first, then the aliases). */
export function detectColumns(header, spec, mapping = {}) {
  const lower = new Map(header.map((h) => [h.trim().toLowerCase(), h]));
  return Object.fromEntries(ROLES.map((r) => [r, header.includes(mapping[r]) ? mapping[r] : spec.aliases[r].map((a) => lower.get(a.toLowerCase())).find(Boolean) || null]));
}

/** → {header, columns, rows: [{target, files, code, extra}], problems: [{line, message}]} */
export function parseTargets(text, spec, mapping = {}) {
  const lines = String(text ?? "").replace(/^\ufeff/, "").split(/\r?\n/);
  const first = lines[0] || "";
  const d = first.split("\t").length > first.split(",").length ? "\t" : ",";
  const { header } = parseCsv(first, d);
  const columns = detectColumns(header, spec, mapping);
  const problems = [];
  const rows = [];
  if (!columns.key) {
    problems.push({ line: 0, message: `no target column (looked for ${spec.aliases.key.join(", ")})` });
    return { header, columns, rows, problems };
  }
  const seen = new Map();
  const used = new Set(Object.values(columns).filter(Boolean));
  // One record per line (file line numbers in problems; the header is line 1).
  lines.slice(1).forEach((line, i) => {
    if (!line.trim()) return;
    const n = i + 2;
    const raw = parseCsv(`${first}\n${line}`, d).rows[0] || {};
    if (!Object.values(raw).some((v) => String(v).trim())) return;
    let name;
    try { name = checkTarget(raw[columns.key]); } catch (e) { problems.push({ line: n, message: e.message }); return; }
    const code = columns.code ? String(raw[columns.code] || "").trim() : "";
    const key = rowKey(name, code);
    if (seen.has(key)) { problems.push({ line: n, message: `${key} repeats line ${seen.get(key)}` }); return; }
    seen.set(key, n);
    const extra = Object.fromEntries(Object.entries(raw).filter(([k]) => k && !used.has(k)).map(([k, v]) => [k, String(v ?? "")]));
    rows.push({ target: name, files: columns.files ? joinFiles(raw[columns.files]) : "", code, extra });
  });
  return { header, columns, rows, problems };
}

/** Merge (or replace) incoming rows into existing ones → {rows, added, updated, unchanged, removed}. */
export function mergeTargets(existing, incoming, mode = "merge") {
  const k = (r) => rowKey(r.target, r.code || "");
  const old = new Map(existing.map((r) => [k(r), r]));
  const added = [], updated = [], unchanged = [];
  if (mode === "replace") {
    incoming.forEach((r) => {
      const prev = old.get(k(r));
      (!prev ? added : (prev.files || "") === (r.files || "") ? unchanged : updated).push(k(r));
    });
    const keep = new Set(incoming.map(k));
    return { rows: incoming.map((r) => ({ ...r })), added, updated, unchanged, removed: [...old.keys()].filter((x) => !keep.has(x)) };
  }
  const out = existing.map((r) => ({ ...r }));
  const index = new Map(out.map((r, i) => [k(r), i]));
  incoming.forEach((r) => {
    const key = k(r);
    if (!index.has(key)) { index.set(key, out.length); out.push({ ...r }); added.push(key); return; }
    const cur = out[index.get(key)];
    const files = r.files || cur.files || "";
    const extra = { ...(cur.extra || {}), ...Object.fromEntries(Object.entries(r.extra || {}).filter(([, v]) => v)) };
    if (files !== (cur.files || "") || JSON.stringify(extra) !== JSON.stringify(cur.extra || {})) {
      out[index.get(key)] = { ...cur, files, extra };
      updated.push(key);
    } else unchanged.push(key);
  });
  return { rows: out, added, updated, unchanged, removed: [] };
}

/** CSV text with the written column names, then any extra columns. */
export function dumpTargets(spec, rows) {
  const w = spec.written;
  const fixed = new Set([w.key, w.files, w.code].map((c) => c.toLowerCase()));
  const extras = [...new Set(rows.flatMap((r) => Object.keys(r.extra || {})).filter((c) => !fixed.has(c.toLowerCase())))];
  return toCsv([[w.key, w.files, w.code, ...extras], ...rows.map((r) => [r.target, r.files || "", r.code || "", ...extras.map((c) => r.extra?.[c] ?? "")])]);
}

/** Rows without `names`: a bare target drops all its rows, `target@code` one row → {rows, removed, missing}. */
export function dropTargets(rows, names) {
  const wanted = names.map((n) => String(n).trim()).filter(Boolean);
  const hit = new Set();
  const out = [], removed = [];
  rows.forEach((r) => {
    const key = rowKey(r.target, r.code || "");
    const m = wanted.find((n) => n === key || (!n.includes("@") && n === r.target));
    if (m == null) out.push({ ...r });
    else { hit.add(m); removed.push(key); }
  });
  return { rows: out, removed, missing: wanted.filter((n) => !hit.has(n)) };
}
