import { esc } from "../utils/dom.js";

const counters = [
  ["tool_calls", "tool calls"], ["web_search", "web searches"], ["file_edit", "edits"],
  ["shell", "commands"], ["tool_errors", "tool errors"], ["web_fetch", "web fetches"],
  ["file_read", "file reads"], ["subagent", "delegations"], ["mcp", "MCP calls"],
  ["mailbox", "mailbox calls"], ["turns", "turns"], ["files_changed", "files changed"],
];

export function countText(count) {
  if (!Number.isSafeInteger(count?.value) || count.value < 0) return "Not reported";
  if (count.coverage === "complete") return count.value.toLocaleString();
  return count.coverage === "partial" && count.value > 0 ? `At least ${count.value.toLocaleString()}` : "Not reported";
}

export function quietCounts(telemetry) {
  return counters.slice(0, 5).flatMap(([key, label]) => {
    const text = countText(telemetry?.counts?.[key]);
    return text === "Not reported" ? [] : [`${text} ${label}`];
  }).join(" · ");
}

// Enumerate real unit ids, including failed attempts and orchestrator rows. Never sum task totals.
export function usageUnits(state, request) {
  return [...new Set((state?.tasks || []).filter((t) => t.request === request)
    .flatMap((t) => [...(t.units || []), ...(t.logs || []).map((l) => l.unit)]).filter(Boolean))];
}

export function usageCountsHtml(request, telemetry, items, units, snapshot) {
  const rows = items.filter((r) => r.request === request?.id && units.includes(r.unit));
  const cells = (doc) => counters.map(([key, label]) => {
    const count = doc?.counts?.[key];
    return `<div>${esc(countText(count))} ${label} <span class="muted">(${esc(count?.coverage || "unavailable")}${count?.reason ? `: ${esc(count.reason)}` : ""})</span></div>`;
  }).join("");
  const total = telemetry || request?.telemetry;
  return `<p class="muted small">Searches, commands and edits are included in tool calls. At least means partial coverage. Not reported means unknown; zero requires complete coverage. Request totals include failed attempts and orchestrator work.</p>
    <p role="status" class="small">${snapshot.unsupported ? "Attempt detail unavailable on this server." : snapshot.stale ? "Updates paused — reconnecting; figures may be stale." : !snapshot.at ? "Loading attempt detail…" : ""} ${rows.length}/${units.length} known attempts loaded${total?.attempts != null ? `; ${esc(total.attempts)} attempts in request total` : ""}. Detail loading is separate from counter coverage.</p>
    <table class="tbl small"><caption>Usage for request ${esc(request?.id || "")}</caption><thead><tr><th scope="col">Attempt</th><th scope="col">Counts and coverage</th></tr></thead><tbody>
    <tr><th scope="row">Request total</th><td>${cells(total)}</td></tr>
    ${rows.map((r) => `<tr><th scope="row">${esc(r.title || r.row || r.unit)} · ${esc(r.kind || "task")} · attempt ${esc(r.attempt ?? "not reported")} · ${esc(r.status || "unknown")} · ${esc(r.unit)}</th><td>${cells(r.telemetry)}</td></tr>`).join("")}</tbody></table>`;
}
