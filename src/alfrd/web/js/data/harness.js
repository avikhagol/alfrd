// Observer-only presentation helpers. No mutation or current-run inference in replay.
export const stateWord = (state) => ({ running: 'working', idle: 'idle', blocked: 'waiting', stuck: 'possibly stuck', failed: 'failed', done: 'done' })[state] || state;
export function bounds(state) {
  const start = Date.parse(state.coverage.from || state.clock.at);
  const end = Math.max(start + 1000, Date.parse(state.clock.mode === 'live' ? state.clock.server_now : state.coverage.to || state.clock.server_now));
  return [start, end];
}
export function stations(state) {
  const order = state.settings.role_order || [];
  return state.agents.map((a) => {
    const roles = [...new Set((state.view.tasks || []).filter((t) => t.owner === a.id).map((t) => t.task?.role).filter(Boolean))];
    const role = a.role || roles.sort((a, b) => order.indexOf(a) - order.indexOf(b))[0] || 'other';
    const rank = order.indexOf(role);
    return { ...a, role, layer: rank < 0 ? order.length : rank };
  }).sort((a, b) => a.layer - b.layer || a.id.localeCompare(b.id));
}
export function handoffs(edges) {
  const pairs = new Map();
  for (const e of edges) {
    if (!e.from_agent || !e.to_agent || e.from_agent === e.to_agent) continue;
    const key = `${e.from_agent}>${e.to_agent}:${e.kind}`;
    const pair = pairs.get(key) || { ...e, id: key, count: 0, evidence: 0 };
    pair.evidence++;
    if (e.kind === 'published' || e.status === 'consumed') pair.count++;
    pairs.set(key, pair);
  }
  return [...pairs.values()];
}
export function feedSentence(e) {
  if (e.text && e.text !== String(e.kind).replaceAll('.', ' ').replaceAll('_', ' ')) return e.text;
  const who = e.agent || 'An agent', task = e.task || 'a task';
  if (e.severity === 'error' && e.kind.endsWith('.finished')) return `${e.agent || e.request || 'The task'} could not finish ${e.task || 'the work'}.`;
  const words = {
    'task.created': `${task} was added for ${who}.`, 'task.started': `${who} started ${task}.`,
    'task.finished': `${who} finished ${task}.`, 'task.failed': `${who} could not finish ${task}.`,
    'task.blocked': `${task} is waiting for help.`, 'task.stuck': `${task} may be stuck.`,
    'task.reassigned': `${task} moved to ${who}.`, 'handoff.published': `${who} published outputs for ${task}.`,
    'decision.needed': `${e.request || 'A request'} needs your decision.`, 'decision.resolved': 'A decision was made.',
    'turn.started': `${who} began a turn on ${task}.`, 'turn.finished': `${who} ended a turn on ${task}.`,
    'turn.failed': `${who}'s turn on ${task} stopped.`, 'turn.retrying': `${who} is retrying ${task}.`,
    'request.finished': `${e.request} finished.`, 'request.failed': `${e.request} did not finish.`,
  };
  return words[e.kind] || `${String(e.kind).replaceAll('.', ' ').replaceAll('_', ' ')}${e.agent ? ` (${who})` : ''}.`;
}
export function filterFeed(events, { agent = '', type = '', errors = false, search = '' } = {}) {
  return events.filter((e) => (!agent || e.agent === agent) && (!type || e.kind.split('.')[0] === type)
    && (!errors || e.severity === 'error') && (!search || feedSentence(e).toLowerCase().includes(search.toLowerCase())))
    .sort((a, b) => b.seq - a.seq);
}
