// Job log and restart state survive closing Settings.
import { $, esc, icon } from "../utils/dom.js";
import { server } from "../data/server.js";
const key = encodeURIComponent;
const button = (text, attr, cls = "") => `<button type="button" class="btn sm ${cls}" ${attr}>${text}</button>`;
export let currentJob = null;
let restartNeeded = false, dismissedJob = null, later = false;
export const listeners = new Set();
const emit = () => listeners.forEach(fn => fn());
export const dismissJob = () => { dismissedJob = currentJob?.job.id; };
export const hideRestart = () => { later = true; };
export async function tailJob(job, offset = 0) {
  const response = await server.request(`/api/studio/plugins/jobs/${key(job.id)}?offset=${offset}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Couldn’t read plugin log (${response.status}).`);
  return { text: await response.text(), offset: Number(response.headers.get("X-Offset")), status: response.headers.get("X-Job-Status"), restart: response.headers.get("X-Restart-Required") === "1" };
}

export function startJob(job, ctx) {
  if (!job || (currentJob?.job.id === job.id && (currentJob.polling || currentJob.job.status !== "running"))) return;
  const same = currentJob?.job.id === job.id;
  const state = currentJob = same ? currentJob : { job, text: "", offset: 0, open: false };
  state.job = job; state.polling = true; server.pluginJob = job; ctx.refreshJobs?.(); emit();
  const poll = async () => {
    if (currentJob !== state || server.authRequired) { state.polling = false; return; }
    try {
      const chunk = await tailJob(state.job, state.offset);
      state.text += chunk.text; state.offset = chunk.offset; state.job = { ...state.job, status: chunk.status, restart_required: chunk.restart };
      server.pluginJob = state.job; ctx.refreshJobs?.();
      if (chunk.status !== "running") {
        state.polling = false; state.ended = Date.now(); restartNeeded ||= chunk.restart; later = false;
        if (chunk.status !== "done") state.open = true;
        emit();
        if (![...listeners].some(fn => fn.visible?.())) ctx.toast(chunk.status === "done" ? "Plugin change complete. Restart alfrd serve to load it." : "Plugin job failed. Open Settings → Plugins for the log.", chunk.status === "done" ? "ok" : "warn");
        return;
      }
      emit(); state.timer = setTimeout(poll, 1000);
    } catch (error) {
      state.error = error.message; emit(); state.timer = setTimeout(poll, 2000);
    }
  };
  poll();
}

export async function restartServer({ request = (...a) => server.request(...a), mutate = (...a) => server.mutate(...a), sleep = ms => new Promise(r => setTimeout(r, ms)), now = Date.now, reload = () => location.reload() } = {}) {
  await mutate("/studio/restart");
  const start = now(); let down = false;
  while (now() - start < 30000) {
    await sleep(500);
    let up = false;
    try { up = (await request("/api/health", { cache: "no-store", signal: AbortSignal.timeout(1500) })).ok; } catch { /* restarting */ }
    if (!up) down = true;
    else if (down || now() - start >= 3000) { reload(); return; }
  }
  throw new Error("The server has not returned. Check the terminal, then reload the Studio.");
}

export function renderJob(panel, restartTitles) {
    const host = $("[data-job]", panel), restart = $("[data-restart]", panel);
    if (!host || !restart) return;
    const s = currentJob, job = s?.job;
    if (job && dismissedJob !== job.id) {
      const old = $("pre", host), atBottom = !old || old.scrollHeight - old.scrollTop - old.clientHeight < 20, top = old?.scrollTop || 0;
      const elapsed = Math.max(0, Math.floor(((s.ended || Date.now()) - Date.parse(job.started)) / 1000));
      const label = job.status === "running" ? `${({ install: "Installing", "install-pinned": "Installing", update: "Updating", remove: "Removing" })[job.action] || "Installing"} ${job.target}…` : job.status === "done" ? `${job.action === "remove" ? "Removed" : job.action === "update" ? "Updated" : "Installed"} ${job.target}` : job.status === "timeout" ? "Stopped after 10 min" : "Failed";
      host.innerHTML = `<div class="plug-job"><p role="status" aria-live="polite"><span class="badge tone-${job.status === "done" ? "ok" : job.status === "running" ? "run" : "fail"}">${job.status === "running" ? icon("sync", "spin") : ""}${esc(label)}</span> <span class="tabular">${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}</span>${button("×", 'data-dismiss aria-label="Dismiss plugin job"', "ghost")}</p>${job.status === "failed" ? `<p class="plug-failure">${esc(s.text.trim().split("\n").at(-1) || "Job failed")}</p>` : ""}${s.error ? `<p class="callout warn">${esc(s.error)}</p>` : ""}<details ${s.open ? "open" : ""}><summary>Show log</summary><pre class="mono small">${esc(s.text)}</pre></details></div>`;
      $("details", host).ontoggle = e => { s.open = e.target.open; };
      const pre = $("pre", host); pre.scrollTop = atBottom ? pre.scrollHeight : top;
    } else host.innerHTML = "";
    restart.innerHTML = !later && (restartNeeded || restartTitles.size) ? `<p class="callout warn"><b>Restart <code>alfrd serve</code> to load the change.</b> ${server.session?.can_restart ? button("Restart now", "data-restart-now", "primary") : "Restart it in the terminal where it runs."} ${button("Later", "data-later", "ghost")}</p>` : "";
  }
