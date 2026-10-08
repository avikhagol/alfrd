// Settings → Plugins controller; Browse and job UI load on first use.
import { $, esc, copyText } from "../utils/dom.js";
import { server } from "../data/server.js";
import { reportServerErrors, applyTheme, fetchPlugins } from "./plugin_api.js";
import { restartTitles, key, title, renderPlugins, pluginProblems } from "./settings_plugins_view.js";
export { renderPlugins, pluginProblems } from "./settings_plugins_view.js";
let browse;
const extend = (...args) => (browse ||= import("./plugins_browse.js")).then(m => m.enhance(...args));
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
    if (browse || list.job || server.pluginJob || restartTitles.size) extend(ctx, panel, list, refresh, restartTitles);
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
    if (event.target.closest("[data-plug-open],[data-plug-more]")) return (await extend(ctx, panel, list, refresh, restartTitles)).click(event);
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
  panel.onkeydown = e => {
    if (!browse && e.target.role === "tab" && e.key === "Home") { e.preventDefault(); $("#plug-tab-installed", panel)?.focus(); }
    if (!browse && e.target.role === "tab" && /^(ArrowLeft|ArrowRight|End)$/.test(e.key)) { e.preventDefault(); $("[data-plug-open]", panel)?.click(); }
  };
  const jobEvent = () => panel.isConnected ? extend(ctx, panel, list, refresh, restartTitles) : window.removeEventListener("plugin-job", jobEvent);
  window.addEventListener("plugin-job", jobEvent);
  await refresh();
}
