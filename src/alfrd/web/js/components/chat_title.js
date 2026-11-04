// A title is editable as soon as it receives focus. Keep drafts local until blur.
export function bindChatTitle(input, { save, report, settled }) {
  let title = input.value, saving = false;
  input.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault(); event.stopPropagation();
      input.value = title;
      input.blur();
    } else if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    }
  });
  input.addEventListener("blur", async () => {
    if (saving) return;
    const next = input.value.trim().slice(0, 120);
    if (!next || next === title) { input.value = title; settled(); return; }
    saving = true; input.readOnly = true; input.dataset.saving = "true";
    input.setAttribute("aria-busy", "true");
    try { title = await save(next); }
    catch (error) { report(`Rename failed: ${error.message}`); }
    finally {
      input.value = title; input.readOnly = false; saving = false;
      delete input.dataset.saving; input.removeAttribute("aria-busy");
      settled();
    }
  });
}
