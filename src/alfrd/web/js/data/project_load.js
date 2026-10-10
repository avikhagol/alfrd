// DOM-free startup jobs. Catalog order is stable; request/application order is not.
// onSettle(job, source, generation) may be async. Check isCurrent(generation)
// again after any await in that hook before writing app state.
export function createProjectLoader({ scan, runtime, onSettle = () => {}, onChange = () => {} }) {
  const sources = { scan, runtime };
  let generation = 0, jobs = [], queue = [], active = 0, finish;

  function complete() {
    if (jobs.every((job) => !job.running && [job.scan, job.runtime].every((s) => s.status === "ready" || s.status === "failed"))) {
      finish?.({ generation, jobs, cancelled: false });
      finish = null;
    }
  }

  function pump() {
    while (active < 2 && queue.length) {
      const job = queue.shift();
      const gen = generation;
      job.running = true;
      active++;
      const pending = Object.keys(sources).filter((key) => job[key].status === "queued");
      pending.forEach((key) => { job[key].status = "loading"; });
      onChange();
      Promise.all(pending.map(async (key) => {
        let value, error;
        try { value = await sources[key](job.project); } catch (reason) { error = reason || new Error("Could not load project data."); }
        if (gen !== generation) return;
        const source = job[key];
        if (!error) source.value = value;
        source.error = error || (value?.ok === false || value?.failed?.length ? value.error || "Some project data could not be loaded." : null);
        source.status = source.error ? "failed" : "ready";
        source.applying = true;
        try { await onSettle(job, key, gen); }
        catch (reason) {
          if (gen === generation) { source.status = "failed"; source.error = reason; }
        } finally { source.applying = false; if (gen === generation) onChange(); }
      })).finally(() => {
        job.running = false;
        active--;
        pump();
        complete();
        onChange();
      });
    }
    complete();
  }

  function invalidate() {
    finish?.({ generation, jobs, cancelled: true });
    finish = null;
    generation++;
    queue = [];
    jobs = [];
    // Old requests still occupy slots until they settle, but cannot publish.
  }

  function promote(project) {
    const i = queue.findIndex((job) => job.project.name === project);
    if (i > 0) queue.unshift(...queue.splice(i, 1));
  }

  return {
    get generation() { return generation; },
    get jobs() { return jobs; },
    isCurrent: (gen) => gen === generation,
    invalidate,
    reloadAll(projects, selected) {
      invalidate();
      jobs = projects.map((project) => ({ project, running: false, scan: { status: "queued" }, runtime: { status: "queued" } }));
      queue = [...jobs];
      promote(selected);
      const done = new Promise((resolve) => { finish = resolve; });
      pump();
      return done;
    },
    promote,
    retry(project) {
      const job = jobs.find((job) => job.project.name === project);
      if (!job || job.running) return false;
      const failed = Object.keys(sources).filter((key) => job[key].status === "failed");
      if (!failed.length) return false;
      failed.forEach((key) => { job[key].status = "queued"; job[key].error = null; });
      job.retained ||= Boolean(job.scan.value || job.runtime.value);
      queue.unshift(job);
      pump();
      return true;
    },
  };
}

// Presentation is derived from both sources; failed and pending never mean empty.
export function projectLoadInfo(job) {
  if (!job) return { badge: "", pending: false, failed: false, usable: true };
  const entries = [job.scan, job.runtime];
  const pending = entries.some((s) => s.status === "queued" || s.status === "loading" || s.applying);
  const failed = entries.some((s) => s.status === "failed");
  const usable = job.retained || entries.some((s) => s.value);
  const badge = job.retained ? pending ? "Updating" : failed ? "Update failed" : ""
    : pending ? entries.every((s) => s.status === "queued") ? "Waiting"
      : job.scan.status === "ready" && !job.scan.applying ? "Loading runtime" : job.runtime.status === "ready" && !job.runtime.applying ? "Loading files" : "Loading"
    : failed ? usable ? "Partly loaded" : "Could not load" : "";
  const name = job.project.title || job.project.name;
  const message = job.retained && (pending || failed) ? pending ? `Updating ${name}…` : "Update failed. Showing previously loaded data."
    : failed ? usable ? "Some project data could not be loaded." : `Could not load ${name}.`
    : badge === "Waiting" ? `Waiting to load ${name}…` : badge === "Loading runtime" ? "Loading runtime data…"
    : badge === "Loading files" ? "Loading project files…" : pending ? `Loading ${name}…` : "";
  const errors = entries.map((s, i) => s.status === "failed" ? `${i ? "Runtime data" : "Project files"}: ${s.error?.message || s.error || "Could not load"}` : "").filter(Boolean).join(" ");
  return { badge, message, errors, pending, failed, usable, retrying: Boolean(job.retrying && (pending || job.running)) };
}
