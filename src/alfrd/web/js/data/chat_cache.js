import { createChatState } from "./chat_state.js";
import { server } from "./server.js";
export const snapshots = createChatState({ request: (...args) => server.request(...args) });
server.onAuthRequired(() => snapshots.clear());
window.addEventListener("alfrd-events", ({ detail }) => {
  for (const ev of detail?.events || []) if (String(ev.plan || "").startsWith("chat-")) snapshots.invalidate(detail.project, String(ev.plan).slice(5));
});

let prefetching = false;
export async function prefetchSnapshot(project, sid) {
  if (prefetching || document.hidden || navigator.connection?.saveData) return;
  prefetching = true;
  try { await snapshots.load(project, sid); } catch {} finally { prefetching = false; }
}

window.addEventListener("alfrd-chat-resync", ({ detail }) => {
  const projects = detail?.project ? [detail.project] : [...new Set([...snapshots.cache.values()].map((e) => e.project))];
  for (const p of projects) snapshots.invalidate(p, null, { reset: true });
});
