// Overview filters as pure functions (no DOM), shared by the grid, the CSV
// export and the tests.

/** Quick filters next to the status bar: (target, rollup, codes) → bool. */
export const PRESETS = {
  nometa: (t, r, codes) => !codes.length,
};

export const NO_CODE = "(none)";

/**
 * Targets that pass `ui` = {project, status, code, preset, search}.
 * helpers: rollup(t) → {status}, codes(t) → ["BV019", …], text(t) → lower-case search text.
 */
export function filterTargets(targets, ui, { rollup, codes, text }) {
  const q = String(ui.search || "").trim().toLowerCase();
  return targets.filter((t) => {
    if (ui.project && ui.project !== "all" && t.project !== ui.project) return false;
    const r = rollup(t);
    if (ui.status && ui.status !== "all" && r.status !== ui.status) return false;
    const list = codes(t);
    if (ui.code && ui.code !== "all") {
      if (ui.code === NO_CODE ? list.length : !list.includes(ui.code)) return false;
    }
    if (ui.preset && PRESETS[ui.preset] && !PRESETS[ui.preset](t, r, list)) return false;
    if (q && !text(t).includes(q)) return false;
    return true;
  });
}

/** The filters that are set, as removable chips: [{key, label}]. */
export function activeFilters(ui, labels = {}) {
  const out = [];
  if (ui.status && ui.status !== "all") out.push({ key: "status", label: `Status: ${labels.status?.[ui.status] || ui.status}` });
  if (ui.preset && PRESETS[ui.preset]) out.push({ key: "preset", label: labels.preset?.[ui.preset] || ui.preset });
  if (ui.code && ui.code !== "all") out.push({ key: "code", label: `Code: ${ui.code === NO_CODE ? "none" : ui.code}` });
  if (ui.project && ui.project !== "all") out.push({ key: "project", label: `Project: ${labels.project?.[ui.project] || ui.project}` });
  if (String(ui.search || "").trim()) out.push({ key: "search", label: `“${String(ui.search).trim()}”` });
  return out;
}

/** Default value of each filter (what "clear" sets it back to). */
export const FILTER_DEFAULTS = { status: "all", preset: null, code: "all", project: "all", search: "" };
