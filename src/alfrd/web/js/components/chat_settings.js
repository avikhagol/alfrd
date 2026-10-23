// Settings → Agent chat: the `chat:` block of the active alfrd.yaml (agents and their role presets →
// entrypoint / model / adapter / persona / lean startup, the orchestrator and its models, bounds, final
// summary, task cost warning, fallback agent; presets from the server's catalogue). Edits the Settings
// draft; "Apply and save" saves it through Settings' save path (the active file, with the conflict guard).
import { $, $$, esc, icon } from "../utils/dom.js";
import { server } from "../data/server.js";
import { parseYaml, dumpYaml } from "../utils/yaml_parser.js";
import { replaceSection } from "../data/agent_settings.js";
import { DEFAULTS, chatBlock, chatProblems, mergeChat, freeKey, AGENT_KEY } from "../data/chat.js";

const ADAPTERS = ["", "claude", "codex", "generic"];

/** Open the editor for draft `text`; `apply(text, save)` writes the new text into the Settings draft (and saves). */
export function openChatSettings(ctx, project, text, apply) {
  let data;
  try { data = parseYaml(text) || {}; } catch (error) { ctx.toast(`Fix the YAML first: ${error.message}`, "fail"); return; }
  let s = mergeChat(data.chat || {});
  const personas = Object.keys(data.project_settings?.personas || {});
  const entrypoints = (data.entrypoint || []).map((e) => e?.name).filter(Boolean);
  let catalogue = {}; // GET chat/settings → presets (Ollama, Claude on Ollama, Hugging Face, OpenAI-compatible)
  const num = (name, label, value, attrs = "") => `<label class="field small"><span>${label}</span><input class="input sm" type="number" data-s="${name}" value="${esc(value)}" ${attrs}></label>`;
  const row = (key, a) => `<tr data-agent="${esc(key)}">
    <th scope="row"><span class="mono">@${esc(key)}</span></th>
    <td><input class="input sm" data-a="label" value="${esc(a.label || "")}" aria-label="Label of @${esc(key)}"></td>
    <td><input class="input sm mono" data-a="entrypoint" value="${esc(a.entrypoint || "")}" list="cs-entrypoints" placeholder="${esc(key)}" aria-label="Entrypoint of @${esc(key)}" ${a.cmd ? `disabled title="Uses its own cmd from alfrd.yaml"` : ""}></td>
    <td><input class="input sm" data-a="model" value="${esc(a.model || "")}" placeholder="CLI default" aria-label="Model of @${esc(key)}"></td>
    <td><select class="input sm" data-a="adapter" aria-label="Adapter of @${esc(key)}">${ADAPTERS.map((x) => `<option value="${x}" ${x === (a.adapter || "") ? "selected" : ""}>${x || "auto"}</option>`).join("")}</select></td>
    <td><select class="input sm" data-a="persona" aria-label="Persona of @${esc(key)}"><option value="">none</option>${personas.map((p) => `<option ${p === a.persona ? "selected" : ""}>${esc(p)}</option>`).join("")}</select></td>
    <td><select class="input sm" data-a="fallback" aria-label="Fallback agent of @${esc(key)} (when it hits a usage limit)"><option value="">none</option>${Object.keys(s.agents).filter((k) => k !== key).map((k) => `<option ${k === a.fallback ? "selected" : ""}>${esc(k)}</option>`).join("")}</select></td>
    <td><input class="input sm" data-a="roles" value="${esc((a.roles || []).join(", "))}" placeholder="design, development" aria-label="Roles of @${esc(key)} (comma separated)"></td>
    <td><input type="checkbox" data-a="lean" ${a.lean ? "checked" : ""} aria-label="Lean startup for @${esc(key)} (Claude: no MCP servers, user plugins or skills)" title="Claude: start without MCP servers, user plugins or skills"></td>
    <td><button type="button" class="icon-btn xs" data-rm="${esc(key)}" aria-label="Remove @${esc(key)}">${icon("trash")}</button></td></tr>`;
  ctx.modal(`<header class="modal-h"><h2>Agent chat</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b cs">
      <p class="muted small">Who does what in the project chat: roles are words the orchestrator reads when it assigns tasks, not capabilities. Writes the <code>chat:</code> block of this project's alfrd.yaml.</p>
      <datalist id="cs-entrypoints">${[...new Set([...entrypoints, "claude", "codex", "agy"])].map((e) => `<option value="${esc(e)}">`).join("")}</datalist>
      <div class="row gap wrap">
        <label class="field small"><span>Orchestrator</span><select class="input sm" data-s="orchestrator" id="cs-orch"></select></label>
        <label class="field small"><span>Orchestrator model</span><input class="input sm" data-s="orchestrator_model" value="${esc(s.orchestrator_model || "")}" placeholder="agent's own model" title="Plans (and the summary call)"></label>
        <label class="field small"><span>Escalation model</span><input class="input sm" data-s="escalation_model" value="${esc(s.escalation_model || "")}" placeholder="agent's own model" title="Repairs an invalid plan and decides blockers (replans)"></label>
        <label class="row gap small"><input type="checkbox" data-s="final_summary" ${s.final_summary ? "checked" : ""}> Final summary call per request (off: ALFRD writes it from the task results)</label>
        ${num("warn_task_cost_usd", "Warn when a task costs more than (USD)", s.warn_task_cost_usd ?? "", 'min="0" step="0.1" placeholder="no warning"')}
        ${num("concurrency", "Tasks at once", s.concurrency, 'min="1"')}${num("history_messages", "Recent messages in planning", s.history_messages, 'min="0"')}
      </div>
      <fieldset><legend>Defaults per request (bounds)</legend><div class="row gap wrap">
        ${num("bounds.max_depth", "Delegation depth", s.bounds.max_depth, 'min="1"')}${num("bounds.max_retries", "Retries per task", s.bounds.max_retries, 'min="0" max="5"')}
        ${num("bounds.max_cost_usd", "Cost limit (USD)", s.bounds.max_cost_usd, 'min="0" step="0.5"')}${num("bounds.max_seconds", "Time limit (s)", s.bounds.max_seconds, 'min="1"')}
        ${num("bounds.max_tasks", "Tasks per request", s.bounds.max_tasks, 'min="1"')}${num("bounds.max_replans", "Replans per request", s.bounds.max_replans ?? 3, 'min="0" title="Times the orchestrator is woken for blockers; past it, you decide"')}</div></fieldset>
      <div class="cs-table"><table class="tbl small"><caption class="sr-only">Chat agents</caption><thead><tr><th scope="col">Agent</th><th scope="col">Label</th><th scope="col">Entrypoint</th><th scope="col">Model</th><th scope="col">Adapter</th><th scope="col">Persona</th><th scope="col" title="Takes over when this agent reports a usage limit or a missing model">Fallback</th><th scope="col">Roles</th><th scope="col" title="Claude: start without MCP servers, user plugins or skills">Lean</th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead><tbody id="cs-agents"></tbody></table></div>
      <div class="row gap wrap"><label class="sr-only" for="cs-new">New agent key</label><input class="input sm mono" id="cs-new" placeholder="new-agent" aria-describedby="cs-errors">
        <button type="button" class="btn sm" data-cs="add">${icon("plus")} Add agent</button>
        <select class="input sm" id="cs-preset" aria-label="Add a preset agent"></select></div>
      <div id="cs-errors" role="alert" class="fail-t small"></div>
    </div>
    <footer class="modal-f row gap"><span class="grow"></span><button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn" data-cs="apply">Apply to draft</button><button type="button" class="btn primary" data-cs="save">${icon("save")} Apply and save</button></footer>`, (root, close) => {
    const draw = () => {
      $("#cs-agents", root).innerHTML = Object.entries(s.agents).map(([k, a]) => row(k, a)).join("");
      $("#cs-orch", root).innerHTML = Object.keys(s.agents).map((k) => `<option ${k === s.orchestrator ? "selected" : ""}>${esc(k)}</option>`).join("");
      presets();
    };
    const presets = () => {
      const missing = Object.keys(DEFAULTS.agents).filter((k) => !(k in s.agents));
      const cat = Object.entries(catalogue);
      $("#cs-preset", root).hidden = !missing.length && !cat.length;
      $("#cs-preset", root).innerHTML = `<option value="">Add a preset…</option>${missing.map((k) => `<option value="${k}">${esc(DEFAULTS.agents[k].label)} (@${k})</option>`).join("")}${cat.length ? `<optgroup label="Catalogue">${cat.map(([k, x]) => `<option value="cat:${esc(k)}">${esc(x.label || k)} (@${esc(k)})</option>`).join("")}</optgroup>` : ""}`;
    };
    draw();
    server.request(`/api/studio/projects/${encodeURIComponent(project)}/chat/settings`, { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null)).then((body) => {
        const list = body?.presets;
        catalogue = Array.isArray(list) ? Object.fromEntries(list.map((x) => [x.key || x.id, x])) : list || {};
        if (root.isConnected && Object.keys(catalogue).length) presets();
      }).catch(() => {});
    root.addEventListener("input", (e) => {
      const t = e.target;
      if (t.dataset.s) {
        const v = t.type === "checkbox" ? t.checked : t.type === "number" ? (t.value === "" ? "" : Number(t.value)) : t.value;
        const [a, b] = t.dataset.s.split(".");
        if (b) s.bounds[b] = v; else s[a] = v;
      }
      const tr = t.closest("[data-agent]");
      if (tr && t.dataset.a) {
        const a = s.agents[tr.dataset.agent];
        a[t.dataset.a] = t.dataset.a === "roles" ? t.value.split(",").map((r) => r.trim()).filter(Boolean) : t.type === "checkbox" ? t.checked : t.value;
      }
    });
    root.addEventListener("change", (e) => {
      const v = e.target.value;
      if (e.target.id === "cs-preset" && v.startsWith("cat:")) { const k = v.slice(4); s.agents[freeKey(s.agents, k)] = { roles: [], ...JSON.parse(JSON.stringify(catalogue[k].agent || catalogue[k])) }; draw(); }
      else if (e.target.id === "cs-preset" && v) { s.agents[v] = JSON.parse(JSON.stringify(DEFAULTS.agents[v])); draw(); }
    });
    root.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.rm) { delete s.agents[b.dataset.rm]; for (const a of Object.values(s.agents)) if (a.fallback === b.dataset.rm) delete a.fallback; if (s.orchestrator === b.dataset.rm) s.orchestrator = Object.keys(s.agents)[0] || ""; draw(); }
      if (b.dataset.cs === "add") {
        const key = $("#cs-new", root).value.trim().toLowerCase();
        if (!AGENT_KEY.test(key) || key in s.agents) { $("#cs-errors", root).textContent = key in s.agents ? `@${key} exists already.` : "An agent key uses lowercase letters, digits, _ or - (up to 40)."; return; }
        s.agents[key] = { label: key, entrypoint: key, roles: [] }; $("#cs-new", root).value = ""; draw();
        $(`[data-agent="${key}"] input`, root)?.focus();
      }
      if (b.dataset.cs === "apply" || b.dataset.cs === "save") {
        const errors = chatProblems(s, personas);
        $("#cs-errors", root).innerHTML = errors.length ? `<ul>${errors.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : "";
        if (errors.length) return;
        const next = replaceSection(text, "chat", dumpYaml({ chat: chatBlock(s) }));
        close();
        apply(next, b.dataset.cs === "save");
      }
    });
    $$("input, select", root)[0]?.focus();
  }, "wide");
}
