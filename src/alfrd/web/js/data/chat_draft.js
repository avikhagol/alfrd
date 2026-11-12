import { attachmentPrompt, textBytes, MAX_TEXT_ATTACHMENT_BYTES } from "./text_attachments.js";
// One draft per project/session, shared by both composers. Accepted image refs and text chips survive reload.
const drafts = new Map();

export function sessionDraft(project, session) {
  const key = `alfrd:chat:draft:${JSON.stringify([project, session])}`;
  if (drafts.has(key)) return drafts.get(key);
  let value = { text: "", refs: [], pending: null }, storageError = "";
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || "null");
    if (saved && typeof saved.text === "string" && Array.isArray(saved.refs) && saved.refs.every((ref) => /^att_[a-f0-9]{16}$/.test(ref))) {
      const pending = saved.pending;
      value = { text: saved.text, refs: saved.refs, texts: (saved.texts || []).filter(c => c.kind === "text" && typeof c.text === "string" && typeof c.name === "string" && typeof c.id === "string" && textBytes(c.text) <= MAX_TEXT_ATTACHMENT_BYTES), pending: typeof pending?.content === "string" && typeof pending?.client_message_id === "string" ? pending : null };
    }
  } catch { storageError = "Draft recovery is unavailable in this browser session."; }
  const listeners = new Set(), chips = new Map(value.refs.map((ref) => [ref, { id: ref, ref, name: "Image", status: "done" }]));
  for (const chip of value.texts || []) chips.set(chip.id, chip);
  const content = () => JSON.stringify({ text: attachmentPrompt(value.text, chips), attachments: value.refs });
  let sending = false;
  const persist = () => {
    try { sessionStorage.setItem(key, JSON.stringify(value)); storageError = ""; }
    catch { storageError = "Draft kept in memory; browser session storage is full or unavailable. It will not survive a reload."; }
    for (const listener of listeners) listener(value, storageError);
  };
  const model = {
    chips,
    sending: () => sending,
    read: () => value,
    busy: () => sending || [...chips.values()].some((c) => c.status === "uploading"),
    failed: () => [...chips.values()].some((c) => c.status === "error"),
    sync() { value = { ...value, texts: [...chips.values()].filter(c => c.kind === "text"), refs: [...chips.values()].filter((c) => c.status === "done" && c.ref).map((c) => c.ref) }; persist(); },
    finish() { sending = false; persist(); },
    error: () => storageError,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    edit(text, refs) { value = { ...value, text, refs: [...refs] }; persist(); },
    beginSend(target = {}) {
      if (model.busy() || model.failed()) return null; // failed files must be retried or removed first
      const text = attachmentPrompt(value.text, chips), attachments = [...value.refs];
      if (!text && !attachments.length) return null;
      if (text.length > 20000) throw new Error("Message with text attachments exceeds 20,000 characters. Remove a file or shorten the message.");
      const content = JSON.stringify({ text, attachments });
      if (value.pending?.content !== content) {
        value.pending = { content, target, client_message_id: crypto.randomUUID() };
        persist();
      }
      sending = true; persist();
      return { text, attachments, ...value.pending.target, client_message_id: value.pending.client_message_id };
    },
    rebaseSchedule(revision, scheduleId) {
      if (value.pending?.target?.schedule_id === scheduleId) {
        value.pending.target.expected_schedule_revision = revision; persist();
      }
    },
    accepted(status, payload) {
      if (![200, 201].includes(status) || value.pending?.client_message_id !== payload.client_message_id) return;
      if (content() === value.pending.content) { value = { text: "", refs: [], pending: null }; chips.clear(); }
      else value = { ...value, pending: null };
      persist();
    },
  };
  drafts.set(key, model);
  return model;
}
