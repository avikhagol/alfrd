import { esc } from "../utils/dom.js";
import { server } from "../data/server.js";

// Permission UX only: the server remains authoritative for every mutation.
export function confirmPermissions(changes) {
  const labels = { "lockdown.file_edits": "project file edits", "lockdown.project_delete": "forgetting and deleting projects" };
  const enabled = Object.keys(labels).filter((key) => changes[key] === true);
  return !enabled.length || confirm(`Allow ${enabled.map((key) => labels[key]).join(" and ")}?\n\nThis applies to every Studio on this machine. Server access checks and action-specific confirmations still apply.`);
}

export async function permissionBadge(host, project) {
  try {
    const res = await server.request(`/api/studio/ui-flags?project=${encodeURIComponent(project)}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error();
    const body = await res.json();
    const changed = body.flags.filter((f) => f.dangerous && f.effective !== f.default);
    if (host.isConnected) host.innerHTML = changed.length ? `<span class="badge">Non-default permissions</span> ${changed.map((f) => `${esc(f.label)}: ${f.effective ? "on" : "off"}`).join("; ")}<small class="muted"> Machine permissions apply across projects; server guards remain authoritative. Change them in Preferences → Machine.</small>` : "";
  } catch {
    if (host.isConnected) host.textContent = "Permission status unavailable. Server guards remain authoritative.";
  }
}
