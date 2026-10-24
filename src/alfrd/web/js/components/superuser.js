// Superuser page (#/superuser, also http://host:port/superuser): the per-user settings every
// `alfrd serve` / `alfrd gui` reads (alfrd.superuser, `alfrd su`), statistics and an ops panel
// (active runs and agents of every project, other servers, system). Loopback browsers only.
import { esc, icon, loadCss } from "../utils/dom.js";
import { server } from "../data/server.js";

const API = "/api/studio/superuser";
const S = { left: false, view: null, ops: null, error: "", opsError: "", draft: null, saving: false, timer: 0, ctx: null, host: null, busy: new Set() };
const SECTION_LABELS = { workflow: "Workflow", metadata: "Metadata", results: "Results", logs: "Logs", chat: "Chat", config: "Settings" };
const LOCK_HELP = {
  plugins: "Install, update, remove, enable, disable and configure plugins",
  plugin_actions: "Plugin actions that change files or external data",
  project_create: "New project, Open/Connect, Rediscover, new folders",
  project_delete: "Forget and Delete projects",
  runs: "Start and resume workflow runs, reviews, turn overrides",
  chat: "Chat sessions, messages, decisions, retries, mailbox replies",
  file_edits: "alfrd.yaml, tasks, targets, notes, handoffs, setup forms, history restore",
};

async function getJson(url) {
  const res = await server.request(url, { headers: { Accept: "application/json", "X-CSRF-Token": server.session?.csrf_token || "" } });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.error?.message || `${res.status} ${res.statusText}`);
  return body;
}

const clone = (x) => JSON.parse(JSON.stringify(x));
const dirty = () => S.view && S.draft && JSON.stringify(S.draft) !== JSON.stringify(S.view.settings);
const fmtBytes = (n) => (n == null ? "–" : n > 1 << 30 ? `${(n / (1 << 30)).toFixed(1)} GiB` : n > 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MiB` : `${Math.round(n / 1024)} KiB`);
const fmtAge = (s) => (s == null ? "–" : s < 120 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);

export function leave() { clearInterval(S.timer); S.timer = 0; S.left = true; }

export function render(el, ctx) {
  loadCss("css/superuser.css");
  S.ctx = ctx;
  if (ctx.state.mode !== "server" || !server.session?.can_superuser) {
    el.innerHTML = `<div class="empty"><h2>Superuser</h2><p>${ctx.state.mode !== "server" ? "Superuser settings need <code>alfrd serve</code>."
      : "Superuser settings open only in a browser on the server machine."} On that machine, <code>alfrd su show</code> and <code>alfrd su set key=value</code> do the same.</p></div>`;
    S.host = null; delete el.dataset.su;
    return;
  }
  if (S.host !== el || !el.dataset.su) mount(el);
  else if (S.left) { S.left = false; if (!dirty()) { load(); loadOps(); } } // back on the page: what the CLI or another tab saved
  if (!S.timer) S.timer = setInterval(() => { if (S.host?.isConnected && !document.hidden) loadOps(); else if (!S.host?.isConnected) leave(); }, 5000);
}

function mount(el) {
  S.host = el; el.dataset.su = "1";
  el.innerHTML = `<div class="su">
    <header class="su-head"><h2 tabindex="-1">${icon("power")} Superuser</h2><span class="grow"></span>
      <span class="muted small" id="su-file"></span>
      <button type="button" class="btn sm" data-su="reload">${icon("sync")} Reload</button>
      <button type="button" class="btn sm danger" data-su="reset">${icon("reset")} Reset to defaults</button></header>
    <p class="muted small">These settings apply to every <code>alfrd serve</code> / <code>alfrd gui</code> of this user on this machine, on their next request. Command-line flags stay stronger: <code>--experimental-features</code> turns experimental features on; <code>--no-gui-install</code> and <code>--no-plugin-actions</code> turn those off.</p>
    <div id="su-alert" role="alert"></div>
    <form id="su-form" class="su-grid" novalidate></form>
    <div class="su-bar" id="su-bar"><span class="grow muted small" id="su-state" role="status" aria-live="polite"></span>
      <button type="button" class="btn sm" data-su="discard">Discard</button><button type="submit" form="su-form" class="btn sm primary" id="su-save">${icon("save")} Save</button></div>
    <h3 class="su-h">Statistics</h3><div class="kpis small" id="su-stats"></div>
    <h3 class="su-h"><span class="grow">Active runs and agents</span><button type="button" class="icon-btn sm" data-su="ops" aria-label="Refresh active runs" title="Refresh">${icon("sync")}</button></h3>
    <div id="su-runs" aria-live="polite"></div>
    <h3 class="su-h">Servers</h3><div id="su-servers"></div>
    <h3 class="su-h">This server</h3><div class="set-facts" id="su-system"></div>
  </div>`;
  el.addEventListener("click", onClick);
  el.addEventListener("change", onChange);
  el.addEventListener("input", onChange);
  el.addEventListener("submit", (e) => { e.preventDefault(); save(); });
  load(); loadOps();
}

async function load() {
  try { S.view = await getJson(API); S.draft = clone(S.view.settings); S.error = ""; }
  catch (error) { S.error = error.message; }
  drawForm(); drawSystem();
}

async function loadOps() {
  try { S.ops = await getJson(`${API}/ops`); S.opsError = ""; }
  catch (error) { S.opsError = error.message; }
  drawOps(); drawSystem();
}

// --- settings form ------------------------------------------------------------------------

const check = (path, label, help, value, disabled = false) => `<label class="check"><input type="checkbox" data-path="${path}" ${value ? "checked" : ""} ${disabled ? "disabled" : ""}> <span><b>${esc(label)}</b>${help ? `<small>${help}</small>` : ""}</span></label>`;

function drawForm() {
  const form = S.host?.querySelector("#su-form");
  if (!form) return;
  const alert = S.host.querySelector("#su-alert");
  alert.innerHTML = S.error ? `<p class="callout fail small">${esc(S.error)}</p>` : S.view?.error ? `<p class="callout warn small">The settings file has an error, so the defaults apply: ${esc(S.view.error)}</p>` : "";
  if (!S.view) { form.innerHTML = `<p class="muted small" role="status">${S.error ? "" : "Loading…"}</p>`; return; }
  const d = S.draft, v = S.view, eff = v.effective, flags = v.flags;
  S.host.querySelector("#su-file").innerHTML = `<code title="${v.exists ? "Settings file" : "Not created yet: every setting is at its default"}">${esc(v.file)}</code>${v.exists ? "" : " (defaults)"}`;
  const locked = d.lockdown.read_only;
  form.innerHTML = `
    <fieldset class="card"><legend>Features</legend>
      ${check("features.experimental", "Experimental features", "The Chat mode preference (Studio settings → Preferences) and the agent-loop project template.", d.features.experimental)}
      ${flags.experimental_features ? `<p class="hint small">This server runs with <code>--experimental-features</code>: on here whatever this says.</p>` : ""}
    </fieldset>
    <fieldset class="card"><legend>Studio sections</legend><p class="muted small">Unticked sections disappear from the rail and their links open Overview. Overview always stays.</p>
      <div class="su-checks">${v.choices.sections.map((s) => check(`studio.hidden_sections:${s}`, SECTION_LABELS[s] || s, s === "chat" ? "Also needs experimental features and Chat mode." : "", !d.studio.hidden_sections.includes(s))).join("")}</div></fieldset>
    <fieldset class="card"><legend>New-project templates</legend><p class="muted small">Templates offered by New project (Studio). The command line is not affected.</p>
      <div class="su-checks">${v.choices.templates.map((t) => check(`studio.hidden_templates:${t}`, t, t === "agent-loop" ? "Also needs experimental features." : "", !d.studio.hidden_templates.includes(t))).join("")}</div></fieldset>
    <fieldset class="card"><legend>What the Studio may change</legend>
      ${check("lockdown.read_only", "Read-only Studio", "Refuse every change. Pausing, cancelling and stopping running work stays allowed.", d.lockdown.read_only)}
      <div class="su-checks">${v.choices.locks.map((k) => check(`lockdown.${k}`, (v.labels[`lockdown.${k}`] || k), LOCK_HELP[k], d.lockdown[k], locked)).join("")}</div>
      ${flags.no_gui_install ? `<p class="hint small">This server runs with <code>--no-gui-install</code>: plugin installs stay off here.</p>` : ""}
      ${flags.no_plugin_actions ? `<p class="hint small">This server runs with <code>--no-plugin-actions</code>: plugin actions stay off here.</p>` : ""}
    </fieldset>
    <fieldset class="card"><legend>Limits and defaults</legend>
      <label class="field"><span>Live update interval (seconds; empty: the server's <code>--live-interval</code>, ${esc(flags.live_interval)} s; 0 turns live updates off)</span>
        <input class="input sm" type="number" min="0" step="0.5" data-path="studio.live_interval" value="${d.studio.live_interval ?? ""}" placeholder="${esc(flags.live_interval)}"></label>
      <label class="field"><span>Chat: max cost per request, USD (caps every project's <code>chat.bounds.max_cost_usd</code>)</span>
        <input class="input sm" type="number" min="0" step="0.5" data-path="limits.chat_max_cost_usd" value="${d.limits.chat_max_cost_usd ?? ""}" placeholder="no cap"></label>
      <label class="field"><span>Chat: max replans per request (caps <code>chat.bounds.max_replans</code>)</span>
        <input class="input sm" type="number" min="0" step="1" data-path="limits.chat_max_replans" value="${d.limits.chat_max_replans ?? ""}" placeholder="no cap"></label>
      <label class="field"><span>Chat: allowed agents (comma-separated keys, e.g. <code>claude, sonnet</code>; empty: all). The orchestrator must be one of them.</span>
        <input class="input sm" data-path="limits.chat_allowed_agents" value="${esc(d.limits.chat_allowed_agents.join(", "))}" placeholder="all agents"></label>
    </fieldset>
    <fieldset class="card su-eff"><legend>In effect on this server</legend><div class="set-facts">
      ${[["Experimental", eff.experimental], ["Read-only", eff.read_only], ...v.choices.locks.map((k) => [v.labels[`lockdown.${k}`] || k, eff[k]])]
        .map(([k, on]) => `<div><span>${esc(k)}</span><b class="${on ? "ok-t" : "muted"}">${on ? "on" : "off"}</b></div>`).join("")}
      <div><span>Live interval</span><b>${eff.live_interval ? `${eff.live_interval} s` : "off"}</b></div></div></fieldset>`;
  drawBar();
}

function drawBar() {
  const state = S.host?.querySelector("#su-state"), save = S.host?.querySelector("#su-save");
  if (!state) return;
  state.textContent = S.saving ? "Saving…" : dirty() ? "Unsaved changes" : S.view ? "Saved" : "";
  save.disabled = S.saving || !dirty();
  S.host.querySelector("[data-su=discard]").disabled = S.saving || !dirty();
}

function onChange(e) {
  const input = e.target.closest?.("[data-path]");
  if (!input || !S.draft) return;
  const [path, item] = input.dataset.path.split(":");
  const [section, key] = path.split(".");
  if (item) { // a list of hidden names: a ticked box means "shown"
    const list = new Set(S.draft[section][key]);
    if (input.checked) list.delete(item); else list.add(item);
    S.draft[section][key] = [...list];
  } else if (input.type === "checkbox") {
    S.draft[section][key] = input.checked;
    if (path === "lockdown.read_only" && e.type === "change") { drawForm(); S.host.querySelector('[data-path="lockdown.read_only"]')?.focus(); return; }
  } else if (key === "chat_allowed_agents") {
    S.draft[section][key] = input.value.split(",").map((x) => x.trim()).filter(Boolean);
  } else {
    S.draft[section][key] = input.value.trim() === "" ? null : Number(input.value);
  }
  drawBar();
}

async function save() {
  if (!dirty() || S.saving) return;
  // Only the changed keys, so what the CLI or another tab saved meanwhile stays. A broken file is replaced whole.
  const broken = Boolean(S.view.error);
  if (broken && !confirm("The settings file has an error. Saving replaces it with the settings shown here.")) return;
  const changes = {};
  for (const [section, values] of Object.entries(S.draft)) {
    if (section === "version") continue;
    for (const [key, value] of Object.entries(values)) if (JSON.stringify(value) !== JSON.stringify(S.view.settings[section][key])) changes[`${section}.${key}`] = value;
  }
  S.saving = true; drawBar();
  try {
    S.view = await server.mutate("/studio/superuser", broken ? { settings: S.draft, replace: true } : { settings: changes });
    S.draft = clone(S.view.settings); S.error = "";
    S.ctx.toast("Superuser settings saved; every running Studio applies them now", "ok");
    await refreshSession();
  } catch (error) { S.error = error.message; S.ctx.toast(`Superuser: ${error.message}`, "fail"); }
  S.saving = false; drawForm(); drawSystem();
}

/** This tab follows at once (sections, locks, experimental); other tabs on their next reload or focus. */
async function refreshSession() {
  try { await S.ctx.refreshSession?.(); } catch { /* the next focus retries */ }
}

// --- ops ----------------------------------------------------------------------------------

function drawOps() {
  const h = S.host;
  if (!h) return;
  const o = S.ops;
  if (S.opsError) { h.querySelector("#su-runs").innerHTML = `<p class="callout fail small">${esc(S.opsError)}</p>`; return; }
  if (!o) return;
  const st = o.stats;
  h.querySelector("#su-stats").innerHTML = [["Projects", st.projects], ["Workflow runs", st.plans], ["Chat sessions", st.chat_sessions],
    ["Active runs", st.active_plans], ["Runners alive", st.runners_alive], ["Agents / commands running", st.running_units], ["Servers up", st.servers_alive]]
    .map(([k, v]) => `<div><span>${esc(k)}</span><b class="tabular">${esc(v)}</b></div>`).join("");
  h.querySelector("#su-runs").innerHTML = o.runs.length ? `<div class="table-wrap"><table class="su-table"><thead><tr><th>Project</th><th>Run</th><th>Status</th><th>Runner</th><th>Running now</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>
    ${o.runs.map((r) => {
      const key = `${r.project}|${r.plan}`, busy = S.busy.has(key);
      return `<tr><td>${esc(r.project_name)}<br><span class="muted small mono">${esc(r.root)}</span></td>
        <td>${esc(r.kind === "chat" ? "Chat" : "Workflow")}: ${esc(r.title || r.plan)}<br><span class="muted small mono">${esc(r.plan)}</span></td>
        <td>${esc(r.status || "–")}</td><td>${r.runner_alive ? `<span class="ok-t">alive</span>${r.runner_pid ? ` <span class="muted small">pid ${esc(r.runner_pid)}</span>` : ""}` : `<span class="muted">stopped</span>`}</td>
        <td>${r.units.length ? r.units.map((u) => `<div class="su-unit"><span class="mono small">${esc((u.steps || []).join(", ") || u.id)}${u.row ? ` · ${esc(u.row)}` : ""}${u.pid ? ` · pid ${esc(u.pid)}` : ""}</span>
          <button type="button" class="btn sm" data-op="stop-unit" data-key="${esc(key)}" data-unit="${esc(u.id)}" ${busy ? "disabled" : ""} aria-label="Stop ${esc(u.id)}">${icon("stop")} Stop</button></div>`).join("") : '<span class="muted">–</span>'}</td>
        <td class="su-acts">${r.status === "paused" ? `<button type="button" class="btn sm" data-op="resume" data-key="${esc(key)}" ${busy ? "disabled" : ""}>${icon("play")} Resume</button>`
          : `<button type="button" class="btn sm" data-op="pause" data-key="${esc(key)}" ${busy ? "disabled" : ""}>${icon("pause")} Pause</button>`}
          <button type="button" class="btn sm danger" data-op="cancel" data-key="${esc(key)}" ${busy ? "disabled" : ""}>${icon("close")} Cancel</button></td></tr>`;
    }).join("")}</tbody></table></div>` : `<p class="muted small">Nothing is running in any project.</p>`;
  h.querySelector("#su-servers").innerHTML = o.servers.length ? `<ul class="su-servers">${o.servers.map((s) => `<li><span class="badge tone-${s.alive ? "ok" : "warn"}">${s.alive ? "up" : "gone"}</span> <code>${esc(s.url || `port ${s.port}`)}</code> <span class="muted small">pid ${esc(s.pid ?? "–")}${s.this ? " · this server" : ""}</span></li>`).join("")}</ul>`
    : `<p class="muted small">No server files.</p>`;
}

function drawSystem() {
  const box = S.host?.querySelector("#su-system"), sys = S.ops?.system;
  if (!box || !sys) return;
  const f = S.view?.flags || {};
  box.innerHTML = [["Version", sys.version], ["Python", sys.python], ["Host", sys.host], ["Platform", sys.platform], ["Server pid", sys.pid], ["Uptime", fmtAge(sys.uptime_s)],
    ["CPU", sys.cpu == null ? "–" : `${sys.cpu}% of ${sys.cores} cores`], ["Memory", sys.mem_total ? `${fmtBytes(sys.mem_used)} / ${fmtBytes(sys.mem_total)}` : "–"],
    ["Load", sys.load ? sys.load.map((x) => x.toFixed(2)).join(" ") : "–"], ["Runtime database", sys.runtime_db || "–"], ["Database size", fmtBytes(sys.runtime_db_bytes)],
    ["Project scope", Array.isArray(f.projects_scope) ? `${f.projects_scope.length} project(s)` : "all projects"], ["Demo data", f.demo ? "on" : "off"]]
    .map(([k, v]) => `<div><span>${esc(k)}</span><b class="mono small" title="${esc(v)}">${esc(v)}</b></div>`).join("");
}

async function op(button) {
  const [project, plan] = button.dataset.key.split("|"), action = button.dataset.op;
  if (action === "cancel" && !confirm(`Cancel run ${plan}? Running commands are stopped.`)) return;
  S.busy.add(button.dataset.key); drawOps();
  try {
    await server.mutate("/studio/superuser/ops", { project, plan, action, unit: button.dataset.unit });
    S.ctx.toast(`${action === "stop-unit" ? "Stop requested for " + button.dataset.unit : `Run ${plan}: ${action}`}`, "ok");
  } catch (error) { S.ctx.toast(`${action}: ${error.message}`, "fail"); }
  S.busy.delete(button.dataset.key);
  loadOps();
}

async function onClick(e) {
  const b = e.target.closest?.("button");
  if (!b) return;
  if (b.dataset.op) return op(b);
  const a = b.dataset.su;
  if (a === "reload") { load(); loadOps(); }
  else if (a === "ops") loadOps();
  else if (a === "discard") { S.draft = clone(S.view.settings); drawForm(); }
  else if (a === "reset" && confirm("Reset every superuser setting to its default (removes the settings file)?")) {
    try { S.view = await server.mutate("/studio/superuser/reset", {}); S.draft = clone(S.view.settings); S.error = ""; S.ctx.toast("Superuser settings reset", "ok"); await refreshSession(); }
    catch (error) { S.ctx.toast(`Reset: ${error.message}`, "fail"); }
    drawForm();
  }
}
