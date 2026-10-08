// Settings form helpers; edits replace only their top-level section.

import { parseYaml, dumpYaml } from "../utils/yaml_parser.js";
import { replaceSection } from "./agent_settings.js";

// Known keys: type, choices and defaults (alfrd/execution.py DEFAULT_EXECUTION and
// schemas/project-manifest-v1.schema.json). `*` matches any list index or map key.
const yes = { type: "bool", default: false };
export const HINTS = {
  name: { type: "text", required: true, help: "Shown in Studio and the CLI." },
  description: { type: "long" },
  version: { type: "number", default: 1, min: 1, max: 1 },
  template: { type: "text", help: "Built-in defaults, e.g. avica." },
  primary_key: { type: "text", default: "TARGET_NAME", help: "Plan CSV column that names a target." },
  "execution.cwd": { type: "text", default: ".", help: "Relative to alfrd.yaml." },
  "execution.plan_csv": { type: "text", default: "alfrd.plan.csv" },
  "execution.mode": { type: "select", options: ["step", "target", "batch"], default: "step" },
  "execution.concurrency": { type: "number", default: 1, min: 1 },
  "execution.on_failure": { type: "select", options: ["stop_target", "continue", "stop_plan"], default: "stop_target" },
  "execution.status_from": { type: "select", options: ["exit_code", "result_csv", "both"], default: "exit_code" },
  "execution.launcher": { type: "select", options: ["detach", "systemd-run"], default: "detach" },
  "execution.auto_resume": { type: "select", options: ["adopt", "always", "never"], default: "adopt" },
  "execution.timeout": { type: "number", min: 1, help: "Seconds per command; blank = no limit." },
  "execution.kill_grace": { type: "number", default: 30, min: 0, help: "Seconds from SIGTERM to SIGKILL." },
  "execution.heartbeat": { type: "number", default: 10, min: 1 },
  "execution.key_column": { type: "text", help: "Default: primary_key, else TARGET_NAME." },
  "execution.files_column": { type: "text", default: "FILENAMES" },
  "execution.code_column": { type: "text", default: "PROJECT_CODE" },
  "execution.workdir_column": { type: "text", default: "WORKDIR" },
  "execution.serialize_on": { type: "list", help: "With concurrency > 1: rows sharing these columns run one at a time." },
  "execution.serialize_match": { type: "select", options: ["all", "any"], default: "all" },
  "execution.usage_interval": { type: "number", default: 5, min: 0, help: "Seconds between resource samples (0 = off)." },
  "execution.max_runtime": { type: "number", min: 1, help: "Plan wall-clock limit in seconds, pauses included." },
  "loop.max_input_chars": { type: "number", default: 40000, min: 1 },
  "loop.progress": { type: "bool", default: true, help: "Agents keep a checklist (PROGRESS.md) and continue from it each turn; a retried turn picks up from it." },
  "loop.workspace": { type: "select", options: ["shared", "worktree"], default: "shared", help: "worktree gives each task its own git worktree." },
  "project_settings.human_review": { ...yes, help: "Pause for review after every agent turn." },
  "project_settings.agent_access.sandbox": { type: "select", options: ["read-only", "workspace-write"] },
  "entrypoint.*.output_capture": { type: "select", options: ["file", "stdout"] },
  "entrypoint.*.adapter": { type: "select", options: ["claude", "codex", "generic"] },
  "entrypoint.*.manual": { ...yes, help: "Copy the prompt into a chat and paste the response." },
  "entrypoint.*.human_review": yes,
  "artifacts.*.kind": { type: "select", options: ["file", "directory", "table", "json", "yaml", "text", "log", "image", "image_collection", "collection", "html", "archive"] },
  "artifacts.*.show_in_logs": yes,
  "steps.*.skip": yes,
};

const pattern = (path) => path.map((p) => (typeof p === "number" ? "*" : p)).join(".");

/** The hint for a path: exact keys first, then `*` for any map key. */
export function hintFor(path) {
  if (HINTS[pattern(path)]) return HINTS[pattern(path)];
  const parts = pattern(path).split(".");
  const hit = Object.keys(HINTS).find((k) => {
    const h = k.split(".");
    return h.length === parts.length && h.every((x, i) => x === "*" || x === parts[i]);
  });
  return hit ? HINTS[hit] : null;
}

/** Known child keys of a mapping at `path` (for keys the file leaves out). */
function knownChildren(path) {
  const parts = pattern(path).split(".").filter(Boolean);
  const out = new Set();
  for (const key of Object.keys(HINTS)) {
    const h = key.split(".");
    if (h.length > parts.length && parts.every((x, i) => h[i] === "*" || h[i] === x) && h[parts.length] !== "*") out.add(h[parts.length]);
  }
  return [...out];
}

const isMap = (v) => v && typeof v === "object" && !Array.isArray(v);
const scalarish = (v) => v === null || typeof v !== "object";

function leafType(value, hint) {
  if (hint?.type) return hint.type;
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number") return "number";
  if (Array.isArray(value)) return "list";
  if (typeof value === "string" && (value.includes("\n") || value.length > 80)) return "long";
  return "text";
}

/** A short label for a list item: its name/id/title, else its position. */
export function itemLabel(item, i) {
  if (isMap(item)) {
    const k = ["name", "id", "title", "panel", "file"].find((x) => scalarish(item[x]) && item[x] != null && item[x] !== "");
    if (k) return String(item[k]);
  }
  if (scalarish(item) && item != null && String(item).length <= 40) return String(item);
  return `Item ${i + 1}`;
}

/**
 * The form tree of parsed alfrd.yaml data. Nodes: {kind: "group"|"field", key, path,
 * label, unset, ...}; groups have `children`, fields `type`, `value`, `hint`.
 */
export function formTree(data, path = [], unset = false) {
  const node = data ?? {};
  const keys = Array.isArray(node) ? node.map((_, i) => i) : [...Object.keys(node), ...knownChildren(path).filter((k) => !(k in node))];
  return keys.map((key) => {
    const p = [...path, key];
    const here = !unset && (Array.isArray(node) ? true : key in node);
    const value = here ? node[key] : undefined;
    const hint = hintFor(p);
    const label = typeof key === "number" ? itemLabel(value, key) : String(key);
    const group = (isMap(value) && (Object.keys(value).length || knownChildren(p).length))
      || (Array.isArray(value) && value.some((v) => !scalarish(v)))
      || (!here && !hint && knownChildren(p).length);
    if (group) return { kind: "group", key, path: p, label, unset: !here, list: Array.isArray(value), children: formTree(value, p, !here) };
    return { kind: "field", key, path: p, label, unset: !here, type: leafType(value, hint), value, hint, empty: isMap(value) };
  });
}

/** The value an input holds. Returns `undefined` for "leave the key out". */
export function coerce(type, raw, { original, hint } = {}) {
  if (type === "bool") return Boolean(raw);
  const text = String(raw ?? "");
  if (type === "list") {
    const items = text.split("\n").map((x) => x.trim()).filter(Boolean);
    if (!items.length) return original === undefined ? undefined : [];
    const numeric = Array.isArray(original) && original.length && original.every((x) => typeof x === "number");
    return numeric ? items.map((x) => { const n = Number(x); if (!Number.isFinite(n)) throw new Error(`“${x}” is not a number`); return n; }) : items;
  }
  if (text.trim() === "") {
    if (hint?.required) throw new Error("required");
    return original === null ? null : undefined;
  }
  if (type === "number") {
    const n = Number(text);
    if (!Number.isFinite(n)) throw new Error("must be a number");
    if (hint?.min != null && n < hint.min) throw new Error(`must be at least ${hint.min}`);
    if (hint?.max != null && n > hint.max) throw new Error(`must be at most ${hint.max}`);
    return n;
  }
  return type === "long" ? text : text.trim();
}

function setIn(data, path, value) {
  let node = data;
  for (const [i, part] of path.slice(0, -1).entries()) {
    if (node[part] == null || typeof node[part] !== "object") {
      if (value === undefined) return;
      node[part] = typeof path[i + 1] === "number" ? [] : {};
    }
    node = node[part];
  }
  const last = path.at(-1);
  if (value !== undefined) node[last] = value;
  else if (Array.isArray(node)) node.splice(last, 1);
  else delete node[last];
}

function removeSection(text, key) {
  const lines = String(text).split("\n");
  const start = lines.findIndex((l) => l.startsWith(`${key}:`) || l.startsWith(`${JSON.stringify(key)}:`));
  if (start < 0) return text;
  let end = start + 1;
  while (end < lines.length && !/^[^\s#-][^:]*:/.test(lines[end])) end++;
  while (end > start + 1 && (/^\s*$/.test(lines[end - 1]) || /^#/.test(lines[end - 1]))) end--;
  lines.splice(start, end - start);
  return lines.join("\n");
}

/** An empty value of the same shape (a new list item modelled on an existing one). */
export function blankLike(value) {
  if (Array.isArray(value)) return [];
  if (isMap(value)) return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, blankLike(v)]));
  if (typeof value === "boolean") return false;
  return "";
}

/** A new item for a list: the shape of its first mapping item, else an empty text. */
export function newItem(list) {
  const model = (list || []).find(isMap);
  return model ? blankLike(model) : "";
}

/** Set (or, with `undefined`, remove) one value; only its top-level section is rewritten. */
export function applyFormEdit(text, path, value) {
  const data = parseYaml(text);
  if (!isMap(data)) throw new Error("alfrd.yaml must be a YAML mapping.");
  setIn(data, path, value);
  const top = path[0];
  return top in data ? replaceSection(text, top.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), dumpYaml({ [top]: data[top] })) : removeSection(text, top);
}

// Preview a template, preserving the current name and version.
export function templateDraft(templateText, currentText, template) {
  const data = parseYaml(templateText) || {};
  let current = {};
  try { current = parseYaml(currentText) || {}; } catch { /* an unparsable draft keeps nothing */ }
  const { version: _v, name: _n, template: _t, ...rest } = data;
  return dumpYaml({ version: current.version ?? 1, name: current.name ?? data.name ?? "", template, ...rest });
}
