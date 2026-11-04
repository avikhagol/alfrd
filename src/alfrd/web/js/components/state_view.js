import { esc } from "../utils/dom.js";

const STATUS_TEXT = new Map([
  [0, "Server unreachable"],
  [403, "Read-only or CSRF"],
  [404, "Gone"],
  [409, "Changed elsewhere"],
  [413, "Too large"],
  [415, "Unsupported type"],
  [422, "Unsupported adapter capability"],
]);

const statusOf = (err) => {
  if (typeof err === "number") return err;
  return Number(err?.status ?? err?.response?.status ?? 0) || 0;
};

const retry = (onRetry) => typeof onRetry === "function"
  ? '<button type="button" class="btn sm" data-state-retry>Retry</button>'
  : "";

/** Render a shared empty state. Both arguments are plain text. */
export function empty(title, nextStep = "") {
  return `<div class="state-view state-empty" role="status"><p>${esc(title)}</p>${nextStep ? `<p class="muted small">${esc(nextStep)}</p>` : ""}</div>`;
}

/**
 * Render a loading state. A slow request gets a retry affordance after the
 * timeout, and onRetry is called once at that point so callers can restart it.
 * The retry button is marked for the caller's normal event delegation.
 */
export function loading(label = "Loading…", { timeoutMs = 10000, onRetry } = {}) {
  const delay = Number(timeoutMs);
  if (typeof onRetry === "function" && Number.isFinite(delay) && delay >= 0) {
    const timer = setTimeout(() => onRetry(), delay);
    timer.unref?.();
  }
  return `<div class="state-view state-loading" role="status" aria-live="polite"><p>${esc(label)}</p>${retry(onRetry)}</div>`;
}

/** Render a safe, human-readable error state for a request failure. */
export function error(err, { onRetry } = {}) {
  const status = statusOf(err);
  const message = STATUS_TEXT.get(status) || esc(err?.message || String(err || "Request failed"));
  return `<div class="state-view state-error" role="alert"><p>${message}</p>${retry(onRetry)}</div>`;
}

export { STATUS_TEXT };
