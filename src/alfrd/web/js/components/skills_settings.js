// Shared draft controls. The enclosing dialog owns the single conflict-guarded save.
import { esc } from "../utils/dom.js";
import { server } from "../data/server.js";
import { render as renderSkills } from "./skills_explorer.js";

const keys = ["skills", "plugins", "mcp"];
const label = (key) => key === "mcp" ? "MCP" : key[0].toUpperCase() + key.slice(1);
export const previewHtml = (skill) => `<h4>${esc(skill.name)}</h4><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(skill.content)}</pre>`;
export function selectionSize(s, catalogue) {
  const ids = new Set(s.skill_selection?.selected_ids || []);
  return { count: ids.size, bytes: catalogue.filter((x) => ids.has(x.id)).reduce((n, x) => n + x.bytes, 0) };
}
export function skillsHtml(s, catalogue, agents, flags) {
  const flag = (key) => flags.find((f) => f.key === key);
  const unavailable = (key) => { const f = flag(key); return !f || f.disabled || f.locked; };
  const reason = (key) => unavailable(key) ? `<span class="small muted">${esc(flag(key)?.reason || "Setting unavailable")}</span>` : "";
  return `<fieldset><legend>Saved skills</legend>
    <p class="small muted">Activate Global or Project skills for Orchestrator and individual roles in Chat → ⋯ → Skills. Applies to new requests.</p>
    <button type="button" class="btn" data-open-skills>Open Skills</button></fieldset>
    <fieldset><legend>Native agent startup</legend><p class="small muted">Lean is a preset: Skills off, Plugins off, MCP none. Individual switches override the preset. Only supported adapters have an effect.</p>
    ${Object.entries(s.agents).map(([id, a]) => { const info = agents[id], supported = info?.adapter === "claude";
      return `<fieldset><legend>@${esc(id)} · ${esc(info?.adapter || "save to check adapter")}</legend>
      <button type="button" class="btn sm" data-lean-agent="${esc(id)}" ${!supported || keys.some((key) => unavailable(`chat.native_${key}`)) ? "disabled" : ""}>Apply Lean preset</button>
      ${keys.map((key) => { const value = a[key] ?? (a.lean ? (key === "mcp" ? "none" : false) : info?.native[key]); const checked = key === "mcp" ? value !== false && value !== "none" : value;
        return `<label class="row gap small"><input type="checkbox" data-native-agent="${esc(id)}" data-native-key="${key}" ${checked && supported ? "checked" : ""} ${!supported || unavailable(`chat.native_${key}`) ? "disabled" : ""}> ${label(key)}</label>${reason(`chat.native_${key}`)}`;
      }).join("")}
      ${Array.isArray(a.mcp) ? '<p class="small">MCP uses a configured list; toggling replaces it with default or none.</p>' : ""}
      <p class="small muted">${esc(info?.notes?.join(" ") || "Save and reopen to check this agent’s startup.")}</p>
      <p class="small">Effective args from saved settings (save and reopen to refresh):</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(info ? JSON.stringify(info.effective_args) : "Unavailable")}</pre>
      ${info?.error ? `<p role="alert">${esc(info.error)}</p>` : ""}</fieldset>`;
    }).join("")}</fieldset>`;
}

export function mountSkills(project, host, s, ctx) {
  let catalogue = [], agents = {}, flags = [], ready = false;
  const path = `/studio/projects/${encodeURIComponent(project)}/chat/skills`;
  const read = async (url) => {
    const res = await server.request(`/api${url}`, { headers: { Accept: "application/json" } });
    const body = await res.json();
    if (!res.ok) throw new Error(body?.error?.message || "Skills unavailable");
    return body;
  };
  const draw = () => {
    if (!ready) return;
    const active = document.activeElement;
    const focus = active && host.contains?.(active) ? { ...active.dataset } : null;
    host.innerHTML = skillsHtml(s, catalogue, agents, flags);
    if (focus) [...host.querySelectorAll("input,button")].find((el) => Object.keys(focus).every((key) => el.dataset[key] === focus[key]))?.focus();
  };
  host.textContent = "Loading available skills…";
  Promise.all([read(path), read(`/studio/ui-flags?project=${encodeURIComponent(project)}`)]).then(([body, registry]) => {
    catalogue = body.skills; agents = body.agents; flags = registry.flags; ready = true; draw();
  }).catch((error) => { host.textContent = error.message; });
  host.addEventListener("change", (e) => {
    const t = e.target;
    if (t.disabled) return;
    if (t.dataset.nativeAgent) s.agents[t.dataset.nativeAgent][t.dataset.nativeKey] = t.dataset.nativeKey === "mcp" ? (t.checked ? "default" : "none") : t.checked;
    else return;
    draw();
  });
  host.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t || t.disabled) return;
    if (t.hasAttribute("data-open-skills")) {
      if (!ctx?.modal) return;
      ctx.modal('<header class="modal-h"><h2>Skills Explorer</h2><button type="button" class="btn" data-close>Close</button></header><div class="modal-b" data-skills-body></div>', (root) => {
        renderSkills(root.querySelector("[data-skills-body]"), { state: { mode: "server", selectedProject: project } });
      });
    }
    if (t.dataset.leanAgent) { Object.assign(s.agents[t.dataset.leanAgent], { lean: true, skills: false, plugins: false, mcp: "none" }); draw(); }

  });
  return { refresh: draw, problem: () => null };
}
