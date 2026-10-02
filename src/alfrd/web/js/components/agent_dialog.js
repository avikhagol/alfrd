import { parseYaml } from "../utils/yaml_parser.js";
import { DEFAULT_ITERATIONS, MAX_ITERATIONS, loadTemplate } from "../data/defs.js";
import { loopTurnCount, hasDirtyHandoff } from "./loop_helpers.js";
import { dockLog } from "./logview.js";
import { server } from "../data/server.js";
import { esc } from "../utils/dom.js";

export async function openCreateProject(ctx, onCreated) {
  const { templates } = await server.projectTemplates();
  ctx.modal(`<header class="modal-h"><h2>New project</h2><button class="btn" data-close>Close</button></header>
    <form id="create-project"><div class="modal-b">
    <label class="field"><span>Folder</span><input class="input" name="path" required placeholder="/path/to/project"></label>
    <label class="field"><span>Name</span><input class="input" name="name" placeholder="Folder name"></label>
    <label class="field"><span>Template</span><select class="input" name="template">${templates.map((t) => `<option value="${esc(t.name)}" ${t.name === "basic" ? "selected" : ""}>${esc(t.name)} — ${esc(t.description)}</option>`).join("")}</select></label>
    <label class="field" data-loop-field><span>Initial task (for agent workflows)</span><textarea class="input" name="task" rows="5"></textarea></label>
    <label class="field" data-loop-field><span>Iterations</span><input class="input" name="iterations" type="number" min="1" max="${MAX_ITERATIONS}" value="${DEFAULT_ITERATIONS}"></label>
    <p id="loop-preview" data-loop-field class="muted small"></p><p class="muted small">One iteration runs every workflow step once. Creation saves the project; start execution from Workflow → Run.</p>
    <p id="create-error" role="alert"></p></div><footer class="modal-f"><button class="btn primary" type="submit">Create project</button></footer></form>`, (root, close) => {
    const template = root.querySelector('[name="template"]');
    const iterations = root.querySelector('[name="iterations"]');
    let previewGeneration = 0;
    const preview = async () => {
      const generation = ++previewGeneration;
      const looping = template.value === "agent-loop";
      root.querySelectorAll("[data-loop-field]").forEach((field) => { field.hidden = !looping; });
      iterations.disabled = !looping;
      root.querySelector('[name="task"]').disabled = !looping;
      if (looping) {
        try {
          const info = await loadTemplate(template.value, parseYaml);
          if (generation !== previewGeneration) return;
          const count = info?.workflows?.[0]?.steps?.length || 0;
          root.querySelector("#loop-preview").textContent = `${iterations.value} iterations × ${count} agents = ${loopTurnCount(Number(iterations.value), count)} turns`;
        } catch { root.querySelector("#loop-preview").textContent = "Unable to load workflow preview"; }
      }
    };
    template.addEventListener("change", preview);
    iterations.addEventListener("input", preview);
    preview();
    root.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = root.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        const payload = Object.fromEntries(new FormData(event.target));
        if (payload.iterations !== undefined) payload.iterations = Number(payload.iterations);
        const project = await server.createProject(payload);
        close();
        await onCreated(project);
        ctx.toast("Project created", "ok");
      } catch (error) { root.querySelector("#create-error").textContent = error.message; }
      finally { button.disabled = false; }
    });
  });
}

export async function openHandoffs(ctx, project, id) {
  let [{ handoffs }, info] = await Promise.all([server.handoffs(project, id), server.executionInfo(project)]);
  const files = [...new Set(info.steps.flatMap((s) => Object.values(s.handoff || {})))];
  let active = handoffs.find((h) => h.manual && h.status === "running");
  const canWrite = Boolean(server.session?.mutations_enabled);
  ctx.modal(`<header class="modal-h"><h2>Agent handoffs</h2><button class="btn" id="handoff-refresh">Refresh</button><button class="btn" data-close>Close</button></header>
    <div class="modal-b">
    <div id="handoff-turns"></div>
    <section id="manual-section" ${active ? "" : "hidden"}><h3>Submit a chat response</h3><p>Copy the incoming prompt above into chat, then paste the complete Markdown response.</p><textarea class="input" id="manual-response" rows="8"></textarea><button class="btn" id="manual-submit" ${canWrite ? "" : "disabled"}>Submit response</button></section>
    <h3>Edit a next step</h3><p class="muted small">An active turn keeps its frozen input. Pause before changing a prompt for the next turn.</p>
    <select class="input" id="handoff-file">${files.map((f) => `<option>${esc(f)}</option>`).join("")}</select>
    <textarea class="input" id="handoff-text" rows="8"></textarea>
    <button class="btn" id="handoff-save" ${canWrite ? "" : "disabled"}>Save next step</button><p id="handoff-message" role="status"></p>
    </div>`, (root) => {
    const select = root.querySelector("#handoff-file"), text = root.querySelector("#handoff-text"), message = root.querySelector("#handoff-message");
    let loaded = null, generation = 0;
    const renderTurns = () => {
      const list = root.querySelector("#handoff-turns");
      list.innerHTML = handoffs.map((h, index) => `<details data-turn="${index}"><summary>Iteration ${esc(h.iteration_label)} · ${esc(h.agent)} · ${esc(h.phase)} · ${h.prompt_bytes} / ${h.response_bytes} bytes</summary>
        ${h.error ? `<pre style="white-space:pre-wrap">${esc(h.error)}</pre><p>${/heading|sections/i.test(h.error) ? "Fix the response and resubmit" : "Review the unit log, fix the cause, and retry the turn"}</p>` : ""}
        ${h.log ? `<button class="btn" data-unit-log>Open unit log</button>` : ""}
        ${["prompt", "response"].map((kind) => `<section data-artifact="${kind}"><h4>${kind === "prompt" ? "Incoming prompt" : "Response"}</h4><pre style="white-space:pre-wrap"></pre><p data-notice></p><button class="btn" data-more>Load more</button><button class="btn" data-copy>Copy</button></section>`).join("")}</details>`).join("") || "No turns have started.";
      list.querySelectorAll("details").forEach((details) => {
        const h = handoffs[Number(details.dataset.turn)];
        details.querySelector("[data-unit-log]")?.addEventListener("click", () => dockLog(ctx, project, h.log));
        details.querySelectorAll("[data-artifact]").forEach((section) => {
          let offset = 0, busy = false, started = false;
          const pre = section.querySelector("pre"), more = section.querySelector("[data-more]"), notice = section.querySelector("[data-notice]");
          const page = async () => {
            if (busy) return;
            busy = true; more.disabled = true;
            try {
              const result = await server.handoffArtifact(project, id, h.id, section.dataset.artifact, offset);
              pre.textContent += result.content;
              offset += result.returned_bytes;
              notice.textContent = result.truncated ? `Showing ${(offset / 1024).toFixed(1)} of ${(result.total_bytes / 1024).toFixed(1)} KiB — Load more` : "";
              more.hidden = !result.truncated; started = true;
            } catch (error) { notice.textContent = error.message; }
            finally { busy = false; more.disabled = false; }
          };
          more.addEventListener("click", page);
          section.querySelector("[data-copy]").addEventListener("click", async () => {
            try {
              let copied = "", next = 0;
              while (true) {
                const result = await server.handoffArtifact(project, id, h.id, section.dataset.artifact, next);
                copied += result.content;
                next += result.returned_bytes;
                if (!result.truncated) break;
              }
              await navigator.clipboard.writeText(copied); ctx.toast("Copied", "ok");
            } catch (error) { message.textContent = error.message; }
          });
          details.addEventListener("toggle", () => { if (details.open && !started) page(); });
        });
      });
    };
    renderTurns();
    const load = async () => {
      const current = ++generation;
      try { const result = await server.handoff(project, select.value); if (current === generation) { loaded = result; text.value = result.text; } }
      catch (error) { message.textContent = error.message; }
    };
    select.addEventListener("change", () => {
      if (hasDirtyHandoff(loaded, text.value) && !confirm("Discard unsaved next-step edits?")) { select.value = loaded.file; return; }
      load();
    });
    root.querySelector("#handoff-refresh").addEventListener("click", async () => {
      if (hasDirtyHandoff(loaded, text.value) && !confirm("Discard unsaved next-step edits and refresh?")) return;
      try {
        ({ handoffs } = await server.handoffs(project, id));
        active = handoffs.find((h) => h.manual && h.status === "running");
        root.querySelector("#manual-section").hidden = !active;
        root.querySelector("#manual-submit").disabled = !canWrite;
        renderTurns();
        await load();
      } catch (error) { message.textContent = error.message; }
    });
    if (files.length) load();
    root.querySelector("#handoff-save").addEventListener("click", async () => {
      if (!loaded || loaded.file !== select.value) return;
      try { loaded = await server.handoffSave(project, { file: loaded.file, text: text.value, base_hash: loaded.hash }); message.textContent = "Saved"; }
      catch (error) { message.textContent = error.status === 409 ? "The file changed. Reopen Handoffs to review the current version before saving." : error.message; }
    });
    root.querySelector("#manual-submit")?.addEventListener("click", async (event) => {
      if (!active) return;
      event.target.disabled = true;
      try { await server.planResponse(project, id, { unit: active.id, text: root.querySelector("#manual-response").value }); message.textContent = "Response submitted"; }
      catch (error) { message.textContent = error.message; event.target.disabled = false; }
    });
  });
}
