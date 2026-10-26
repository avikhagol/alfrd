// M5 is authoritative; legacy requests have no inferred step.
export function currentSteps(request, row) {
  const steps = (request?.steps || []).filter((s) => s.request_id === request.id && s.kind !== "delegate");
  return steps.filter((s) => (row === undefined || s.task_row === row)
    && ["running", "waiting"].includes(s.status)
    && !steps.some((n) => n.task_row === s.task_row && n.kind === s.kind && n.attempt > s.attempt));
}
export function stepLabel(s) {
  const kind = { plan: "Plan", execute: "Execute", repair: "Repair", replan: "Replan", summary: "Summary" }[s.kind] || s.kind;
  return `${s.kind !== "execute" ? "Orchestrator · " : ""}${kind}${s.task_row ? ` ${s.task_row}` : ""}${s.attempt > 1 ? ` · attempt ${s.attempt}` : ""} · ${s.status}`;
}
