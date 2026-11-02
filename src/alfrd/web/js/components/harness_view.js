import { $, esc, routeHash, setRoute } from "../utils/dom.js";
import { refreshHTML } from "../utils/refresh.js";
import { server } from "../data/server.js";
import { defaultRequest } from "../data/harness.js";
import { createChatView } from "./chat_view.js";
import { createRunCard } from "./harness_run.js";
import { renderHome } from "./harness_home.js";

let current = null;
const panels = new Map();
const query = () => new URLSearchParams(location.hash.split("?")[1] || "");
export function leave() { document.body.classList.remove("shell-harness"); current?.stop(); }
export function render(el, ctx) {
  if (ctx.state.mode !== "server") { el.textContent = "Harness needs alfrd serve."; return; }
  const project = ctx.activeProject(); if (!project) return;
  const sid = decodeURIComponent(location.hash.split("?")[0].split("/")[2] || "");
  const key = `${project}:${sid}`;
  if (current?.key !== key || !current.root.isConnected) { current?.destroy(); current = createHarness(el, ctx, project, sid, key); }
  document.body.classList.add("shell-harness"); current.render();
}
function createHarness(host, ctx, project, sid, key) {
  const root = document.createElement("div"); root.className = "h-harness"; host.replaceChildren(root);
  const homeURL = routeHash("harness", project, ctx.state.selectedTarget);
  root.innerHTML = `<header class="h-top"><b>ALFRD Harness</b><span class="h-project">${esc(ctx.projectName?.(project) || project)}</span><a href="${esc(homeURL)}" ${!sid ? `aria-current="page"` : ""}>Home</a>${sid ? '<span class="h-conversation">Conversation</span>' : ""}<span class="h-connection" data-h-connection role="status">Connecting…</span><span data-h-older hidden title="This server is older; some details are unavailable">ⓘ Older server</span><a class="h-studio" href="${esc(routeHash(sid ? `chat/${sid}` : "", project, ctx.state.selectedTarget))}">Open in Studio ↗</a></header><div class="h-content"></div>`;
  const content = $(".h-content", root);
  let active = true, timer, controller, loading = false, generation = 0, failures = 0, route = "", A, B, run, slots, drawerOpener, previousCount = -1, next = 0, closed = false;
  let panel = panels.get(key) || { checklist: false, decisions: innerWidth > 900 };
  const decisionDrafts = new Map();
  function restoreDecisionDrafts() {
    for (const form of slots?.decisions.querySelectorAll("[data-decision]") || []) {
      const draft = decisionDrafts.get(form.dataset.decision);
      if (!draft) continue;
      const answer = form.querySelector("[name=answer]"); if (answer) answer.value = draft.answer;
      for (const radio of form.querySelectorAll("[name=choice]")) radio.checked = radio.value === draft.choice;
    }
  }
  const navigate = (changes, opts = { replace: true }) => {
    const params = query(); for (const [name, value] of Object.entries(changes)) value ? params.set(name, value) : params.delete(name);
    const base = routeHash(`harness/${encodeURIComponent(sid)}`, project, ctx.state.selectedTarget);
    const merged = new URLSearchParams(base.split("?")[1]); for (const [name, value] of params) merged.set(name, value);
    for (const [name, value] of Object.entries(changes)) if (!value) merged.delete(name);
    setRoute(`${base.split("?")[0]}?${merged}`, opts); ctx.update();
  };
  if (!sid) {
    const options = { onStatus(text) { $("[data-h-connection]", root).textContent = text; } };
    let home = renderHome(content, ctx, project, options), running = true;
    return { key, root, render() { if (!running) { home = renderHome(content, ctx, project, options); running = true; } }, stop() { home.destroy(); running = false; }, destroy() { home.destroy(); root.remove(); } };
  }
  const chat = createChatView({ projected: true, layout: "harness", readOnly: () => query().has("at"), refresh: () => schedule(),
    onSession(id, opts) { if (id && id !== sid) { ctx.state.chatSession = id; setRoute(routeHash(`harness/${encodeURIComponent(id)}`, project, ctx.state.selectedTarget), opts); ctx.update(); } },
    onTask: openDrawer, onTaskClose: hideDrawer });
  content.append(chat.root);
  function setup() {
    if (slots) return;
    slots = chat.slots(); if (!slots?.top) { slots = null; return; }
    const replay = document.createElement("div"); replay.className = "h-replay"; replay.dataset.hReplay = ""; replay.hidden = true;
    slots.top.before(replay);
    const backdrop = document.createElement("button"); backdrop.type = "button"; backdrop.className = "h-backdrop"; backdrop.dataset.hBackdrop = ""; backdrop.setAttribute("aria-label", "Close task details"); backdrop.hidden = true; slots.drawer.before(backdrop);
    slots.attention.innerHTML = `<span data-h-count></span><button type="button" data-h-next>Next</button><button type="button" data-h-decisions aria-controls="chat-decisions" aria-expanded="false">Show ▾</button><span data-h-mail></span><span class="sr-only" data-h-announce aria-live="polite" aria-atomic="true"></span>`;
    run = createRunCard(slots.top, { onTask: (row) => chat.openTask(row), onRequest: (id) => navigate({ request: id }), onAttention: () => attention(true), onToggle(open) { panel.checklist = open; if (open && innerWidth <= 900) panel.decisions = false; syncPanels(); } });
    requestAnimationFrame(() => $("#chat-input", root)?.focus());
  }
  function syncPanels() {
    if (!slots) return;
    const count = A?.view.attention?.count ?? A?.view.blockers?.filter((d) => d.needs === "user").length ?? 0;
    slots.decisions.hidden = !panel.decisions || !count;
    const button = $("[data-h-decisions]", root); button.setAttribute("aria-expanded", String(panel.decisions && count > 0)); button.textContent = panel.decisions ? "Hide ▴" : "Show ▾";
    panels.set(key, panel);
  }
  function attention(open = !panel.decisions) { panel.decisions = open; if (open && innerWidth <= 900 && run?.isOpen()) run.toggle(false); syncPanels(); }
  function openDrawer() {
    if (!slots) return;
    if (slots.drawer.hidden) drawerOpener = document.activeElement;
    slots.drawer.hidden = false; $("[data-h-backdrop]", root).hidden = false;
    const row = $("#chat-task [data-transcript]", root)?.dataset.transcript;
    if (row && query().get("task") !== row) navigate({ task: row });
    slots.drawer.focus({ preventScroll: true });
  }
  function hideDrawer() {
    if (!slots || closed) return; closed = true;
    slots.drawer.hidden = true; $("[data-h-backdrop]", root).hidden = true;
    if (query().has("task")) navigate({ task: null });
    (drawerOpener?.isConnected ? drawerOpener : $("[data-h-checklist]", root))?.focus({ preventScroll: true }); closed = false;
  }
  function closeDrawer() { chat.closeTask(); }
  function nextAttention() {
    attention(true); const items = A?.view.attention?.items || [];
    const item = items[next++ % (items.length || 1)];
    if (item?.source === "mailbox") { const button = document.createElement("button"); button.dataset.mailJump = item.id; slots.attention.append(button); button.click(); button.remove(); return; }
    const form = item ? slots.decisions.querySelector(`[data-decision="${CSS.escape(item.id)}"]`) : slots.decisions.querySelector("form");
    form?.scrollIntoView({ block: "nearest" }); form?.querySelector("textarea,input,button")?.focus();
  }
  function schedule() { if (!active || query().has("at")) return; clearTimeout(timer); timer = setTimeout(load, 100); }
  async function get(params, signal) {
    const response = await server.request(`/api/studio/projects/${encodeURIComponent(project)}/chat/sessions/${encodeURIComponent(sid)}/harness/state?${params}`, { signal, headers: { Accept: "application/json" } });
    const body = await response.json(); if (!response.ok) throw Error(body?.error?.message || `Harness: ${response.status}`); return body;
  }
  async function load(onlyRequest = false) {
    if (!active || document.hidden) return;
    clearTimeout(timer); controller?.abort(); controller = new AbortController(); const gen = ++generation; loading = true;
    const q = query(), at = q.get("at"), params = new URLSearchParams({ scope: "all", limit: "1" }); if (at) params.set("at", at);
    try {
      const all = onlyRequest && A ? A : await get(params, controller.signal);
      const request = defaultRequest(all.view.requests || [], q.get("request"));
      const scoped = new URLSearchParams({ scope: "request", request: request?.id || "" }); if (at) scoped.set("at", at);
      const selected = request ? await get(scoped, controller.signal) : null;
      if (!active || gen !== generation) return;
      A = all; B = selected; failures = 0;
      if (!onlyRequest) {
        const scroll = slots.decisions.scrollTop;
        chat.setProjection({ ...A, view: { ...A.view, blockers: (A.view.blockers || []).filter((d) => d.needs === "user") } });
        for (const id of decisionDrafts.keys()) if (!A.view.blockers?.some((d) => d.id === id)) decisionDrafts.delete(id);
        restoreDecisionDrafts(); slots.decisions.scrollTop = scroll;
      }
      run.update(B, A, { replay: !!at }); if (B && panel.checklist && !run.isOpen()) run.toggle(true);
      const count = A.view.attention?.count ?? (A.view.blockers || []).filter((d) => d.needs === "user").length;
      slots.attention.hidden = !count && !at;
      $("[data-h-count]", root).textContent = at ? "Mailbox questions are not shown in replay." : `⚑ ${count} ${count === 1 ? "needs" : "need"} you`;
      for (const b of slots.attention.querySelectorAll("[data-h-next], [data-h-decisions]")) b.hidden = !count;
      if (count !== previousCount) { $("[data-h-announce]", root).textContent = `${count} ${count === 1 ? "item needs" : "items need"} you`; previousCount = count; }
      refreshHTML($("[data-h-mail]", root), (A.view.attention?.items || []).filter((i) => i.source === "mailbox" && !i.decision).map((i) => `<button type="button" data-mail-jump="${esc(i.id)}">${i.kind === "permission_request" ? "Permission request" : "Question"} in the conversation</button>`).join(""));
      if (q.get("attention")) { attention(true); navigate({ attention: null }); }
      syncPanels();
      const banner = $("[data-h-replay]", root); banner.hidden = !at;
      refreshHTML(banner, at ? `Viewing ${esc(new Date(at).toLocaleString())} · read-only <button type="button" data-h-now>Return to now</button>` : "");
      $("[data-h-older]", root).hidden = A.view.attention !== undefined;
      $("[data-h-connection]", root).title = A.view.attention === undefined ? "This server is older; some details are unavailable" : "";
      $("[data-h-connection]", root).textContent = `Connected · updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
      if (q.get("task") && slots.drawer.hidden) { if (A.view.tasks.some((t) => t.row === q.get("task"))) chat.openTask(q.get("task")); else navigate({ task: null }); }
      if (!at) timer = setTimeout(load, 1000 * (A.settings?.poll_seconds || 2));
    } catch (error) { if (error.name !== "AbortError" && active && gen === generation) { failures++; $("[data-h-connection]", root).textContent = failures >= 3 ? "Offline · retrying" : "Reconnecting…"; $("[data-h-connection]", root).title = error.message; timer = setTimeout(load, 5000); } }
    finally { if (gen === generation) loading = false; }
  }
  const click = (e) => { const b = e.target.closest("button"); if (!b) return;
    if (b.hasAttribute("data-h-decisions")) attention(); else if (b.hasAttribute("data-h-next")) nextAttention(); else if (b.hasAttribute("data-h-backdrop")) closeDrawer(); else if (b.hasAttribute("data-h-now")) navigate({ at: null }); };
  const keydown = (e) => {
    if (slots && !slots.drawer.hidden) {
      if (e.key === "Escape") { e.preventDefault(); closeDrawer(); }
      else if (e.key === "Tab") { const focusable = [...slots.drawer.querySelectorAll("button,a,textarea,input,select,[tabindex]")].filter((n) => !n.disabled && !n.closest("[inert]") && n.getClientRects().length); const first = focusable[0], last = focusable.at(-1); if (!first) { e.preventDefault(); slots.drawer.focus(); } else if (e.shiftKey && (document.activeElement === first || document.activeElement === slots.drawer)) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && (document.activeElement === last || document.activeElement === slots.drawer)) { e.preventDefault(); first.focus(); } }
      return;
    }
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.target.closest("input,textarea,select,[contenteditable=true],details[open],.chat-mention-menu")) return;
    if (e.key === "/") { e.preventDefault(); $("#chat-input", root)?.focus(); }
    else if (e.key.toLowerCase() === "n") { e.preventDefault(); nextAttention(); }
    else if (e.key.toLowerCase() === "c") { e.preventDefault(); run?.toggle(); }
    else if (e.key === "Escape") { run?.toggle(false); attention(false); }
  };
  const events = (e) => { if (!e.detail?.project || e.detail.project === project) schedule(); };
  const visibility = () => { if (!document.hidden) schedule(); };
  let narrow = innerWidth <= 900;
  const resize = () => { const next = innerWidth <= 900; if (next && !narrow) { run?.toggle(false); attention(false); } narrow = next; };
  const editDecision = (e) => { const form = e.target.closest("[data-decision]"); if (form) decisionDrafts.set(form.dataset.decision, { answer: form.querySelector("[name=answer]")?.value || "", choice: form.querySelector("[name=choice]:checked")?.value }); };
  const blurDecision = (e) => { if (e.target.closest("#chat-decisions")) setTimeout(() => { if (active) restoreDecisionDrafts(); }, 0); };
  root.addEventListener("click", click); root.addEventListener("keydown", keydown);
  root.addEventListener("input", editDecision); root.addEventListener("change", editDecision); root.addEventListener("focusout", blurDecision);
  window.addEventListener("alfrd-events", events); document.addEventListener("visibilitychange", visibility);
  window.addEventListener("resize", resize);
  return { key, root,
    render() { const was = active; active = true; if (!was) chat.resume(); ctx.state.chatSession = sid; chat.render(chat.root, ctx); setup(); const nextRoute = JSON.stringify([query().get("at"), query().get("request")]); if (!A || !was || route !== nextRoute) { const only = A && was && JSON.parse(route)[0] === JSON.parse(nextRoute)[0]; route = nextRoute; if (!loading || A) load(only); } },
    stop() { active = false; clearTimeout(timer); controller?.abort(); generation++; loading = false; chat.suspend(); },
    destroy() { this.stop(); run?.destroy(); chat.destroy(); root.removeEventListener("click", click); root.removeEventListener("keydown", keydown); root.removeEventListener("input", editDecision); root.removeEventListener("change", editDecision); root.removeEventListener("focusout", blurDecision); window.removeEventListener("alfrd-events", events); window.removeEventListener("resize", resize); document.removeEventListener("visibilitychange", visibility); root.remove(); }
  };
}
