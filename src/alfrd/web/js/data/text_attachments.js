// The existing upload API is image-only. Text uses the message prompt transport.
export const LONG_PASTE_THRESHOLD = Object.freeze({ characters: 2000, lines: 40 });
export const MAX_TEXT_ATTACHMENT_BYTES = 16384;
export const MAX_TEXT_DRAFT_BYTES = 65536;
export const lineCount = text => text.split(/\r\n|\r|\n/).length;
export const textBytes = text => new TextEncoder().encode(text).length;
export const longPaste = text => text.length > LONG_PASTE_THRESHOLD.characters || lineCount(text) > LONG_PASTE_THRESHOLD.lines;
export function validateText(text) {
  if (textBytes(text) > MAX_TEXT_ATTACHMENT_BYTES) throw new Error("Text attachment is too big (maximum 16 KiB per file). Split the file or paste inline.");
}
export async function readTextFile(file) {
  if (file.size > MAX_TEXT_ATTACHMENT_BYTES) throw new Error("Text attachment is too big (maximum 16 KiB per file).");
  const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
  if (/[\x00-\x08\x0e-\x1f]/.test(text)) throw new Error("Only images and UTF-8 text files can be attached.");
  validateText(text); return text;
}
export function attachmentPrompt(text, chips) {
  return [text.trim(), ...[...chips.values()].filter(c => c.kind === "text").map(c => `--- BEGIN TEXT ATTACHMENT ${JSON.stringify(c.name)} (JSON encoded) ---\n${JSON.stringify(c.text)}\n--- END TEXT ATTACHMENT ---`)].filter(Boolean).join("\n\n");
}
