// Command palette (Ctrl/Cmd+K): go to a project, target, project code, work dir,
// step, log or view, or run an action. Actions call the same functions as the
// buttons (planAct, …), so write access, CSRF and confirm dialogs still apply.
// Other modules add items with ctx.palette.register(fn). `?` (or Tab) turns the
// query into a full-text search. Loaded with import() on first use.

import { $, esc, icon, loadCss, storage } from "../utils/dom.js";
import { fuzzyRank } from "../data/fuzzy.js";
import { projectLogs, openFileFull } from "./logview.js";
import { planAct, planOf, plansAvailable } from "./plans.js";
import { showCode } from "./overview.js";
import { showStep } from "./canvas.js";

const RECENT = "palette:recent";
const MAX_RECENT = 12;

const projectOf = (ctx) => (ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : ctx.target()?.project || ctx.projects()[0]?.id || null);

function pickTarget(ctx, t, view) {
  ctx.update((s) => { s.selectedTarget = t.id; });
  if (view) ctx.goTo(view);
}

/** The built-in items (lazily: called on every keystroke, cheap). */
function builtIns(ctx) {
  const items = [];
  const add = (group, it) => items.push({ group, ...it, id: `${group}:${it.key ?? it.label}` });
  const here = ctx.state.view;
  ctx.views.forEach((v) => add("view", { label: `Go to ${v.label}`, icon: v.icon, run: () => ctx.goTo(v.id), key: v.id }));
  ctx.projects().forEach((p) => add("project", { label: p.name, detail: `project · ${p.targets.length} targets`, icon: "folder", key: p.id,
    run: () => ctx.update((s) => { s.selectedProject = p.id; }) }));
  const codes = new Map();
  ctx.state.targets.forEach((t) => {
    add("target", { label: t.name, detail: `target · ${ctx.projectName(t.project)}${(t.codes || []).length ? ` · ${(t.codes || []).map((c) => c.code || c).join(", ")}` : ""}`,
      icon: "target", key: t.id, run: () => pickTarget(ctx, t, ["overview", "config"].includes(here) ? "metadata" : here) });
    (t.codes || []).forEach((c) => { const code = c.code || c; codes.set(code, (codes.get(code) || 0) + 1); });
  });
  codes.forEach((n, code) => add("code", { label: code, detail: `project code · ${n} target(s)`, icon: "filter", key: code, run: () => showCode(ctx, code) }));
  Object.entries(ctx.state.avica || {}).forEach(([p, index]) => Object.values(index?.codes || {}).forEach((c) => {
    if (!c.workdir) return;
    add("workdir", { label: `${c.code}/${c.workdir}`, detail: `work dir · ${(c.targets || []).slice(0, 3).join(", ")}${(c.targets || []).length > 3 ? " …" : ""}`, icon: "folder", key: `${p}:${c.id}`,
      run: () => { const t = ctx.state.targets.find((x) => x.project === p && (c.targets || []).includes(x.name)); if (t) pickTarget(ctx, t, "metadata"); } });
  }));
  (ctx.state.workflow?.steps || []).forEach((s) => add("step", { label: s.key, detail: `step · ${s.label || ""}`, icon: "pipeline", key: s.key,
    run: () => showStep(ctx, s.key) }));
  const p = projectOf(ctx);
  if (p) {
    projectLogs(ctx, p).slice(0, 2000).forEach((f) => add("log", { label: f.name || f.rel.split("/").pop(), detail: `log · ${[f.target, ...(f.steps || [])].filter(Boolean).join(" · ")} · ${f.rel}`,
      icon: "log", key: f.rel, run: () => openFileFull(ctx, p, f.rel).catch((e) => ctx.toast(e.message, "warn")) }));
    const act = (label, action, iconName, detail = "action") => add("action", { label, detail, icon: iconName, key: action, run: () => planAct(ctx, p, action) });
    if (plansAvailable(ctx, p)) {
      const plan = planOf(p)?.plan;
      act("Run…", "new", "play", "action · new plan");
      if (plan) {
        if (plan.status === "running") act("Pause plan", "pause", "pause", `action · ${plan.id}`);
        if (["paused", "interrupted"].includes(plan.status)) act("Resume plan", "resume", "play", `action · ${plan.id}`);
        if (["running", "paused", "interrupted"].includes(plan.status)) act("Cancel plan", "cancel", "stop", `action · ${plan.id} (asks first)`);
        act("Add target to plan", "add-row", "plus", `action · ${plan.csv}`);
      }
    }
    if (ctx.state.mode === "server") add("action", { label: "Search file contents…", detail: "action · logs, CSVs, alfrd.yaml, notes (or start with ?)", icon: "search", key: "search", run: () => ctx.openSearch("") });
    if (ctx.state.mode === "server") add("action", { label: "alfrd.yaml history", detail: "action · versions, diff, restore", icon: "clock", run: () => ctx.openHistory(p) });
  }
  add("action", { label: "Re-scan", detail: "action · re-read the project folder", icon: "sync", run: () => ctx.rescan() });
  add("action", { label: ctx.state.prefs.live !== false ? "Turn live updates off" : "Turn live updates on", detail: "action", icon: "power", key: "live",
    run: () => ctx.setLive(ctx.state.prefs.live === false) });
  add("action", { label: "Settings", detail: "action · project settings (alfrd.yaml)", icon: "gear", run: () => ctx.goTo("config") });
  return items;
}

function allItems(ctx, query) {
  const extra = ctx.palette.providers.flatMap((fn) => { try { return fn(ctx, query) || []; } catch { return []; } })
    .map((it) => ({ group: "more", ...it, id: it.id || `${it.group || "more"}:${it.label}` }));
  return [...builtIns(ctx), ...extra];
}

export function openPalette(ctx, initial = "") {
  loadCss("css/lazy.css");
  const recent = storage.get(RECENT, []) || [];
  let shown = [];
  let active = 0;
  ctx.modal(`<div class="pal" role="dialog" aria-label="Command palette">
      <div class="pal-in">${icon("search")}<input id="pal-q" class="input" role="combobox" aria-expanded="true" aria-controls="pal-list" aria-autocomplete="list"
        placeholder="Go to a target, code, step, log, view — or type an action. ? searches file contents" autocomplete="off" spellcheck="false" value="${esc(initial)}"></div>
      <ul id="pal-list" role="listbox" aria-label="Results"></ul>
      <p class="pal-f muted small"><kbd>↑</kbd><kbd>↓</kbd> move · <kbd>Enter</kbd> open · <kbd>?</kbd> or <kbd>Tab</kbd> full-text search · <kbd>Esc</kbd> close</p>
    </div>`, (root, close) => {
    const input = $("#pal-q", root);
    const list = $("#pal-list", root);
    const search = (q) => { close(); ctx.openSearch ? ctx.openSearch(q) : ctx.toast("Full-text search needs alfrd serve", "warn"); };
    const choose = (it) => {
      if (!it) return;
      close();
      storage.set(RECENT, [it.id, ...recent.filter((x) => x !== it.id)].slice(0, MAX_RECENT));
      Promise.resolve().then(() => it.run?.()).catch((e) => ctx.toast(e.message, "fail"));
    };
    const draw = () => {
      const q = input.value.trim();
      if (q.startsWith("?")) {
        shown = [{ id: "search", label: q.slice(1).trim() ? `Search file contents for “${q.slice(1).trim()}”` : "Type what to search for in logs, CSVs, alfrd.yaml and notes",
          icon: "search", group: "search", run: () => q.slice(1).trim() && search(q.slice(1).trim()) }];
      } else {
        const items = allItems(ctx, q);
        if (q) shown = fuzzyRank(q, items, 60);
        else {
          const byId = new Map(items.map((it) => [it.id, it]));
          const rec = recent.map((id) => byId.get(id)).filter(Boolean).map((it) => ({ ...it, recent: true }));
          shown = [...rec, ...items.filter((it) => it.group === "view" || it.group === "action").filter((it) => !recent.includes(it.id))].slice(0, 40);
        }
      }
      active = Math.min(active, Math.max(0, shown.length - 1));
      list.innerHTML = shown.map((it, i) => `<li role="option" id="pal-o${i}" data-i="${i}" aria-selected="${i === active}" class="${i === active ? "on" : ""}">${icon(it.icon || "caret")}<span class="pal-l">${esc(it.label)}</span><span class="pal-d muted small">${esc(it.recent ? `recent · ${it.detail || it.group}` : it.detail || it.group || "")}</span></li>`).join("")
        || '<li class="muted small pal-none">No match. Start with ? to search file contents.</li>';
      input.setAttribute("aria-activedescendant", shown.length ? `pal-o${active}` : "");
      list.querySelector("li.on")?.scrollIntoView({ block: "nearest" });
    };
    input.addEventListener("input", () => { active = 0; draw(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + shown.length) % Math.max(1, shown.length);
        draw();
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(shown[active]);
      } else if (e.key === "Tab" && input.value.trim() && !input.value.startsWith("?")) {
        e.preventDefault();
        search(input.value.trim());
      }
    });
    list.addEventListener("mousedown", (e) => { const li = e.target.closest("li[data-i]"); if (li) { e.preventDefault(); choose(shown[Number(li.dataset.i)]); } });
    draw();
    input.focus();
  }, "pal-modal");
}
