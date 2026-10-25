import { server } from "../data/server.js";

// Each graph consumer supplies its own session state and navigation.
export function taskMenuApi(opts) {
  const path = (p, sid, row) => `/studio/projects/${encodeURIComponent(p)}/chat/sessions/${encodeURIComponent(sid)}/tasks/${encodeURIComponent(row)}`;
  return {
    target(row) {
      const t = opts.state()?.tasks?.find((t) => t.row === row);
      if (!t) return null;
      const last = t.logs?.at(-1);
      return { ...t, project: opts.project(), sid: opts.sid(), context: `${opts.project()}/${opts.sid()}`,
        prompt: Boolean(last?.handoff), reply: Boolean(last?.handoff),
        log: t.logs?.findLast((l) => l.log)?.log, logs: (t.logs || []).filter((l) => l.log), handoff: last?.handoff, files: t.result?.artifacts };
    },
    async preview(row) {
      const res = await server.request(`/api${path(opts.project(), opts.sid(), row)}/retry-preview`);
      const body = await res.json();
      if (!res.ok) throw new Error(body?.error?.message || `Preview: ${res.status}`);
      return body;
    },
    refresh: opts.refresh,
    async run(action, target) {
      if (action === "retry" || action === "cancel") {
        await server.mutate(`${path(target.project, target.sid, target.row)}/${action}`, {
          expected_status: target.status, expected_unit: target.current_unit,
        });
        await opts.refresh();
      } else if (action === "copy_id") await navigator.clipboard.writeText(target.row);
      else if (action === "log") await opts.openLog(target.log);
      else if (action === "prompt" || action === "reply") await opts.openLog(`${target.handoff}/${action === "prompt" ? "prompt" : "response"}.md`);
      else await opts.files(target.row);
    },
  };
}
