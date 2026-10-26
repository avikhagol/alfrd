// Identity is independent of arrival order, provider and execution status.
export function identitySlot(key) {
  let hash = 0;
  for (const c of String(key)) hash = Math.imul(hash, 31) + c.codePointAt(0) | 0;
  return hash & 1 ? "purple" : "sky";
}

export function messageIdentity(message, { agentName, orchestrator, agents = {} } = {}) {
  agents = typeof agents === "function" ? agents() : agents;
  orchestrator = typeof orchestrator === "function" ? orchestrator() : orchestrator;
  const key = message.role === "user" ? "you" : message.role === "system" ? "alfrd" : message.agent || "alfrd";
  const config = agents?.[key];
  const name = key === "you" ? "You" : key === "alfrd" ? "ALFRD" : config?.label || agentName?.(key) || key;
  const roles = config?.roles || [];
  const role = key === orchestrator ? "Orchestrator" : message.agent_role || config?.role || (Array.isArray(roles) ? roles.join(" · ") : roles);
  const words = String(name).trim().split(/[\s_-]+/u);
  const initials = [...(words.length > 1 ? [...words[0]][0] + [...words.at(-1)][0] : [...String(name)].slice(0, 2).join("")).toLocaleUpperCase()].slice(0, 2).join("");
  return { key, name, role, initials, slot: key === orchestrator ? "orchestrator" : identitySlot(key) };
}

export function messageTime(at) {
  const date = at ? new Date(at) : null;
  if (!date || !Number.isFinite(date.getTime())) return null;
  return { datetime: date.toISOString(), short: date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
    full: date.toLocaleString([], { dateStyle: "full", timeStyle: "long" }), day: date.toLocaleDateString() };
}

export function adjacentMessages(a, b, requestA, requestB) {
  const first = messageTime(a?.at), second = messageTime(b?.at);
  return Boolean(a && b && first && second && !a.kind && !b.kind && a.role !== "system" && a.role === b.role && a.agent === b.agent &&
    requestA === requestB && first.day === second.day && new Date(b.at) - new Date(a.at) >= 0 && new Date(b.at) - new Date(a.at) <= 120000);
}
