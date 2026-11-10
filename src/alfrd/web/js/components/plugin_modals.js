// Plugin dialogs reuse the shell's single modal, focus trap and close gestures.
import { esc } from "../utils/dom.js";

const active = new WeakMap();
let nextId = 0;
const button = (label, tone) => {
  const node = document.createElement("button");
  node.type = "button";
  node.className = `btn${["primary", "danger"].includes(tone) ? ` ${tone}` : ""}`;
  node.textContent = label;
  return node;
};

export function dialog(ctx, { title, body, actions = [], wide = false }) {
  if (!(body instanceof HTMLElement)) throw new TypeError("dialog body must be an HTMLElement");
  const titleId = `plug-dialog-title-${++nextId}`;
  let handle;
  ctx.modal(`<header class="modal-h"><h2 id="${titleId}">${esc(title)}</h2><button class="icon-btn" data-close aria-label="Close">×</button></header><div class="modal-b"></div><footer class="modal-f row gap wrap plug-dialog-footer"></footer>`, (root, shellClose) => {
    root.setAttribute("aria-labelledby", titleId);
    root.querySelector(".modal-b").append(body);
    const footer = root.querySelector(".modal-f");
    let dirty = false, busy = false, running = false, queuedClose = false, pending = null;
    let disabled = [];
    let observer;
    const dispose = () => {
      pending?.finish(false);
      if (active.get(ctx) === handle) active.delete(ctx);
      observer?.disconnect();
      handle.onDispose?.();
    };
    handle = {
      root, footer, dispose,
      close() { if (!root.isConnected) return; if (running) queuedClose = true; else shellClose(); },
      setDirty(value) { dirty = !!value; },
      setBusy(value) {
        value = !!value;
        if (value === busy) return;
        busy = value;
        if (busy) {
          disabled = [...footer.querySelectorAll("button")].map((b) => [b, b.disabled]);
          disabled.forEach(([b]) => { b.disabled = true; });
        } else {
          disabled.forEach(([b, was]) => { b.disabled = was; });
        }
      },
      confirm(text, { tone = "default", confirmLabel = "Confirm", cancelLabel = "Cancel" } = {}) {
        if (pending) return pending.promise;
        const original = [...footer.childNodes], focus = document.activeElement;
        const inert = [root.querySelector(".modal-h"), root.querySelector(".modal-b")].map((node) => [node, node.inert]);
        inert.forEach(([node]) => { node.inert = true; });
        const strip = document.createElement("div");
        strip.className = "row gap wrap plug-confirm";
        strip.setAttribute("role", "alertdialog");
        strip.setAttribute("aria-modal", "true");
        const message = document.createElement("span");
        message.id = `plug-confirm-${++nextId}`;
        message.className = "grow";
        message.textContent = text;
        strip.setAttribute("aria-labelledby", message.id);
        const cancel = button(cancelLabel), accept = button(confirmLabel, tone);
        strip.append(message, cancel, accept);
        footer.replaceChildren(strip);
        const promise = new Promise((resolve) => {
          const finish = (answer) => {
            if (!pending) return;
            pending = null;
            inert.forEach(([node, was]) => { node.inert = was; });
            footer.replaceChildren(...original);
            if (focus?.isConnected) focus.focus();
            resolve(answer);
          };
          pending = { finish };
          cancel.onclick = () => finish(false);
          accept.onclick = () => finish(true);
        });
        pending.promise = promise;
        cancel.focus();
        return promise;
      },
    };
    root.addEventListener("beforeclose", (event) => {
      if (pending) { event.preventDefault(); pending.finish(false); return; }
      if (busy) { event.preventDefault(); ctx.toast("Wait for the current step to finish", "warn"); return; }
      if (dirty) {
        event.preventDefault();
        handle.confirm("Discard unsaved changes?", { tone: "danger", confirmLabel: "Discard" }).then((answer) => {
          if (answer && root.isConnected) { dirty = false; shellClose(); }
        });
        return;
      }
      dispose();
    });
    // The shell may replace its singleton with a non-plugin modal. Settle any pending choice.
    observer = new MutationObserver(() => { if (!root.isConnected) dispose(); });
    observer.observe(root.parentNode, { childList: true });
    active.get(ctx)?.dispose();
    active.set(ctx, handle);
    for (const action of [...actions].sort((a, b) => (a.tone === "primary") - (b.tone === "primary"))) {
      const node = button(action.label, action.tone);
      node.onclick = async () => {
        if (busy || pending) return;
        running = true;
        handle.setBusy(true);
        node.textContent = `${action.label} …`;
        node.setAttribute("aria-busy", "true");
        try { await action.run?.(handle); }
        catch (error) { queuedClose = false; ctx.toast(error?.message || String(error), "fail"); }
        finally {
          running = false;
          handle.setBusy(false);
          node.textContent = action.label;
          node.removeAttribute("aria-busy");
          if (queuedClose) { queuedClose = false; if (root.isConnected) shellClose(); }
        }
      };
      footer.append(node);
    }
  }, wide ? "wide" : "");
  return handle;
}

export function confirm(ctx, text, options = {}) {
  const current = active.get(ctx);
  if (current?.root.isConnected) return current.confirm(text, options);
  return new Promise((resolve) => {
    const body = document.createElement("p");
    body.textContent = text;
    let settled = false;
    const finish = (answer, handle) => { settled = true; resolve(answer); handle.close(); };
    const handle = dialog(ctx, { title: "Confirm", body, actions: [
      { label: options.cancelLabel || "Cancel", run: (h) => finish(false, h) },
      { label: options.confirmLabel || "Confirm", tone: options.tone, run: (h) => finish(true, h) },
    ] });
    handle.root.setAttribute("role", "alertdialog");
    handle.root.setAttribute("aria-describedby", body.id = `plug-confirm-${++nextId}`);
    handle.onDispose = () => { if (!settled) resolve(false); };
  });
}
