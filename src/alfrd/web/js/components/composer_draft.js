// Session binding shared by Chat and dock; uploads keep their original model after navigation.
import { mountChatSchedule } from "./chat_schedule.js";
import { scheduleController } from "../data/chat_schedule.js";
import { $ } from "../utils/dom.js";
import { server } from "../data/server.js";
import { sessionDraft } from "../data/chat_draft.js";
import { autogrow, mountComposerAttachments } from "./chat_composer.js";

export function imageWarning(text, settings, probe, scheduled = false) {
  const target = !scheduled && /^@([\w-]+)/.exec(text.trim())?.[1] || settings?.orchestrator;
  const capability = probe?.agents?.[target];
  if (capability?.images === true || capability?.supports_images === true || capability?.capabilities?.images === true) return "";
  return capability?.images === false || capability?.supports_images === false || capability?.capabilities?.images === false
    ? `Images are not delivered to this agent (@${target}).`
    : "Image delivery is unverified for this agent. Images may not reach the model.";
}

export function mountComposerDraft(root, id, { settings, createSession = null, blocked = () => false, onError = () => {} }) {
  const input = $(`#${id}-input`, root), button = $(`#${id}-send`, root), warning = $(`#${id}-image-warning`, root);
  let schedule = null, scheduleUI = null;
  let key = "", model = null, tray = null, unsubscribe = () => {}, probe = null, project = null, session = null;
  const refresh = () => {
    if (model && input.value !== model.read().text) input.value = model.read().text;
    autogrow(input);
    tray?.refresh();
    const draft = model?.read(), sending = !!model?.sending(), uploading = !!model?.busy() && !sending, failed = !!model?.failed(), empty = !draft?.text.trim() && !draft?.refs.length;
    const reason = !session ? "Choose a session to send a message."
      : blocked() || !server.session?.mutations_enabled ? "Sending is unavailable: this session is read-only or not ready."
      : uploading ? "Wait for uploads to finish." : failed ? "Retry or remove failed images before sending." : "";
    button.disabled = !!reason || sending || empty || schedule?.read()?.schedule?.status === "dispatching";
    button.setAttribute("aria-busy", String(sending));
    const label = $("[data-send-label]", button); if (label) label.textContent = sending ? (schedule?.read()?.schedule ? "Adding…" : "Sending…") : schedule?.read()?.schedule ? "Add to queue" : "Send";
    const note = $(`#${id}-send-reason`, root); if (note) note.textContent = reason;
    warning.textContent = [model?.error(), model?.read().refs.length ? imageWarning(input.value, settings(), probe, !!schedule?.read()?.schedule) : ""].filter(Boolean).join(" ");
  };
  return {
    edit(text) { if (model) model.edit(text, model.read().refs); refresh(); },
    select(p, sid) {
      const next = JSON.stringify([p, sid]);
      if (next === key && scheduleUI) { scheduleUI.refresh().catch(() => {}); refresh(); return; }
      unsubscribe(); tray?.destroy(); scheduleUI?.destroy(); scheduleUI = null; schedule = null; tray = null; key = next; project = p; session = sid;
      model = sid ? sessionDraft(p, sid) : null;
      const slot = $(`#${id}-schedule`, root), toggle = $(`#${id}-schedule-toggle`, root);
      if (toggle) { toggle.onclick = null; toggle.setAttribute("aria-pressed", "false"); toggle.setAttribute("aria-expanded", "false"); toggle.disabled = false; }
      const form = $(`#${id}-form`, root); if (form?.dataset) form.dataset.scheduled = "false";
      if (sid && slot) {
        schedule = scheduleController(p, sid);
        scheduleUI = mountChatSchedule(slot, schedule, { toggle, onChange: () => {
          const form = $(`#${id}-form`, root); if (form?.dataset) form.dataset.scheduled = String(!!schedule?.read()?.schedule);
          refresh();
        } });
      } else if (slot) {
        slot.innerHTML = ""; slot.hidden = true;
        if (toggle) {
          toggle.disabled = !createSession || !server.session?.mutations_enabled;
          toggle.onclick = async () => {
            try { await createSession?.(); await scheduleUI?.open(); }
            catch (error) { onError(error.message); }
          };
        }
      }
      input.value = model?.read().text || "";
      if (model) {
        const draft = model, url = `/api/studio/projects/${encodeURIComponent(p)}/chat/sessions/${encodeURIComponent(sid)}`;
        tray = mountComposerAttachments(root, id, { chips: draft.chips, sendButton: null, uploadUrl: `${url}/attachments`,
          csrfToken: server.session?.csrf_token || "", fetchImpl: (...args) => server.request(...args),
          canUpload: () => !!server.session?.mutations_enabled && !draft.sending(),
          onChange: () => draft.sync(), onError: (error) => { if (model === draft) onError(error.message); } });
        unsubscribe = draft.subscribe(refresh);
      }
      probe = null;
      const selected = key;
      server.request(`/api/studio/projects/${encodeURIComponent(p)}/chat/probe`).then(async (res) => {
        const data = res.ok ? await res.json() : null;
        if (key === selected) { probe = data; refresh(); }
      }).catch(() => {});
      refresh();
    },
    async send() {
      if (!model || button.disabled || blocked() || !server.session?.mutations_enabled) return false;
      const draft = model, p = project, sid = session, sendSchedule = schedule, payload = draft.beginSend(sendSchedule?.target() || {});
      if (!payload) return false;
      try {
        const res = await server.request(`/api/studio/projects/${encodeURIComponent(p)}/chat/sessions/${encodeURIComponent(sid)}/messages`, {
          method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": server.session.csrf_token }, body: JSON.stringify(payload) });
        const body = await res.json().catch(() => null);
        if (!res.ok && res.status === 409 && body?.schedule?.id === payload.schedule_id) {
          draft.rebaseSchedule(body.schedule_revision, payload.schedule_id);
          sendSchedule?.accept(body);
        }
        if (!res.ok) throw new Error(body?.error?.message || body?.error || `${res.status} ${res.statusText}`);
        draft.accepted(res.status, payload);
        if (body?.schedule_revision !== undefined) sendSchedule?.accept(body);
        sendSchedule?.refresh().catch(() => {});
        return true;
      } finally { draft.finish(); }
    },
    suspend() { scheduleUI?.destroy(); scheduleUI = null; },
    destroy() { unsubscribe(); tray?.destroy(); scheduleUI?.destroy(); schedule = null; key = ""; model = null; },
  };
}
