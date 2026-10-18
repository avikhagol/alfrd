// Header Target picker ▸ "New target…": create a target (targets-file row) or, in a
// folder-based agent-loop project, a task folder; then select it and show its workflow.
// Loaded on first use.

import { $, esc, icon } from "../utils/dom.js";
import { server } from "../data/server.js";

/** Problem with `name` for a new target, or "" (mirrors plan_csv.check_target, agent_loop.validate_target for tasks). */
export function targetNameError(name, existing = [], task = false) {
  const n = String(name || "").trim();
  if (!n) return "Enter a name.";
  if (task && !/^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$/.test(n)) return "Use 1–64 letters, digits, . _ or - (no leading dot); it becomes the task folder.";
  if (n.startsWith("#")) return "A name cannot start with # (that marks a comment row).";
  if (n.includes("@")) return "A name cannot contain @ (it separates target and project code).";
  if (/[/\\]/.test(n)) return "A name cannot contain / or \\.";
  if (existing.includes(n)) return `“${n}” already exists in this project.`;
  return "";
}

/** After a create: re-read the project, select `name` and show its workflow (unless the user moved on). */
export async function showNewTarget(ctx, project, name) {
  await ctx.refreshProject(project);
  if (ctx.state.selectedProject !== project) { ctx.toast(`Created ${name} in ${ctx.projectName(project)}`, "ok"); return; }
  const t = ctx.state.targets.find((x) => x.project === project && x.name === name);
  if (!t) { ctx.toast(`Created ${name}; it appears after the next scan`, "ok"); return; }
  ctx.selectTarget(t.id);
  ctx.goTo("workflow");
  ctx.toast(`Created ${name}`, "ok");
}

/** Open the form for `project`. `existing`: the project's target names (checked while typing). */
export async function openNewTarget(ctx, project, existing = []) {
  let info;
  try {
    const res = await server.request(`/api/studio/projects/${encodeURIComponent(project)}/targets/new`);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    info = await res.json();
  } catch (error) { ctx.toast(`Cannot create a target here: ${error.message}`, "fail"); return; }
  const task = info.kind === "task";
  const noun = task ? "task" : "target";
  let busy = false, cancelled = false;
  ctx.modal(`<header class="modal-h"><h2>${icon("plus")} New ${noun}</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <form class="modal-b" id="nt-form" novalidate>
      <p class="muted small">In <b>${esc(ctx.projectName(project))}</b>: ${task ? `creates the folder <code>&lt;name&gt;/</code> with its <code>task.md</code> and adds a row to the plan.` : `adds a row to <code>${esc(info.file)}</code>.`}</p>
      <label class="field"><span>Name</span><input class="input" id="nt-name" autocomplete="off" spellcheck="false" required aria-describedby="nt-err" aria-invalid="false"></label>
      ${task ? `<label class="field"><span>Task</span><textarea class="input" id="nt-task" rows="4" placeholder="What should the agents do?" aria-describedby="nt-err"></textarea></label>`
        : `<label class="field"><span>Project code <span class="muted">(optional)</span></span><input class="input" id="nt-code" autocomplete="off" spellcheck="false"></label>`}
      <p id="nt-err" class="small fail-t" role="alert"></p>
    </form>
    <footer class="modal-f row gap"><span class="grow"></span><button class="btn" id="nt-cancel" data-close>Cancel</button>
      <button class="btn primary" id="nt-go" type="submit" form="nt-form" disabled>Create</button></footer>`, (root, close) => {
    const name = $("#nt-name", root), go = $("#nt-go", root), err = $("#nt-err", root);
    const body = $("#nt-task", root);
    const check = (show) => {
      const problem = targetNameError(name.value, existing, task) || (task && !body.value.trim() ? "Describe the task." : "");
      go.disabled = busy || Boolean(problem);
      const visible = show && name.value ? problem : "";
      err.textContent = visible;
      name.setAttribute("aria-invalid", String(Boolean(visible) && !visible.startsWith("Describe")));
      return problem;
    };
    name.addEventListener("input", () => check(true));
    body?.addEventListener("input", () => check(true));
    root.addEventListener("beforeclose", () => { cancelled = busy; }); // a late reply only toasts
    $("#nt-form", root).addEventListener("submit", async (e) => {
      e.preventDefault();
      if (busy || check(true)) return;
      busy = true;
      go.disabled = true;
      go.innerHTML = `${icon("sync", "spin")} Creating…`;
      const payload = { name: name.value.trim(), ...(task ? { task: body.value } : { code: $("#nt-code", root).value.trim() }) };
      try {
        const made = await server.mutate(`/studio/projects/${encodeURIComponent(project)}/targets/add`, payload);
        if (cancelled) { ctx.toast(`${task ? "Task" : "Target"} ${made.name} was created`, "ok"); return; }
        close();
        await showNewTarget(ctx, project, made.name);
      } catch (error) {
        busy = false;
        if (cancelled) { ctx.toast(`${task ? "Task" : "Target"} not created: ${error.message}`, "fail"); return; }
        go.textContent = "Create";
        err.textContent = error.status === 409 ? `“${payload.name}” already exists in this project.` : error.message;
        go.disabled = false;
        name.focus();
      }
    });
  }, "sm");
}
