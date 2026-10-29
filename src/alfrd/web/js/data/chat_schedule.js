import { server } from "./server.js";

export const pendingItems = (batch) => (batch?.items || []).filter((i) => !i.removed_at);
export function scheduleSummary(batch) {
  if (!batch) return "";
  const count = pendingItems(batch).length;
  return `${batch.status} · ${count} item${count === 1 ? "" : "s"} · ${new Date(batch.run_at).toLocaleString(undefined, { timeZone: batch.timezone })} (${batch.timezone})`;
}
export function scheduleCountdown(batch, now = Date.now()) {
  if (!batch) return "";
  const seconds = Math.ceil((Date.parse(batch.run_at) - now) / 1000);
  if (seconds <= 0) return batch.hold_reason ? `Due · ${batch.hold_reason.replaceAll("_", " ")} ${(batch.waiting_on_requests || []).join(", ")}` : "Due; waiting for server";
  return `In ${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m ${seconds % 60}s`;
}
// Resolve a wall time through its IANA zone. Multiple candidates require an explicit offset choice.
export function timeCandidates(local, zone) {
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(local)) throw new Error("Choose a date and time.");
  const target = local + ":00", naive = Date.parse(target + "Z");
  const format = new Intl.DateTimeFormat("sv-SE", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  const wall = (ms) => format.format(new Date(ms)).replace(" ", "T");
  const offsets = new Set();
  for (let hour = -36; hour <= 36; hour += 6) {
    const instant = naive + hour * 3600000;
    offsets.add(Date.parse(wall(instant) + "Z") - instant);
  }
  return [...offsets].map((offset) => ({ instant: naive - offset, offset })).filter((v) => wall(v.instant) === target).sort((a, b) => a.instant - b.instant);
}
export function schedulePayload(local, zone, choice = null) {
  const candidates = timeCandidates(local, zone);
  if (!candidates.length) throw new Error("This local time does not exist because the clock changes. Choose another time.");
  if (candidates.length > 1 && choice === null) throw new Error("This local time occurs twice. Choose its UTC offset.");
  const selected = candidates[choice === null ? 0 : Number(choice)];
  if (!selected) throw new Error("Choose a UTC offset.");
  return { run_at: new Date(selected.instant).toISOString(), timezone: zone };
}

const controllers = new Map();
export function scheduleController(project, sid) {
  const key = JSON.stringify([project, sid]);
  if (controllers.has(key)) return controllers.get(key);
  const base = `/api/studio/projects/${encodeURIComponent(project)}/chat/sessions/${encodeURIComponent(sid)}/schedule`;
  const listeners = new Set(), attempts = new Map();
  let state = null, received = 0, timer = null, serial = Promise.resolve();
  const publish = (body) => { if (!state || body.schedule_revision >= state.schedule_revision) { state = body; received = performance.now(); for (const fn of listeners) fn(state); } };
  const focus = () => controller.refresh().catch(() => {});
  const controller = {
    read: () => state,
    now: () => state ? Date.parse(state.server_now) + performance.now() - received : Date.now(),
    target: () => state?.schedule ? { schedule_id: state.schedule.id, expected_schedule_revision: state.schedule_revision } : {},
    accept: publish,
    async refresh() { const res = await server.request(base); if (!res.ok) throw new Error("Could not load schedule."); const body = await res.json(); if (body.schedule_revision !== undefined) publish(body); return state; },
    mutate(action, payload = {}, item = null) {
      const job = async () => {
        const signature = JSON.stringify([action, payload, item]);
        let attempt = attempts.get(signature);
        if (!attempt) {
          const body = { ...payload, expected_schedule_revision: state?.schedule_revision || 0,
            ...(action === "create" ? { client_schedule_id: crypto.randomUUID() } : { operation_id: crypto.randomUUID() }) };
          const suffix = action === "create" ? "" : `/${state?.schedule?.id}${item ? `/items/${item}` : action === "time" ? "" : `/${action}`}`;
        attempt = { body, suffix }; attempts.set(signature, attempt);
        }
        const { body, suffix } = attempt;
        const method = action === "remove" ? "DELETE" : ["edit", "time"].includes(action) ? "PATCH" : "POST";
        const res = await server.request(base + suffix, { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": server.session?.csrf_token || "" }, body: JSON.stringify(body) });
        const result = await res.json();
        if (result.schedule_revision !== undefined) publish(result);
        if (!res.ok) { if (res.status === 409) attempts.delete(signature); throw new Error(result.error?.message || "Schedule could not be saved."); }
        attempts.delete(signature); return result;
      };
      const result = serial.then(job); serial = result.catch(() => {}); return result;
    },
    subscribe(fn) {
      listeners.add(fn); if (state) fn(state);
      if (!timer) { timer = setInterval(focus, 15000); timer.unref?.(); globalThis.addEventListener?.("focus", focus); }
      focus();
      return () => { listeners.delete(fn); if (!listeners.size) { clearInterval(timer); timer = null; globalThis.removeEventListener?.("focus", focus); } };
    },
  };
  controllers.set(key, controller); return controller;
}
