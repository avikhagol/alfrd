// Presence: tells `alfrd serve` that someone is using the Studio, so notifiers with
// "mute while active" (Telegram) stay quiet. At most one POST a minute, only while the
// tab is visible and the user clicks, types or scrolls; errors are ignored.

import { server } from "./server.js";

const EVERY_MS = 60000;

export function trackPresence({ enabled = () => true, now = () => Date.now(), target = document } = {}) {
  let last = 0;
  const ping = () => {
    if (target.visibilityState === "hidden" || !enabled()) return;
    const t = now();
    if (t - last < EVERY_MS) return;
    last = t;
    if (!server.session?.mutations_enabled || server.authRequired) return;
    server.mutate("/studio/presence", {}).catch(() => {});
  };
  for (const name of ["pointerdown", "keydown", "wheel", "visibilitychange"]) {
    target.addEventListener(name, ping, { passive: true, capture: true });
  }
  return ping;
}
