const ACTIONS = [
  ["log", "Open log"],
  ["prompt", "View prompt"],
  ["reply", "View reply"],
  ["files", "View files"],
  ["retry", "Retry"],
  ["cancel", "Cancel"],
  ["copy_id", "Copy ID"],
];

const CANCELABLE = new Set(["queued", "waiting", "running", "retrying", "needs_decision"]);
const RETRYABLE = new Set(["failed", "blocked", "cancelled", "stopped"]);

const first = (...values) => values.find((value) => value !== undefined && value !== null);
const rowsOf = (preview) => Array.isArray(preview?.affected_rows) ? preview.affected_rows : [];

const availability = (enabled, reason) => enabled
  ? { enabled: true }
  : { enabled: false, reason: reason || "Not available for this task" };

/**
 * Return the actions supported by a task row. This is intentionally a pure
 * projection: callers perform navigation, fetches and mutations themselves.
 * `target.retry_preview` is the response from F4's retry-preview endpoint.
 */
export function actions(target = {}) {
  const status = target.status || "";
  const preview = first(target.retry_preview, target.retryPreview, target.retry?.preview);
  const row = first(target.row, target.id, "");
  const hasLog = Boolean(target.log || target.logs?.length || target.attempts?.some?.((attempt) => attempt.log));
  const hasPrompt = Boolean(target.prompt || target.prompts?.length || target.attempts?.some?.((attempt) => attempt.prompt));
  const hasReply = Boolean(target.reply || target.result || target.attempts?.length || target.response);
  const hasFiles = Boolean(target.files?.length || target.artifacts?.length || target.result?.artifacts?.length);
  const retryEnabled = Boolean(preview ? preview.eligible : RETRYABLE.has(status));
  const retryReason = preview && !preview.eligible
    ? preview.reason || "This task cannot be retried"
    : status ? `Retry is unavailable while task is ${status}` : "Retry status is unknown";
  const retry = availability(retryEnabled, retryReason);
  if (retryEnabled && preview && rowsOf(preview).length) {
    retry.confirmation = {
      affected_rows: [...rowsOf(preview)],
      message: `Retry will requeue: ${rowsOf(preview).join(", ")}`,
    };
  }

  return [
    { id: "log", label: "Open log", ...availability(hasLog, "No log is available for this task") },
    { id: "prompt", label: "View prompt", ...availability(hasPrompt, "No prompt is available for this task") },
    { id: "reply", label: "View reply", ...availability(hasReply, "No reply is available for this task") },
    { id: "files", label: "View files", ...availability(hasFiles, "No files are associated with this task") },
    { id: "retry", label: "Retry", ...retry },
    { id: "cancel", label: "Cancel", ...availability(CANCELABLE.has(status), status ? `A ${status} task cannot be cancelled` : "Task status is unknown") },
    { id: "copy_id", label: "Copy ID", ...availability(Boolean(row), "Task ID is unavailable") },
  ];
}

export { ACTIONS };
