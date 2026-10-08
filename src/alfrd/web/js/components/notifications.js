import { $, esc, icon, loadUi, saveUi } from "../utils/dom.js";
import { server } from "../data/server.js";

const DEFAULT_KINDS = ["review.pending", "plan.failed", "plan.finished", "turn.idle"];
const browserPrefs = loadUi("notifications", { browser: false });
export const browserEnabled = () => browserPrefs.browser;
export function browserState() {
  if (typeof Notification !== "function" || globalThis.isSecureContext === false) return "unsupported";
  return Notification.permission;
}
export async function setBrowserNotifications(on) {
  let permission = browserState();
  if (on && permission === "default") permission = await Notification.requestPermission();
  const enabled = Boolean(on && permission === "granted");
  browserPrefs.browser = enabled;
  saveUi("notifications", browserPrefs, ["browser"]);
  return enabled;
}
export function matchesKind(pattern, kind) {
  return pattern === "*" || pattern === kind || (pattern.endsWith(".*") && kind.startsWith(pattern.slice(0, -1)));
}
export async function handleNotifications(ctx, project, events) {
  const data = await server.notificationRoutes(project);
  const patterns = data.routes.length ? data.routes.flatMap((r) => r.on) : DEFAULT_KINDS;
  for (const event of events) {
    const title = `${event.kind.replaceAll(".", " ")}${event.target ? ` in ${event.target}` : ""}`;
    const sticky = event.kind === "review.pending" || event.kind.endsWith(".failed");
    const link = event.link?.slice(event.link.indexOf("#"));
    if (patterns.some((p) => matchesKind(p, event.kind))) ctx.toast(title, sticky ? "warn" : "info", { sticky, link });
    if (browserEnabled() && browserState() === "granted" && DEFAULT_KINDS.includes(event.kind)) {
      try {
        const notification = new Notification(title, { body: event.turn ? `Turn ${event.turn}/${event.turns || "?"}` : "ALFRD", tag: `${event.plan}:${event.kind}`, silent: true });
        notification.onclick = () => { window.focus(); if (link) window.location.hash = link; notification.close(); };
      } catch (error) { ctx.log?.("warn", `Browser notification: ${error.message}`, "studio"); }
    }
  }
}
export function notificationPanel(ctx) {
  const projects = ctx.projects();
  const picked = projects.some((p) => p.id === ctx.state.selectedProject) ? ctx.state.selectedProject : projects[0]?.id;
  return `<h3>This browser</h3><label class="check"><input type="checkbox" role="switch" data-notify-browser><span>Show browser notifications</span></label><p class="muted small" data-notify-hint></p>
    <h3>Routes</h3>${ctx.state.mode === "server" ? `<label class="field"><span>Project</span><select class="input" data-notify-project>${projects.map((p) => `<option value="${esc(p.id)}" ${p.id === picked ? "selected" : ""}>${esc(ctx.projectName(p.id))}</option>`).join("")}</select></label><div data-notify-routes aria-live="polite">Loading routes…</div>` : '<p class="muted">Connect to an ALFRD server to see notification routes.</p>'}
    <p class="muted small">Routes are read when a run starts. Edits apply to runs started afterwards.</p>`;
}
export function mountNotifications(ctx, root) {
  const toggle = $("[data-notify-browser]", root), hint = $("[data-notify-hint]", root);
  const drawToggle = () => {
    const permission = browserState();
    if (permission === "denied" && browserPrefs.browser) {
      browserPrefs.browser = false; saveUi("notifications", browserPrefs, ["browser"]);
    }
    toggle.disabled = permission === "unsupported";
    toggle.checked = permission === "granted" && browserEnabled();
    hint.className = permission === "denied" ? "callout warn small" : "muted small";
    hint.textContent = permission === "unsupported" ? "This browser can't show notifications here" : permission === "denied" ? "Notifications are blocked for this site. Allow them in the browser's site settings, then turn this on again." : toggle.checked ? "On for this browser" : "Reviews pending, failed or finished runs and idle turns, while a Studio tab is open.";
  };
  drawToggle();
  toggle.addEventListener("change", async () => {
    toggle.disabled = true;
    try { await setBrowserNotifications(toggle.checked); } catch (error) { ctx.toast(error.message, "warn"); }
    drawToggle(); toggle.disabled = browserState() === "unsupported";
  });
  const select = $("[data-notify-project]", root), table = $("[data-notify-routes]", root);
  if (!select || !table) return;
  let generation = 0;
  const draw = async () => {
    const gen = ++generation, project = select.value;
    if (!project) { table.textContent = "Select a connected project to see routes."; return; }
    table.textContent = "Loading routes…";
    try {
      const data = await server.notificationRoutes(project);
      if (gen !== generation) return;
      table.innerHTML = `${data.warnings.length ? `<div class="callout warn">${data.warnings.map(esc).join("<br>")}</div>` : ""}${data.routes.length ? `<div class="grid-scroll"><table class="tbl small"><caption class="sr-only">Notification routes</caption><thead><tr><th>Source</th><th>Notifier</th><th>Events</th><th>Options</th><th>Test</th></tr></thead><tbody>${data.routes.map((r) => `<tr><td><span class="chip">${r.source === "project" ? "Project" : "User"}</span></td><td>${esc(r.via)}</td><td>${r.on.map((p) => `<span class="chip-code">${esc(p)}</span>`).join(" ")}</td><td class="mono">${esc(Object.entries(r.options).map(([k, v]) => `${k}: ${v}`).join(" · "))}</td><td><button class="btn sm" data-notify-test="${r.index}" aria-label="Send test to route ${r.index + 1} (${esc(r.via)})" ${server.session?.mutations_enabled ? "" : 'disabled title="Only from a browser on the same machine as alfrd serve"'}>Send test</button><span class="small" data-notify-result aria-live="polite"></span></td></tr>`).join("")}</tbody></table></div>` : `<p>No notification routes yet.</p><p class="chip-code">${esc(data.user_file)}</p><pre class="code">${esc('{"routes": [{"via": "desktop", "on": ["review.pending", "plan.*", "turn.idle"]}]}')}</pre><p class="small">Configuration guide: <span class="chip-code">docs/notifications.md</span></p>`}`;
      table.querySelectorAll("[data-notify-test]").forEach((button) => button.addEventListener("click", async () => {
        button.disabled = true; button.innerHTML = `${icon("sync", "spin")} Sending…`;
        const result = button.parentElement.querySelector("[data-notify-result]");
        result.textContent = "";
        try {
          const response = await server.notificationTest(project, Number(button.dataset.notifyTest));
          result.textContent = response.ok ? "Sent" : response.skipped ? `Skipped: ${response.skipped}` : `Failed: ${response.error}`;
          result.className = `small tone-${response.ok ? "ok" : response.skipped ? "warn" : "fail"}`;
        } catch (error) { result.textContent = `Failed: ${error.message}`; result.className = "small tone-fail"; }
        finally { button.textContent = "Send test"; button.disabled = !server.session?.mutations_enabled; }
      }));
    } catch (error) { if (gen === generation) table.textContent = `Couldn't load routes: ${error.message}`; }
  };
  select.addEventListener("change", draw); draw();
}
