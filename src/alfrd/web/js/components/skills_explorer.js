// Source-aware library and conflict-guarded project activation draft.
import { esc, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";
import { previewHtml } from "./skills_settings.js";

const roots = ["Global", ".alfrd/skills", ".claude/skills", "skills", "Unavailable"];
const identity = (s) => `${s.origin || "project"}:${s.id}`;
const mounted = new WeakMap();
const badge = (text, label, tone = "muted") => `<span class="badge tone-${tone}" aria-label="${esc(label)}" title="${esc(label)}">${esc(text)}</span>`;
const roleName = (role) => ({ development: "Developer", design: "Designer", verification: "Verifier" }[role] || role);
const badges = (skill) => Object.entries(skill.providers || {}).map(([provider, policy]) => {
  const label = `${provider}: ${policy.delivery}${policy.fallback ? ` (fallback: ${policy.fallback})` : ""}`;
  return badge(label, label);
}).join(" ");

async function read(path) {
  const res = await server.request(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => null);
  if (!res.ok || !body) throw new Error(body?.error?.message || `Skills unavailable (${res.status})`);
  return body;
}

export function render(el, ctx) {
  loadCss("css/skills_explorer.css");
  const project = ctx.state.selectedProject;
  const key = JSON.stringify([ctx.state.mode, project]);
  if (mounted.get(el)?.key === key && el.querySelector("[data-skills-explorer]")) return;
  const previous=mounted.get(el);
  if (previous && el.skillsCanLeave?.() === false) return;
  previous?.dispose?.();
  const state = { key, skills: [], query: "", selected: null, inventoryRequest: 0, previewRequest: 0, activations: [], roles: [], hash: null };
  mounted.set(el, state);
  el.innerHTML = `<div data-skills-explorer><div class="row gap wrap"><strong>Installed skills</strong><span class="grow"></span><button type="button" class="btn" data-skill-new>New skill</button><button type="button" class="btn" data-skill-import>Import from URL</button><button type="button" class="btn" data-skills-reload>Refresh skills</button></div>
    <div class="skills-explorer-meta">${badge(project || "(none)", `Project: ${project || "(none)"}`)}<span class="badge tone-muted" data-activation-mode></span>${badge("New requests", "Applies to new requests. Retries retain their frozen guidance.")}</div>
    <details class="skills-explorer-help small muted"><summary>About skills</summary><p>Browse installed skills for project <strong>${esc(project || "(none)")}</strong>. Delivery badges in the detail view describe selected skills with native skills enabled; launch settings and failures can change delivery. Selected manifests also retain prompt injection.</p><p>Changes apply to new requests. Legacy selection is replaced by explicit scopes when saved; role-less tasks will receive none.</p></details>
    <div class="row gap wrap"><button type="button" class="btn" data-activation-save disabled>Save activations</button><button type="button" class="btn" data-activation-cancel>Cancel</button></div><p role="status" data-activation-status></p>
    <label>Search skills <input type="search" class="input" data-skills-search placeholder="Name, description, path or provider"></label>
    <p role="status" aria-live="polite" data-skills-status></p>
    <div class="skills-explorer-grid"><nav class="card" aria-label="Installed skills" data-skills-list></nav>
      <section id="skills-explorer-preview" class="card skills-explorer-detail" aria-label="Skill details" data-skills-detail><p>Choose a skill to preview its SKILL.md.</p></section></div></div>`;
  const list = el.querySelector("[data-skills-list]"), detail = el.querySelector("[data-skills-detail]"), status = el.querySelector("[data-skills-status]");
  const search = el.querySelector("[data-skills-search]"), reload = el.querySelector("[data-skills-reload]");
  const current = () => mounted.get(el) === state && el.isConnected;
  const base = `/api/studio/projects/${encodeURIComponent(project)}/chat/skills`;

  let draft = null, activationBaseline = '[]', importToken = null;
  const dirty = () => Boolean(importToken || (draft && (draft.initial !== detail.querySelector('[data-author-content]')?.value || (detail.querySelector('[data-author-id]') && (detail.querySelector('[data-author-id]').value !== draft.targetId || detail.querySelector('[data-author-origin]').value !== draft.targetOrigin))))) || JSON.stringify(state.activations) !== activationBaseline;
  function discardGuard() {
    if (!dirty()) return true;
    if (!window.confirm('Discard changes? Choose Cancel to keep editing.')) return false;
    draft=null; state.activations=JSON.parse(activationBaseline); cancelImport(); return true;
  }
  el.skillsCanLeave = discardGuard;
  const modal = el.closest('[role="dialog"]');
  modal?.addEventListener('beforeclose', (e) => { if (!discardGuard()) { e.preventDefault(); } else { cancelImport(); window.removeEventListener('beforeunload', unload); } });
  const unload = (e) => { if (el.isConnected && dirty()) { e.preventDefault(); e.returnValue = ''; } };
  window.addEventListener('beforeunload', unload);
  state.dispose=()=>{window.removeEventListener('beforeunload',unload);cancelImport();};
  async function mutate(path, payload, method='POST') {
    const response = await server.request(base+path, { method, headers: { 'Content-Type':'application/json' }, body: JSON.stringify(payload) });
    const body = await response.json();
    if (!response.ok) { const error = new Error(body?.error?.message || 'Could not save skill'); error.details = body?.error; throw error; }
    return body;
  }
  function destination() {
    const scope = detail.querySelector('[data-author-origin]')?.value || draft?.origin || 'project';
    const id = detail.querySelector('[data-author-id]')?.value || draft?.id || '';
    const path = scope === 'global' ? state.globalPath : `${state.projectPath}/.alfrd/skills`;
    const target = detail.querySelector('[data-author-target]');
    if (target) target.textContent = `Project: ${project}. Destination: ${path || '(loading)'}/${id}. ${scope === 'global' ? 'Global changes affect the shared library.' : ''}`;
  }
  async function finishSave(saved) {
    const activations = state.activations, baseline = activationBaseline;
    draft = null; importToken = null; state.selected = identity(saved);
    await load(); state.activations = activations; activationBaseline = baseline; drawList(); await preview(state.selected);
    status.textContent = 'Skill saved. Activation changes require Save activations.';
    list.querySelector(`[data-origin="${CSS.escape(saved.origin)}"][data-skill="${CSS.escape(saved.id)}"]`)?.focus();
  }
  function targetFields(id='', origin='project') {
    return `<label>ID <input class="input" data-author-id value="${esc(id)}" maxlength="64" pattern="[a-z0-9]+(?:-[a-z0-9]+)*"></label><label>Scope <select class="input" data-author-origin><option value="project" ${origin==='project'?'selected':''}>Current project</option><option value="global" ${origin==='global'?'selected':''}>Global library</option></select></label><p class="mono" data-author-target></p>`;
  }
  function editor(skill=null, copy=false) {
    if (!discardGuard()) return;
    ++state.previewRequest;
    const content = skill?.content || '---\nname: "new-skill"\ndescription: "Describe when to use this skill."\n---\n# Instructions\n\n1. Describe the action and expected result.\n';
    draft = { initial: content, origin: skill?.origin || 'project', id: skill?.id, revision: skill?.revision, copy };
    detail.innerHTML = `<h3>${copy?'Copy skill':skill?'Edit skill':'New skill'}</h3>${!skill || copy ? targetFields(copy ? `${skill.id}-copy` : '') : `<p class="mono">${esc(skill.origin)}:${esc(skill.id)} · ${esc(skill.path)}</p>${skill.origin==='global'?'<p>Global changes affect the shared library.</p>':''}`}
      <label>Name <input class="input" data-author-name></label><label>Description <input class="input" data-author-description></label>
      <label>SKILL.md <textarea class="input skills-author-text" data-author-content spellcheck="false" ${copy?'readonly':''}></textarea></label><p data-author-bytes></p><p role="status" data-author-validation></p>
      <details><summary>Live preview</summary><pre data-author-live></pre></details><div data-author-conflict></div>
      <button type="button" class="btn" data-author-save disabled>Save skill</button> <button type="button" class="btn" data-author-cancel>Cancel edit</button>`;
    const area = detail.querySelector('[data-author-content]'); area.value = content;
    draft.targetId=detail.querySelector('[data-author-id]')?.value; draft.targetOrigin=detail.querySelector('[data-author-origin]')?.value;
    const fields = ['name','description']; let validation = 0, timer, spans;
    async function validate() {
      if (!detail.contains(area)) return;
      detail.querySelector('[data-author-validation]').textContent='Validating…';
      const request = ++validation, value = area.value;
      detail.querySelector('[data-author-bytes]').textContent = `${new TextEncoder().encode(value).length} / 65,536 bytes`;
      detail.querySelector('[data-author-live]').textContent = value;
      detail.querySelector('[data-author-save]').disabled = true;
      try {
        const result = await mutate('/validate', {content:value});
        if (request !== validation || !detail.contains(area)) return;
        spans=result.scalar_spans;
        fields.forEach((f) => { detail.querySelector(`[data-author-${f}]`).value = result.fields[f]; detail.querySelector(`[data-author-${f}]`).disabled = copy; });
        detail.querySelector('[data-author-validation]').textContent = 'Valid SKILL.md';
        detail.querySelector('[data-author-save]').disabled = false;
      } catch(error) {
        if (request !== validation || !detail.contains(area)) return;
        fields.forEach((f) => { detail.querySelector(`[data-author-${f}]`).disabled = true; });
        detail.querySelector('[data-author-validation]').textContent = `${error.message}${error.details?.line ? ` (line ${error.details.line})` : ''}`;
      }
    }
    area.oninput = () => { ++validation; detail.querySelector('[data-author-save]').disabled=true; detail.querySelector('[data-author-validation]').textContent='Validating…'; clearTimeout(timer); timer=setTimeout(validate,200); };
    fields.forEach((f) => { detail.querySelector(`[data-author-${f}]`).oninput = (e) => {
      // Server YAML node spans preserve comments and unknown metadata byte-for-byte.
      const span=spans?.[f];
      if (!span) return;
      const replacement=JSON.stringify(e.target.value), delta=replacement.length-(span[1]-span[0]);
      area.value=area.value.slice(0,span[0])+replacement+area.value.slice(span[1]);
      spans=Object.fromEntries(Object.entries(spans).map(([key,range])=>[key,key===f?[span[0],span[0]+replacement.length]:range.map(n=>n>=span[1]?n+delta:n)]));
      validate();
    }; });
    detail.querySelector('[data-author-id]')?.addEventListener('input', destination);
    detail.querySelector('[data-author-origin]')?.addEventListener('change', destination);
    detail.querySelector('[data-author-cancel]').onclick = () => { draft=null; preview(state.selected); if (!state.selected) detail.textContent='Choose a skill.'; };
    detail.querySelector('[data-author-save]').onclick = async (e) => {
      e.target.disabled=true;
      try {
        const origin=detail.querySelector('[data-author-origin]')?.value || draft.origin, id=detail.querySelector('[data-author-id]')?.value || draft.id;
        const result = copy ? await mutate('/copy',{source_origin:skill.origin,source_id:skill.id,base_revision:skill.copy_revision,target_origin:origin,id}) :
          await mutate(skill ? '/content' : '/create', {origin,id,content:area.value,...(skill?{base_revision:draft.revision}:{})},skill?'PUT':'POST');
        await finishSave(result);
      } catch(error) {
        if (!detail.contains(area)) return;
        detail.querySelector('[data-author-validation]').textContent=error.message;
        if (error.details?.current_content !== undefined) {
          const box=detail.querySelector('[data-author-conflict]'); box.innerHTML='<h4>Current saved version</h4><pre></pre><p>Your draft remains in the editor. Copy it before reloading.</p>'; box.querySelector('pre').textContent=error.details.current_content;
        }
        e.target.disabled=false;
      }
    };
    destination(); validate(); area.focus();
  }
  async function cancelImport() {
    if (importToken) { const token=importToken; importToken=null; await mutate(`/imports/${encodeURIComponent(token)}`,{},'DELETE').catch(()=>{}); }
  }
  function importForm(source='', baseline=null) {
    if (!discardGuard()) return;
    draft=null; const epoch=++state.previewRequest;
    detail.innerHTML=`<h3>Import from URL</h3><p>Fetch a public HTTPS raw SKILL.md, GitHub skill folder, ZIP or tar.gz. Text references only; links in Markdown are not fetched. Imported guidance grants no permissions.</p><label>URL <input class="input" type="url" data-import-url value="${esc(source)}"></label><button class="btn" data-import-fetch>Fetch preview</button><p role="status" data-import-status></p><div data-import-preview></div>`;
    detail.querySelector('[data-import-fetch]').onclick=async(e)=>{
      e.target.disabled=true;
      try {
        await cancelImport(); const body=await mutate('/imports/preview',{url:detail.querySelector('[data-import-url]').value});
        if (!current() || epoch !== state.previewRequest) { mutate(`/imports/${encodeURIComponent(body.token)}`,{},'DELETE').catch(()=>{}); return; }
        importToken=body.token;
        const box=detail.querySelector('[data-import-preview]'); if (!box) return;
        box.innerHTML=`<h4>Review before saving</h4>${targetFields(body.proposed_id)}<dl><dt>Original URL</dt><dd>${esc(body.source.source_url)}</dd><dt>Resolved URL</dt><dd>${esc(body.source.resolved_url)}</dd><dt>Commit</dt><dd>${esc(body.source.commit || '—')}</dd><dt>Fetched</dt><dd>${esc(body.source.fetched_at)}</dd><dt>Bundle SHA-256</dt><dd>${esc(body.digest)}</dd></dl><p>${esc(body.total_bytes)} bytes</p><ul>${body.files.map(f=>`<li>${esc(f.path)} (${esc(f.bytes)} bytes)</li>`).join('')}</ul><pre>${esc(body.content)}</pre><button class="btn" data-import-confirm>Save skill</button> <button class="btn" data-import-cancel>Discard preview</button>`;
        if (baseline) {
          const before = baseline.provenance?.files || [], after = body.files;
          const old = new Map(before.map(f=>[f.path,f.sha256])), next = new Map(after.map(f=>[f.path,f.sha256]));
          const added = after.filter(f=>!old.has(f.path)).map(f=>f.path), removed = before.filter(f=>!next.has(f.path)).map(f=>f.path), changed=after.filter(f=>old.has(f.path)&&old.get(f.path)!==f.sha256).map(f=>f.path);
          const comparison=document.createElement('div');
          comparison.innerHTML=`<h4>Source comparison</h4><p>Added: ${esc(added.join(', ')||'none')}. Removed: ${esc(removed.join(', ')||'none')}. Changed: ${esc(changed.join(', ')||'none')}.</p><details><summary>Current installed manifest</summary><pre>${esc(baseline.content)}</pre></details><p>Changed upstream content can be saved as a new copy. Choose another ID or scope.</p>`;
          box.prepend(comparison);
          if (body.digest === baseline.provenance?.bundle_sha256) {
            comparison.insertAdjacentHTML('afterbegin','<p>No upstream content change</p>');
            box.querySelector('[data-import-confirm]').remove();
            cancelImport();
          }
        }
        destination(); box.querySelector('[data-author-id]').oninput=destination; box.querySelector('[data-author-origin]').onchange=destination;
        box.querySelector('[data-import-cancel]').onclick=async()=>{await cancelImport();box.textContent='Preview discarded.';};
        const confirm=box.querySelector('[data-import-confirm]');
        if (confirm) confirm.onclick=async(ev)=>{
          ev.target.disabled=true;
          try { const result=await mutate('/imports/confirm',{token:importToken,id:box.querySelector('[data-author-id]').value,origin:box.querySelector('[data-author-origin]').value}); await finishSave(result); }
          catch(error) { detail.querySelector('[data-import-status]').textContent=error.message; ev.target.disabled=false; }
        };
      } catch(error) { const message=detail.querySelector('[data-import-status]'); if(message) message.textContent=error.message; }
      finally { e.target.disabled=false; }
    };
  }
  el.querySelector('[data-skill-new]').onclick=()=>editor();
  el.querySelector('[data-skill-import]').onclick=()=>importForm();

  function activationMode(legacy) {
    const mode = el.querySelector("[data-activation-mode]");
    mode.textContent = legacy ? "Legacy selection" : "Project activations";
    const label = legacy ? "Legacy selection. Saving replaces it with explicit scopes; role-less tasks will receive none." : "Project activations. Saved scopes apply to new requests.";
    mode.title = label; mode.setAttribute("aria-label", label);
  }

  function cardBadges(skill) {
    const source = skill.origin || "project";
    const active = state.activations.filter((a) => a.source === source && a.id === skill.id);
    return badge(source === "global" ? "Global" : "Project", `Scope: ${source === "global" ? "Global" : "Project"}`)
      + (source === "project" && skill.source_root !== "Unavailable" ? badge(skill.source_root, `Source: ${skill.source_root}`) : "")
      + (skill.unavailable ? badge("Unavailable", "Skill is unavailable; remove its activations or reinstall it.", "warn") : "")
      + (active.length ? active.map((a) => {
        const overridden = source === "global" && state.activations.some((other) => other.source === "project" && other.id === skill.id && other.scope === a.scope);
        const name = a.scope === "orchestrator" ? "Orchestrator" : roleName(a.scope.slice(5));
        return badge(name, `Active for ${a.scope === "orchestrator" ? "Orchestrator" : `role: ${a.scope.slice(5)}`}${overridden ? "; overridden by project skill" : ""}`, overridden ? "muted" : "ok");
      }).join("") : badge("Inactive", "Inactive: no activation scopes selected"));
  }

  function drawList() {
    const query = state.query.trim().toLowerCase();
    const visible = state.skills.filter((s) => [s.id, s.name, s.description, s.source, s.path, JSON.stringify(s.providers)].join(" ").toLowerCase().includes(query));
    status.textContent = `${visible.length} of ${state.skills.length} skills`;
    list.innerHTML = roots.map((root) => {
      const skills = visible.filter((s) => s.source_root === root);
      return skills.length ? `<section><h3>${esc(root)}/</h3><ul class="skills-explorer-list">${skills.map((s) => `<li><button type="button" class="btn skills-explorer-item" data-skill="${esc(s.id)}" data-origin="${esc(s.origin || "project")}" aria-pressed="${identity(s) === state.selected}" aria-controls="skills-explorer-preview"><strong class="skills-explorer-name" title="${esc(s.name)}">${esc(s.name)}</strong><span class="small skills-explorer-description" title="${esc(s.description || "No description")}">${esc(s.description || "No description")}</span><span class="skills-explorer-badges">${cardBadges(s)}</span></button>${s.editable ? `<button class="btn skills-card-edit" data-card-edit="${esc(s.id)}" data-card-origin="${esc(s.origin || 'project')}">Edit</button>` : ''}</li>`).join("")}</ul></section>` : "";
    }).join("") || `<p class="empty">${state.skills.length ? "No skills match your search." : "No installed skills found for this project. Add a SKILL.md under .alfrd/skills/&lt;name&gt;, .claude/skills/&lt;name&gt; or skills/&lt;name&gt;, then refresh."}</p>`;
  }

  function drawControls(skill) {
    const target = detail.querySelector("[data-activation-controls]");
    const scopes = ["orchestrator", ...state.roles.map((r) => `role:${r}`)];
    target.innerHTML = `<fieldset><legend>Active for</legend>${scopes.map((scope) => {
      const checked = state.activations.some((a) => a.source === (skill.origin || "project") && a.id === skill.id && a.scope === scope);
      const overridden = checked && skill.origin === "global" && state.activations.some((a) => a.source === "project" && a.id === skill.id && a.scope === scope);
      return `<label class="row gap"><input type="checkbox" data-activation-scope="${esc(scope)}" ${checked ? "checked" : ""}>${esc(scope === "orchestrator" ? "Orchestrator" : scope.slice(5))}${overridden ? " · Overridden" : ""}${skill.unavailable ? " · Unavailable" : ""}</label>`;
    }).join("")}</fieldset>`;
  }
  detail.onchange = (e) => {
    const scope = e.target.dataset.activationScope;
    if (!scope) return;
    const skill = state.skills.find((s) => identity(s) === state.selected);
    state.activations = state.activations.filter((a) => !(a.source === (skill.origin || "project") && a.id === skill.id && a.scope === scope));
    if (e.target.checked) state.activations.push({ source: skill.origin || "project", id: skill.id, scope });
    drawControls(skill);
    drawList();
    detail.querySelector(`[data-activation-scope="${CSS.escape(scope)}"]`)?.focus();
  };
  el.querySelector("[data-activation-save]").onclick = async () => {
    const button = el.querySelector("[data-activation-save]"), message = el.querySelector("[data-activation-status]");
    button.disabled = true;
    try {
      const response = await server.request(`${base}/activations`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: 1, activations: state.activations, base_hash: state.hash }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.error?.message || "Could not save activations");
      if (!current()) return;
      state.hash = body.hash; activationBaseline = JSON.stringify(state.activations); message.textContent = "Activations saved. Applies to new requests.";
      activationMode(false);
    } catch (error) { if (current()) message.textContent = error.message; }
    finally { if (current()) button.disabled = false; }
  };
  el.querySelector("[data-activation-cancel]").onclick = () => load();

  async function preview(key) {
    const skill = state.skills.find((s) => identity(s) === key);
    if (!skill) return;
    const id = skill.id;
    state.selected = key;
    list.querySelectorAll("[data-skill]").forEach((b) => b.setAttribute("aria-pressed", String(`${b.dataset.origin}:${b.dataset.skill}` === key)));
    const request = ++state.previewRequest;
    detail.setAttribute("aria-busy", "true");
    detail.innerHTML = `<div><h3>${esc(skill.name)}</h3><p>${esc(skill.description || "No description")}</p><div class="skills-explorer-badges">${badges(skill)}</div>
      <div data-activation-controls></div>
      <dl><dt>Path</dt><dd class="mono">${esc(skill.path)}</dd><dt>Size</dt><dd>${esc(skill.bytes)} bytes</dd><dt>SHA-256</dt><dd class="mono">${esc(skill.sha256)}</dd></dl>
      <h4>Reference files</h4>${skill.reference_files?.length ? `<ul>${skill.reference_files.map((path) => `<li class="mono">${esc(path)}</li>`).join("")}</ul>` : "<p>No reference files.</p>"}
      <div data-skill-content role="status">Loading SKILL.md preview…</div></div>`;
    drawControls(skill);
    try {
      if (skill.unavailable) throw new Error("Skill is unavailable; uncheck its activations to remove it.");
      const body = await read(`${base}/preview?id=${encodeURIComponent(id)}&origin=${encodeURIComponent(skill.origin || "project")}`);
      if (!current() || request !== state.previewRequest) return;
      const target = detail.querySelector("[data-skill-content]");
      // A refreshed preview may differ from the inventory snapshot.
      target.innerHTML = previewHtml(body);
      const actions = document.createElement('div');
      actions.innerHTML = `${body.editable ? '<button class="btn" data-skill-edit>Edit</button>' : `<p>${esc(body.read_only_reason || 'Read-only source')}</p>`}${body.can_copy ? '<button class="btn" data-skill-copy>Copy skill…</button>' : ''}${body.provenance?.source_url ? '<button class="btn" data-skill-refetch>Re-fetch source</button>' : ''}${body.locally_modified ? '<span class="badge tone-warn">Locally modified</span>' : ''}`;
      target.prepend(actions);
      actions.querySelector('[data-skill-edit]')?.addEventListener('click',()=>editor(body));
      actions.querySelector('[data-skill-copy]')?.addEventListener('click',()=>editor(body,true));
      actions.querySelector('[data-skill-refetch]')?.addEventListener('click',()=>importForm(body.provenance.source_url,body));
      if (body.sha256 !== skill.sha256) target.insertAdjacentHTML("afterbegin", '<p class="callout warn">File changed since inventory was loaded. Refresh skills to update metadata.</p>');
    } catch (error) {
      if (!current() || request !== state.previewRequest) return;
      const target = detail.querySelector("[data-skill-content]");
      target.innerHTML = `<p role="alert">${esc(error.message)}</p><button type="button" class="btn" data-preview-retry>Retry preview</button>`;
    } finally {
      if (current() && request === state.previewRequest) detail.setAttribute("aria-busy", "false");
    }
  }

  async function load() {
    const request = ++state.inventoryRequest;
    ++state.previewRequest;
    reload.disabled = true; search.disabled = true;
    list.setAttribute("aria-busy", "true"); detail.setAttribute("aria-busy", "false");
    status.textContent = "Loading installed skills…"; list.textContent = "Loading…";
    detail.innerHTML = "<p>Choose a skill to preview its SKILL.md.</p>";
    try {
      const [body, activation] = await Promise.all([read(`${base}/inventory`), read(`${base}/activations`)]);
      if (!current() || request !== state.inventoryRequest) return;
      state.activations = activation.activations; state.roles = activation.roles; state.hash = activation.hash;
      state.skills = body.skills; state.projectPath=body.project_path; state.globalPath=body.global_path; activationBaseline=JSON.stringify(state.activations);
      for (const a of activation.unavailable || []) if (!state.skills.some((s) => identity(s) === `${a.source}:${a.id}`))
        state.skills.push({ id: a.id, name: a.id, origin: a.source, source_root: "Unavailable", unavailable: true });
      activationMode(activation.legacy);
      el.querySelector("[data-activation-save]").disabled = false;
      el.querySelector("[data-activation-status]").textContent = "";
      drawList();
      if (state.skills.some((s) => identity(s) === state.selected)) preview(state.selected);
      else state.selected = null;
    } catch (error) {
      if (!current() || request !== state.inventoryRequest) return;
      status.textContent = "Could not load installed skills.";
      list.innerHTML = `<p role="alert">${esc(error.message)}</p><p>Use Refresh skills to retry.</p>`;
    } finally {
      if (current() && request === state.inventoryRequest) { reload.disabled = false; search.disabled = false; list.setAttribute("aria-busy", "false"); }
    }
  }
  search.oninput = () => { state.query = search.value; drawList(); };
  reload.onclick = () => { if(discardGuard()) {draft=null; cancelImport(); load();} };
  list.onclick = async (e) => {
    const edit=e.target.closest('[data-card-edit]');
    if(edit) {
      if (!discardGuard()) return;
      try { const body=await read(`${base}/preview?id=${encodeURIComponent(edit.dataset.cardEdit)}&origin=${encodeURIComponent(edit.dataset.cardOrigin)}`); state.selected=identity(body); drawList(); editor(body); } catch(error) {status.textContent=error.message;}
      return;
    }
    const b = e.target.closest("[data-skill]"); if (b && (!(draft || importToken) || discardGuard())) {draft=null; cancelImport(); preview(`${b.dataset.origin}:${b.dataset.skill}`);} };
  list.onkeydown = (e) => {
    const buttons = [...list.querySelectorAll("[data-skill]")], index = buttons.indexOf(document.activeElement);
    if (index < 0 || !["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    buttons[e.key === "Home" ? 0 : e.key === "End" ? buttons.length - 1 : (index + (e.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
  };
  detail.onclick = (e) => { if (e.target.closest("[data-preview-retry]")) preview(state.selected); };
  if (ctx.state.mode !== "server" || !project || project === "all") {
    status.textContent = ctx.state.mode !== "server" ? "Skills Explorer needs alfrd serve." : "Choose a project to browse its installed skills.";
    el.querySelector('[data-skill-new]').disabled = el.querySelector('[data-skill-import]').disabled = true;
    search.disabled = reload.disabled = true; list.textContent = "No project inventory loaded.";
    return;
  }
  load();
}
