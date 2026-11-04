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
        prompt: false, reply: false,
        log: t.logs?.findLast((l) => l.log)?.log, logs: (t.logs || []).filter((l) => l.log), handoff: last?.handoff, files: opts.files ? t.result?.artifacts : [], can_files: Boolean(opts.files) };
    },
    async preview(row) {
      const url = `/api${path(opts.project(), opts.sid(), row)}`;
      const read = async (suffix) => {
        const res = await server.request(`${url}/${suffix}`);
        const body = await res.json();
        if (!res.ok) throw new Error(body?.error?.message || `Preview: ${res.status}`);
        return body;
      };
      const [retry, transcript] = await Promise.allSettled([read("retry-preview"), read("transcript")]);
      const latest = (transcript.status === "fulfilled" ? transcript.value.attempts : []).at(-1);
      return { ...(retry.status === "fulfilled" ? retry.value : { eligible: false }),
        preview_error: [retry, transcript].filter((r) => r.status === "rejected").map((r) => r.reason.message).join("; "),
        mutation_ready: retry.status === "fulfilled" && Boolean(server.session?.mutations_enabled), prompt: Boolean(latest?.prompt_bytes), reply: Boolean(latest?.response_bytes),
        transcript_unit: latest?.unit };
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
      else if (action === "prompt" || action === "reply") {
        const part = action === "prompt" ? "prompt" : "response";
        const url = `/api${path(target.project, target.sid, target.row)}/transcript/${encodeURIComponent(target.transcript_unit)}/${part}`;
        let content = "", offset = 0;
        while (true) {
          const res = await server.request(`${url}?offset=${offset}`);
          const page = await res.json();
          if (!res.ok) throw new Error(page?.error?.message || `Transcript: ${res.status}`);
          content += page.content;
          if (page.eof) break;
          if (!page.length) throw new Error("Transcript read made no progress");
          offset = page.offset + page.length;
        }
        await opts.openMarkdown(`${part}.md`, content);
      } else if (action === "files") await opts.files(target.row);
      else throw new Error(`Unknown task action: ${action}`);
    },
  };
}
