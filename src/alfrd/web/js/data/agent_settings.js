// Pure helpers for agent and review settings.
export function replaceSection(text, key, block) {
  const lines = String(text).split("\n"), start = lines.findIndex((l) => new RegExp(`^${key}\\s*:`).test(l));
  if (start < 0) return text.trimEnd() + "\n\n" + block.trimEnd() + "\n";
  let end = start + 1;
  while (end < lines.length && !/^[^\s#-][^:]*:/.test(lines[end])) end++;
  while (end > start + 1 && (/^\s*$/.test(lines[end - 1]) || /^#/.test(lines[end - 1]))) end--;
  lines.splice(start, end - start, ...block.trimEnd().split("\n"));
  return lines.join("\n");
}

export function loopRunSelection(info) {
  const rows = info.table?.rows || [];
  return { rows: rows.length ? rows.map(({ target, files, code, workdir }) => ({ target, files, code, workdir })) : [{ target: info.loop?.task_row || "task", files: "", code: "", workdir: "" }],
    preferNew: !rows.length || rows.every((r) => Object.values(r.cells).every((v) => v === "done")) };
}

export { agentRows, reviewRows, applyAgentSettings, personaRows, turnRoleRows, applyPersonaSettings } from "./defs.js";
