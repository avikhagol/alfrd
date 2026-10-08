// Loaded only when Settings → Plugins is selected. Restart state survives dialog reopen.
import { $, esc, icon, copyText } from "../utils/dom.js";
import { server } from "../data/server.js";
import { pluginErrors, reportServerErrors, applyTheme, fetchPlugins } from "./plugin_api.js";

const restartTitles = new Set();
const problem = (p) => !["ok", "disabled"].includes(p.status);
const failed = (p, errors = pluginErrors) => problem(p) || errors.some((e) => e.id === p.id);
const key = (id) => encodeURIComponent(id);
const title = (p) => p.title || p.id;
const status = (p, errors) => !p.enabled ? ["Off", "muted"] : failed(p, errors) ? ["Error", "fail"] : p.active ? ["Active", "ok"] : ["Restart needed", "warn"];

export function pluginProblems(list, errors = pluginErrors) {
  return [...(list.plugins || []).filter(problem), ...errors.map((p) => ({ ...(list.plugins || []).find((entry) => entry.id === p.id), ...p, status: "browser", error: `browser: ${p.message}` }))];
}

export function renderPlugins(list, canWrite, errors = pluginErrors) {
  const problems = pluginProblems(list, errors);
  return `<div class="set-sec-h"><h3>${icon("metadata")} Plugins</h3><span class="grow"></span><button type="button" class="btn sm" data-plug-refresh>${icon("sync")} Refresh</button></div>
    <div data-plug-banner>${list.safe_mode ? '<p class="callout warn" role="status"><b>Safe mode</b>: plugins are not loaded (<code>--safe-mode</code> / <code>ALFRD_NO_PLUGINS</code>).</p>' : ""}${restartTitles.size ? `<p class="callout warn" role="status"><b>Restart <code>alfrd serve</code> to finish.</b> ${esc([...restartTitles].join(", "))} changes take effect after a restart.</p>` : ""}</div>
    ${problems.length ? `<details class="plug-diag" open><summary>${icon("alert")} <b>Diagnostics</b> · ${problems.length} problem${problems.length === 1 ? "" : "s"}</summary>${problems.map((p, i) => `<div class="plug-diag-row" data-plug-diag="${esc(p.id)}"><b>${esc(title(p))}</b><p class="mono small">${esc(p.error || p.status)}</p>${p.missing_bin?.length ? `<p class="small">Install <code>${esc(p.missing_bin.join(", "))}</code> and restart <code>alfrd serve</code>.</p>` : ""}${p.traceback_tail ? `<details><summary>Traceback</summary><pre class="mono small">${esc(p.traceback_tail)}</pre></details>` : ""}<button type="button" class="btn sm" data-plug-copy="${i}">Copy details</button></div>`).join("")}</details>` : ""}
    <fieldset class="plug-themes"><legend>Theme</legend><div class="plug-theme-grid">${(list.themes || []).map((t) => `<label class="plug-theme"><input type="radio" name="plug-theme" value="${esc(t.id)}" ${t.id === list.theme ? "checked" : ""} ${canWrite ? "" : "disabled"}><span class="plug-swatch ${t.source === "builtin" ? "plug-swatch-builtin" : ""}" aria-hidden="true"></span><span>${esc(title(t))}<small class="muted">${esc(t.source === "plugin" ? `from plugin ${t.id}` : t.source === "builtin" ? "built-in" : "drop-in")}</small></span><span class="plug-theme-check" aria-hidden="true">${icon("check")}</span></label>`).join("")}</div>${canWrite ? "" : '<small class="muted">Change themes from a browser on the server host.</small>'}</fieldset>
    ${(list.plugins || []).length ? `<ul class="plug-list">${list.plugins.map((p) => { const [label, tone] = status(p, errors); return `<li class="plug-row"><input type="checkbox" role="switch" class="plug-switch" data-plug-toggle="${esc(p.id)}" aria-label="Enable ${esc(title(p))}" aria-describedby="plug-status-${key(p.id)}" ${p.enabled ? "checked" : ""} ${canWrite && !list.safe_mode ? "" : "disabled"}><div><b>${esc(title(p))}</b> <small class="muted tabular">v${esc(p.version || "")}</small><div class="plug-kinds">${(p.kinds || []).map((kind) => `<span class="badge tone-muted">${esc(kind)}</span>`).join(" ")}</div><small class="muted plug-description">${esc(p.description || "")}</small></div>${failed(p, errors) ? `<button type="button" data-plug-error="${esc(p.id)}"` : "<span"} id="plug-status-${key(p.id)}" class="badge tone-${tone}">${label}${failed(p, errors) ? "</button>" : "</span>"}</li>`; }).join("")}</ul>` : '<div class="empty">No plugins installed.<p><code>alfrd plugin install &lt;wheel or folder&gt;</code> · <a href="https://github.com/avikhagol/alfrd/blob/HEAD/docs/plugins.md" target="_blank" rel="noopener">Learn more</a></p></div>'}
    <p class="sr-only" role="status" aria-live="polite" data-plug-live></p>`;
}

export async function mountPlugins(ctx, root) {
  const panel = $("#set-panel-plugins", root);
  if (!panel) return;
  if (ctx.state.mode !== "server" || ctx.state.demoEnabled || !server.session || server.authRequired) {
    panel.innerHTML = '<p class="muted">Plugins run in <code>alfrd serve</code>. No ALFRD server is connected.</p>';
    return;
  }
  let list, busy = false;
  const canWrite = Boolean(server.session.mutations_enabled);
  const announce = (text) => { const live = $("[data-plug-live]", panel); if (live) live.textContent = text; };
  const draw = (message = "", focus = null) => {
    panel.innerHTML = renderPlugins(list, canWrite);
    if (busy) panel.querySelectorAll("button,input").forEach((b) => { b.disabled = true; });
    announce(message);
    if (focus) [...panel.querySelectorAll("input")].find((b) => b.dataset.plugToggle === focus || b.value === focus)?.focus();
  };
  const refresh = async () => {
    if (busy) return;
    busy = true;
    $("[data-plug-refresh]", panel)?.setAttribute("disabled", "");
    try {
      list = await fetchPlugins();
      reportServerErrors(list, ctx);
      busy = false;
      draw("Plugins refreshed.");
    } catch (error) {
      busy = false;
      panel.innerHTML = `<p class="callout fail" role="alert">Couldn’t load plugins. ${esc(error.message)} <button type="button" class="btn sm" data-plug-refresh>Retry</button></p><p class="sr-only" role="status" aria-live="polite" data-plug-live></p>`;
      announce(`Couldn’t load plugins. ${error.message}`);
    }
  };
  panel.onclick = async (event) => {
    if (event.target.closest("[data-plug-refresh]")) return refresh();
    const errorButton = event.target.closest("[data-plug-error]");
    if (errorButton) {
      const diag = $(".plug-diag", panel);
      if (diag) diag.open = true;
      [...panel.querySelectorAll("[data-plug-diag]")].find((row) => row.dataset.plugDiag === errorButton.dataset.plugError)?.scrollIntoView({ block: "nearest" });
    }
    const copy = event.target.closest("[data-plug-copy]");
    if (copy) {
      const p = pluginProblems(list)[Number(copy.dataset.plugCopy)];
      const ok = await copyText([p.id, p.version || "", p.status, p.error || "", p.traceback_tail || ""].join("\n"));
      ctx.toast(ok ? "Copied" : "Couldn’t copy details", ok ? "ok" : "warn");
    }
  };
  panel.onchange = async (event) => {
    const input = event.target;
    if (busy || !canWrite || server.authRequired) return;
    const id = input.dataset.plugToggle;
    if (id) {
      if (list.safe_mode) return;
      const p = list.plugins.find((p) => p.id === id);
      if (!p) return;
      const before = { ...p }, enabled = input.checked;
      Object.assign(p, { enabled, active: enabled && before.status === "ok" && !before.has_python });
      busy = true; draw(`${title(p)} turning ${enabled ? "on" : "off"}.`);
      let message;
      try {
        const result = await server.mutate(`/studio/plugins/${key(id)}/${enabled ? "enable" : "disable"}`);
        Object.assign(p, result.plugin);
        if (result.restart_required) restartTitles.add(title(p));
        message = `${title(p)} turned ${enabled ? "on" : "off"}.`;
        if (!result.restart_required && before.web) ctx.toast(`${message} Reload the Studio to apply.`, "ok", { link: location.href, linkLabel: "Reload" });
      } catch (error) {
        Object.assign(p, before);
        message = error.message; ctx.toast(message, "warn");
      }
      busy = false; draw(message, id);
    } else if (input.name === "plug-theme") {
      const previous = list.theme, next = input.value;
      const undo = applyTheme(list, next);
      list.theme = next; busy = true; draw("Saving theme…");
      let message;
      try {
        await server.mutate("/studio/theme", { id: next });
        // Re-fetch the base stylesheet after the server persists its selection.
        applyTheme(list, next);
        message = "Theme saved.";
      } catch (error) {
        undo(); list.theme = previous;
        message = `Theme not saved: ${error.message}`; ctx.toast(message, "warn");
      }
      busy = false; draw(message, list.theme);
    }
  };
  await refresh();
}
