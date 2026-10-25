// Session binding shared by Chat and dock; uploads keep their original model after navigation.
import { $ } from "../utils/dom.js";
import { server } from "../data/server.js";
import { sessionDraft } from "../data/chat_draft.js";
import { mountComposerAttachments } from "./chat_composer.js";

export function imageWarning(text, settings, probe) {
  const target = /^@([\w-]+)/.exec(text.trim())?.[1] || settings?.orchestrator;
  const capability = probe?.agents?.[target];
  if (capability?.images === true || capability?.supports_images === true || capability?.capabilities?.images === true) return "";
  return capability?.images === false || capability?.supports_images === false || capability?.capabilities?.images === false
    ? `Images are not delivered to this agent (@${target}).`
    : "Image delivery is unverified for this agent. Images may not reach the model.";
}

export function mountComposerDraft(root, id, { settings, blocked = () => false, onError = () => {} }) {
  const input = $(`#${id}-input`, root), button = $(`#${id}-send`, root), warning = $(`#${id}-image-warning`, root);
  let key = "", model = null, tray = null, unsubscribe = () => {}, probe = null, project = null, session = null;
  const refresh = () => {
    if (model && input.value !== model.read().text) input.value = model.read().text;
    tray?.refresh();
    button.disabled = !session || blocked() || !server.session?.mutations_enabled || !!model?.busy();
    button.setAttribute("aria-busy", String(!!model?.busy()));
    warning.textContent = [model?.error(), model?.read().refs.length ? imageWarning(input.value, settings(), probe) : ""].filter(Boolean).join(" ");
  };
  return {
    edit(text) { if (model) model.edit(text, model.read().refs); refresh(); },
    select(p, sid) {
      const next = JSON.stringify([p, sid]);
      if (next === key) { refresh(); return; }
      unsubscribe(); tray?.destroy(); tray = null; key = next; project = p; session = sid;
      model = sid ? sessionDraft(p, sid) : null;
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
      const draft = model, p = project, sid = session, payload = draft.beginSend();
      if (!payload) return false;
      try {
        const res = await server.request(`/api/studio/projects/${encodeURIComponent(p)}/chat/sessions/${encodeURIComponent(sid)}/messages`, {
          method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": server.session.csrf_token }, body: JSON.stringify(payload) });
        const body = await res.json().catch(() => null);
        if (!res.ok) throw new Error(body?.error?.message || body?.error || `${res.status} ${res.statusText}`);
        draft.accepted(res.status, payload);
        if (res.status !== 201 && model === draft) onError("Message already recorded; draft retained. Edit it to send a new message.");
        return true;
      } finally { draft.finish(); }
    },
    destroy() { unsubscribe(); tray?.destroy(); key = ""; model = null; },
  };
}
