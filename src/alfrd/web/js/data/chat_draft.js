// One draft per project/session, shared by both composers. Only accepted refs survive reload.
const drafts = new Map();

export function sessionDraft(project, session) {
  const key = `alfrd:chat:draft:${JSON.stringify([project, session])}`;
  if (drafts.has(key)) return drafts.get(key);
  let value = { text: "", refs: [], pending: null }, storageError = "";
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || "null");
    if (saved && typeof saved.text === "string" && Array.isArray(saved.refs) && saved.refs.every((ref) => /^att_[a-f0-9]{16}$/.test(ref))) {
      const pending = saved.pending;
      value = { text: saved.text, refs: saved.refs, pending: typeof pending?.content === "string" && typeof pending?.client_message_id === "string" ? pending : null };
    }
  } catch { storageError = "Draft recovery is unavailable in this browser session."; }
  const listeners = new Set(), chips = new Map(value.refs.map((ref) => [ref, { id: ref, ref, name: "Image", status: "done" }]));
  let sending = false;
  const persist = () => {
    try { sessionStorage.setItem(key, JSON.stringify(value)); storageError = ""; }
    catch { storageError = "Draft kept in memory; browser session storage is unavailable."; }
    for (const listener of listeners) listener(value, storageError);
  };
  const model = {
    chips,
    sending: () => sending,
    read: () => value,
    busy: () => sending || [...chips.values()].some((c) => c.status === "uploading"),
    failed: () => [...chips.values()].some((c) => c.status === "error"),
    sync() { value = { ...value, refs: [...chips.values()].filter((c) => c.status === "done" && c.ref).map((c) => c.ref) }; persist(); },
    finish() { sending = false; persist(); },
    error: () => storageError,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    edit(text, refs) { value = { ...value, text, refs: [...refs] }; persist(); },
    beginSend() {
      if (model.busy() || model.failed()) return null; // failed files must be retried or removed first
      const text = value.text.trim(), attachments = [...value.refs];
      if (!text && !attachments.length) return null;
      const content = JSON.stringify({ text, attachments });
      if (value.pending?.content !== content) {
        value.pending = { content, client_message_id: crypto.randomUUID() };
        persist();
      }
      sending = true; persist();
      return { text, attachments, client_message_id: value.pending.client_message_id };
    },
    accepted(status, payload) {
      if (status !== 201 || value.pending?.client_message_id !== payload.client_message_id) return;
      if (JSON.stringify({ text: value.text.trim(), attachments: value.refs }) === value.pending.content) { value = { text: "", refs: [], pending: null }; chips.clear(); }
      else value = { ...value, pending: null };
      persist();
    },
  };
  drafts.set(key, model);
  return model;
}
