// Results view statistics. Pure functions, no DOM access.
//
// Modes: "recent" counts only the most recent attempt of each step,
// "history" counts every attempt in the result CSVs.
// Calibration counts are per target, never per attempt: a target is
// calibrated when its calibration step (alfrd.yaml `results.calibration_step`,
// default: the last workflow step) completed — in "recent" mode its latest run,
// in "history" mode any run. Calibrators are the unique sources named in that
// counted run (the latest successful one) by `results.calibrated_sources`.

/** Attempts of one step counted in a mode. */
export function attemptsOf(s, history) {
  if (!s) return [];
  if (!history) return [s];
  return s.attempts?.length ? s.attempts : [s];
}

function sourceRegex(spec) {
  if (!spec) return null;
  try {
    return new RegExp(spec, "i");
  } catch {
    return null;
  }
}

/** Source names a run reports as calibrated (from its desc / note). */
export function calibratedSources(attempt, spec) {
  const re = sourceRegex(spec);
  if (!re || !attempt) return [];
  const parts = Array.isArray(attempt.desc) ? attempt.desc : String(attempt.note || "").split(" · ");
  const out = [];
  parts.forEach((p) => {
    const m = re.exec(String(p).trim());
    if (m && m[1]) String(m[1]).split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean).forEach((x) => !out.includes(x) && out.push(x));
  });
  return out;
}

/** The run of the calibration step that decides whether a target is calibrated. */
export function calibrationRun(target, step, history) {
  const s = target?.steps?.[step];
  if (!s) return null;
  if (!history) return s;
  const list = s.attempts?.length ? s.attempts : [s];
  for (let i = list.length - 1; i >= 0; i -= 1) if (list[i].status === "completed") return list[i];
  return list[list.length - 1] || null;
}

/**
 * @param {Array} targets targets in scope
 * @param {string[]} steps workflow step keys in order
 * @param {{history?: boolean, rollup: (t) => object, results?: object}} opts
 */
export function resultStats(targets, steps, { history = false, rollup, results = {} } = {}) {
  const calStep = results.calibration_step && steps.includes(results.calibration_step) ? results.calibration_step : steps[steps.length - 1];
  const per = Object.fromEntries(steps.map((k) => [k, { sum: 0, n: 0, completed: 0, failed: 0, warning: 0, running: 0, pending: 0, retries: 0 }]));
  let total = 0;
  let attempts = 0;
  let stepN = 0;
  let failedItems = 0;
  const targetNames = new Set(targets.map((t) => t.name));
  const calibratedTargets = [];
  const failedTargets = [];
  const calibrators = new Set();
  targets.forEach((t) => {
    const r = rollup(t);
    const run = calStep ? calibrationRun(t, calStep, history) : null;
    const ok = run?.status === "completed";
    if (ok) {
      calibratedTargets.push(t.name);
      calibratedSources(run, results.calibrated_sources).forEach((src) => { if (!targetNames.has(src) && src !== t.name) calibrators.add(src); });
    } else if (r.status === "failed" || run?.status === "failed") {
      failedTargets.push(t.name);
    }
    if (!history && r.status === "failed") failedItems += 1;
    steps.forEach((k) => {
      const s = t.steps?.[k];
      const bucket = per[k];
      const list = attemptsOf(s, history);
      if (s?.attempts?.length > 1) bucket.retries += s.attempts.length - 1;
      if (!list.length) bucket.pending += 1;
      list.forEach((a) => {
        const st = a.status || "pending";
        bucket[st === "queued" || st === "skipped" ? "pending" : st] += 1;
        attempts += 1;
        if (history && (st === "failed" || st === "warning")) failedItems += 1;
        if (Number.isFinite(a.duration)) {
          total += a.duration;
          stepN += 1;
          if (st === "completed") { bucket.sum += a.duration; bucket.n += 1; }
        }
      });
    });
  });
  return {
    targets, steps, per, total, attempts, avg: stepN ? total / stepN : null, failedItems,
    calStep, calibratedTargets, failedTargets, calibrators: [...calibrators].sort(),
  };
}
