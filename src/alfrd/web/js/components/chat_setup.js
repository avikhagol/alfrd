// /setup in the chat composer: who does what, with which model. The orchestrator and its models,
// which agent holds each role word, each agent's model, fallback agent (used when it hits a usage
// limit) and availability (GET chat/probe), and agents added from the presets catalogue (Ollama,
// Claude on Ollama, Hugging Face, any OpenAI-compatible endpoint). Saves the chat: block of the active
// alfrd.yaml (POST chat/settings, conflict-guarded by its hash). Lazy: loaded by /setup.
import { $, esc, icon, routeHash } from "../utils/dom.js";
import { server } from "../data/server.js";
import {
  AGENT_KEY, assignRole, backendOf, chatBlock, chatProblems, mergeChat, freeKey, modelHints, probeBadge, roleOwner, roleWords, setupSummary,
} from "../data/chat.js";

const base = (p) => `/studio/projects/${encodeURIComponent(p)}/chat`;
const read = (p, path) => server.request(`/api${base(p)}${path}`, { headers: { Accept: "application/json" } })
  .then(async (res) => { const body = await res.json().catch(() => null); if (!res.ok) throw new Error(body?.error?.message || `${res.status} ${res.statusText}`); return body; });

/** Open the dialog for `project`. o.onSaved(settings, summary) after a save. */
export async function openSetup(ctx, project, o = {}) {
  let loaded, probe = null;
  try { [loaded, probe] = await Promise.all([read(project, "/settings"), read(project, "/probe").catch(() => null)]); }
  catch (error) { ctx.toast(`Setup: ${error.message}`, "fail"); return; }
  // Edit the file's own chat: block (what a save replaces; keeps cmd/env), merged over the presets.
  const s = loaded.block && typeof loaded.block === "object" ? mergeChat(loaded.block) : JSON.parse(JSON.stringify(loaded));
  const presetList = (Array.isArray(loaded.presets) ? loaded.presets.map((x) => [x.key || x.id, x]) : Object.entries(loaded.presets || {}))
    .map(([k, x]) => [k, { ...(x.agent || x), description: x.description || "" }]);
  const suggested = Array.isArray(loaded.roles) ? loaded.roles : [];
  const opt = (v, label, on) => `<option value="${esc(v)}" ${on ? "selected" : ""}>${esc(label)}</option>`;
  const agentOpts = (sel, none) => (none ? opt("", none, !sel) : "") + Object.entries(s.agents).map(([k, a]) => opt(k, `@${k} · ${a.label || k}`, k === sel)).join("");

  const rolesHtml = () => `<div class="su-roles">
      <label class="field small"><span>Orchestrator (plans, repairs, decides)</span><select class="input sm" data-s="orchestrator">${agentOpts(s.orchestrator)}</select></label>
      <label class="field small"><span>Orchestrator model</span><input class="input sm" data-s="orchestrator_model" value="${esc(s.orchestrator_model || "")}" list="su-m-${esc(s.orchestrator)}" placeholder="agent's own model"></label>
      <label class="field small"><span>Escalation model (repairs, blockers)</span><input class="input sm" data-s="escalation_model" value="${esc(s.escalation_model || "")}" list="su-m-${esc(s.orchestrator)}" placeholder="agent's own model"></label>
      ${roleWords(s.agents, suggested).map((r, i) => `<label class="field small"><span>${esc(r[0].toUpperCase() + r.slice(1))}</span><select class="input sm" data-role="${esc(r)}" id="su-role-${i}">${agentOpts(roleOwner(s.agents, r), "unassigned")}</select></label>`).join("")}
    </div>`;
  const agentHtml = (k, a) => {
    const badge = probeBadge(k, a, probe), hints = modelHints(a, probe), id = `su-${k}`;
    return `<div class="su-agent" data-agent="${esc(k)}" role="group" aria-labelledby="${id}-k">
      <div class="su-key"><b class="mono" id="${id}-k">@${esc(k)}</b>${badge ? `<span class="badge tone-${badge.tone}" title="${esc(badge.detail)}">${icon(badge.tone === "ok" ? "checkCircle" : "alert")}<span>${esc(badge.label)}</span></span>` : ""}
        <span class="muted small">${esc((a.roles || []).join(", ") || "no roles")}</span></div>
      <label class="field small"><span>Label</span><input class="input sm" data-a="label" value="${esc(a.label || "")}"></label>
      <label class="field small"><span>Model</span><input class="input sm mono" data-a="model" value="${esc(a.model || "")}" list="su-m-${esc(k)}" placeholder="CLI default"></label>
      <datalist id="su-m-${esc(k)}">${hints.map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
      <label class="field small"><span>If unavailable, fall back to</span><select class="input sm" data-a="fallback">${opt("", "none", !a.fallback)}${Object.keys(s.agents).filter((x) => x !== k).map((x) => opt(x, `@${x}`, x === a.fallback)).join("")}</select></label>
      <button type="button" class="icon-btn sm" data-rm="${esc(k)}" aria-label="Remove @${esc(k)}">${icon("trash")}</button>
      ${a.cmd || a.env ? `<p class="su-cmd muted small mono" title="Edit in Settings → Agent chat / alfrd.yaml">${a.cmd ? esc([].concat(a.cmd).join(" ")) : ""}${a.env ? ` ${esc(Object.entries(a.env).map(([n, v]) => `${n}=${/TOKEN|KEY|SECRET/i.test(n) ? "…" : v}`).join(" "))}` : ""}</p>` : ""}
    </div>`;
  };
  const probeLine = () => !probe ? "Availability unknown (this server cannot check agents)."
    : `Ollama ${probe.ollama?.reachable ? `reachable at ${probe.ollama.url} · ${probe.ollama.models?.length || 0} model(s)` : `not reachable${probe.ollama?.url ? ` at ${probe.ollama.url}` : ""}`} · Hugging Face token ${probe.huggingface?.token ? "set" : "missing (HF_TOKEN)"}`;
  const b = s.bounds || {};

  ctx.modal(`<header class="modal-h"><h2 id="su-h">${icon("gear")} Chat setup</h2><span class="grow"></span><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b su">
      <p class="muted small"><b>Who does what, with which model.</b> Pick the orchestrator and the agent for each role; change a model when one hits its usage limit, or add a free local model (Ollama) or a Hugging Face one. A fallback takes over a task when its agent reports a usage limit or a missing model.</p>
      <h3>Roles</h3><div id="su-roles"></div>
      <h3>Agents</h3><p class="muted small" role="status">${esc(probeLine())}</p><div class="su-agents" id="su-agents"></div>
      ${presetList.length ? `<fieldset class="su-new"><legend>New agent</legend><div class="row gap wrap">
        <label class="field small"><span>Runs with</span><select class="input sm" id="su-new-kind">${presetList.map(([k, x], i) => opt(k, x.label || k, presetList.some(([p]) => p === "codex") ? k === "codex" : i === 0).replace("<option", `<option title="${esc(x.description)}"`)).join("")}</select></label>
        <label class="field small"><span>Name (@key)</span><input class="input sm mono" id="su-new-key" placeholder="luna" aria-describedby="su-note"></label>
        <label class="field small"><span>Model</span><input class="input sm mono" id="su-new-model" list="su-new-models" placeholder="CLI default"></label><datalist id="su-new-models"></datalist>
        <button type="button" class="btn sm" data-su="add">${icon("plus")} Add agent</button></div>
        <p class="muted small" id="su-new-desc"></p></fieldset>` : ""}
      <p id="su-note" class="muted small" role="status"></p>
      <h3>Bounds per request</h3><p class="small">${esc(`${b.max_tasks ?? "?"} tasks · depth ${b.max_depth ?? "?"} · ${b.max_retries ?? "?"} retries · $${b.max_cost_usd ?? "?"} · ${b.max_seconds ?? "?"} s · ${s.concurrency ?? "?"} at once`)}
        · <a href="${esc(routeHash("config", project, ctx.state.selectedTarget))}" data-close>Advanced: Settings → Agent chat</a></p>
      <div id="su-errors" role="alert" class="fail-t small"></div>
    </div>
    <footer class="modal-f row gap"><span class="grow"></span><button type="button" class="btn" data-close>Cancel</button><button type="button" class="btn primary" id="su-save">${icon("save")} Save</button></footer>`, (root, close) => {
    root.setAttribute("aria-labelledby", "su-h");
    const draw = () => {
      $("#su-roles", root).innerHTML = rolesHtml();
      $("#su-agents", root).innerHTML = Object.entries(s.agents).map(([k, a]) => agentHtml(k, a)).join("");
    };
    draw();
    root.addEventListener("input", (e) => {
      const t = e.target, card = t.closest("[data-agent]");
      if (t.dataset.s) s[t.dataset.s] = t.value;
      else if (card && t.dataset.a) s.agents[card.dataset.agent][t.dataset.a] = t.value || undefined;
    });
    root.addEventListener("change", (e) => {
      const t = e.target;
      if (t.dataset.role) { assignRole(s.agents, t.dataset.role, t.value); draw(); $(`[data-role="${CSS.escape(t.dataset.role)}"]`, root)?.focus(); }
      else if (t.dataset.s === "orchestrator") {
        s.orchestrator = t.value;
        if (!(s.agents[t.value].roles || []).includes("orchestration")) assignRole(s.agents, "orchestration", t.value);
        draw(); $('[data-s="orchestrator"]', root).focus();
      } else if (t.id === "su-new-kind") newKind();
    });
    const newKind = () => {
      const sel = $("#su-new-kind", root);
      if (!sel) return;
      const [, x] = presetList.find(([pk]) => pk === sel.value) || [];
      $("#su-new-models", root).innerHTML = modelHints(x || {}, probe).map((m) => `<option value="${esc(m)}">`).join("");
      $("#su-new-model", root).placeholder = x?.model || "CLI default";
      $("#su-new-desc", root).textContent = x?.description || "";
    };
    newKind();
    const addAgent = () => {
      const kind = $("#su-new-kind", root).value, [k, x] = presetList.find(([pk]) => pk === kind);
      const typed = $("#su-new-key", root).value.trim().toLowerCase();
      if (typed && (!AGENT_KEY.test(typed) || typed in s.agents)) {
        $("#su-note", root).textContent = typed in s.agents ? `@${typed} exists already.` : "A name uses lowercase letters, digits, _ or - (up to 40).";
        $("#su-new-key", root).focus(); return;
      }
      const key = typed || freeKey(s.agents, k);
      const { description, ...preset } = JSON.parse(JSON.stringify(x));
      const model = $("#su-new-model", root).value.trim();
      s.agents[key] = { ...preset, ...(model ? { model } : {}), ...(typed ? { label: `${preset.label || k} · ${model || typed}` } : {}), roles: [] };
      $("#su-new-key", root).value = $("#su-new-model", root).value = "";
      draw();
      $(`[data-agent="${CSS.escape(key)}"] input`, root)?.focus();
      $("#su-note", root).textContent = `Added @${key}${model ? ` (model ${model})` : ""}: ${description} Assign it a role above, or talk to it with @${key}.${backendOf(preset) === "hf" && probe && !probe.huggingface?.token ? " HF_TOKEN is missing in the server's environment." : ""}`;
    };
    root.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (btn?.dataset.rm) {
        const k = btn.dataset.rm;
        delete s.agents[k];
        for (const a of Object.values(s.agents)) if (a.fallback === k) delete a.fallback;
        if (s.orchestrator === k) s.orchestrator = Object.keys(s.agents)[0] || "";
        draw(); $("#su-agents button, #su-new-kind", root)?.focus();
      } else if (btn?.dataset.su === "add") addAgent();
      else if (btn?.id === "su-save") save(btn);
      else if (btn?.dataset.su === "reload") { close(); openSetup(ctx, project, o); }
    });
    async function save(btn) {
      const errs = $("#su-errors", root);
      const personas = Object.values(s.agents).map((a) => a.persona).filter(Boolean);
      const problems = chatProblems(s, personas);
      if (problems.length) { errs.innerHTML = `<ul>${problems.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`; return; }
      btn.disabled = true; errs.textContent = "";
      try {
        const out = await server.mutate(`${base(project)}/settings`, { chat: chatBlock(s), ...(loaded.hash ? { base_hash: loaded.hash } : {}) });
        close();
        ctx.toast("Chat setup saved", "ok");
        o.onSaved?.(out?.settings || s, setupSummary(s));
      } catch (error) {
        btn.disabled = false;
        if (error.status === 409) errs.innerHTML = `alfrd.yaml changed since you opened setup. <button type="button" class="btn sm" data-su="reload">${icon("sync")} Reload</button>`;
        else if (error.status === 404 || error.status === 405) errs.textContent = "This server cannot save chat settings yet: use Settings → Agent chat.";
        else errs.textContent = `Not saved: ${error.message}`;
      }
    }
    $('[data-s="orchestrator"]', root)?.focus();
  }, "wide");
}
