// Composer attachments: deliberately mountable so Chat and the dock can opt in separately.
import { sourceHTML } from "../utils/refresh.js";
import { esc, icon } from "../utils/dom.js";
import { error as stateError } from "./state_view.js";

import { longPaste, lineCount, textBytes, validateText, readTextFile, MAX_TEXT_DRAFT_BYTES } from "../data/text_attachments.js";

export const MAX_CLIENT_EDGE = 2000;

const imageType = (file) => /^image\/(png|jpe?g|webp)$/i.test(file?.type || "");

/** Decode and re-encode an image. Re-encoding is intentional: it drops EXIF metadata. */
export async function downscaleImage(file, { maxEdge = MAX_CLIENT_EDGE, quality = 0.88 } = {}) {
  if (!imageType(file)) throw new Error("Choose a PNG, JPEG, or WebP image.");
  const bitmap = typeof createImageBitmap === "function" ? await createImageBitmap(file) : await decodeImage(file);
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const context = canvas.getContext("2d");
  context.drawImage(bitmap, 0, 0, width, height);
  bitmap.close?.();
  const blob = await new Promise((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error("Could not prepare image")), "image/jpeg", quality));
  return new File([blob], (file.name || "image").replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg", lastModified: Date.now() });
}

function decodeImage(file) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = () => { URL.revokeObjectURL?.(url); resolve(image); };
    image.onerror = () => reject(new Error("Could not read image"));
    image.src = url;
  });
}

export function attachmentButtonHtml(id) {
  return `<button type="button" class="icon-btn chat-attach" id="${esc(id)}-attach" title="Attach images or text files" aria-label="Attach images or text files">${icon("plus")}</button>`;
}

export function attachmentTrayHtml(id) {
  return `<section class="attach-tray" id="${esc(id)}-attachments" aria-label="Attachments">
    <div class="attach-chips" data-attach-chips aria-live="polite"></div>
    <input id="${esc(id)}-file" type="file" multiple hidden>
    <p class="fail small chat-composer-note" data-attach-reason role="status"></p>
  </section>`;
}

async function upload(file, options) {
  const prepared = await downscaleImage(file);
  const body = new FormData(); body.append("file", prepared, prepared.name);
  const response = await options.fetchImpl(options.uploadUrl, { method: "POST", body,
    headers: { "X-CSRF-Token": options.csrfToken }, credentials: "same-origin" });
  let data = null;
  try { data = await response.json(); } catch { /* error responses may be empty */ }
  if (!response.ok) { const error = new Error(data?.error?.message || data?.error || `${response.status} ${response.statusText}`); error.status = response.status; throw error; }
  if (!data?.ref) throw new Error("Upload response did not include an attachment ref.");
  return { ...data, name: file.name || prepared.name, file: prepared };
}

function renderChip(chip) {
  const text = chip.status === "uploading" ? "Uploading…" : chip.status === "error" ? `Upload failed: ${chip.error}` : (chip.name || "Image");
  if (chip.kind === "text") return `<span class="attach-chip" data-attach-chip="${esc(chip.id)}"><button type="button" class="attach-preview" data-attach-preview="${esc(chip.id)}" aria-label="Preview ${esc(chip.name)}" title="Preview ${esc(chip.name)}">${icon("file")}<span>${esc(chip.name)}<small>${textBytes(chip.text)} B · ${lineCount(chip.text)} lines</small></span></button><button type="button" class="icon-btn" data-attach-restore="${esc(chip.id)}" aria-label="Put text back in input" title="Put text back in input">↩</button><button type="button" class="icon-btn" data-attach-remove="${esc(chip.id)}" aria-label="Remove ${esc(chip.name)}" title="Remove ${esc(chip.name)}">${icon("close")}</button></span>`;
  const action = chip.status === "error" ? `<button type="button" class="btn sm" data-attach-retry="${esc(chip.id)}" aria-label="Retry attachment" title="Retry attachment">↻</button>` : "";
  const state = chip.status === "error" ? stateError({ message: text, status: 499 }) : "";
  return `<span class="attach-chip attach-${chip.status}" data-attach-chip="${esc(chip.id)}"><span>${state || esc(text)}</span>${action}<button type="button" class="icon-btn" title="Remove attachment" aria-label="Remove ${esc(chip.name || "image")}" data-attach-remove="${esc(chip.id)}">${icon("close")}</button></span>`;
}

/** Mount the tray inside an element containing attachmentTrayHtml(). Returns draft accessors and destroy(). */
export function mountAttachmentTray(root, { id = "chat", uploadUrl = "S/attachments", csrfToken = "", fetchImpl = globalThis.fetch,
  sendButton = null, chips = new Map(), canUpload = () => true, onChange = () => {}, onError = () => {}, onRestore = () => {}, onPreview = null } = {}) {
  const tray = root.querySelector(`#${id}-attachments`) || root;
  const chipsEl = tray.querySelector("[data-attach-chips]");
  const fileInput = tray.querySelector(`#${id}-file`);
  const attach = root.querySelector(`#${id}-attach`);
  const openFiles = () => fileInput?.click();
  attach?.addEventListener("click", openFiles);
  const reason = tray.querySelector("[data-attach-reason]");
  let serial = 0, disposed = false;
  const active = () => [...chips.values()].some((chip) => chip.status === "uploading");
  const refs = () => [...chips.values()].filter((chip) => chip.status === "done" && chip.ref).map((chip) => chip.ref);
  const update = (notify = true) => {
    const busy = active();
    if (!disposed) {
      sourceHTML(chipsEl, [...chips.values()].map(renderChip).join(""));
      if (sendButton) { sendButton.disabled = busy; sendButton.title = busy ? "Wait for attachments to finish" : "Send"; sendButton.setAttribute("aria-disabled", String(busy)); }
      reason.textContent = busy ? "Wait for attachments to finish before sending." : "";
    }
    if (notify) onChange({ refs: refs(), uploading: busy, chips: [...chips.values()] });
  };
  const addText = (text, name) => {
    try {
      if (!canUpload()) throw new Error("Choose a writable session before attaching text.");
      validateText(text);
      if ([...chips.values()].reduce((n,c) => n + textBytes(c.text || ""), textBytes(text)) > MAX_TEXT_DRAFT_BYTES) throw new Error("Text draft is too big (maximum 64 KiB). Remove an attachment first.");
      let n = 1; while ([...chips.values()].some(c => c.name === `pasted-text-${n}.txt`)) n++;
      const chip = { id: `text-${Date.now()}-${++serial}`, kind: "text", type: "text/plain", name: name || `pasted-text-${n}.txt`, text, status: "done" };
      chips.set(chip.id, chip); update(); return chip;
    } catch (error) { onError(error); return null; }
  };
  const add = (file) => {
    if (!canUpload()) { onError(new Error("Choose a session with mutations enabled before attaching files.")); return Promise.resolve(); }
    if (!imageType(file)) {
      // Reserve a busy chip before decoding so a fast Send cannot omit the file.
      const pending = { id: `reading-${Date.now()}-${++serial}`, name: file.name, status: "uploading" };
      chips.set(pending.id, pending); update();
      return readTextFile(file).then((text) => {
        if (chips.get(pending.id) !== pending) return;
        chips.delete(pending.id); addText(text, file.name);
      }).catch(onError).finally(() => { chips.delete(pending.id); update(); });
    }
    const chip = { id: `attachment-${Date.now()}-${++serial}-${Math.random()}`, name: file.name, file, status: "uploading" }; chips.set(chip.id, chip); update();
    return upload(file, { uploadUrl, csrfToken, fetchImpl }).then((data) => { if (chips.get(chip.id) !== chip) return; Object.assign(chip, data, { status: "done" }); update(); })
      .catch((error) => { if (chips.get(chip.id) !== chip) return; chip.status = "error"; chip.error = error.message; update(); onError(error); });
  };
  const onFiles = (files) => Promise.all([...files].map(add));
  const onFileChange = () => { onFiles(fileInput.files || []); fileInput.value = ""; };
  fileInput?.addEventListener("change", onFileChange);
  const onClick = (event) => {
    const remove = event.target.closest("[data-attach-remove]");
    if (remove) { chips.delete(remove.dataset.attachRemove); update(); return; }
    const restore = event.target.closest("[data-attach-restore]");
    if (restore) { const chip = chips.get(restore.dataset.attachRestore); if (chip) { onRestore(chip.text); chips.delete(chip.id); update(); } return; }
    const preview = event.target.closest("[data-attach-preview]");
    if (preview) {
      const chip = chips.get(preview.dataset.attachPreview); if (!chip) return;
      if (onPreview) onPreview(chip);
      else {
        const dialog = document.createElement("dialog"); dialog.className = "text-attachment-preview";
        dialog.innerHTML = `<h2>${esc(chip.name)}</h2><pre>${esc(chip.text)}</pre><button type="button" class="icon-btn" aria-label="Close preview" title="Close preview">${icon("close")}</button>`;
        document.body.append(dialog); dialog.addEventListener("close", () => { dialog.remove(); root.querySelector(`#${id}-input`)?.focus(); });
        dialog.querySelector("button").onclick = () => dialog.close(); dialog.showModal();
      } return;
    }
    const retry = event.target.closest("[data-attach-retry]");
    if (retry) { const chip = chips.get(retry.dataset.attachRetry); if (chip && chip.status === "error" && canUpload()) { chip.status = "uploading"; chip.error = ""; update(); upload(chip.file, { uploadUrl, csrfToken, fetchImpl }).then((data) => { if (chips.get(chip.id) !== chip) return; Object.assign(chip, data, { status: "done" }); update(); }).catch((error) => { if (chips.get(chip.id) !== chip) return; chip.status = "error"; chip.error = error.message; update(); onError(error); }); } }
  };
  tray.addEventListener("click", onClick);
  update(false);
  const dropRoot = root.querySelector(`#${id}-form`) || tray;
  const drag = event => { if ([...(event.dataTransfer?.types || [])].includes("Files")) event.preventDefault(); };
  const drop = event => { if (event.dataTransfer?.files?.length) { event.preventDefault(); onFiles(event.dataTransfer.files); } };
  dropRoot.addEventListener("dragover", drag); dropRoot.addEventListener("drop", drop);
  return { addText, refresh: () => update(false), add, onFiles, refs, isUploading: active, getState: () => [...chips.values()], remove: (chipId) => { chips.delete(chipId); update(); }, destroy: () => { disposed = true; dropRoot.removeEventListener("dragover", drag); dropRoot.removeEventListener("drop", drop); tray.removeEventListener("click", onClick); attach?.removeEventListener("click", openFiles); fileInput?.removeEventListener("change", onFileChange); } };
}

export function bindAttachmentPaste(input, tray) {
  let inline = false;
  const keydown = e => { inline = e.key?.toLowerCase() === "v" && e.shiftKey && (e.ctrlKey || e.metaKey); };
  const keyup = () => { inline = false; };
  const handler = (event) => {
    const text = event.clipboardData?.getData?.("text/plain") || "";
    if (!inline && !event.shiftKey && longPaste(text)) { event.preventDefault(); tray.addText(text); return; }
    const items = [...(event.clipboardData?.items || [])];
    const images = items.filter((item) => item.kind === "file" && imageType(item.getAsFile?.()));
    if (!images.length) return; // Native text paste and IME remain untouched.
    if (!items.some((item) => item.kind === "string")) event.preventDefault();
    images.forEach((item) => tray.add(item.getAsFile()));
  };
  input.addEventListener("keydown", keydown); input.addEventListener("keyup", keyup); input.addEventListener("blur", keyup);
  input.addEventListener("paste", handler);
  return () => { input.removeEventListener("paste", handler); input.removeEventListener("keydown", keydown); input.removeEventListener("keyup", keyup); input.removeEventListener("blur", keyup); };
}
