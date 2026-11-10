// Re-read the superuser part of the session (hidden sections, locks, experimental features, live
// interval) after a save on #/superuser, or when the tab comes back (at most every 10 s). Lazy.
import { server } from "./server.js";

let at = 0;

export async function refreshSession(ctx, { views, shown, chatOn, goTo, render, throttle = false }) {
  if (ctx.state.mode !== "server" || !server.session || (throttle && Date.now() - at < 10000)) return;
  at = Date.now();
  const res = await server.request("/api/studio/session", { headers: { Accept: "application/json" } });
  if (!res.ok) return;
  const fresh = await res.json();
  for (const k of ["experimental", "superuser", "can_superuser", "live"]) server.session[k] = fresh[k];
  if (!views.some((v) => v.id === ctx.state.view && shown(v.id) && (v.id !== "chat" || chatOn()))) goTo("overview");
  render();
}
