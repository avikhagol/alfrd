// Settings → Plugins → Configure: a plugin's settings form, Test, and its services. Loaded on first use.
import { esc } from "../utils/dom.js";
import { server } from "../data/server.js";
import { fetchJSON } from "./plugin_api.js";
const opened = new Map(); // plugin id → its last config reply (kept across Settings redraws)
const panels = new WeakMap();
const base = (id) => `/studio/plugins/${encodeURIComponent(id)}`;
const TONE = { running: "ok", starting: "warn", restarting: "warn", failed: "fail", stopped: "muted" };
const time = (t) => (t ? new Date(t * 1000).toLocaleTimeString() : "");

function field(f, canWrite) {
  const id = `plug-set-${esc(f.key)}`, secret = f.kind === "secret", off = canWrite ? "" : "disabled";
  const help = f.help ? `<small class="muted">${esc(f.help)}</small>` : "";
  if (f.kind === "bool") return `<label class="plug-field-bool"><input type="checkbox" name="${esc(f.key)}" ${f.value ? "checked" : ""} ${off}> ${esc(f.label)}</label>${help}`;
  const hint = secret && f.set ? "Saved · leave empty to keep it" : f.placeholder || "";
  return `<label for="${id}">${esc(f.label)}${f.required ? ' <small class="muted">required</small>' : ""}${secret ? ` <span class="badge tone-${f.set ? "ok" : "muted"}">${f.set ? "set" : "not set"}</span>` : ""}</label>
<div class="plug-field"><input id="${id}" name="${esc(f.key)}" type="${secret ? "password" : f.kind === "number" ? "number" : "text"}" value="${secret ? "" : esc(f.value ?? "")}" placeholder="${esc(hint)}" autocomplete="off" spellcheck="false" ${off}>${secret && f.set && canWrite ? `<button type="button" class="btn sm ghost" data-plug-clear="${esc(f.key)}">Clear</button>` : ""}</div>${help}`;
}

function service(s, canWrite) {
  const live = ["running", "starting", "restarting"].includes(s.status), off = canWrite ? "" : "disabled";
  const detail = s.status === "running" ? `since ${time(s.started)} · pid ${s.pid}` : s.exit_code != null ? `last exit code ${s.exit_code}` : "";
  return `<div class="plug-svc" data-plug-svc="${esc(s.id)}"><div><b>${esc(s.title)}</b> <span class="badge tone-${TONE[s.status] || "muted"}">${esc(s.status)}</span> <small class="muted tabular">${esc(detail)}</small>
<small class="muted plug-description">${esc(s.description)}</small><code class="small">${esc(s.command.join(" "))}</code></div>
<div class="plug-svc-actions">${live ? `<button type="button" class="btn sm" data-plug-svc-do="stop" ${off}>Stop</button><button type="button" class="btn sm" data-plug-svc-do="restart" ${off}>Restart</button>` : `<button type="button" class="btn sm primary" data-plug-svc-do="start" ${off}>Start</button>`}
<button type="button" class="btn sm ghost" data-plug-log aria-expanded="false">Log</button>
<label class="small"><input type="checkbox" data-plug-auto ${s.autostart ? "checked" : ""} ${off}> Start with the Studio</label></div>
<pre class="mono small plug-log" hidden></pre></div>`;
}

export function render(c, canWrite) {
  return `<form class="plug-form" data-plug-form="${esc(c.id)}" novalidate>${c.settings.length ? `<fieldset><legend>Settings</legend>${c.settings.map((f) => field(f, canWrite)).join("")}
<div class="plug-form-actions">${canWrite ? '<button type="submit" class="btn sm primary">Save</button>' : '<small class="muted">Change settings from a browser on the server host.</small>'}${c.can_check ? `<button type="button" class="btn sm" data-plug-check ${canWrite ? "" : "disabled"}>Test</button>` : ""}</div>
<p class="small" role="status" aria-live="polite" data-plug-msg>${c.missing.length ? `Not configured: ${esc(c.missing.join(", "))}.` : ""}</p></fieldset>` : ""}
${c.services.length ? `<fieldset><legend>Background</legend>${c.services.map((s) => service(s, canWrite)).join("")}<small class="muted">Runs as a child of <code>alfrd serve</code> and stops with it.</small></fieldset>` : ""}</form>`;
}

export function attach(ctx, panel, canWrite) {
  if (panels.has(panel)) return panels.get(panel);
  const body = (id) => [...panel.querySelectorAll("[data-plug-config-body]")].find((b) => b.dataset.plugConfigBody === id);
  const button = (id) => [...panel.querySelectorAll("[data-plug-config]")].find((b) => b.dataset.plugConfig === id);
  const say = (id, text, tone = "") => { const m = body(id)?.querySelector("[data-plug-msg]"); if (m) { m.textContent = text; m.className = `small ${tone ? `tone-${tone}` : ""}`; } };
  const show = (id, c, message, tone) => {
    opened.set(id, c);
    const b = body(id);
    if (!b) return;
    b.innerHTML = render(c, canWrite);
    b.hidden = false;
    button(id)?.setAttribute("aria-expanded", "true");
    if (message) say(id, message, tone);
  };
  const load = async (id) => {
    const b = body(id);
    if (b) { b.hidden = false; b.innerHTML = '<p class="muted small">Loading…</p>'; }
    try { show(id, await fetchJSON(`${base(id)}/config`)); } catch (error) { if (b) b.innerHTML = `<p class="callout fail">${esc(error.message)}</p>`; }
  };
  const busy = async (el, work) => { el.disabled = true; try { return await work(); } finally { el.disabled = false; } };
  const api = {
    toggle(id) {
      if (opened.has(id)) { opened.delete(id); const b = body(id); if (b) { b.hidden = true; b.innerHTML = ""; } button(id)?.setAttribute("aria-expanded", "false"); return; }
      load(id);
    },
    restore() { for (const [id, c] of opened) show(id, c); },
  };
  panel.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-plug-form]");
    if (!form) return;
    event.preventDefault();
    const id = form.dataset.plugForm, values = {};
    for (const input of form.querySelectorAll("input[name]")) values[input.name] = input.type === "checkbox" ? input.checked : input.value;
    await busy(form.querySelector("[type=submit]"), async () => {
      try {
        const c = await server.mutate(`${base(id)}/config`, { values });
        show(id, c, c.restarted?.length ? "Saved. Restarted the running service with the new settings." : "Saved.", "ok");
      } catch (error) { say(id, error.message, "fail"); }
    });
  });
  panel.addEventListener("click", async (event) => {
    const el = event.target.closest("[data-plug-clear],[data-plug-check],[data-plug-svc-do],[data-plug-log]");
    const id = el?.closest("[data-plug-form]")?.dataset.plugForm;
    if (!id) return;
    if (el.dataset.plugClear) {
      if (!confirm(`Remove the saved ${el.dataset.plugClear.replace(/_/g, " ")}?`)) return;
      return busy(el, async () => { try { show(id, await server.mutate(`${base(id)}/config`, { values: {}, clear: [el.dataset.plugClear] }), "Removed.", "ok"); } catch (error) { say(id, error.message, "fail"); } });
    }
    if (el.dataset.plugCheck !== undefined) {
      say(id, "Testing…");
      return busy(el, async () => { try { const r = await server.mutate(`${base(id)}/check`); say(id, r.message, r.ok ? "ok" : "fail"); } catch (error) { say(id, error.message, "fail"); } });
    }
    const svc = el.closest("[data-plug-svc]").dataset.plugSvc, path = `${base(id)}/services/${encodeURIComponent(svc)}`;
    if (el.dataset.plugLog !== undefined) {
      const pre = el.closest("[data-plug-svc]").querySelector(".plug-log");
      if (!pre.hidden) { pre.hidden = true; el.setAttribute("aria-expanded", "false"); return; }
      const response = await server.request(`/api${path}/log?lines=60`, { cache: "no-store" });
      pre.textContent = response.ok ? (await response.text()) || "No output yet." : `Couldn’t read the log (${response.status}).`;
      pre.hidden = false; el.setAttribute("aria-expanded", "true"); pre.scrollTop = pre.scrollHeight;
      return;
    }
    await busy(el, async () => {
      try {
        await server.mutate(`${path}/${el.dataset.plugSvcDo}`);
        // A crash right after start shows up on the next look; refresh once it had a moment.
        show(id, await fetchJSON(`${base(id)}/config`));
        setTimeout(() => opened.has(id) && fetchJSON(`${base(id)}/config`).then((c) => show(id, c)).catch(() => {}), 2500);
      } catch (error) { say(id, error.message, "fail"); ctx.toast?.(error.message, "warn"); }
    });
  });
  panel.addEventListener("change", async (event) => {
    const box = event.target.closest("[data-plug-auto]");
    const id = box?.closest("[data-plug-form]")?.dataset.plugForm;
    if (!id) return;
    const svc = box.closest("[data-plug-svc]").dataset.plugSvc;
    try { await server.mutate(`${base(id)}/services/${encodeURIComponent(svc)}/autostart-${box.checked ? "on" : "off"}`); say(id, box.checked ? "Starts with the Studio." : "Won’t start with the Studio.", "ok"); }
    catch (error) { box.checked = !box.checked; say(id, error.message, "fail"); }
  });
  panels.set(panel, api);
  return api;
}
