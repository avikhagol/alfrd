import { agentRows, reviewRows, applyAgentSettings, replaceSection, personaRows, turnRoleRows, applyPersonaSettings, nextTaskName, turnSettingRows, applyTurnSettings } from "../data/agent_settings.js";
import { parseYaml, dumpYaml } from "../utils/yaml_parser.js";
import { DEFAULT_ITERATIONS, MAX_TURNS, loadTemplate, isSequence, sequenceAgents } from "../data/defs.js";
import { loopTurnCount, hasDirtyHandoff } from "./loop_helpers.js";
import { dockLog } from "./logview.js";
import { activePlan } from "./plans.js";
import { server } from "../data/server.js";
import { esc, icon, storage, copyText, loadCss } from "../utils/dom.js";

export async function openCreateProject(ctx, onCreated) {
  const { templates } = await server.projectTemplates();
  ctx.modal(`<header class="modal-h"><h2>New project</h2><button class="btn" data-close>Close</button></header>
    <form id="create-project"><div class="modal-b">
    <label class="field"><span>Folder</span><div class="row gap"><input class="input grow" name="path" required placeholder="/path/to/project"><button type="button" class="btn" id="create-browse">Browse…</button></div></label><div id="create-folders" hidden></div>
    <label class="field"><span>Name</span><input class="input" name="name" placeholder="Folder name"></label>
    <label class="field"><span>Template</span><select class="input" name="template">${templates.map((t) => `<option value="${esc(t.name)}" ${t.name === "basic" ? "selected" : ""}>${esc(t.name)} — ${esc(t.description)}</option>`).join("")}</select></label>
    <label class="field" data-loop-field><span>Initial task (for agent workflows)</span><textarea class="input" name="task" rows="5"></textarea></label>
    <label class="field" data-loop-field><span>Agent sequence (one pass, repeats)</span><input class="input" name="sequence" placeholder="claude, codex" value="claude, codex"></label>
    <label class="field" data-loop-field><span>Iterations (total turns; the project maximum)</span><input class="input" name="iterations" type="number" min="1" max="${MAX_TURNS}" value="${DEFAULT_ITERATIONS}"></label>
    <p id="loop-preview" data-loop-field class="muted small"></p><p class="muted small">Each iteration is one agent turn; the sequence repeats until the total is reached. Start runs in Workflow.</p>
    <p id="create-error" class="callout fail" role="alert" hidden></p></div><footer class="modal-f"><button class="btn primary" type="submit">Create project</button></footer></form>`, (root, close) => {
    let browser;
    root.querySelector("#create-browse").onclick = () => { browser?.destroy(); browser = ctx.browseFolder(root.querySelector("#create-folders"), root.querySelector('[name="path"]')); };
    root.addEventListener("beforeclose", () => browser?.destroy());
    const template = root.querySelector('[name="template"]');
    const iterations = root.querySelector('[name="iterations"]');
    const sequence = root.querySelector('[name="sequence"]');
    const agentsOf = () => sequence.value.split(/[\s,→>]+/).map((a) => a.trim()).filter(Boolean);
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
          const workflow = info?.workflows?.[0];
          if (isSequence(workflow)) {
            const agents = sequenceAgents({ sequence: agentsOf(), iterations: Number(iterations.value) }).slice(0, -1);
            const shown = agents.slice(0, 12).join(" → ") + (agents.length > 12 ? " → …" : "");
            root.querySelector("#loop-preview").textContent = `${agents.length} turns: ${shown}`;
          } else {
            const count = workflow?.steps?.length || 0;
            root.querySelector("#loop-preview").textContent = `${iterations.value} iterations × ${count} agents = ${loopTurnCount(Number(iterations.value), count)} turns`;
          }
        } catch (error) { root.querySelector("#loop-preview").textContent = error?.message || "Unable to load workflow preview"; }
      }
    };
    template.addEventListener("change", preview);
    iterations.addEventListener("input", preview);
    sequence.addEventListener("input", preview);
    preview();
    root.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = root.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        const payload = Object.fromEntries(new FormData(event.target));
        if (payload.iterations !== undefined) payload.iterations = Number(payload.iterations);
        if (payload.sequence !== undefined) payload.sequence = agentsOf();
        if (payload.template !== "agent-loop") delete payload.sequence;
        const project = await server.createProject(payload);
        close();
        await onCreated(project, payload.template);
        ctx.toast(payload.template === "agent-loop" ? "Project created · start a run in Workflow" : "Project created · follow Get started on Overview", "ok");
      } catch (error) { const alert = root.querySelector("#create-error"); alert.hidden = false; alert.textContent = error.message; }
      finally { button.disabled = false; }
    });
  });
}

export async function openHandoffs(ctx, project, id, { unit = null } = {}) {
  loadCss("css/lazy.css");
  let { handoffs } = await server.handoffs(project, id);
  const target = handoffs[0]?.target || storage.get(`task:selected:${project}`, null) || "task";
  const info = await server.executionInfo(project, target);
  const files = [...new Set(info.steps.flatMap((s) => Object.values(s.handoff || {})).map((f) => f.replaceAll("{target}", target)))];
  let active = handoffs.find((h) => h.phase === "awaiting_response" && !h.response_bytes);
  let review = handoffs.find((h) => h.review_status === "pending" && h.status === "running");
  const canWrite = Boolean(server.session?.mutations_enabled);
  ctx.modal(`<header class="modal-h"><h2>Agent handoffs</h2><button class="btn" id="handoff-refresh">Refresh</button><button class="btn" data-close>Close</button></header>
    <div class="modal-b">
    <section id="review-section" ${review ? "" : "hidden"}><h3>Human adjustment</h3><p>Review the response, then approve to continue.</p><label for="review-response">Response for review</label><textarea class="input" id="review-response" rows="14" disabled></textarea><p id="review-error" role="alert"></p><button class="btn primary" id="review-approve" disabled>Approve and continue</button>
    <label class="field"><span>Reason for rejecting (optional)</span><input class="input" id="review-reason" maxlength="2000"></label><button class="btn" id="review-reject" disabled>Reject and stop</button></section>
    <details id="plan-turns-box"><summary>Turns of this run: review, human turn, delay</summary><p class="muted small">Changes apply to this run only. Review can still be added to the running turn; other settings only to turns that have not started.</p><div id="plan-turns">Loading…</div></details>
    <div id="handoff-turns"></div>
    <section id="manual-section" ${active ? "" : "hidden"}><h3>Submit a chat response</h3><p>Copy the prompt into chat; paste the full Markdown response.</p><button class="btn" id="manual-copy">Copy prompt</button><label for="manual-response">Manual response</label><textarea class="input" id="manual-response" rows="8"></textarea><p id="manual-error" role="alert"></p><button class="btn" id="manual-submit" ${canWrite ? "" : "disabled"}>Submit response</button></section>
    <h3>Edit a next step</h3><p class="muted small">Pause before editing the next prompt; active input is frozen.</p>
    <label for="handoff-file">Next-step file</label><select class="input" id="handoff-file">${files.map((f) => `<option>${esc(f)}</option>`).join("")}</select>
    <label for="handoff-text">Next-step text</label><textarea class="input" id="handoff-text" rows="8"></textarea>
    <button class="btn" id="handoff-save" ${canWrite ? "" : "disabled"}>Save next step</button><p id="handoff-message" role="status"></p>
    </div>`, (root) => {
    const select = root.querySelector("#handoff-file"), text = root.querySelector("#handoff-text"), message = root.querySelector("#handoff-message");
    let loaded = null, generation = 0;
    let reviewHash = null, reviewOriginal = "";
    const fullArtifact = async (unit, kind) => {
      let result = "", offset = 0;
      do {
        const page = await server.handoffArtifact(project, id, unit.id, kind, offset);
        result += page.content; offset += page.returned_bytes;
        if (!page.truncated) return result;
        if (!page.returned_bytes) throw new Error("Unable to advance artifact page");
      } while (true);
    };
    const loadReview = async () => {
      const field = root.querySelector("#review-response"), button = root.querySelector("#review-approve");
      root.querySelector("#review-section").hidden = !review;
      reviewHash = null; button.disabled = true; root.querySelector("#review-reject").disabled = true;
      if (!review) { field.value = ""; reviewOriginal = ""; return; }
      try {
        const target = review;
        const content = await fullArtifact(target, "response");
        const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
        if (review !== target) return;
        reviewHash = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
        field.value = content; reviewOriginal = content; field.disabled = !canWrite; button.disabled = !canWrite;
        root.querySelector("#review-reject").disabled = !canWrite || review.review_gate === "shim";
      } catch (error) { root.querySelector("#review-error").textContent = error.message; }
    };
    const dirty = () => hasDirtyHandoff(loaded, text.value) || root.querySelector("#manual-response").value.trim() || root.querySelector("#review-response").value !== reviewOriginal;
    root.querySelector("#manual-copy").addEventListener("click", async () => {
      try { if (active) await navigator.clipboard.writeText(await fullArtifact(active, "prompt")); message.textContent = "Prompt copied"; }
      catch (error) { root.querySelector("#manual-error").textContent = error.message; }
    });
    root.addEventListener("beforeclose", (event) => {
      if (dirty() && !confirm("Discard unsaved handoff edits?")) event.preventDefault();
    });
    const loadPlanTurns = async () => {
      const box = root.querySelector("#plan-turns");
      try {
        const { turns } = await server.planTurns(project, id);
        const can = (t, key) => canWrite && t.editable.includes(key);
        box.innerHTML = `<table class="turn-table"><thead><tr><th>Turn</th><th>Agent</th><th>Review before handoff</th><th>Human writes</th><th>Start after</th><th>State</th></tr></thead><tbody>${turns.map((t) => `<tr data-step="${esc(t.step)}">
          <td class="mono">${esc(t.step)}</td><td>${esc(t.agent || "")}</td>
          <td><input type="checkbox" data-set="human_review" aria-label="Review ${esc(t.step)}" ${t.human_review ? "checked" : ""} ${can(t, "human_review") ? "" : "disabled"}></td>
          <td><input type="checkbox" data-set="manual" aria-label="Human writes ${esc(t.step)}" ${t.manual ? "checked" : ""} ${can(t, "manual") ? "" : "disabled"}></td>
          <td><input type="text" class="input" data-set="after" aria-label="Delay before ${esc(t.step)}" placeholder="${can(t, "after") ? "+1h" : ""}" value="${t.after ? esc(t.overrides.after ?? `${Math.round(t.after / 60)}m`) : ""}" ${can(t, "after") ? "" : "disabled"}>${t.after && can(t, "after") ? ` <button class="btn sm" data-run-now>Run now</button>` : ""}</td>
          <td>${esc(t.state)}</td></tr>`).join("")}</tbody></table><p id="plan-turns-error" role="alert"></p>`;
        box.querySelectorAll("tr[data-step]").forEach((row) => {
          const send = async (payload) => {
            try { await server.planTurnSet(project, id, row.dataset.step, payload); await loadPlanTurns(); }
            catch (error) { box.querySelector("#plan-turns-error").textContent = error.message; }
          };
          row.querySelectorAll("input[type=checkbox]").forEach((input) => input.addEventListener("change", () => send({ [input.dataset.set]: input.checked })));
          row.querySelector('[data-set="after"]')?.addEventListener("change", (event) => send({ after: event.target.value.trim() || null }));
          row.querySelector("[data-run-now]")?.addEventListener("click", () => send({ after: 0 }));
        });
      } catch (error) { box.textContent = error.message; }
    };
    root.querySelector("#plan-turns-box").addEventListener("toggle", (event) => { if (event.target.open) loadPlanTurns(); });
    root.querySelector("#review-reject").addEventListener("click", async (event) => {
      if (!review || !confirm("Reject this response? Nothing is published and the run stops.")) return;
      event.target.disabled = true;
      try { await server.planReject(project, id, { unit: review.id, reason: root.querySelector("#review-reason").value }); ctx.toast("Response rejected · the run stops", "ok"); root.querySelector("#handoff-refresh").click(); }
      catch (error) { root.querySelector("#review-error").textContent = error.message; event.target.disabled = false; }
    });
    const renderTurns = () => {
      const list = root.querySelector("#handoff-turns");
      list.innerHTML = handoffs.map((h, index) => `<details data-turn="${index}"><summary>${/^t\d{3}-/.test(h.steps?.[0] || "") ? "Turn" : "Iteration"} ${esc(h.iteration_label)} · ${esc(h.agent)} · ${esc(h.phase)} · Model: ${esc(h.model || (h.requested_model ? `${h.requested_model} (requested)` : "not reported"))} · ${h.prompt_bytes} / ${h.response_bytes} bytes</summary>
        ${h.error ? `<pre class="handoff-artifact">${esc(h.error)}</pre><p>${/heading|sections/i.test(h.error) ? "Fix the response and resubmit" : "Review the unit log, fix the cause, and retry the turn"}</p>` : ""}
        ${h.log ? `<button class="btn" data-unit-log>Open unit log</button>` : ""}
        ${["prompt", "response"].map((kind) => `<section data-artifact="${kind}"><h4>${kind === "prompt" ? "Incoming prompt" : "Response"}</h4><pre class="handoff-artifact"></pre><p data-notice></p><button class="btn" data-more>Load more</button><button class="btn" data-copy>Copy</button></section>`).join("")}</details>`).join("") || "No turns have started.";
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
              await navigator.clipboard.writeText(await fullArtifact(h, section.dataset.artifact)); ctx.toast("Copied", "ok");
            } catch (error) { message.textContent = error.message; }
          });
          details.addEventListener("toggle", () => { if (details.open && !started) page(); });
        });
        if (h.id === unit) {
          details.open = true;
          requestAnimationFrame(() => details.scrollIntoView({ block: "start" }));
        }
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
      if (dirty() && !confirm("Discard unsaved handoff edits and refresh?")) return;
      try {
        ({ handoffs } = await server.handoffs(project, id));
        active = handoffs.find((h) => h.phase === "awaiting_response" && !h.response_bytes);
        review = handoffs.find((h) => h.phase === "awaiting_review");
        root.querySelector("#manual-response").value = "";
        await loadReview();
        root.querySelector("#manual-section").hidden = !active;
        root.querySelector("#manual-submit").disabled = !canWrite;
        renderTurns();
        await load();
      } catch (error) { message.textContent = error.message; }
    });
    if (files.length) load();
    loadReview();
    root.querySelector("#handoff-save").addEventListener("click", async () => {
      if (!loaded || loaded.file !== select.value) return;
      try { loaded = await server.handoffSave(project, { file: loaded.file, text: text.value, base_hash: loaded.hash }); message.textContent = "Saved"; }
      catch (error) { message.textContent = error.status === 409 ? "The file changed. Reopen Handoffs to review the current version before saving." : error.message; }
    });
    root.querySelector("#manual-submit")?.addEventListener("click", async (event) => {
      if (!active) return;
      event.target.disabled = true;
      try { await server.planResponse(project, id, { unit: active.id, text: root.querySelector("#manual-response").value }); root.querySelector("#manual-response").value = ""; message.textContent = "Response submitted"; }
      catch (error) { root.querySelector("#manual-error").textContent = error.message; event.target.disabled = false; }
    });
    root.querySelector("#review-approve")?.addEventListener("click", async (event) => {
      if (!review || !reviewHash) return;
      event.target.disabled = true;
      try { await server.planReview(project, id, { unit: review.id, text: root.querySelector("#review-response").value, base_hash: reviewHash }); reviewOriginal = root.querySelector("#review-response").value; message.textContent = "Approved. The next agent will receive your adjusted response."; ctx.update(); }
      catch (error) { root.querySelector("#review-error").textContent = error.message; event.target.disabled = false; }
    });
  });
}

export async function openAgentSettings(ctx, project, text, apply) {
  loadCss("css/lazy.css");
  const data = parseYaml(text), agents = agentRows(data), reviews = reviewRows(data);
  let turnRows = [];
  try { turnRows = turnSettingRows(data); } catch { /* invalid workflow: edit it in the YAML */ }
  let personas = personaRows(data);
  const turns = turnRoleRows(data).map((turn) => ({ ...turn, rows: turn.keys.map((key) => personas.findIndex((p) => p.key === key)).filter((i) => i >= 0) }));
  const access = data.project_settings?.agent_access || {};
  const enabled = Boolean(data.project_settings?.human_review) || reviews.some((r) => r.enabled);
  ctx.modal(`<header class="modal-h"><h2>Agents &amp; review</h2><button class="btn" data-close>Close</button></header>
    <form><div class="modal-b"><p class="muted small">Changes apply to new runs. Blank models use CLI defaults.</p>
    ${agents.map((a, i) => `<fieldset><legend>${esc(a.name)} <span class="muted small">(${esc(a.adapter)})</span></legend><label class="field"><span>Model name or alias</span><input class="input" data-model="${i}" value="${esc(a.model)}" placeholder="CLI default"></label><label class="field"><span>Fallback models, in order (comma-separated)</span><input class="input" data-fallback="${i}" value="${esc(a.fallback_models.join(", "))}" placeholder="none"></label><label><input type="checkbox" data-manual="${i}" ${a.manual ? "checked" : ""}> Run every turn of this agent through chat</label></fieldset>`).join("") || "No agent CLI entrypoints configured."}
    <h3>Personalities</h3>
    <div id="personas"></div><button class="btn" type="button" id="persona-add">Add personality</button>
    <h3>Turn roles</h3><div id="turn-roles"></div>
    <h3>Folders and shell commands</h3>
    <label class="field"><span>Additional folders (one per line)</span><textarea class="input" id="agent-folders" rows="4">${esc((access.folders || []).join("\n"))}</textarea></label>
    <p class="muted small">Paths are relative to the project. Claude gets tool access; Codex gets writable folders under workspace-write.</p>
    <label class="field"><span>Claude Bash commands to approve (one pattern per line)</span><textarea class="input" id="agent-bash" rows="3" placeholder="git status *">${esc((access.bash_commands || []).join("\n"))}</textarea></label>
    <label class="field"><span>Codex shell and file permissions</span><select class="input" id="agent-sandbox"><option value="">Keep CLI settings</option><option value="read-only" ${access.sandbox === "read-only" ? "selected" : ""}>Read only</option><option value="workspace-write" ${access.sandbox === "workspace-write" ? "selected" : ""}>Write in workspace and added folders</option></select></label>
    <h3>Human in the loop</h3><label><input type="checkbox" id="review-enabled" ${enabled ? "checked" : ""}> Enable review</label>
    <p class="muted small">Approval releases the handoff to the next agent; code changes remain.</p>
    ${reviews.map((r, i) => `<label class="field"><span><input type="checkbox" data-review="${i}" ${r.enabled ? "checked" : ""} ${enabled ? "" : "disabled"}> Review after every ${esc(r.id)} turn</span></label>`).join("")}
    ${turnRows.length ? `<details><summary>Per-turn settings (${turnRows.length} turns)</summary><p class="muted small">Choose single turns a person writes or reviews, or delay. A running plan is changed under Handoffs → Turns of this run.</p>
    <table class="turn-table"><thead><tr><th>Turn</th><th>Agent</th><th>Human writes</th><th>Review</th><th>Start after</th></tr></thead><tbody>${turnRows.map((t, i) => `<tr><td class="mono">${esc(t.id)}</td><td>${esc(t.agent)}</td>
      <td><input type="checkbox" data-turn-manual="${i}" aria-label="Human writes ${esc(t.id)}" ${t.manual ? "checked" : ""}></td><td><input type="checkbox" data-turn-review="${i}" aria-label="Review ${esc(t.id)}" ${t.human_review ? "checked" : ""}></td>
      <td><input type="text" class="input" data-turn-after="${i}" aria-label="Delay before ${esc(t.id)}" placeholder="+1h" value="${esc(t.after)}"></td></tr>`).join("")}</tbody></table></details>` : ""}
    <p role="alert" id="agent-settings-error"></p></div><footer class="modal-f"><button class="btn primary" type="submit">Apply settings</button></footer></form>`, (root, close) => {
    const readPersonas = () => {
      personas = personas.map((p, i) => ({ ...p, key: root.querySelector(`[data-persona-key="${i}"]`).value,
        label: root.querySelector(`[data-persona-label="${i}"]`).value, instructions: root.querySelector(`[data-persona-instructions="${i}"]`).value }));
    };
    const readTurns = () => {
      turns.forEach((turn, i) => { turn.rows = [...root.querySelectorAll(`[data-turn="${i}"]:checked`)].map((el) => Number(el.dataset.persona)); });
    };
    const renderTurns = () => {
      root.querySelector("#turn-roles").innerHTML = turns.map((turn, i) => `<fieldset><legend>${turn.iteration}/${turn.iterations} · ${esc(turn.agent)}</legend>${personas.map((p, j) => `<label><input type="checkbox" data-turn="${i}" data-persona="${j}" ${turn.rows.includes(j) ? "checked" : ""}> ${esc(p.label || p.key || "New personality")}</label>`).join(" ")}</fieldset>`).join("");
    };
    const renderPersonas = () => {
      root.querySelector("#personas").innerHTML = personas.map((p, i) => `<fieldset><legend>Personality ${i + 1}</legend>
        <label class="field"><span>Key</span><input class="input" data-persona-key="${i}" value="${esc(p.key)}" maxlength="40" required pattern="[a-z0-9][a-z0-9_-]{0,39}"></label>
        <label class="field"><span>Label</span><input class="input" data-persona-label="${i}" value="${esc(p.label)}" maxlength="60" required></label>
        <label class="field"><span>Instructions</span><textarea class="input" data-persona-instructions="${i}" rows="4" maxlength="4000">${esc(p.instructions)}</textarea></label>
        <button class="btn" type="button" data-persona-remove="${i}">Remove personality</button></fieldset>`).join("");
      root.querySelectorAll("[data-persona-remove]").forEach((button) => button.addEventListener("click", () => {
        readTurns(); readPersonas(); const index = Number(button.dataset.personaRemove);
        personas.splice(index, 1); turns.forEach((turn) => { turn.rows = turn.rows.filter((i) => i !== index).map((i) => i > index ? i - 1 : i); });
        renderPersonas(); renderTurns();
      }));
      root.querySelectorAll("[data-persona-key], [data-persona-label]").forEach((input) => input.addEventListener("change", () => {
        readTurns(); readPersonas(); renderTurns();
      }));
    };
    renderPersonas(); renderTurns();
    root.querySelector("#persona-add").addEventListener("click", () => {
      readTurns(); readPersonas(); personas.push({ key: "", label: "", instructions: "" }); renderPersonas(); renderTurns();
    });
    root.querySelector("#review-enabled").addEventListener("change", (event) => {
      root.querySelectorAll("[data-review]").forEach((input) => { input.disabled = !event.target.checked; if (event.target.checked) input.checked = true; });
    });
    root.querySelector("form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = root.querySelector("button[type=submit]"); button.disabled = true;
      try {
        let next = applyAgentSettings(data, agents.map((a, i) => ({ ...a, model: root.querySelector(`[data-model="${i}"]`).value, manual: root.querySelector(`[data-manual="${i}"]`).checked,
          fallback_models: root.querySelector(`[data-fallback="${i}"]`).value.split(",").map((m) => m.trim()).filter(Boolean) })),
          root.querySelector("#review-enabled").checked, reviews.map((r, i) => ({ ...r, enabled: root.querySelector(`[data-review="${i}"]`).checked })));
        next = applyTurnSettings(next, turnRows.map((t, i) => {
          const row = { ...t, manual: root.querySelector(`[data-turn-manual="${i}"]`).checked, human_review: root.querySelector(`[data-turn-review="${i}"]`).checked,
            after: root.querySelector(`[data-turn-after="${i}"]`).value.trim() };
          return { ...row, changed: row.manual !== t.manual || row.human_review !== t.human_review || row.after !== String(t.after ?? "") };
        }));
        readTurns(); readPersonas();
        next = applyPersonaSettings(next, personas, turns.map((turn) => ({ keys: turn.rows.map((i) => personas[i].key) })));
        const lines = (selector) => root.querySelector(selector).value.split("\n").map((line) => line.trim()).filter(Boolean);
        const agentAccess = { folders: lines("#agent-folders"), bash_commands: lines("#agent-bash") };
        const sandbox = root.querySelector("#agent-sandbox").value;
        if (sandbox) agentAccess.sandbox = sandbox;
        if (JSON.stringify(agentAccess) !== JSON.stringify({ folders: access.folders || [], bash_commands: access.bash_commands || [], ...(access.sandbox ? { sandbox: access.sandbox } : {}) }))
          next.project_settings = { ...(next.project_settings || {}), agent_access: agentAccess };
        let updated = text;
        for (const key of ["entrypoint", "project_settings", "workflows"]) if (next[key] && JSON.stringify(next[key]) !== JSON.stringify(data[key])) updated = replaceSection(updated, key, dumpYaml({ [key]: next[key] }));
        await apply(updated); close();
      } catch (error) { root.querySelector("#agent-settings-error").textContent = error.message; }
      finally { button.disabled = false; }
    });
  });
}

// Local drafts retain their original file hash until saved or discarded.
const DRAFT_V = 1;
const drafts = new Map(); // Storage failure fallback
export function forgetTaskDrafts() { drafts.clear(); }
const draftPrefix = (project, target = null) => `draft:task:${location.origin}|${project}|${target ? `${target}|` : ""}`;
function readDraft(key) {
  const d = drafts.get(key) || storage.get(key, null);
  return d?.v === DRAFT_V && typeof d.text === "string" && typeof d.base_hash === "string" ? d : null;
}
function otherDrafts(project, run, target = null) {
  const prefix = draftPrefix(project, target);
  let stored = [];
  try { stored = Object.keys(localStorage).filter((k) => k.startsWith(`alfrd-studio:${prefix}`)).map((k) => k.slice(13)); } catch { /* storage unavailable */ }
  return [...new Set([...drafts.keys(), ...stored])].filter((k) => k.startsWith(prefix) && readDraft(k)).map((k) => k.slice(prefix.length)).filter((r) => r !== run);
}

export async function openTask(ctx, project, { run = null, target = null } = {}) {
  let tasks = [], folderLayout = true;
  try { const list = await server.tasks(project); tasks = list.tasks; folderLayout = list.folder_layout !== false; }
  catch (error) { if (error.status && error.status !== 404) throw error; }
  if (tasks.length) {
    const preferred = target || storage.get(`task:selected:${project}`, null) || ctx.target?.()?.name;
    target = tasks.some((t) => t.name === preferred) ? preferred : tasks[0].name;
    storage.set(`task:selected:${project}`, target);
  }
  run ||= activePlan(project, target)?.plan?.id || "next";
  const key = `${draftPrefix(project, target)}${run}`;
  let loaded = await server.task(project, target);
  const kept = readDraft(key);
  let base = kept ? { hash: kept.base_hash, text: kept.base_text ?? "" } : { hash: loaded.hash, text: loaded.text };
  const others = otherDrafts(project, run, target);
  const runName = (r) => (r === "next" ? "the next run" : `run ${r}`);
  loadCss("css/lazy.css");
  ctx.modal(`<header class="modal-h"><h2>Task</h2><button class="btn" data-close>Close</button></header><div class="modal-b">
    ${tasks.length ? `<div class="row gap wrap"><label class="field grow"><span>Task</span><select class="input" id="task-picker">${tasks.map((t) => `<option value="${esc(t.name)}" ${t.name === target ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></label><button class="btn" id="task-new" ${folderLayout ? "" : "hidden"} ${server.session?.mutations_enabled ? "" : "disabled"}>New task</button><button class="btn" id="task-rename" ${folderLayout ? "" : "hidden"} ${server.session?.mutations_enabled && !activePlan(project, target) ? "" : "disabled"} title="An active task cannot be renamed">Rename</button></div>
    <section id="task-name-panel" hidden><label class="field"><span>Task name</span><input class="input" id="task-name" maxlength="64" aria-describedby="task-name-hint"></label><p class="muted small" id="task-name-hint">1–64 letters, digits, . _ or -; no leading dot. Each task has its own folder.</p><label class="field" id="task-new-goal-field"><span>Goal for the new task</span><textarea class="input" id="task-new-goal" rows="5"></textarea></label><button class="btn primary" id="task-name-save">Create task</button> <button class="btn" id="task-name-cancel">Cancel</button></section>` : ""}
    <p>Edit ${esc(loaded.file || (folderLayout && target ? `${target}/task.md` : "task.md"))}. Include goal, constraints and acceptance criteria.</p>
    ${others.length ? `<p class="muted small">Unsaved draft also kept for ${others.map((r) => `<button type="button" class="link-btn" data-task-run="${esc(r)}">${esc(runName(r))}</button>`).join(", ")}.</p>` : ""}
    <div class="callout warn task-draft" id="task-draft" hidden>${icon("file")}<span class="grow"><b>Unsaved draft</b> <span id="task-draft-note">Kept in this browser. Save task to update task.md.</span></span>
      <span id="task-draft-acts"><button type="button" class="link-btn" id="task-copy" hidden>Copy text</button> <button type="button" class="link-btn" id="task-discard">Discard</button></span>
      <span id="task-draft-confirm" hidden>Discard this unsaved task draft? <button type="button" class="btn sm danger" id="task-discard-yes">Discard draft</button> <button type="button" class="btn sm" id="task-discard-no">Keep editing</button></span></div>
    <p class="sr-only" role="status" id="task-draft-status"></p>
    <textarea class="input" id="task-text" rows="16" aria-label="task.md">${esc(kept ? kept.text : loaded.text)}</textarea>
    <label><input type="checkbox" id="task-seed" ${(kept ? kept.seed : true) ? "checked" : ""}> Use this goal for the next run (replace the initial handoff)</label>
    <p class="muted small">Replacing the initial handoff requires finishing or cancelling this task’s run. Saving task.md leaves active input frozen.</p><p id="task-message" role="status"></p><p id="task-error" class="fail-t" role="alert"></p>
    <details id="task-current" hidden><summary>View current file</summary><pre class="code small"></pre></details>
    </div><footer class="modal-f row gap"><button class="btn primary" id="task-save" ${server.session?.mutations_enabled ? "" : "disabled"}>Save task</button><span class="muted small">Closing keeps your draft.</span></footer>`, (root, close) => {
    const $r = (s) => root.querySelector(s);
    const text = $r("#task-text"), seed = $r("#task-seed"), save = $r("#task-save");
    let nameAction = "new";
    const reopen = async (name) => { close(); await openTask(ctx, project, { target: name }); ctx.update?.(); };
    $r("#task-picker")?.addEventListener("change", async (event) => {
      try { await reopen(event.target.value); } catch (error) { ctx.toast(error.message, "fail"); }
    });
    const namePanel = (action) => {
      nameAction = action;
      $r("#task-name-panel").hidden = false;
      $r("#task-name").value = action === "new" ? nextTaskName(tasks) : target;
      $r("#task-new-goal-field").hidden = action !== "new";
      $r("#task-name-save").textContent = action === "new" ? "Create task" : "Rename task";
      $r("#task-name").focus();
    };
    $r("#task-new")?.addEventListener("click", () => namePanel("new"));
    $r("#task-rename")?.addEventListener("click", () => namePanel("rename"));
    $r("#task-name-cancel")?.addEventListener("click", () => { $r("#task-name-panel").hidden = true; $r("#task-picker").focus(); });
    $r("#task-name-save")?.addEventListener("click", async (event) => {
      const name = $r("#task-name").value.trim();
      if (!/^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$/.test(name)) { $r("#task-error").textContent = "Enter a valid task name."; return; }
      event.target.disabled = true;
      try {
        if (nameAction === "new") await server.taskCreate(project, { name, task: $r("#task-new-goal").value });
        else {
          await server.taskRename(project, target, name);
          if (text.value !== base.text) {
            const nextKey = `${draftPrefix(project, name)}${run}`;
            const kept = { ...draft(), target: name };
            drafts.set(nextKey, kept); storage.set(nextKey, kept);
            drafts.delete(key); storage.remove(key);
          }
        }
        await reopen(name);
      } catch (error) { $r("#task-error").textContent = error.message; }
      finally { event.target.disabled = false; }
    });
    let stored = true, timer = 0;
    const draft = () => ({ v: DRAFT_V, project, target, run, text: text.value, base_hash: base.hash, base_text: base.text, seed: seed.checked, updated: new Date().toISOString() });
    const persist = () => {
      clearTimeout(timer);
      const d = drafts.get(key);
      if (d) stored = storage.set(key, d); else storage.remove(key);
      $r("#task-draft-note").textContent = stored ? "Kept in this browser. Save task to update task.md."
        : "Draft kept for this tab only. Browser storage is unavailable; copy your text before closing this tab.";
      $r("#task-copy").hidden = stored;
    };
    const changed = () => {
      if (text.value === base.text) drafts.delete(key); else drafts.set(key, draft());
      $r("#task-draft").hidden = !drafts.has(key);
      clearTimeout(timer);
      timer = setTimeout(persist, 250);
    };
    text.addEventListener("input", changed);
    seed.addEventListener("change", changed);
    window.addEventListener("pagehide", persist);
    root.addEventListener("beforeclose", () => { persist(); window.removeEventListener("pagehide", persist); });
    queueMicrotask(() => text.focus());
    if (kept) {
      drafts.set(key, kept);
      $r("#task-draft").hidden = false;
      $r("#task-draft-status").textContent = `Unsaved draft restored for ${runName(run)}.`;
    }
    root.querySelectorAll("[data-task-run]").forEach((b) => b.addEventListener("click", () => { close(); openTask(ctx, project, { run: b.dataset.taskRun, target }).catch((e) => ctx.toast(e.message, "fail")); }));
    $r("#task-copy").addEventListener("click", async () => ctx.toast((await copyText(text.value)) ? "Copied" : "Copy failed", "ok"));
    const confirming = (on) => { $r("#task-draft-acts").hidden = on; $r("#task-draft-confirm").hidden = !on; (on ? $r("#task-discard-no") : $r("#task-discard")).focus(); };
    $r("#task-discard").addEventListener("click", () => confirming(true));
    $r("#task-discard-no").addEventListener("click", () => { confirming(false); text.focus(); });
    $r("#task-discard-yes").addEventListener("click", async () => {
      try { loaded = await server.task(project, target); } catch (error) { $r("#task-error").textContent = error.message; confirming(false); return; }
      base = { hash: loaded.hash, text: loaded.text };
      text.value = loaded.text;
      seed.checked = true;
      drafts.delete(key);
      persist();
      confirming(false);
      $r("#task-draft").hidden = true;
      $r("#task-current").hidden = true;
      $r("#task-error").textContent = "";
      text.focus();
    });
    save.addEventListener("click", async () => {
      const submitted = text.value;
      save.disabled = true; save.textContent = "Saving…"; text.readOnly = true; seed.disabled = true;
      $r("#task-error").textContent = ""; $r("#task-message").textContent = "";
      try {
        loaded = await server.taskSave(project, { text: submitted, base_hash: base.hash, use_for_next_run: seed.checked }, target);
        base = { hash: loaded.hash, text: loaded.text };
        drafts.delete(key);
        persist();
        $r("#task-draft").hidden = true;
        $r("#task-current").hidden = true;
        $r("#task-message").textContent = "Saved. Start a new run for this task.";
      } catch (error) {
        persist();
        const conflict = error.status === 409 && typeof error.body?.current === "string";
        $r("#task-error").textContent = conflict ? "task.md changed since this draft started. Your draft is kept. Review the current file before saving." : error.message;
        if (conflict) { $r("#task-current pre").textContent = error.body.current; $r("#task-current").hidden = false; }
      } finally { save.disabled = false; save.textContent = "Save task"; text.readOnly = false; seed.disabled = false; }
    });
  });
}
