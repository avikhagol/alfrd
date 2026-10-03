import { parseYaml, dumpYaml } from "../utils/yaml_parser.js";

export function configFields(text) {
  const data = parseYaml(text);
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("alfrd.yaml must be a YAML mapping.");
  const workflows = Array.isArray(data.workflows) ? data.workflows : Object.values(data.workflows || {});
  const repeated = workflows.filter((w) => w?.repeat);
  return { name: data.name || "", description: data.description || "", timeout: data.execution?.timeout ?? "",
    iterations: repeated.length === 1 ? repeated[0].repeat.iterations : null };
}

// Rewrite only a changed section; unrelated keys and comments stay byte-for-byte.
function section(text, key, value) {
  const lines = text.split("\n"), start = lines.findIndex((l) => l.startsWith(`${key}:`));
  const block = dumpYaml({ [key]: value }).trimEnd().split("\n");
  if (start < 0) return text.trimEnd() + "\n\n" + block.join("\n") + "\n";
  let end = start + 1;
  while (end < lines.length && !/^[^\s#-][^:]*:/.test(lines[end])) end++;
  while (end > start + 1 && (/^\s*$/.test(lines[end - 1]) || /^#/.test(lines[end - 1]))) end--;
  lines.splice(start, end - start, ...block);
  return lines.join("\n");
}

export function setConfigField(text, key, value) {
  const data = parseYaml(text);
  configFields(text);
  if (key === "name" || key === "description") return section(text, key, value);
  if (key === "timeout") {
    if (value !== "" && (!Number.isInteger(Number(value)) || Number(value) < 1)) throw new Error("Timeout must be a positive whole number, or blank for the default.");
    const execution = { ...(data.execution || {}) };
    if (value === "") delete execution.timeout;
    else execution.timeout = Number(value);
    return section(text, "execution", execution);
  }
  if (key === "iterations") {
    const n = Number(value);
    if (!Number.isInteger(n) || n < 1 || n > 100) throw new Error("Iterations must be a whole number from 1 to 100.");
    const workflows = Array.isArray(data.workflows) ? data.workflows : Object.values(data.workflows || {});
    const repeated = workflows.filter((w) => w?.repeat);
    if (repeated.length !== 1) throw new Error("Edit multiple repeating workflows in the YAML file.");
    repeated[0].repeat.iterations = n;
    return section(text, "workflows", data.workflows);
  }
  throw new Error(`Unknown setting: ${key}`);
}
