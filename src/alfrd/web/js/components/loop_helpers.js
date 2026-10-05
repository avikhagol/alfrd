import { MAX_ITERATIONS, MAX_TURNS } from "../data/defs.js";

export function loopTurnCount(iterations, steps) {
  if (!Number.isInteger(iterations) || iterations < 1 || iterations > MAX_ITERATIONS) return 0;
  const count = Array.isArray(steps) ? steps.length : steps;
  return Number.isInteger(count) && count > 0 ? iterations * count : 0;
}

export function summarizeLoop(units, definition = {}) {
  const turns = units.filter((u) => u.iteration);
  const current = turns.find((u) => u.status === "running") || turns.at(-1);
  const iterations = definition.iterations || current?.iterations || Math.max(0, ...turns.map((u) => u.iteration));
  const agents = new Set(turns.map((u) => u.agent)).size;
  const steps = definition.steps || agents;
  const turnsUnit = definition.unit === "turns";
  const total = turnsUnit ? (Number.isInteger(iterations) && iterations >= 1 && iterations <= MAX_TURNS ? iterations : 0) : loopTurnCount(iterations, steps);
  const state = definition.status === "paused" ? "paused" : current?.status === "failed" ? "failed"
    : definition.status === "finished" || (total && turns.length >= total && turns.every((u) => u.status === "done")) ? "done"
      : current?.status === "running" ? "running" : current?.status || "pending";
  const turn = current ? turns.indexOf(current) + 1 : 0;
  if (turnsUnit) return `Turn ${turn} of ${total} · ${current?.agent || "ready"} · ${state}`;
  return `Turn ${turn} of ${total} · Iteration ${current?.iteration || 0}/${iterations} · ${current?.agent || "ready"} · ${state}`;
}

export function hasDirtyHandoff(loaded, text) {
  return Boolean(loaded && loaded.text !== text);
}
