import { viewerFor } from "./viewers.js";

// Use the installed browser viewer; keep the original text independent of its HTML.
export function mountMarkdown(section, file) {
  const source = section.querySelector("pre"), rendered = section.querySelector("[data-rendered]");
  const buttons = [...section.querySelectorAll("[data-mode]")];
  const status = section.querySelector("[data-render-status]");
  const viewer = viewerFor("handoff.md", "text/markdown");
  const available = viewer && viewer.id !== "text";
  let text = "", mode = available ? "rendered" : "source", revision = 0;
  buttons[0].disabled = !available;
  if (!available) status.textContent = "Install and enable the Markdown viewer in Settings → Plugins to use Rendered.";
  const paint = async () => {
    const current = ++revision;
    buttons.forEach((b) => { const on = b.dataset.mode === mode; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    source.hidden = mode !== "source"; rendered.hidden = mode !== "rendered";
    if (mode !== "rendered") return;
    try {
      const host = document.createElement("div");
      await viewer.render({ ...file, text, mime: "text/markdown" }, host);
      const { default: purify } = await import("../vendor/purify.es.mjs");
      if (current !== revision || !section.isConnected) return;
      rendered.innerHTML = purify.sanitize(host.innerHTML, { USE_PROFILES: { html: true }, FORBID_TAGS: ["style"], FORBID_ATTR: ["style", "id", "name"] });
      status.textContent = "";
    } catch {
      if (current !== revision) return;
      mode = "source";
      status.textContent = "Markdown preview unavailable. Showing source; select Rendered to retry.";
      paint();
    }
  };
  buttons.forEach((b) => b.addEventListener("click", () => { mode = b.dataset.mode; paint(); }));
  // Parsing the accumulated text lets a later page complete a fence, list or table.
  return (content) => { text += content; source.textContent = text; paint(); };
}
