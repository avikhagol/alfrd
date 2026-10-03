export const waitingTurn = (h) => ["awaiting_response", "awaiting_review"].includes(h.phase);

export function pollLoopResults(status, handoffs = []) {
  if (["finished", "failed", "cancelled"].includes(status?.plan?.status)) return false;
  return ["running", "paused", "interrupted"].includes(status?.plan?.status) || handoffs.some(waitingTurn);
}

export function loopResults(status, handoffs, now = Date.now()) {
  const cells = (status.table?.rows || []).flatMap((r) => Object.values(r.cells || {})).filter((v) => v && v !== "skip");
  let counts = {};
  cells.forEach((v) => { counts[v] = (counts[v] || 0) + 1; });
  if (["finished", "failed", "cancelled"].includes(status.plan?.status) && status.plan.counts) {
    counts = status.plan.counts;
  }
  const latest = new Map();
  const key = (h) => h.steps?.length ? JSON.stringify([h.row, h.steps]) : h.id;
  handoffs.forEach((h) => latest.set(key(h), h));
  const turns = handoffs.map((h) => {
    const start = Date.parse(h.started), end = h.finished ? Date.parse(h.finished) : (h.status === "running" || waitingTurn(h)) && latest.get(key(h)) === h ? now : NaN;
    return { ...h, seconds: Number.isFinite(start) && Number.isFinite(end) ? Math.max(0, (end - start) / 1000) : null };
  });
  return { total: status.plan?.steps?.length || cells.length, done: counts.done || 0,
    failed: (counts.failed || 0) + (counts.blocked || 0) + (counts.interrupted || 0),
    waiting: [...latest.values()].filter(waitingTurn).length,
    replies: turns.filter((h) => h.response_bytes > 0).length,
    seconds: turns.reduce((n, h) => n + (h.seconds || 0), 0), turns };
}
