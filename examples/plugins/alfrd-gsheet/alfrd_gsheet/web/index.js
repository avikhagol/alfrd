// No network in the panel renderer. The palette's explicit check uses the protected host endpoint;
// the project card and dialogs (project_ui.js) call the plugin's project actions.
import { registerProjectUI } from "./project_ui.js";

const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
const tones = { syncing: ["ok", "✓"], "dry run": ["warn", "◐"], off: ["muted", "–"], invalid: ["fail", "!"] };
const results = { ok: ["ok", "✓"], "no changes": ["muted", "–"], "dry run": ["warn", "◐"],
  "skipped (conflict)": ["warn", "!"], error: ["fail", "!"] };
let sequence = 0;

function chip(text, tone, glyph) {
  return `<span class="gsheet-chip tone-${tone}"><span aria-hidden="true">${glyph}</span> ${esc(text)}</span>`;
}

function relative(iso) {
  const elapsed = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(elapsed)) return "Unknown time";
  if (elapsed < 0) return "Just now";
  if (elapsed < 60) return "Just now";
  for (const [seconds, unit] of [[86400, "day"], [3600, "hour"], [60, "minute"]]) {
    if (elapsed >= seconds) {
      const count = Math.floor(elapsed / seconds);
      return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
    }
  }
}

function row(entry, hidden = false) {
  const [tone, glyph] = results[entry.result] || results.error;
  const label = entry.result === "error" ? entry.error || "Sync failed. Validate the mapping." : entry.result;
  return `<tr${hidden ? ' data-gsheet-more hidden' : ""}><th scope="row">${esc(entry.step)}</th>
    <td><time datetime="${esc(entry.at)}" title="${esc(entry.at)}">${esc(relative(entry.at))}</time></td>
    <td>${esc(entry.cells_written ?? 0)}</td><td>${esc(entry.conflicts ?? 0)}</td><td>${chip(label, tone, glyph)}</td></tr>`;
}

export function render(inst) {
  const [tone, glyph] = tones[inst.state] || tones.invalid;
  const id = `gsheet-rows-${++sequence}`;
  let html = `<section class="gsheet-summary" aria-label="Google Sheet sync"><p class="gsheet-heading"><strong>Google Sheet</strong>
    ${chip(inst.label || "Summary unavailable", tone, glyph)}</p>`;
  if (inst.sheet && /^https:\/\/docs\.google\.com\/spreadsheets\/d\/[A-Za-z0-9_-]{20,}\/edit(?:#gid=\d+)?$/.test(inst.sheet.url)) {
    html += `<p class="small"><span class="gsheet-sheet">${esc(inst.sheet.id)} › ${esc(inst.sheet.worksheet)}</span>
      <a href="${esc(inst.sheet.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open the Google Sheet in a new tab">Open sheet ↗</a></p>`;
  }
  if (inst.sheet_note) html += `<p class="small muted">${esc(inst.sheet_note)}</p>`;
  if (inst.error) html += `<p class="gsheet-error" role="status">! ${esc(inst.error)}</p>`;
  if (inst.empty) html += `<p>${esc(inst.empty)}</p>`;
  if (inst.command) html += `<p><code class="gsheet-command" tabindex="0" aria-label="Select and copy this setup command">${esc(inst.command)}</code></p>`;
  const rows = inst.rows || [];
  if (rows.length) {
    html += `<div class="grid-scroll gsheet-scroll" tabindex="0" role="region" aria-label="Last sync per step; scroll horizontally to see all columns"><table class="tbl small gsheet-table"><caption>Last sync per step</caption>
      <thead><tr>${["Step", "When", "Cells written", "Conflicts", "Result"].map((name) => `<th scope="col">${name}</th>`).join("")}</tr></thead>
      <tbody id="${id}">${rows.slice(0, 10).map((e) => row(e)).join("")}${(inst.more_rows || []).map((e) => row(e, true)).join("")}</tbody></table></div>
      <p class="small muted gsheet-scroll-hint">Scroll the table to see all columns.</p>
      <p class="small muted">Counts are unit totals; a unit that syncs several steps shares these counts.</p>`;
    if (inst.more_rows?.length) html += `<button type="button" class="btn small" data-gsheet-toggle data-total="${esc(inst.total)}" aria-expanded="false" aria-controls="${id}">Show all (${esc(inst.total)})</button>`;
  }
  if (inst.history_truncated) html += `<p class="small muted">Showing steps found in the most recent 1 MiB of sync history. Use alfrd gsheet status for more.</p>`;
  if (inst.note) html += `<p class="small muted">${esc(inst.note)}</p>`;
  return html + "</section>";
}

export function activate(api) {
  api.registerPanel("gsheet_sync", (inst) => api.sanitize(render(inst)));
  if (api.registerProjectSection && api.dialog && api.action) registerProjectUI(api);
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("button[data-gsheet-toggle]");
    if (!button) return;
    const expanded = button.getAttribute("aria-expanded") !== "true";
    button.closest(".gsheet-summary").querySelectorAll("[data-gsheet-more]").forEach((row) => { row.hidden = !expanded; });
    button.setAttribute("aria-expanded", String(expanded));
    button.textContent = expanded ? "Show fewer" : `Show all (${button.dataset.total})`;
  });
  api.registerCommand({
    id: "gsheet-validate", label: "Google Sheet: validate mapping",
    async run() {
      const project = api.project();
      if (!project) { api.toast("Select a project to validate its Google Sheet mapping.", "warn"); return; }
      try {
        const result = await api.fetchJSON(`/studio/projects/${encodeURIComponent(project)}/plugins/gsheet/check`, { method: "POST" });
        api.toast(result.text, result.level);
      } catch {
        api.toast("Could not validate the mapping. Check your Studio connection and try again.", "fail");
      }
    },
  });
}
