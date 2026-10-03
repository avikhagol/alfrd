import { $, on, esc, icon, elapsed } from "../utils/dom.js";
import { server } from "../data/server.js";
import { loopResults, pollLoopResults } from "../data/loop_results.js";

import { RUN_STATUS } from "./plans.js";

const cache = new Map();
let timer;
const phases = { done: "Done", failed: "Failed", running: "Running", awaiting_response: "Waiting for a response", awaiting_review: "Waiting for review" };

async function load(ctx, project, id = null) {
  const entry = cache.get(project) || {};
  if (entry.loading) return;
  const before = JSON.stringify([entry.status, entry.handoffs, entry.error]);
  entry.loading = true; entry.error = null; cache.set(project, entry);
  try {
    const status = await server.planStatus(project, id);
    const handoffs = status.plan?.loop ? (await server.handoffs(project, status.plan.id)).handoffs : [];
    entry.status = status; entry.handoffs = handoffs;
  } catch (error) { entry.error = error.message; }
  finally { entry.loading = false; if (before !== JSON.stringify([entry.status, entry.handoffs, entry.error])) ctx.update(); }
}

export function mount(el, ctx) {
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || ctx.state.view !== "results") return;
    for (const [project, entry] of cache) {
      if (pollLoopResults(entry.status, entry.handoffs)) load(ctx, project, entry.selected || null);
    }
  });
  on(el, "click", "[data-loop-refresh]", (e, b) => load(ctx, b.dataset.loopRefresh, cache.get(b.dataset.loopRefresh)?.selected || null));
  on(el, "change", "[data-loop-plan]", (e, select) => {
    const project = select.dataset.loopPlan;
    cache.get(project).selected = select.value || null;
    load(ctx, project, select.value || null);
  });
  on(el, "click", "[data-loop-handoffs]", async (e, b) => {
    try { await (await import("./agent_dialog.js")).openHandoffs(ctx, b.dataset.loopHandoffs, cache.get(b.dataset.loopHandoffs).status.plan.id, { unit: b.dataset.loopUnit }); }
    catch (error) { ctx.toast(error.message, "fail"); }
  });
}

export function render(el, ctx, projects) {
  clearTimeout(timer);
  el.innerHTML = projects.map((project) => {
    const tree = ctx.state.trees[project];
    if (ctx.state.mode !== "server" || tree.provider !== "server") return `<div class="card"><h2>Agent-loop results</h2><p class="muted">Connect with <code>alfrd serve</code> to see turns and replies.</p><button class="link-btn" data-classic-results>CSV results / collections</button></div>`;
    let entry = cache.get(project);
    if (!entry) { load(ctx, project); entry = cache.get(project); }
    const s = entry.status, p = s?.plan;
    const summary = p?.loop ? loopResults(s, entry.handoffs || []) : null;
    return `<div class="card loop-results"><div class="row gap wrap"><h2>Agent-loop results</h2><span class="chip">${esc(ctx.projectName(project))}</span><span class="grow"></span><button class="link-btn" data-classic-results>CSV results / collections</button>
      ${p ? `<label class="field loop-run"><span>Run</span><select class="input sm mono" data-loop-plan="${esc(project)}"><option value="">Latest run</option>${(s.plans || []).map((plan) => `<option value="${esc(plan.id)}" ${entry.selected === plan.id ? "selected" : ""}>Run ${esc(plan.id)} · ${esc(RUN_STATUS[plan.status] || plan.status)}</option>`).join("")}</select></label>` : ""}
      <button class="btn sm" data-loop-refresh="${esc(project)}" ${entry.loading ? "disabled" : ""}>${icon("sync")} Refresh</button>
      ${summary ? `<button class="btn sm" data-loop-handoffs="${esc(project)}">${icon("file")} Read responses / handoffs</button>` : ""}</div>
      ${entry.error ? `<p class="fail-t" role="alert">${esc(entry.error)} — Refresh to retry.</p>` : ""}
      ${entry.loading && !s ? '<p class="muted" role="status">Loading turn results…</p>' : !p ? '<p class="muted">No run yet. Start a run from Workflow → Run.</p>' : !summary ? '<p class="muted">No loop turns in this run. Choose another run.</p>' : `
      <p class="muted small mono">Run ${esc(p.id)} · ${esc(RUN_STATUS[p.status] || p.status)}${entry.loading ? " · refreshing" : ""}</p>
      <div class="kpis"><div><span>Done turns</span><b>${summary.done} / ${summary.total}</b></div><div><span>Archived replies</span><b>${summary.replies}</b><small>${summary.turns.length} attempts</small></div><div><span>Waiting for you</span><b>${summary.waiting}</b><small>Responses or review</small></div><div><span>Elapsed turn time</span><b>${elapsed(summary.seconds)}</b><small>Includes waiting · ${summary.failed} failed or blocked turns</small></div></div>
      <div class="loop-result-table"><table class="tbl"><thead><tr><th>Iteration</th><th>Agent / model</th><th>Status</th><th>Elapsed</th><th>Response</th></tr></thead><tbody>${summary.turns.map((h) => `<tr><td>${esc(h.iteration_label || h.iteration)}</td><td>${esc(h.agent || "—")}<div class="muted small">${esc(h.model || h.requested_model || "Default model")}</div></td><td>${esc(phases[h.phase] || h.phase || h.status)}${h.error ? `<div class="fail-t small">${esc(h.error)}</div>` : ""}</td><td>${h.seconds == null ? "—" : elapsed(h.seconds)}</td><td>${h.response_bytes ? `<button class="link-btn" data-loop-handoffs="${esc(project)}" data-loop-unit="${esc(h.id)}">Read archived reply</button><div class="mono small muted">${esc(h.artifact?.path || "Awaiting publication")}</div>` : "No reply yet"}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No turns have started.</td></tr>'}</tbody></table></div>`}</div>`;
  }).join("");
  const busy = projects.filter((p) => pollLoopResults(cache.get(p)?.status, cache.get(p)?.handoffs));
  const poll = () => {
    if (!busy.some((p) => pollLoopResults(cache.get(p)?.status, cache.get(p)?.handoffs))) return;
    timer = setTimeout(async () => {
      if (ctx.state.view !== "results" || document.hidden) return;
      await Promise.all(busy.map((p) => load(ctx, p, cache.get(p).selected || null)));
      clearTimeout(timer); poll();
    }, 3000);
  };
  poll();
}
