// Settings → Plugins → "Change where it applies…": the project types a plugin applies to, either for all
// projects (plugins.json) or for this project (its active alfrd.yaml, with the conflict guard). Loaded on first use.
import { esc } from "../utils/dom.js";
import { server } from "../data/server.js";

const ALL = "all";
const asList = (t) => t == null || t === ALL ? null : [].concat(t);

/** Form HTML for plugin p of the listed project. */
export function formHtml(list, p) {
  const rule = p.applicability || {};
  const id = `pa-${p.id.replace(/[^a-z0-9_-]/gi, "")}`;
  const project = rule.project || {};
  const hasProject = Boolean(rule.project);
  const scope = hasProject ? "project" : "global";
  const current = asList(scope === "project" && "project_types" in project ? project.project_types : rule.global ?? rule.default);
  const known = [...new Set([...(list.project_types_known || []), ...(current || [])])];
  const enabled = project.enabled === true ? "on" : project.enabled === false ? "off" : "rule";
  return `<form class="plug-applies-form" data-pa-form novalidate>
  <fieldset><legend>Save for</legend>
    <label><input type="radio" name="${id}-scope" value="global" ${scope === "global" ? "checked" : ""}> All projects (global setting)</label>
    <label><input type="radio" name="${id}-scope" value="project" ${scope === "project" ? "checked" : ""} ${list.has_manifest ? "" : "disabled"}> Only ${esc(list.project)} (its alfrd.yaml)</label>
    ${list.has_manifest ? "" : '<small class="muted">This project has no alfrd.yaml, so only the global setting is possible.</small>'}
  </fieldset>
  <fieldset data-pa-enabled ${scope === "project" ? "" : "hidden"}><legend>In this project</legend>
    <label><input type="radio" name="${id}-enabled" value="rule" ${enabled === "rule" ? "checked" : ""}> Follow the project types below</label>
    <label><input type="radio" name="${id}-enabled" value="on" ${enabled === "on" ? "checked" : ""}> Always on</label>
    <label><input type="radio" name="${id}-enabled" value="off" ${enabled === "off" ? "checked" : ""}> Off</label>
  </fieldset>
  <fieldset><legend>Project types</legend>
    <label><input type="radio" name="${id}-mode" value="all" ${current ? "" : "checked"}> All project types</label>
    <label><input type="radio" name="${id}-mode" value="some" ${current ? "checked" : ""}> Only these:</label>
    <div class="row gap wrap" data-pa-types>${known.map((t) => `<label><input type="checkbox" name="${id}-type" value="${esc(t)}" ${current?.includes(t) ? "checked" : ""}> ${esc(t)}</label>`).join("")}</div>
    <label class="small">Other types (comma separated) <input type="text" class="input sm" name="${id}-other" autocomplete="off" aria-describedby="${id}-msg"></label>
  </fieldset>
  <p class="small" id="${id}-msg" role="alert" data-pa-msg></p>
  <div class="row gap"><button type="submit" class="btn sm primary">Save</button><button type="button" class="btn sm" data-pa-reset>Use the default</button><button type="button" class="btn sm ghost" data-pa-cancel>Cancel</button></div>
</form>`;
}

/** Read the form: {scope, project_types, enabled} or throws a message for the person. */
export function readForm(form) {
  const value = (suffix) => form.querySelector(`input[name$="-${suffix}"]:checked`)?.value;
  const scope = value("scope") || "global";
  let types = ALL;
  if (value("mode") === "some") {
    const other = (form.querySelector('input[name$="-other"]')?.value || "").split(",").map((t) => t.trim()).filter(Boolean);
    types = [...new Set([...[...form.querySelectorAll('input[name$="-type"]:checked')].map((i) => i.value), ...other])];
    if (!types.length) throw new Error("Pick at least one project type, or choose All project types.");
  }
  const enabled = { on: true, off: false, rule: null }[value("enabled") || "rule"];
  return { scope, project_types: types, enabled };
}

/** Save through the host routes; resolves when written. */
export async function save(list, pluginId, choice, reset = false) {
  const id = encodeURIComponent(pluginId);
  if (choice.scope === "global") {
    return server.mutate(`/studio/plugins/${id}/project-types`, { project_types: reset ? null : choice.project_types });
  }
  const body = reset ? { enabled: null, project_types: null } : { enabled: choice.enabled, project_types: choice.project_types };
  return server.mutate(`/studio/projects/${encodeURIComponent(list.project)}/plugins/${id}/applicability`, { ...body, base_hash: list.manifest_hash });
}

/** Open the form inside box; done(message) after a save (the caller refreshes the list). */
export function open(box, list, p, { done, cancel }) {
  box.innerHTML = formHtml(list, p);
  box.hidden = false;
  const form = box.querySelector("form"), msg = box.querySelector("[data-pa-msg]");
  const sync = () => { form.querySelector("[data-pa-enabled]").hidden = readScope() !== "project"; };
  const readScope = () => form.querySelector('input[name$="-scope"]:checked')?.value;
  form.addEventListener("change", sync);
  const run = async (reset) => {
    let choice;
    try { choice = readForm(form); } catch (error) { msg.textContent = error.message; return; }
    form.querySelectorAll("button,input").forEach((el) => { el.disabled = true; });
    msg.textContent = "";
    try {
      await save(list, p.id, choice, reset);
      done(`${p.title || p.id}: ${reset ? "back to the default" : "saved"} (${choice.scope === "global" ? "all projects" : list.project}).`);
    } catch (error) {
      form.querySelectorAll("button,input").forEach((el) => { el.disabled = false; });
      msg.textContent = error.status === 409 || /changed on disk/.test(error.message) ? "alfrd.yaml changed on disk since Settings loaded it. Refresh the list and try again." : error.message;
    }
  };
  form.addEventListener("submit", (event) => { event.preventDefault(); run(false); });
  box.querySelector("[data-pa-reset]").onclick = () => run(true);
  box.querySelector("[data-pa-cancel]").onclick = () => cancel();
  form.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); cancel(); } });
  form.querySelector("input:checked")?.focus();
}
