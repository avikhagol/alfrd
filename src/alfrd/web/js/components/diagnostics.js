// Results → collections (kind: collection artifacts, e.g. rPicard diagnostics_*).
// Loaded when a collection card is opened. Nothing is fetched before that:
// first the runs (one folder each), then one run's file list, then images as
// they scroll into view (60 at a time). "Compare" shows a second run next to
// the first, pairing files with the same folder and name.

import { $, $$, on, esc, icon, bytes, when, loadCss } from "../utils/dom.js";
import { parseCsv } from "../utils/csv_parser.js";
import { server } from "../data/server.js";

const PAGE = 60;
const TEXT_MAX = 400000;

/** Per card: {project, name, runs, filter:{code,band,target}, run, other, compare, files:{run → list}, open:Set, shown:{folder → n}} */
const cards = new WeakMap();

function projectOf(ctx) {
  const t = ctx.target();
  return t?.project || (ctx.state.selectedProject !== "all" ? ctx.state.selectedProject : null);
}

export async function openCollection(host, ctx, name) {
  loadCss("css/lazy.css");
  const card = $(`[data-coll="${CSS.escape(name)}"]`, host);
  if (!card) return;
  const body = $(".coll-body", card);
  const btn = $("[data-coll-open]", card);
  if (cards.has(card) && !body.hidden) { // toggle closed
    body.hidden = true;
    if (btn) btn.innerHTML = `${icon("folder")} Show`;
    return;
  }
  body.hidden = false;
  if (btn) btn.innerHTML = `${icon("fold")} Hide`;
  if (cards.has(card)) return;
  const project = projectOf(ctx);
  const t = ctx.target();
  const st = { project, name, runs: [], filter: { code: "", band: "", target: t?.name || "" }, run: null, other: null, compare: false, files: {}, open: new Set(), shown: {} };
  cards.set(card, st);
  bind(card, ctx, st);
  await loadRuns(card, ctx, st);
}

/** Results re-rendered: follow the selected target (a new run list) without closing the card. */
export function refresh(host, ctx) {
  $$(".coll-card", host).forEach((card) => {
    const st = cards.get(card);
    const name = ctx.target()?.name || "";
    if (!st || $(".coll-body", card).hidden || st.filter.target === name || st.pinnedTarget) return;
    st.filter.target = name;
    st.run = st.other = null;
    loadRuns(card, ctx, st);
  });
}

async function loadRuns(card, ctx, st) {
  const body = $(".coll-body", card);
  body.innerHTML = `<p class="muted small">Listing runs…</p>`;
  try {
    const q = { name: st.name, ...(st.filter.target ? { target: st.filter.target } : {}) };
    const res = await server.collections(st.project, q);
    st.runs = res.runs || [];
    st.run = st.runs.find((r) => r.id === st.run)?.id || filtered(st)[0]?.id || null;
    if (st.other && !st.runs.some((r) => r.id === st.other)) st.other = null;
    draw(card, ctx, st);
  } catch (error) {
    body.innerHTML = `<p class="callout warn">${icon("alert")}<span>${esc(error.message)}</span></p>`;
  }
}

const filtered = (st) => st.runs.filter((r) => (!st.filter.code || r.workdir === st.filter.code || r.code === st.filter.code) && (!st.filter.band || r.band === st.filter.band));

async function files(ctx, st, run) {
  if (!run) return null;
  st.files[run] ||= server.collectionFiles(st.project, st.name, run).catch((error) => { delete st.files[run]; throw error; });
  return st.files[run];
}

const runLabel = (r) => `${r.stamp || r.name}${r.target ? ` · ${r.target}` : ""}${r.band ? ` · ${r.band}` : ""}`;
const url = (st, run, path, dl = false) => server.collectionFileUrl(st.project, st.name, run, path, dl);

async function draw(card, ctx, st) {
  const body = $(".coll-body", card);
  const runs = filtered(st);
  if (!st.runs.length) {
    body.innerHTML = `<p class="muted">No run folder matches <code>${esc(card.querySelector(".mono")?.textContent || "")}</code>${st.filter.target ? ` for <b class="mono">${esc(st.filter.target)}</b> <button class="link-btn" data-coll-alltargets>show every target</button>` : ""}.</p>`;
    return;
  }
  const uniq = (k) => [...new Set(st.runs.map((r) => r[k]).filter(Boolean))];
  const select = (id, label, values, current) => (values.length > 1 ? `<label class="field inline"><span>${label}</span><select class="input sm" data-coll-f="${id}"><option value="">all</option>${values.map((v) => `<option ${v === current ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>` : "");
  const runSelect = (key, current) => `<select class="input sm mono" data-coll-run="${key}">${runs.map((r) => `<option value="${esc(r.id)}" ${r.id === current ? "selected" : ""}>${esc(runLabel(r))}</option>`).join("")}</select>`;
  body.innerHTML = `
    <div class="row gap wrap coll-bar">
      ${select("code", "Code / work dir", uniq("workdir"), st.filter.code)}
      ${select("band", "Band", uniq("band"), st.filter.band)}
      <label class="field inline"><span>Run</span>${runSelect("run", st.run)}</label>
      <label class="check small"><input type="checkbox" data-coll-compare ${st.compare ? "checked" : ""} ${runs.length > 1 ? "" : "disabled title='Only one run'"}> Compare</label>
      ${st.compare ? `<label class="field inline"><span>with</span>${runSelect("other", st.other)}</label>` : ""}
      <span class="grow"></span>
      ${st.filter.target ? `<span class="chip small">target ${esc(st.filter.target)} <button class="link-btn" data-coll-alltargets title="Show runs of every target">×</button></span>` : ""}
      <span class="muted small">${st.runs.length} run(s)</span>
    </div>
    <div class="coll-runs ${st.compare ? "two" : ""}"><div class="coll-run" data-side="run"><p class="muted small">Listing files…</p></div>${st.compare ? `<div class="coll-run" data-side="other"><p class="muted small">Listing files…</p></div>` : ""}</div>`;
  if (st.compare && !st.other) st.other = runs.find((r) => r.id !== st.run)?.id || null;
  if (st.compare) $("[data-coll-run=other]", body).value = st.other || "";
  await Promise.all(["run", ...(st.compare ? ["other"] : [])].map((side) => drawRun(card, ctx, st, side)));
}

async function drawRun(card, ctx, st, side) {
  const box = $(`.coll-run[data-side="${side}"]`, card);
  const run = st[side];
  if (!box) return;
  if (!run) { box.innerHTML = `<p class="muted small">Pick a run.</p>`; return; }
  let list;
  try { list = await files(ctx, st, run); } catch (error) { box.innerHTML = `<p class="bad small">${esc(error.message)}</p>`; return; }
  const pinned = list.files.filter((f) => f.pinned);
  const meta = st.runs.find((r) => r.id === run);
  box.innerHTML = `
    <p class="muted small mono coll-path" title="${esc(run)}">${esc(run)}${meta?.mtime ? ` · ${esc(when(new Date(meta.mtime * 1000).toISOString()))}` : ""}</p>
    ${list.truncated ? `<p class="callout warn small">${icon("alert")}<span>More than ${list.files.length} files: only the first are listed (raise the collection's <code>depth</code> filters in alfrd.yaml).</span></p>` : ""}
    ${pinned.length ? `<div class="coll-pinned">${pinned.map((f) => fileChip(st, run, f)).join("")}</div>` : ""}
    <div class="coll-groups">${list.groups.filter((g) => g.folder).map((g) => `
      <details class="coll-group" data-folder="${esc(g.folder)}" ${st.open.has(g.folder) ? "open" : ""}>
        <summary><b class="mono">${esc(g.folder)}</b> <span class="muted small">${g.count} file(s) · ${esc(bytes(g.bytes))}${Object.entries(g.kinds).map(([k, n]) => ` · ${n} ${k}`).join("")}</span></summary>
        <div class="coll-grid"></div>
      </details>`).join("")}</div>`;
  $$("details.coll-group[open]", box).forEach((d) => fillGroup(d, st, run, list));
}

function fileChip(st, run, f) {
  const act = f.kind === "table" ? "table" : f.kind === "text" ? "text" : f.kind === "image" ? "image" : f.kind === "pdf" ? "open" : "download";
  const ic = { table: "list", text: "file", image: "graph", open: "external", download: "download" }[act];
  return `<button class="btn sm coll-file" data-coll-act="${act}" data-run="${esc(run)}" data-path="${esc(f.path)}" title="${esc(f.path)} · ${esc(bytes(f.size))}">${icon(ic)} ${esc(f.name)}</button>`;
}

function fillGroup(details, st, run, list) {
  const folder = details.dataset.folder;
  const grid = $(".coll-grid", details);
  const items = list.files.filter((f) => f.folder === folder);
  const n = Math.min(items.length, st.shown[folder] || PAGE);
  const tile = (f) => {
    if (f.kind === "image") return `<figure class="coll-tile"><button class="coll-thumb" data-coll-act="image" data-run="${esc(run)}" data-path="${esc(f.path)}" title="${esc(f.path)}"><img loading="lazy" decoding="async" alt="${esc(f.name)}" src="${esc(url(st, run, f.path))}"></button><figcaption class="small mono">${esc(f.name)}</figcaption></figure>`;
    return `<figure class="coll-tile file">${fileChip(st, run, f)}${f.kind === "postscript" ? `<figcaption class="muted small">PostScript: download only</figcaption>` : ""}</figure>`;
  };
  grid.innerHTML = items.slice(0, n).map(tile).join("") + (items.length > n ? `<div class="coll-more"><button class="btn sm" data-coll-more="${esc(folder)}">Show ${Math.min(PAGE, items.length - n)} more (${items.length - n} left)</button></div>` : "");
}

function bind(card, ctx, st) {
  const redraw = () => draw(card, ctx, st);
  on(card, "change", "[data-coll-f]", (e, s) => {
    st.filter[s.dataset.collF] = s.value;
    const runs = filtered(st);
    if (!runs.some((r) => r.id === st.run)) st.run = runs[0]?.id || null;
    if (!runs.some((r) => r.id === st.other)) st.other = runs.find((r) => r.id !== st.run)?.id || null;
    redraw();
  });
  on(card, "change", "[data-coll-run]", (e, s) => { st[s.dataset.collRun] = s.value; redraw(); });
  on(card, "change", "[data-coll-compare]", (e, c) => { st.compare = c.checked; redraw(); });
  on(card, "click", "[data-coll-alltargets]", () => { st.filter.target = ""; st.pinnedTarget = true; st.run = st.other = null; loadRuns(card, ctx, st); });
  card.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!d.matches?.("details.coll-group")) return;
    const folder = d.dataset.folder;
    // Same folder opens / closes on both sides of a comparison.
    if (d.open) st.open.add(folder); else st.open.delete(folder);
    $$(`details.coll-group[data-folder="${CSS.escape(folder)}"]`, card).forEach((x) => { if (x !== d && x.open !== d.open) x.open = d.open; });
    if (d.open && !$(".coll-grid", d).childElementCount) {
      const side = d.closest(".coll-run").dataset.side;
      files(ctx, st, st[side]).then((list) => fillGroup(d, st, st[side], list));
    }
  }, true);
  on(card, "click", "[data-coll-more]", (e, b) => {
    const folder = b.dataset.collMore;
    st.shown[folder] = (st.shown[folder] || PAGE) + PAGE;
    $$(`details.coll-group[data-folder="${CSS.escape(folder)}"]`, card).forEach((d) => {
      const side = d.closest(".coll-run").dataset.side;
      files(ctx, st, st[side]).then((list) => fillGroup(d, st, st[side], list));
    });
  });
  on(card, "click", "[data-coll-act]", (e, b) => act(ctx, st, b.dataset.collAct, b.dataset.run, b.dataset.path));
}

async function act(ctx, st, action, run, path) {
  if (action === "download" || action === "open") {
    const a = document.createElement("a");
    a.href = url(st, run, path, action === "download");
    if (action === "open") { a.target = "_blank"; a.rel = "noopener"; } else a.download = path.split("/").pop();
    document.body.appendChild(a);
    a.click();
    a.remove();
    return;
  }
  if (action === "image") return lightbox(ctx, st, run, path);
  let text;
  try {
    const res = await server.request(url(st, run, path));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    text = await res.text();
  } catch (error) { ctx.toast(error.message, "fail"); return; }
  const cut = text.length > TEXT_MAX;
  if (cut) text = text.slice(-TEXT_MAX);
  const title = `${icon(action === "table" ? "list" : "file")} <span class="mono">${esc(path)}</span>`;
  const dl = `<a class="btn sm" href="${esc(url(st, run, path, true))}" download>${icon("download")} Download</a>`;
  if (action === "table") {
    const { header, rows } = parseCsv(text);
    const state = { key: null, desc: false };
    ctx.modal(`<header class="modal-h"><h2>${title}</h2><span class="grow"></span>${dl}<button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
      <div class="modal-b"><p class="muted small">${rows.length} row(s)${cut ? " (last 400 kB)" : ""}. Click a column to sort.</p><div class="coll-table"></div></div>`, (root) => {
      const draw = () => {
        const num = (v) => (v !== "" && !Number.isNaN(Number(v)) ? Number(v) : null);
        const sorted = state.key == null ? rows : [...rows].sort((a, b) => {
          const x = a[state.key] ?? "", y = b[state.key] ?? "";
          const nx = num(x), ny = num(y);
          const c = nx != null && ny != null ? nx - ny : String(x).localeCompare(String(y));
          return state.desc ? -c : c;
        });
        $(".coll-table", root).innerHTML = `<table class="tbl sm"><thead><tr>${header.map((h) => `<th><button class="link-btn" data-sort="${esc(h)}">${esc(h)}${state.key === h ? (state.desc ? " ▾" : " ▴") : ""}</button></th>`).join("")}</tr></thead>
          <tbody>${sorted.slice(0, 2000).map((r) => `<tr>${header.map((h) => `<td class="mono small">${esc(r[h] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      };
      on(root, "click", "[data-sort]", (e, b) => { state.desc = state.key === b.dataset.sort ? !state.desc : false; state.key = b.dataset.sort; draw(); });
      draw();
    }, "wide");
    return;
  }
  ctx.modal(`<header class="modal-h"><h2>${title}</h2><span class="grow"></span>${dl}<button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b">${cut ? `<p class="muted small">Last 400 kB.</p>` : ""}<pre class="code coll-text">${esc(text)}</pre></div>`, null, "wide");
}

/** Full-size image; ←/→ walk the images of the same folder. */
async function lightbox(ctx, st, run, path) {
  const list = await files(ctx, st, run);
  const folder = path.includes("/") ? path.split("/")[0] : "";
  const images = list.files.filter((f) => f.kind === "image" && f.folder === folder);
  let i = Math.max(0, images.findIndex((f) => f.path === path));
  ctx.modal(`<header class="modal-h"><h2 class="mono small" id="lb-title"></h2><span class="grow"></span><span class="muted small" id="lb-n"></span>
      <button class="icon-btn" data-lb="-1" aria-label="Previous">${icon("chevron", "rot-l")}</button><button class="icon-btn" data-lb="1" aria-label="Next">${icon("chevron", "rot-r")}</button>
      <a class="icon-btn" id="lb-dl" download aria-label="Download">${icon("download")}</a><button class="icon-btn" data-close aria-label="Close">${icon("close")}</button></header>
    <div class="modal-b coll-lightbox"><img id="lb-img" alt=""></div>`, (root, close) => {
    const show = () => {
      const f = images[i];
      $("#lb-img", root).src = url(st, run, f.path);
      $("#lb-img", root).alt = f.name;
      $("#lb-title", root).textContent = f.path;
      $("#lb-n", root).textContent = `${i + 1} / ${images.length}`;
      $("#lb-dl", root).href = url(st, run, f.path, true);
    };
    const step = (d) => { i = (i + d + images.length) % images.length; show(); };
    on(root, "click", "[data-lb]", (e, b) => step(Number(b.dataset.lb)));
    const keys = (e) => {
      if (!root.isConnected) { document.removeEventListener("keydown", keys); return; }
      if (e.key === "ArrowRight") step(1);
      if (e.key === "ArrowLeft") step(-1);
    };
    document.addEventListener("keydown", keys);
    show();
  }, "full");
}
