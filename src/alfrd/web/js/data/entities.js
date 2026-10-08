// Entity paths (same rules as alfrd/entities.py): {project, <levels>, step, file, line}.
// Levels come from the template's `hierarchy`; the AVICA levels otherwise.

export const AVICA_LEVELS = ["target", "project_code", "workdir", "band"];
const TAIL = ["step", "file", "line"];
export const FRAGMENT = "#e?";

export function levelsFrom(manifest) {
  const h = manifest?.hierarchy;
  if (!Array.isArray(h) || !h.length) return AVICA_LEVELS;
  return [...new Set(["target", ...h.map((i) => i?.level).filter(Boolean).map(String)])];
}

export const entityKeys = (levels = AVICA_LEVELS) => ["project", ...levels, ...TAIL];

/** Validated entity in the stable key order (empty values dropped); throws on unknown keys. */
export function makeEntity(raw = {}, levels = AVICA_LEVELS) {
  const allowed = entityKeys(levels);
  const unknown = Object.keys(raw).filter((k) => !allowed.includes(k));
  if (unknown.length) throw new Error(`unknown entity level(s): ${unknown.sort().join(", ")}`);
  const out = {};
  for (const k of allowed) {
    let v = raw[k];
    if (v == null || v === "") continue;
    if (k === "line") {
      v = Number(v);
      if (!Number.isInteger(v) || v < 1) throw new Error("entity line must be an integer ≥ 1");
    } else v = String(v);
    out[k] = v;
  }
  if (!out.project) throw new Error("an entity path needs a project");
  if (out.line && !out.file) throw new Error("an entity line needs a file");
  return out;
}

export function entityToQuery(e, levels) {
  return Object.entries(makeEntity(e, levels)).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
}

export function entityFromQuery(text, levels) {
  let s = String(text || "");
  for (const p of [FRAGMENT, "#", "?"]) if (s.startsWith(p)) { s = s.slice(p.length); break; }
  const raw = {};
  s.split("&").filter(Boolean).forEach((part) => {
    const i = part.indexOf("=");
    raw[decodeURIComponent(i < 0 ? part : part.slice(0, i))] = decodeURIComponent(i < 0 ? "" : part.slice(i + 1));
  });
  return makeEntity(raw, levels);
}

export const entityToFragment = (e, levels) => FRAGMENT + entityToQuery(e, levels);

/** Short label: "J0742+103 · BV019/wd_1 · rpicard · casa.log:812". */
export function entityLabel(e) {
  const where = [e.project_code, e.workdir, e.band].filter(Boolean).join("/");
  const file = e.file ? `${e.file.split("/").pop()}${e.line ? `:${e.line}` : ""}` : "";
  return [e.target, where, e.step, file].filter(Boolean).join(" · ");
}
