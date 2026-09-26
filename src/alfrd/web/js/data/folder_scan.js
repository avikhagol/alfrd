// Targeted scan of an ALFRD project folder through the File System Access API.
//
// Instead of letting the browser enumerate the whole folder (measurement sets
// hold tens of thousands of files), the Studio reads alfrd.yaml first and then
// opens only what it points to:
//   alfrd.yaml → avica.inp + avica.summary.* → target_dir
//   {target_dir}/{target}_result.csv, the workdir patterns and their avica.meta/,
//   input_templates, band dirs (names only), picard_input_template_update, avica.logs/.
// The folder handle is kept in IndexedDB so "Re-scan" works after a reload.
// Returns the same entry list as readFiles(), so buildBundle() is shared.

import { parseYaml } from "../utils/yaml_parser.js";
import { parseKeyValue } from "./importers.js";
import { buildAvicaIndex, layoutPatterns, parseSummaryFile, metaDirNames } from "./avica.js";
import { applyFieldAliases, studioManifest, logSpecs, specInstances, msPathPatterns, segmentRegex, hasWildcard, fillPattern } from "./defs.js";
import { resolveAlias } from "../utils/csv_parser.js";

const MAX_META = 2 * 1024 * 1024;
const MAX_TEXT = 25 * 1024 * 1024;
const MANIFEST = /^\.?alfrd\.ya?ml$/i;

export const canPickDirectory = () => typeof window !== "undefined" && typeof window.showDirectoryPicker === "function";

/** Ask the user for the folder that holds alfrd.yaml. */
export async function pickProjectFolder() {
  return window.showDirectoryPicker({ id: "alfrd-project", mode: "read" });
}

// ---------------------------------------------------------------------------
// Handle store (IndexedDB; handles are structured-cloneable, File objects are not kept)

const DB = "alfrd-studio";
function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore("handles");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function tx(mode, fn) {
  const d = await db();
  return new Promise((resolve, reject) => {
    const t = d.transaction("handles", mode);
    const req = fn(t.objectStore("handles"));
    t.oncomplete = () => resolve(req?.result);
    t.onerror = () => reject(t.error);
  });
}
export async function saveFolderHandle(project, handle) {
  try { await tx("readwrite", (s) => s.put(handle, project)); return true; } catch { return false; }
}
export async function loadFolderHandle(project) {
  try { return (await tx("readonly", (s) => s.get(project))) || null; } catch { return null; }
}
export async function forgetFolderHandles() {
  try { await tx("readwrite", (s) => s.clear()); } catch { /* ignore */ }
}

/** Re-grant read access to a stored handle (needs a user gesture the first time after a reload). */
export async function ensureReadPermission(handle, { request = true } = {}) {
  if (!handle?.queryPermission) return true;
  const opts = { mode: "read" };
  if ((await handle.queryPermission(opts)) === "granted") return true;
  return request ? (await handle.requestPermission(opts)) === "granted" : false;
}

// ---------------------------------------------------------------------------
// Directory helpers

async function children(dir) {
  const out = [];
  try {
    for await (const [name, handle] of dir.entries()) out.push({ name, handle, kind: handle.kind });
  } catch { /* unreadable folder */ }
  return out;
}

async function dirAt(root, rel) {
  let h = root;
  for (const seg of String(rel || "").split("/").filter((s) => s && s !== ".")) {
    try { h = await h.getDirectoryHandle(seg); } catch { return null; }
  }
  return h;
}

async function fileAt(dir, name) {
  try { return await dir.getFileHandle(name); } catch { return null; }
}

const NO_DESCEND = /(\.ms|\.ms\..+|\.flagversions)$|^(raw|tmp_files|tmp_fringe_testing|calibration_tables|__pycache__|\..+)$/;

/**
 * Expand layout patterns segment by segment below `base`.
 * Literal segments are opened directly; segments with a {placeholder} or a
 * wildcard (* ?) list one folder. Measurement sets are never entered.
 * @returns {Promise<Array<{rel:string, handle:FileSystemHandle, groups:object}>>}
 */
export async function expandPatterns(base, patterns, fixed = {}, kind = "directory") {
  const hits = new Map();
  for (const pattern of patterns) {
    const text = fillPattern(pattern, fixed);
    if (/\{(workdir|meta_dir|logs|target_dir)\}/.test(text)) continue;
    const segs = text.split("/").filter((x) => x && x !== ".");
    if (segs.includes("..")) continue;
    let frontier = [{ rel: "", handle: base, groups: {} }];
    for (let i = 0; i < segs.length && frontier.length; i += 1) {
      const last = i === segs.length - 1;
      const want = last ? kind : "directory";
      const seg = segs[i];
      const next = [];
      for (const node of frontier) {
        const join = (name) => (node.rel ? `${node.rel}/${name}` : name);
        if (!hasWildcard(seg)) {
          try {
            const h = want === "directory" ? await node.handle.getDirectoryHandle(seg) : await node.handle.getFileHandle(seg);
            next.push({ rel: join(seg), handle: h, groups: node.groups });
          } catch { /* missing */ }
          continue;
        }
        const re = segmentRegex(seg, node.groups);
        (await children(node.handle)).forEach((c) => {
          if (c.kind !== want) return;
          if (want === "directory" && !(last && /\.ms$/i.test(seg)) && NO_DESCEND.test(c.name)) return;
          const m = re.exec(c.name);
          if (m) next.push({ rel: join(c.name), handle: c.handle, groups: { ...node.groups, ...Object.fromEntries(Object.entries(m.groups || {}).filter(([, v]) => v != null)) } });
        });
      }
      frontier = next.slice(0, 5000);
    }
    frontier.forEach((n) => hits.set(n.rel, n));
  }
  return [...hits.values()];
}

async function pool(items, limit, fn) {
  const out = new Array(items.length);
  let i = 0;
  const worker = async () => { while (i < items.length) { const k = i++; out[k] = await fn(items[k], k); } };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

// ---------------------------------------------------------------------------
// The scan

/**
 * @param {FileSystemDirectoryHandle} root folder picked by the user (holds alfrd.yaml)
 * @param {{onProgress?: (text:string)=>void}} opts
 */
export async function scanProjectFolder(root, { onProgress = () => {}, statOnly = false } = {}) {
  const entries = [];
  const jobs = []; // {rel, handle, max, lazy, hint}
  const t0 = performance.now();
  let listed = 0;
  const add = (rel, handle, opts = {}) => jobs.push({ rel, handle, ...opts });

  // 1. alfrd.yaml (the chosen folder, or a direct child folder that holds it).
  let top = await children(root);
  let base = root;
  let prefix = "";
  let manifest = top.find((c) => c.kind === "file" && MANIFEST.test(c.name));
  if (!manifest) {
    for (const c of top.filter((x) => x.kind === "directory")) {
      const inner = await children(c.handle);
      const m = inner.find((x) => x.kind === "file" && MANIFEST.test(x.name));
      if (m) { base = c.handle; prefix = c.name; top = inner; manifest = m; break; }
    }
  }
  if (!manifest) throw new Error(`No alfrd.yaml in "${root.name}". Pick the folder that contains alfrd.yaml.`);
  const read = async (handle, max = MAX_TEXT) => {
    const file = await handle.getFile();
    return { file, text: file.size <= max ? await file.text() : null };
  };
  const manifestRead = await read(manifest.handle);
  entries.push({ rel: manifest.name, name: manifest.name, size: manifestRead.file.size, mtime: manifestRead.file.lastModified, text: manifestRead.text });
  let parsed = null;
  try { parsed = parseYaml(manifestRead.text || ""); } catch { /* buildBundle reports the error */ }
  const block = (parsed && typeof parsed.avica === "object" && parsed.avica) || {};
  const pats = layoutPatterns(parsed);
  if (parsed) applyFieldAliases(parsed);
  const defs = studioManifest(parsed || {});
  onProgress("alfrd.yaml read");

  // 2. Root files alfrd.yaml refers to: AVICA config, summary cache, dataset tables.
  const configName = block.config || "avica.inp";
  const summaryNames = [block.config_summary_cache, "avica.summary.json", "avica.summary.txt"].filter(Boolean);
  const early = [];
  for (const c of top.filter((x) => x.kind === "file")) {
    if (c.name === manifest.name) continue;
    const isConfig = c.name === configName || resolveAlias(c.name).name === configName || c.name === "avica.inp";
    const isSummary = summaryNames.includes(c.name);
    const isTable = /\.(csv|tsv)$/i.test(c.name);
    if (!isConfig && !isSummary && !isTable) continue;
    const r = await read(c.handle);
    const e = { rel: c.name, name: c.name, size: r.file.size, mtime: r.file.lastModified, text: r.text, hint: isSummary ? "summary" : undefined };
    entries.push(e);
    early.push(e);
  }
  // Resolve target_dir exactly as buildAvicaIndex does (config ← summary).
  const config = Object.assign({}, ...early.filter((e) => /\.inp$/i.test(e.name)).map((e) => parseKeyValue(e.text || "")));
  const summaryEntry = early.find((e) => e.hint === "summary" && /\.json$/i.test(e.name)) || early.find((e) => e.hint === "summary");
  const summary = summaryEntry ? parseSummaryFile(summaryEntry.name, summaryEntry.text || "") : null;
  const pre = buildAvicaIndex([], { manifestAvica: block, manifest: parsed, config, summary });
  let targetDir = pre.targetDir;
  if (!(await dirAt(base, targetDir))) {
    // target_dir missing: look for the folder holding <CODE>/wd*/ (same fallback as the index).
    for (const c of top.filter((x) => x.kind === "directory" && !/^\.|\.logs$/.test(x.name))) {
      const hit = await expandPatterns(c.handle, pats.workdir.map((p) => p.replace(/^\{target_dir\}\//, "")), {}, "directory");
      if (hit.length) { targetDir = c.name; break; }
    }
  }
  onProgress(`target_dir = ${targetDir}/`);

  // 3. Result CSVs from the result_csv pattern.
  (await expandPatterns(base, pats.result_csv, { target_dir: targetDir }, "file")).forEach((h) => add(h.rel, h.handle));

  // 4. Work dirs: avica.meta/, input templates, band dirs (names only).
  const metaDir = pats.meta_dir[0] || "avica.meta";
  const wds = await expandPatterns(base, pats.workdir, { target_dir: targetDir }, "directory");
  onProgress(`${wds.length} work dir(s)`);
  await pool(wds, 6, async (wd) => {
    for (const name of metaDirNames(metaDir)) {
      const meta = await dirAt(wd.handle, name);
      if (!meta) continue;
      (await children(meta)).filter((c) => c.kind === "file").forEach((c) => add(`${wd.rel}/${name}/${c.name}`, c.handle, { max: MAX_META }));
    }
    const bands = await expandPatterns(wd.handle, pats.band_dir, {}, "directory");
    listed += bands.length;
    bands.forEach((b) => entries.push({ rel: `${wd.rel}/${b.rel}/.dir`, name: ".dir", size: 0, marker: true }));
    const tpls = await expandPatterns(wd.handle, pats.input_templates, {}, "directory");
    for (const t of tpls) {
      (await children(t.handle)).filter((c) => c.kind === "file" && /\.inp$/i.test(c.name))
        .forEach((c) => add(`${wd.rel}/${t.rel}/${c.name}`, c.handle, { max: 512 * 1024 }));
    }
  });

  // 5. picard_input_template_update (configured, or an input_temp*update* folder next to alfrd.yaml).
  const updateRel = String(pre.values.picard_input_template_update || "").replace(/^\.\//, "").replace(/\/+$/, "");
  const updateDirs = updateRel
    ? [{ rel: updateRel, handle: await dirAt(base, updateRel) }]
    : top.filter((c) => c.kind === "directory" && /input_temp.*update/i.test(c.name)).map((c) => ({ rel: c.name, handle: c.handle }));
  for (const u of updateDirs.filter((x) => x.handle)) {
    (await children(u.handle)).filter((c) => c.kind === "file" && /\.inp$/i.test(c.name)).forEach((c) => add(`${u.rel}/${c.name}`, c.handle, { max: 512 * 1024 }));
  }

  // 6. avica.logs/: crash snapshots are read, logs stay lazy (File objects).
  const logsDir = block.logs || "avica.logs";
  const logs = await dirAt(base, logsDir);
  if (logs) {
    (await children(logs)).filter((c) => c.kind === "file").forEach((c) => {
      if (/^avica_crash_.*\.json$/i.test(c.name)) add(`${logsDir}/${c.name}`, c.handle);
      else if (/\.log$/i.test(c.name)) add(`${logsDir}/${c.name}`, c.handle, { lazy: true });
    });
  }

  // 7. Step logs and log artifacts from alfrd.yaml (listed; read when opened) and
  //    measurement-set folders for overview.ms_path (names only, never entered).
  const ctxPaths = { logsDir, targetDir, metaDir, workdirs: wds.map((w) => ({ rel: w.rel, id: w.rel })) };
  const taken = new Set(jobs.map((j) => j.rel));
  for (const spec of logSpecs(defs)) {
    for (const inst of specInstances(spec, ctxPaths)) {
      (await expandPatterns(base, [inst.pattern], {}, "file")).forEach((h) => {
        if (taken.has(h.rel)) return;
        taken.add(h.rel);
        add(h.rel, h.handle, { lazy: true });
      });
    }
  }
  const msDirs = new Set();
  for (const pattern of msPathPatterns(defs)) {
    for (const inst of specInstances({ pattern, step: null }, ctxPaths)) {
      (await expandPatterns(base, [inst.pattern], {}, "directory")).forEach((h) => msDirs.add(h.rel));
    }
  }
  onProgress(`${taken.size} file(s) listed`);

  // Read everything in parallel.
  onProgress(`reading ${jobs.length} file(s)…`);
  await pool(jobs, 16, async (j) => {
    const file = await j.handle.getFile();
    const name = j.rel.split("/").pop();
    handleCache(base).set(j.rel, j.handle); // `rel` is relative to the project folder (base)
    if (statOnly) { entries.push({ rel: j.rel, name, size: file.size, mtime: file.lastModified, lazy: Boolean(j.lazy) }); return; }
    if (j.lazy) { entries.push({ rel: j.rel, name, size: file.size, mtime: file.lastModified, file }); return; }
    if (file.size > (j.max || MAX_TEXT)) { entries.push({ rel: j.rel, name, size: file.size, file }); return; }
    entries.push({ rel: j.rel, name, size: file.size, text: await file.text() });
  });
  entries.forEach((e) => { e.path = prefix ? `${prefix}/${e.rel}` : e.rel; });
  entries.rootName = base.name;
  entries.ignored = 0;
  entries.stats = { files: jobs.length + early.length + 1, bandDirs: listed, workdirs: wds.length, ms: Math.round(performance.now() - t0), targetDir };
  entries.folder = base;
  entries.msDirs = [...msDirs];
  return entries;
}

/**
 * Live updates in folder mode: `{rel: "kind:size:mtime"}` from a stat-only scan
 * (alfrd.yaml and the root config files are still read: they decide the layout).
 */
export async function folderFingerprint(root) {
  const t0 = performance.now();
  const entries = await scanProjectFolder(root, { statOnly: true });
  const out = {};
  entries.forEach((e) => {
    out[e.rel] = e.marker ? "marker" : `${e.lazy ? "log" : "content"}:${e.size}:${e.mtime || 0}`;
  });
  return { prints: out, ms: performance.now() - t0 };
}

/** What changed between two fingerprints: content changes and added/removed files vs. log growth. */
export function diffFingerprints(old, next) {
  const changed = [];
  const removed = [];
  const logs = {};
  Object.entries(next).forEach(([rel, v]) => {
    const was = old[rel];
    if (was === undefined) changed.push(rel);
    else if (was !== v) {
      if (v.startsWith("log:") && was.startsWith("log:")) {
        const [, size, mtime] = v.split(":");
        logs[rel] = [Number(size), Number(mtime) / 1000];
      } else changed.push(rel);
    }
  });
  Object.keys(old).forEach((rel) => { if (!(rel in next)) removed.push(rel); });
  return { changed, removed, logs };
}

const handles = new WeakMap(); // root handle -> Map(rel -> FileSystemFileHandle)
function handleCache(root) {
  if (!handles.has(root)) handles.set(root, new Map());
  return handles.get(root);
}

async function fileHandleAt(root, rel) {
  const cache = handleCache(root);
  if (cache.has(rel)) return cache.get(rel);
  const parts = String(rel).split("/").filter(Boolean);
  const name = parts.pop();
  const dir = await dirAt(root, parts.join("/"));
  const f = dir && (await fileAt(dir, name));
  if (!f) throw new Error(`${rel} not found`);
  cache.set(rel, f);
  return f;
}

/**
 * Read a growing file below a folder handle from byte `offset` (null: last `tail` bytes).
 * Same contract as the server's `…/file?offset=`: {text, offset, size, mtime, reset}.
 * `decoder` (a TextDecoder kept by the caller) carries a UTF-8 character split
 * between two reads.
 */
export async function readFileRange(root, rel, offset = null, { tail = 400000, decoder = null } = {}) {
  let file;
  try {
    file = await (await fileHandleAt(root, rel)).getFile();
  } catch (error) {
    handleCache(root).delete(rel);
    throw error;
  }
  const size = file.size;
  // No inode here: a rotated log is only noticed when it is shorter than what was read.
  const reset = offset == null || offset > size || size - offset > tail;
  const start = reset ? Math.max(0, size - tail) : offset;
  const dec = decoder || new TextDecoder();
  if (reset) dec.decode(); // drop a partial character left from the old contents
  let text = start < size ? dec.decode(new Uint8Array(await file.slice(start, size).arrayBuffer()), { stream: true }) : "";
  if (reset && start > 0) {
    const cut = text.indexOf("\n");
    if (cut >= 0 && cut < 4096) text = text.slice(cut + 1);
  }
  return { text, offset: size, size, mtime: file.lastModified, id: null, reset };
}

/** Read a file (last 400 kB) below a stored folder handle, e.g. after a reload. */
export async function readFileFromHandle(handle, rel) {
  const parts = String(rel).split("/").filter(Boolean);
  const name = parts.pop();
  const dir = await dirAt(handle, parts.join("/"));
  const f = dir && (await fileAt(dir, name));
  if (!f) throw new Error(`${rel} not found`);
  const file = await f.getFile();
  return file.size > 400000 ? file.slice(file.size - 400000).text() : file.text();
}

/** Write a text file below a folder handle (asks for write permission). */
export async function writeFileToHandle(handle, rel, text) {
  if (handle.requestPermission && (await handle.requestPermission({ mode: "readwrite" })) !== "granted") throw new Error("Write access was not granted.");
  const parts = String(rel).split("/").filter(Boolean);
  const name = parts.pop();
  let dir = handle;
  for (const p of parts) dir = await dir.getDirectoryHandle(p);
  const f = await dir.getFileHandle(name, { create: true });
  const w = await f.createWritable();
  await w.write(text);
  await w.close();
}
