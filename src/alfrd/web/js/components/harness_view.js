import { $, esc, routeHash, setRoute } from '../utils/dom.js';
import { refreshHTML } from '../utils/refresh.js';
import { server } from '../data/server.js';
import { money, duration } from '../data/chat.js';
import { bounds, stations, handoffs, stateWord, filterFeed, feedSentence } from '../data/harness.js';
import { createChatView } from './chat_view.js';
import { mountPanelResize } from './panel_resize.js';

let current = null;
export function leave() { current?.stop(); }
export function render(el, ctx) {
  if (ctx.state.mode !== 'server') { el.textContent = 'Harness needs alfrd serve.'; return; }
  const project = ctx.activeProject();
  if (!project) return;
  if (!current || current.project !== project) { current?.destroy(); current = createHarness(el, ctx, project); }
  current.render();
}

function createHarness(host, ctx, project) {
  const el = document.createElement("div"); host.replaceChildren(el);
  let state = null, catalog = [], key = '', generation = 0, controller, timer, seekTimer, playback;
  let active = true, playing = false, speed = 1, tab = 'chat', center = 'agents', agent = '', type = '', errors = false, search = '';
  let previousEdges = new Set(), catalogAt = 0, baseline = null, expanded = false, collapsed = false;
  const chat = createChatView({ projected: true, readOnly: () => query().has("at"), refresh: () => schedule(),
    onSession: (sid, opts) => { if (sid !== state?.session) { state = null; baseline = null; catalog = []; previousEdges = new Set(); agent = ''; key = ''; } if (decodeURIComponent(location.hash.split("?")[0].split("/")[2] || "") !== sid) navigate({ sid, request: null, at: null }, opts); schedule(); },
    onTask: () => showTab('chat') });
  el.innerHTML = `<div class="harness"><header class="harness-head row gap wrap"><h2>Harness</h2><label>Scope <select data-h-scope aria-label="Harness request scope"><option value="">Current request</option><option value="all">All requests</option></select></label><span data-h-totals role="status"></span><span class="grow"></span><button class="btn sm" data-h-live>Live</button><button class="btn sm" data-h-play aria-pressed="false">Play</button><label>Speed <select data-h-speed aria-label="Playback speed"><option value="1">1x</option><option value="10">10x</option><option value="60">60x</option></select></label><time data-h-time></time></header><p data-h-error role="alert" class="fail-t"></p><p data-h-coverage class="muted small"></p><div data-h-chat></div></div>`;
  const shell = $('.harness', el), chatHost = $('[data-h-chat]', el);
  chatHost.append(chat.root);
  let grid, room, stage, resize;
  const query = () => new URLSearchParams(location.hash.split('?')[1] || '');
  const sid = () => ctx.state.chatSession || chat.sid();
  function navigate(change = {}, opts = {}) {
    const params = query();
    for (const name of ['request', 'at']) if (name in change) { if (change[name]) params.set(name, change[name]); else params.delete(name); }
    const route = routeHash(`harness/${encodeURIComponent(change.sid ?? sid() ?? '')}`, project, ctx.state.selectedTarget);
    const url = new URLSearchParams(route.split('?')[1]);
    for (const name of ['request', 'at']) if (params.has(name)) url.set(name, params.get(name));
    setRoute(`${route.split('?')[0]}?${url}`, opts);
    ctx.update();
  }
  function setup() {
    grid = $('.chat', chat.root);
    if (!grid || stage?.isConnected) return;
    grid.classList.add('harness-grid');
    stage = document.createElement('section'); stage.className = 'harness-stage'; stage.setAttribute('aria-label', 'Agent and task views');
    stage.innerHTML = `<div class="seg" role="tablist" aria-label="Harness views"><button role="tab" data-h-center="agents" aria-selected="true">Agents</button><button role="tab" data-h-center="tasks" aria-selected="false">Tasks</button></div><div data-h-agents></div>`;
    grid.append(stage);
    stage.append($('.chat-graph-card', chat.root));
    room = document.createElement('aside'); room.className = 'harness-room'; room.id = 'harness-room'; room.setAttribute('aria-label', 'Control Room');
    room.innerHTML = `<div class="harness-resize"></div><header class="row gap"><h3 class="grow">Control Room</h3><button class="btn sm" data-h-expand aria-pressed="false">Expand</button><button class="btn sm" data-h-close aria-expanded="true">Collapse</button></header><div class="seg wrap" role="tablist" aria-label="Control Room">${['timeline', 'feed', 'chat', 'stats', 'blockers'].map((t) => `<button role="tab" data-h-tab="${t}" aria-selected="${tab === t}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</div><p data-h-readonly class="callout warn" hidden>Replay is read-only. Go live to act.</p><div data-h-timeline></div><div data-h-feed></div><div data-h-morning></div>`;
    grid.append(room);
    room.append($('.chat-main', chat.root), $('.chat-side', chat.root));
    resize = mountPanelResize({ panel: room, handle: $('.harness-resize', room), key: 'ui:harness.roomWidth', min: 320, max: 1000, defaultWidth: 480, side: 'right', label: 'Resize Control Room', enabled: () => !expanded && !collapsed && window.innerWidth > 900, apply: (w) => grid.style.setProperty('--harness-room-width', `${w}px`) });
    showTab(tab); showCenter(center);
  }
  function showTab(value) {
    tab = value; if (!room) return;
    collapsed = false; grid.classList.remove('harness-room-collapsed');
    room.dataset.tab = tab;
    for (const b of room.querySelectorAll('[data-h-tab]')) b.setAttribute('aria-selected', String(b.dataset.hTab === tab));
    $('.chat-main', room).hidden = tab !== 'chat';
    $('#chat-progress', room).hidden = tab !== 'stats';
    $('#chat-decisions', room).hidden = tab !== 'blockers';
    $('[data-h-timeline]', room).hidden = tab !== 'timeline';
    $('[data-h-feed]', room).hidden = tab !== 'feed';
    $('[data-h-morning]', room).hidden = !['stats', 'feed'].includes(tab);
    resize?.sync();
  }
  function showCenter(value) {
    center = value; if (!stage) return;
    $('[data-h-agents]', stage).hidden = center !== 'agents';
    $('.chat-graph-card', stage).hidden = center !== 'tasks';
    for (const b of stage.querySelectorAll('[data-h-center]')) b.setAttribute('aria-selected', String(b.dataset.hCenter === center));
  }
  function schedule() { clearTimeout(timer); if (active) timer = setTimeout(() => load(), 100); }
  async function get(params, signal) {
    const response = await server.request(`/api/studio/projects/${encodeURIComponent(project)}/chat/sessions/${encodeURIComponent(sid())}/harness/state?${params}`, { signal, headers: { Accept: 'application/json' } });
    const body = await response.json();
    if (!response.ok) throw new Error(body?.error?.message || `Harness: ${response.status}`);
    return body;
  }
  async function load(older = false) {
    if (!active || !sid() || document.hidden) return;
    clearTimeout(timer); controller?.abort(); controller = new AbortController();
    const gen = ++generation, signal = controller.signal;
    const q = query(), params = new URLSearchParams();
    if (q.get('request')) { params.set('scope', q.get('request') === 'all' ? 'all' : 'request'); if (q.get('request') !== 'all') params.set('request', q.get('request')); }
    if (q.get('at')) params.set('at', q.get('at'));
    if (older && state?.feed_page.next_before_seq) params.set('before_seq', state.feed_page.next_before_seq);
    try {
      const data = await get(params, signal);
      if (gen !== generation || !active) return;
      if (!catalog.length || !q.has("at") && Date.now() - catalogAt > 30000) {
        const all = data.scope.kind === 'all' && !q.has('at') ? data : await get(new URLSearchParams({ scope: 'all' }), signal);
        if (gen !== generation || !active) return;
        catalog = all.view.requests || []; baseline = all; catalogAt = Date.now();
      }
      if (older && state) data.feed_events = [...state.feed_events, ...data.feed_events];
      state = data;
      $('[data-h-error]', el).textContent = '';
      chat.setProjection(data); paint();
      if (!q.get('at')) timer = setTimeout(() => load(), 1000 * (data.settings.poll_seconds || 2));
    } catch (e) { if (e.name !== 'AbortError' && gen === generation && active) { $('[data-h-error]', el).textContent = `${e.message}. Retrying…`; timer = setTimeout(() => load(), 5000); } }
  }
  function paint() {
    if (!state || !stage) return;
    const s = state, cost = s.summary.usage;
    refreshHTML($('[data-h-scope]', el), `<option value="">Current request</option><option value="all">All requests</option>${catalog.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc((r.text || '').slice(0, 60))}</option>`).join('')}`);
    $('[data-h-scope]', el).value = query().get('request') || '';
    $('[data-h-totals]', el).textContent = `${s.scope.request || 'All requests'} · ${money(cost.total_cost_usd)}${cost.complete ? '' : ' (partial)'} · ${duration(s.summary.wall_seconds)}`;
    $('[data-h-time]', el).textContent = `${s.clock.mode === 'live' ? 'Live' : 'Replay'} · ${new Date(s.clock.at).toLocaleString()}`;
    $('[data-h-time]', el).title = s.clock.at;
    $('[data-h-coverage]', el).textContent = `History ${s.coverage.complete ? 'complete' : 'partial'} · ${(s.coverage.warnings || []).map((w) => w.replaceAll('_', ' ')).join(' · ')}${s.revision.consistent ? '' : ' · Snapshot changed while loading'}`;
    $('[data-h-readonly]', room).hidden = s.clock.mode !== 'replay';
    paintAgents(); paintTimeline(); paintFeed(); paintMorning(); showTab(tab); showCenter(center);
  }
  function paintAgents() {
    const items = stations(state), layers = [...new Set(items.map((a) => a.layer))];
    const positions = new Map();
    const rows = Math.max(1, ...layers.map((l) => items.filter((a) => a.layer === l).length));
    const width = Math.max(240, layers.length * 220), height = rows * 170;
    const edges = handoffs(state.handoff_edges);
    items.forEach((a) => { const siblings = items.filter((b) => b.layer === a.layer); positions.set(a.id, { x: layers.indexOf(a.layer) * 220 + 10, y: siblings.indexOf(a) * 170 + 10 }); });
    const fresh = state.clock.mode === 'live' && previousEdges.size ? new Set(state.handoff_edges.filter((e) => !previousEdges.has(e.id)).map((e) => `${e.from_agent}>${e.to_agent}:${e.kind}`)) : new Set();
    previousEdges = new Set(state.handoff_edges.map((e) => e.id));
    refreshHTML($('[data-h-agents]', stage), `<p class="muted small">Stations by role · Select a station to filter Feed and Timeline. Dashed arrows are dependency hints.</p><div class="harness-stations" style="width:${width}px;height:${height}px"><svg width="${width}" height="${height}" aria-label="Agent handoffs"><defs><marker id="h-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0 0 L7 3 L0 6" fill="var(--text-3)"/></marker></defs>${edges.map((e) => { const a = positions.get(e.from_agent), b = positions.get(e.to_agent); if (!a || !b) return ''; return `<path class="harness-edge edge-${esc(e.status)} ${fresh.has(e.id) ? 'handoff-new' : ''}" d="M${a.x + 180},${a.y + 65} C${a.x + 225},${a.y + 65} ${b.x - 40},${b.y + 65} ${b.x},${b.y + 65}" stroke-width="${1 + Math.min(6, e.count)}" ${e.kind === 'published' ? '' : 'stroke-dasharray="5 5"'} marker-end="url(#h-arrow)"><title>${esc(e.from_agent)} to ${esc(e.to_agent)}: ${e.count} published or consumed handoffs; ${e.evidence} ${esc(e.kind)} edges (${esc(e.historical_certainty || 'partial')})</title></path>`; }).join('')}</svg>${items.map((a) => { const p = positions.get(a.id); const active = state.timeline_segments.filter((g) => g.agent === a.id && g.open).map((g) => Date.parse(g.start)); const elapsed = active.length ? duration(Math.max(0, (Date.parse(state.clock.at) - Math.min(...active)) / 1000)) : ''; const highlighted = edges.some((e) => fresh.has(e.id) && [e.from_agent, e.to_agent].includes(a.id)); return `<button class="card harness-station state-${esc(a.status)} ${highlighted ? 'handoff-new' : ''}" data-h-agent="${esc(a.id)}" aria-pressed="${agent === a.id}" style="left:${p.x}px;top:${p.y}px"><b>${esc(a.label || a.id)}</b><span>${esc(a.model || a.id)} · ${esc(a.role)}</span><strong>${esc(stateWord(a.status))}${elapsed ? ` · ${esc(elapsed)}` : ''}</strong><span>${a.tasks_done ?? 0} done · ${money(a.usage?.total_cost_usd)}</span><small title="${esc(a.tasks?.join(', ') || '')}">${esc(a.tasks?.join(', ') || 'No active tasks')}</small></button>`; }).join('')}</div>${items.length ? '' : '<p>No agents recorded at this time.</p>'}`);
    if (fresh.size) setTimeout(() => stage?.querySelectorAll('.handoff-new').forEach((node) => node.classList.remove('handoff-new')), 1500);
  }
  function paintTimeline() {
    const s = state, [start, end] = bounds({ ...s, coverage: baseline?.coverage || s.coverage });
    const segments = s.timeline_segments.filter((x) => !agent || x.agent === agent);
    const rows = [...new Set(segments.slice().sort((a, b) => Date.parse(a.start) - Date.parse(b.start)).map((x) => x.unit || x.task))];
    const x = (at) => 210 + Math.max(0, Math.min(1, (Date.parse(at) - start) / (end - start))) * 680;
    const bars = segments.map((g) => { const y = rows.indexOf(g.unit || g.task) * 32 + 12, left = x(g.start), right = x(g.end), stuck = s.agents.find((a) => a.id === g.agent)?.stuck?.suspected && g.open; return `<g class="harness-bar state-${esc(g.state)}"><title>${esc(g.request)} · ${esc(g.task)} · ${esc(g.agent)} · ${esc(g.state)} ${esc(g.start)}–${esc(g.end)} (attempt ${g.attempt})</title><rect x="${left}" y="${y}" width="${Math.max(2, right - left - 2)}" height="12" rx="3" ${['blocked', 'pending', 'cancelled', 'stopped'].includes(g.state) ? 'fill="url(#h-wait)"' : ''}/>${g.state === 'failed' ? `<path d="M${right - 4},${y + 2} l8,8 m0,-8 l-8,8" stroke="var(--fail)" stroke-width="2"/>` : ''}${stuck ? `<circle cx="${right}" cy="${y + 6}" r="7" fill="none" stroke="var(--warn)"/><path d="M${right},${y + 1} v5 l4,2" stroke="var(--warn)"/>` : ''}</g>`; }).join('');
    refreshHTML($('[data-h-timeline]', room), `<h4>Timeline ${agent ? `· ${esc(agent)}` : ''}</h4><div class="harness-timeline-scroll"><svg viewBox="0 0 920 ${Math.max(50, rows.length * 32 + 25)}" role="img" aria-label="Task attempt timeline"><defs><pattern id="h-wait" width="6" height="6" patternUnits="userSpaceOnUse"><path d="M0,6 L6,0" stroke="var(--warn)" stroke-width="3"/></pattern></defs>${rows.map((id, i) => { const g = segments.find((g) => (g.unit || g.task) === id); return `<text x="4" y="${i * 32 + 23}">${esc(g.request)} · ${esc(g.task)} · ${esc(g.agent)}</text>`; }).join('')}${bars}<path d="M${x(s.clock.at)},0 V${rows.length * 32 + 25}" stroke="var(--focus)" stroke-width="2"/></svg></div><label>Replay time <input data-h-seek type="range" aria-label="Replay time" min="${Math.floor(start / 1000)}" max="${Math.ceil(end / 1000)}" value="${Math.floor(Date.parse(s.clock.at) / 1000)}" step="1" aria-valuetext="${esc(s.clock.at)}"></label><p class="muted small">${new Date(start).toLocaleString()} → ${new Date(end).toLocaleString()} · X failed · ◷ possibly stuck</p>${rows.length ? '' : '<p>No attempts recorded at this time.</p>'}`);
  }
  function paintFeed() {
    const host = $('[data-h-feed]', room), events = filterFeed(state.feed_events, { agent, type, errors, search });
    const controls = `<div class="row gap wrap"><label>Agent <select data-h-agent-filter><option value="">All agents</option>${[...new Set([...(baseline?.agents || state.agents).map((a) => a.id), ...state.feed_events.map((e) => e.agent).filter(Boolean)])].map((id) => `<option ${id === agent ? 'selected' : ''}>${esc(id)}</option>`).join('')}</select></label><label>Type <select data-h-type><option value="">All types</option>${['task', 'turn', 'request', 'decision', 'approval', 'permission', 'handoff', 'budget', 'plan', 'chat', 'review', 'limit', 'plugin'].map((t) => `<option ${type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></label><label><input type="checkbox" data-h-errors ${errors ? 'checked' : ''}> Errors only</label><label>Search <input data-h-search type="search" value="${esc(search)}"></label></div>`;
    const content = `<p class="muted small">Filters apply to loaded events. ${agent ? 'Select the same station again to clear its filter.' : ''}</p><ol class="harness-feed">${events.map((e) => `<li class="severity-${esc(e.severity)}"><time title="${esc(e.at)}">${esc(new Date(e.at).toLocaleTimeString())}</time> <span>${e.severity === 'error' ? 'Error: ' : ''}${esc(feedSentence(e))}</span> <small class="mono">${esc(e.task || e.request || 'Plan')} · #${e.seq}</small></li>`).join('')}</ol>${events.length ? '' : '<p>No events match these filters.</p>'}${state.feed_page.has_more ? '<button class="btn sm" data-h-older>Load older</button>' : ''}`;
    if (!host.querySelector('[data-h-feed-controls]')) host.innerHTML = '<div data-h-feed-controls></div><div data-h-feed-list></div>';
    refreshHTML($('[data-h-feed-controls]', host), controls);
    refreshHTML($('[data-h-feed-list]', host), content);
  }
  function paintMorning() {
    const s = state.summary, m = s.morning, host = $('[data-h-morning]', room);
    const day = new Date().toISOString().slice(0, 10), dismissKey = `harness:morning:${project}:${sid()}:${day}`;
    let dismissed = false; try { dismissed = localStorage.getItem(dismissKey) === 'yes'; } catch { /* optional storage */ }
    if (!m?.finished || tab === 'feed' && dismissed) { host.replaceChildren(); return; }
    refreshHTML(host, `<section class="card"><h3>Morning summary · ${esc(new Date(state.clock.at).toLocaleDateString())} · ${esc(state.scope.request || 'All requests')}</h3><p>${s.tasks.done} of ${s.tasks.total} tasks done, ${s.tasks.failed} failed, ${state.view.blockers?.length || 0} decisions waiting, ${s.stuck || 0} possibly stuck, ${s.tasks.running || 0} still running. ${m.handoffs} published handoffs; ${m.dependency_edges} dependency edges.</p><p>Spend: ${money(s.usage.total_cost_usd)}${s.usage.complete ? '' : ' (partial)'} · Run time: ${duration(s.wall_seconds)}</p><p class="muted small">${esc(m.note || '')} Based on events ${state.coverage.first_seq}–${state.coverage.last_seq}.</p>${(m.errors || []).map((e) => `<p class="fail-t">${esc(feedSentence(e))}</p>`).join('')}<button class="btn sm" data-h-tab="blockers">Open Blockers</button> <button class="btn sm" data-h-tab="timeline">Open Timeline</button> <button class="btn sm" data-h-dismiss>Dismiss for today</button></section>`);
    $('[data-h-dismiss]', host).onclick = () => { try { localStorage.setItem(dismissKey, 'yes'); } catch { /* optional storage */ } host.replaceChildren(); };
  }
  function pause() { playing = false; clearInterval(playback); $('[data-h-play]', el).textContent = 'Play'; $('[data-h-play]', el).setAttribute('aria-pressed', 'false'); }
  el.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.hTab) { showTab(b.dataset.hTab); if (state) paintMorning(); }
    if (b.dataset.hCenter) showCenter(b.dataset.hCenter);
    if ('hAgent' in b.dataset) { agent = agent === b.dataset.hAgent ? '' : b.dataset.hAgent; paint(); }
    if ('hLive' in b.dataset) { pause(); navigate({ at: null }); schedule(); }
    if ('hOlder' in b.dataset) load(true);
    if ('hExpand' in b.dataset) { expanded = !expanded; shell.classList.toggle('harness-expanded', expanded); b.setAttribute('aria-pressed', String(expanded)); b.textContent = expanded ? 'Restore' : 'Expand'; resize.sync(); }
    if ('hClose' in b.dataset) { collapsed = !collapsed; grid.classList.toggle('harness-room-collapsed', collapsed); b.setAttribute('aria-expanded', String(!collapsed)); b.textContent = collapsed ? 'Open Control Room' : 'Collapse'; resize.sync(); }
    if ('hPlay' in b.dataset && state) {
      if (playing) { pause(); return; }
      playing = true; b.textContent = 'Pause'; b.setAttribute('aria-pressed', 'true');
      let cursor = query().has('at') ? Date.parse(query().get('at')) : bounds(state)[0], last = performance.now();
      playback = setInterval(() => { const now = performance.now(); cursor += (now - last) * speed; last = now; const end = bounds({ ...state, coverage: baseline?.coverage || state.coverage })[1]; if (cursor >= end) { pause(); navigate({ at: null }, { replace: true }); } else navigate({ at: new Date(cursor).toISOString() }, { replace: true }); schedule(); }, 500);
    }
  });
  el.addEventListener('change', (e) => {
    const t = e.target;
    if (t.matches('[data-h-scope]')) { pause(); navigate({ request: t.value, at: null }); schedule(); }
    if (t.matches('[data-h-speed]')) speed = Number(t.value);
    if (t.matches('[data-h-agent-filter]')) agent = t.value;
    if (t.matches('[data-h-type]')) type = t.value;
    if (t.matches('[data-h-errors]')) errors = t.checked;
    if (t.matches('[data-h-agent-filter], [data-h-type], [data-h-errors]')) paint();
  });
  el.addEventListener('input', (e) => {
    if (e.target.matches('[data-h-search]')) { search = e.target.value; clearTimeout(seekTimer); seekTimer = setTimeout(paintFeed, 250); }
    if (e.target.matches('[data-h-seek]')) { pause(); const at = new Date(Number(e.target.value) * 1000).toISOString(); e.target.setAttribute('aria-valuetext', at); clearTimeout(seekTimer); seekTimer = setTimeout(() => { navigate({ at }); schedule(); }, 180); }
  });
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && expanded) { expanded = false; shell.classList.remove('harness-expanded'); $('[data-h-expand]', room).setAttribute('aria-pressed', 'false'); $('[data-h-expand]', room).textContent = 'Expand'; resize.sync(); }
    if (e.target.matches('[data-h-seek]') && e.shiftKey && ['ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); e.target.value = Number(e.target.value) + (e.key === 'ArrowRight' ? 60 : -60); e.target.dispatchEvent(new Event('input', { bubbles: true })); }
    if (e.target.matches('[role=tab]') && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) { e.preventDefault(); const tabs = [...e.target.parentElement.querySelectorAll('[role=tab]')]; const i = tabs.indexOf(e.target); const next = tabs[e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length]; next.click(); next.focus(); }
  });
  const events = (e) => { if (active && (!e.detail?.project || e.detail.project === project) && !query().has('at')) schedule(); };
  const visible = () => { if (!document.hidden && active) schedule(); };
  window.addEventListener('alfrd-events', events); document.addEventListener('visibilitychange', visible);
  function stop() { active = false; pause(); clearTimeout(timer); clearTimeout(seekTimer); controller?.abort(); generation++; chat.suspend(); resize?.suspend(); }
  return { project, stop, destroy() { stop(); chat.destroy(); resize?.destroy(); el.remove(); window.removeEventListener('alfrd-events', events); document.removeEventListener('visibilitychange', visible); }, render() {
    if (!active) { active = true; chat.resume(); resize?.resume(); key = ''; }
    chat.render(chat.root, ctx); setup();
    const next = JSON.stringify([project, sid(), query().get('request'), query().get('at')]);
    if (next !== key) { key = next; controller?.abort(); generation++; schedule(); }
    if (state) { showTab(tab); showCenter(center); }
  } };
}
