// Google Sheet project card, attach dialog, mapping editor, preview/backfill and detach.
// Every sheet-derived string reaches the DOM as textContent; credentials never leave the server.
const PLUGIN = "gsheet";
const KEY_ALIASES = ["target_name", "target", "source", "name"];
const KEYED = ["usage", "agent_usage", "results"];
const OFF_TITLE = "Turned off on this server (plugin_actions: false)";
const CONFLICT = "changed on disk";
const CUSTOM = "__custom__";
const confirmedRewrite = new Set();
let nextId = 0;
let mountedSection = null;

// ---- pure helpers (exported for tests) ----

export function gidFrom(text) {
  const match = /[#?&]gid=(\d+)/.exec(String(text || ""));
  return match ? Number(match[1]) : null;
}

export function guessKey(headers, preferred) {
  const names = headers.map((h) => h.name);
  if (preferred && names.includes(preferred)) return preferred;
  for (const alias of KEY_ALIASES) {
    const found = names.find((n) => n.toLowerCase() === alias);
    if (found) return found;
  }
  return "";
}

export function matchCount(samples, targets) {
  const known = new Set(targets.map((t) => String(t).trim().toLowerCase()));
  const keys = samples.map((s) => String(s).trim()).filter(Boolean);
  return { matched: keys.filter((k) => known.has(k.toLowerCase())).length, total: keys.length };
}

/** Status rules `init` would add: a header named like a step that has no status rule yet. */
export function statusRules(outbound, steps, headers) {
  const byName = new Map(headers.map((h) => [h.name.toLowerCase(), h.name]));
  return steps.filter((step) => byName.has(step.toLowerCase())
      && !outbound.some((r) => r.step === step && r.field === "status"))
    .map((step) => ({ step, column: byName.get(step.toLowerCase()), field: "status" }));
}

export function move(list, index, delta) {
  const to = index + delta;
  if (to < 0 || to >= list.length) return index;
  [list[index], list[to]] = [list[to], list[index]];
  return to;
}

export function splitField(field) {
  const [root, ...rest] = String(field || "").split(".");
  return KEYED.includes(root) && rest.length ? { base: `${root}.*`, key: rest.join(".") } : { base: field || "", key: "" };
}

/** The draft as the server expects it: no empty optional keys, one inbound destination. */
export function cleanMapping(draft) {
  const out = structuredClone(draft);
  out.outbound = (out.outbound || []).map((rule) => {
    const r = { ...rule };
    if (!r.when?.length) delete r.when;
    if (!r.format || r.format === "raw") delete r.format;
    if (r.field !== "template") delete r.template;
    return r;
  });
  out.inbound = (out.inbound || []).map((rule) => {
    const r = { ...rule };
    if (r.to === "plan_cell") delete r.plan_column; else delete r.step;
    return r;
  });
  if (!out.outbound.length) delete out.outbound;
  if (!out.inbound.length) delete out.inbound;
  if (!out.read_range) delete out.read_range;
  return out;
}

export function sheetURL(id, gid) {
  return /^[A-Za-z0-9_-]{20,}$/.test(id || "") ? `https://docs.google.com/spreadsheets/d/${id}/edit${gid != null ? `#gid=${gid}` : ""}` : null;
}

// ---- DOM helpers ----

function h(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "for") node.htmlFor = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (["value", "checked", "disabled", "hidden", "selected", "type", "id", "title", "name", "min", "placeholder"].includes(key)) node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  node.append(...kids.flat().filter((kid) => kid != null && kid !== false));
  return node;
}
const uid = (prefix) => `gs-${prefix}-${++nextId}`;
const option = (value, text, selected) => h("option", { value, text, selected: selected || null });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function debounce(fn, ms) {
  let timer;
  const run = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  run.flush = (...args) => { clearTimeout(timer); return fn(...args); };
  run.cancel = () => clearTimeout(timer);
  return run;
}

function field(label, control, hint) {
  if (!control.id) control.id = uid("f");
  return h("div", { class: "field" }, h("label", { for: control.id, text: label }), control,
    hint ? h("p", { class: "small muted", text: hint }) : null);
}

function alertBox(text, tone = "warn") {
  return h("div", { class: `callout ${tone}`, role: "alert", text });
}

/** Best-effort: Settings → Plugins → Google Sheet → Configure, through the shell's own controls. */
async function openPluginSettings() {
  document.querySelector("#btn-settings")?.click();
  for (let i = 0; i < 40; i++) {
    const tab = document.querySelector("#set-tab-plugins");
    if (tab) {
      tab.click();
      for (let j = 0; j < 40; j++) {
        const configure = document.querySelector('[data-plug-config="gsheet"]');
        if (configure) { if (configure.getAttribute("aria-expanded") !== "true") configure.click(); return; }
        await sleep(50);
      }
      return;
    }
    await sleep(50);
  }
}

// ---- the plugin UI ----

export function registerProjectUI(api) {
  const call = (project, id, payload = {}) => api.action(project, PLUGIN, id, payload);
  const policy = async () => {
    try { return (await api.fetchJSON("/studio/plugins")).plugin_actions !== false; } catch { return true; }
  };
  const refresh = (project) => {
    if (mountedSection?.project === project && mountedSection.host.isConnected) renderSection(project, mountedSection.host);
  };
  const where = (mapping) => {
    const spreadsheet = mapping.spreadsheet_id ? { spreadsheet: mapping.spreadsheet_id } : {};
    const tab = Number.isInteger(mapping.gid) ? { gid: mapping.gid } : { worksheet: mapping.worksheet };
    return { ...spreadsheet, ...tab, header_row: mapping.header_row || 1 };
  };

  async function confirmRewrite(project, state, ask) {
    if (confirmedRewrite.has(project)) return true;
    const ok = await ask(`${state.comments_notice || "Saving rewrites the file; comments are removed."} Save anyway?`, { confirmLabel: "Save" });
    if (ok) confirmedRewrite.add(project);
    return ok;
  }

  // -- section card --
  async function renderSection(project, host) {
    mountedSection = { project, host };
    host.replaceChildren(h("p", { class: "muted small", role: "status", text: "Loading Google Sheet…" }));
    const [state, canWrite] = await Promise.all([call(project, "state"), policy()]);
    if (mountedSection?.host !== host || mountedSection.project !== project) return;
    const body = h("div", { class: "gs-card" });
    if (!state.attached) {
      body.append(h("p", { text: "Write step status, run times and resource usage into a Google Sheet row per target. Nothing is sent until you attach a sheet." }));
      if (!state.credentials) {
        body.append(h("div", { class: "callout" }, "Add a service-account key first. ",
          h("button", { type: "button", class: "link-btn", text: "Open Settings → Plugins", onclick: openPluginSettings })));
      } else {
        body.append(h("button", { type: "button", class: "btn primary", text: "Attach Google Sheet", disabled: !canWrite, title: canWrite ? null : OFF_TITLE,
          onclick: () => openAttach(project, state) }));
      }
      host.replaceChildren(body);
      return;
    }
    if (!state.mapping) {
      body.append(alertBox(`alfrd.gsheet.yaml cannot be read: ${state.intrinsic_errors?.[0]?.message || "invalid file"}`),
        h("p", { class: "small muted", text: "Fix the file in an editor, or delete it and attach the sheet again." }),
        h("div", { class: "row gap wrap" },
          h("button", { type: "button", class: "btn", text: "Reload", onclick: () => refresh(project) }),
          h("button", { type: "button", class: "btn danger", text: "Delete alfrd.gsheet.yaml…", disabled: !canWrite, title: canWrite ? null : OFF_TITLE,
            onclick: () => openDetach(project, state, "delete") })));
      host.replaceChildren(body);
      return;
    }
    const m = state.mapping;
    const url = sheetURL(m.spreadsheet_id, m.gid);
    const out = m.outbound?.length || 0, inn = m.inbound?.length || 0;
    const chip = (text) => h("span", { class: "gs-chip", text });
    body.append(h("p", { class: "row gap wrap gs-line" },
      url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open sheet in a new tab", text: "Open sheet ↗" })
        : h("span", { class: "muted", text: "Sheet from Settings default" }),
      chip(`Tab: ${m.worksheet ?? `gid ${m.gid}`}`), chip(`Key: ${m.rows?.key_column || "?"}`), chip(`${out} outbound · ${inn} inbound`)));
    const switchId = uid("enabled");
    const toggle = h("input", { type: "checkbox", role: "switch", id: switchId, checked: state.enabled, disabled: !canWrite, title: canWrite ? null : OFF_TITLE });
    toggle.addEventListener("change", async () => {
      const wanted = toggle.checked;
      toggle.disabled = true;
      try {
        if (!await confirmRewrite(project, state, api.confirm)) { toggle.checked = !wanted; return; }
        const result = await call(project, "save", { mapping: { ...m, enabled: wanted }, base_sha256: state.mapping_sha256 });
        if (!result.saved) throw new Error(result.errors?.map((e) => `${e.path}: ${e.message}`).join("; ") || "The mapping is invalid.");
        api.toast(wanted ? "Sync enabled" : "Sync disabled", "ok");
        refresh(project);
      } catch (error) {
        toggle.checked = !wanted;
        api.toast(error.message.includes(CONFLICT) ? "The file changed on disk; reloaded the card." : error.message, "fail");
        if (error.message.includes(CONFLICT)) refresh(project);
      } finally { toggle.disabled = !canWrite; }
    });
    const last = state.last_sync || {};
    body.append(h("p", { class: "row gap wrap gs-line" },
      h("label", { class: "check", for: switchId }, toggle, h("span", { text: "Sync enabled" })),
      h("span", { class: "muted small" }, "Last sync: ", last.at
        ? h("time", { datetime: last.at, title: last.at, text: `${new Date(last.at).toLocaleString()} (${last.result || "unknown"})` })
        : "never")));
    if (state.dry_run) body.append(h("p", { class: "small muted", text: "Dry run is on in Settings: backfill and hooks write nothing." }));
    const live = h("div", { class: "gs-validate small", "aria-live": "polite" });
    const off = (node) => { if (!canWrite) { node.disabled = true; node.title = OFF_TITLE; } return node; };
    body.append(h("div", { class: "row gap wrap" },
      h("button", { type: "button", class: "btn primary", text: "Edit mapping", onclick: () => openEditor(project) }),
      h("button", { type: "button", class: "btn", text: "Validate", onclick: async (e) => {
        const button = e.currentTarget;
        button.disabled = true;
        live.replaceChildren(h("span", { class: "muted", text: "Validating…" }));
        try {
          const result = await call(project, "validate");
          live.replaceChildren(...result.errors.length
            ? [h("p", { text: `${result.errors.length} problem${result.errors.length === 1 ? "" : "s"}:` }),
              h("ul", {}, result.errors.map((err) => h("li", { text: `${err.path}: ${err.message}` })))]
            : [h("p", { text: "✓ Mapping valid" })], ...(result.warnings || []).map((w) => h("p", { class: "muted", text: w })));
        } catch (error) { live.replaceChildren(alertBox(error.message)); }
        finally { button.disabled = false; }
      } }),
      h("button", { type: "button", class: "btn", text: "Preview", onclick: () => openPreview(project, state, false) }),
      off(h("button", { type: "button", class: "btn", text: "Backfill…", onclick: () => openPreview(project, state, true) })),
      off(h("button", { type: "button", class: "btn", text: "Detach…", onclick: () => openDetach(project, state) }))), live);
    host.replaceChildren(body);
  }

  // -- attach dialog --
  function openAttach(project, state) {
    const steps = h("ol", { class: "gs-steps" }, ["Spreadsheet", "Tab", "Columns"].map((t) => h("li", { text: t })));
    const mark = (n) => [...steps.children].forEach((li, i) => {
      if (i === n) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    const problem = h("div", { class: "gs-problem" });
    const say = (text) => problem.replaceChildren(...text ? [alertBox(text)] : []);
    const sheet = h("input", { class: "input mono", placeholder: "https://docs.google.com/spreadsheets/d/…", autocomplete: "off" });
    const useDefault = h("input", { type: "checkbox", disabled: !state.default_spreadsheet });
    const load = h("button", { type: "button", class: "btn", text: "Load" });
    const tab = h("select", { class: "input" }), row = h("input", { type: "number", min: 1, value: 1, class: "input gs-num" });
    const key = h("select", { class: "input" }), code = h("select", { class: "input" });
    const sample = h("p", { class: "small", "aria-live": "polite" });
    const force = h("input", { type: "checkbox" });
    const tabStep = h("fieldset", { class: "gs-step", hidden: true }, h("legend", { text: "2. Tab" }),
      h("div", { class: "row gap wrap" }, field("Tab", tab), field("Header row", row)));
    const colStep = h("fieldset", { class: "gs-step", hidden: true }, h("legend", { text: "3. Columns" }),
      h("div", { class: "row gap wrap" }, field("Target-name column", key), field("Project-code column (optional)", code)), sample);
    const body = h("div", { class: "gs-attach" }, steps,
      h("fieldset", { class: "gs-step" }, h("legend", { text: "1. Spreadsheet" }),
        field("Sheet URL or ID", sheet),
        h("label", { class: "check" }, useDefault, h("span", { text: state.default_spreadsheet ? "Use the default sheet from Settings" : "Use the default sheet from Settings (none set)" })),
        h("div", { class: "row gap" }, load)),
      tabStep, colStep,
      state.attached ? h("label", { class: "check" }, force, h("span", { text: "Replace the existing mapping" })) : null,
      problem);
    let info = null, attachButton;
    const source = () => useDefault.checked ? {} : { spreadsheet: sheet.value.trim() };
    const ready = () => { if (attachButton) attachButton.disabled = !(info && key.value); };
    const fill = (select, items, chosen, none) => {
      select.replaceChildren(...none ? [option("", none)] : [], ...items.map((x) => option(x.value, x.text, x.value === chosen)));
    };
    mark(0);
    useDefault.addEventListener("change", () => { sheet.disabled = useDefault.checked; });
    load.addEventListener("click", async () => {
      say(""); load.disabled = true; load.setAttribute("aria-busy", "true");
      try {
        info = await call(project, "sheet_info", source());
        const gid = gidFrom(sheet.value);
        fill(tab, info.tabs.map((t) => ({ value: t.title, text: t.title })), info.tabs.find((t) => t.gid === gid)?.title);
        tabStep.hidden = false; mark(1);
        await loadHeaders.flush();
        tab.focus();
      } catch (error) { info = null; say(error.message); }
      finally { load.disabled = false; load.removeAttribute("aria-busy"); ready(); }
    });
    const loadHeaders = debounce(async (keyColumn) => {
      if (!info) return;
      say("");
      try {
        const result = await call(project, "headers", { ...source(), worksheet: tab.value, header_row: Number(row.value) || 1,
          ...keyColumn ? { key_column: keyColumn } : {} });
        const items = result.headers.filter((x) => x.name).map((x) => ({ value: x.name, text: `${x.letter} · ${x.name}` }));
        const chosen = keyColumn || guessKey(result.headers, result.key_column);
        fill(key, items, chosen, "Choose a column…");
        fill(code, items.filter((x) => x.value !== chosen), code.value, "(none)");
        const { matched, total } = matchCount(result.sample_keys, api.targets?.(project) || []);
        sample.replaceChildren(
          h("span", { text: total ? `First keys: ${result.sample_keys.slice(0, 5).join(", ")}${total > 5 ? " …" : ""}` : "No values under this column." }),
          h("br"),
          h("span", { text: matched ? `✓ ${matched} of ${total} match plan targets` : `⚠ 0 of ${total} match plan targets. Is this the right column?` }));
        colStep.hidden = false; mark(2);
      } catch (error) { say(error.message); }
      ready();
    }, 400);
    tab.addEventListener("change", () => loadHeaders());
    row.addEventListener("input", () => loadHeaders());
    key.addEventListener("change", () => { ready(); if (key.value) loadHeaders(key.value); });
    const handle = api.dialog({ title: "Attach Google Sheet", body, actions: [
      { label: "Cancel", run: (d) => d.close() },
      { label: "Attach", tone: "primary", async run() {
        say("");
        try {
          await call(project, "attach", { ...source(), worksheet: tab.value, header_row: Number(row.value) || 1,
            key_column: key.value, ...code.value ? { code_column: code.value } : {}, ...force.checked ? { force: true } : {} });
        } catch (error) { say(error.message); return; }
        api.toast("Sheet attached", "ok");
        refresh(project);
        openEditor(project);
      } },
    ] });
    attachButton = handle.footer.querySelector(".btn.primary");
    ready();
    return handle;
  }

  // -- mapping editor --
  async function openEditor(project) {
    let state;
    try { state = await call(project, "state"); } catch (error) { api.toast(error.message, "fail"); return; }
    if (!state.attached) { api.toast("Attach a Google Sheet first.", "warn"); return openAttach(project, state); }
    if (!state.mapping) { api.toast("alfrd.gsheet.yaml cannot be read; fix or delete it first.", "fail"); return; }
    const canWrite = await policy();
    let headers = [];
    let headerNote = "";
    try { headers = (await call(project, "headers", where(state.mapping))).headers.filter((x) => x.name); }
    catch { headerNote = "Sheet headers could not be loaded; columns can still be typed."; }
    return editor(project, state, headers, headerNote, canWrite);
  }

  function editor(project, state, headers, headerNote, canWrite) {
    let base = state.mapping_sha256;
    let draft = structuredClone(state.mapping);
    let loaded = JSON.stringify(cleanMapping(draft));
    let errors = [];
    const custom = new WeakSet();
    const keyLists = {};
    const outBody = h("tbody"), inBody = h("tbody");
    const summary = h("div", { class: "gs-summary", role: "status", "aria-live": "polite" });
    const options = h("div", { class: "gs-options row gap wrap" });
    const fieldOptions = () => {
      const groups = [["Step", state.fields.filter((f) => !f.takes_key && f.id !== "template")],
        ["Usage and results", state.fields.filter((f) => f.takes_key)], ["Other", state.fields.filter((f) => f.id === "template")]];
      return groups.map(([label, items]) => h("optgroup", { label }, items.map((f) => option(f.id, f.label))));
    };
    for (const f of state.fields.filter((x) => x.takes_key)) {
      keyLists[f.id] = uid("keys");
    }
    const datalists = state.fields.filter((f) => f.takes_key).map((f) => h("datalist", { id: keyLists[f.id] }, (f.keys || []).map((k) => option(k, k))));

    const ctl = (kind, i, name) => `${kind}.${i}.${name}`;
    const changed = () => { handle.setDirty(JSON.stringify(cleanMapping(draft)) !== loaded); validate(); };
    const control = (node, path, label, ctlId) => {
      node.setAttribute("data-path", path);
      node.setAttribute("aria-label", label);
      if (ctlId) node.setAttribute("data-ctl", ctlId);
      return node;
    };
    const rerender = (focusCtl) => {
      const active = focusCtl || document.activeElement?.getAttribute?.("data-ctl");
      drawOutbound(); drawInbound(); drawOptions();
      showErrors();
      if (active) body.querySelector(`[data-ctl="${CSS.escape(active)}"]`)?.focus();
    };
    const rowActions = (kind, list, i) => {
      const n = i + 1, noun = kind === "outbound" ? "rule" : "inbound rule";
      const act = (name, glyph, label, fn, disabled) => h("button", { type: "button", class: "icon-btn", text: glyph, "aria-label": label, title: label,
        "data-ctl": ctl(kind, i, name), disabled, onclick: fn });
      return h("td", { class: "gs-actions" },
        act("up", "↑", `Move ${noun} ${n} up`, () => { const to = move(list, i, -1); rerender(ctl(kind, to, to === 0 ? "down" : "up")); changed(); }, i === 0),
        act("down", "↓", `Move ${noun} ${n} down`, () => { const to = move(list, i, 1); rerender(ctl(kind, to, to === list.length - 1 ? "up" : "down")); changed(); }, i === list.length - 1),
        act("copy", "⧉", `Duplicate ${noun} ${n}`, () => { list.splice(i + 1, 0, structuredClone(list[i])); rerender(ctl(kind, i + 1, "copy")); changed(); }),
        act("del", "🗑", `Delete ${noun} ${n}`, () => {
          list.splice(i, 1);
          rerender(list.length ? ctl(kind, Math.min(i, list.length - 1), "del") : `${kind}.add`);
          changed();
        }));
    };
    const columnCell = (kind, list, rule, i) => {
      const path = `${kind}[${i}].column`, n = i + 1;
      const select = control(h("select", { class: "input" }), path, `${kind === "outbound" ? "Rule" : "Inbound rule"} ${n} column`, ctl(kind, i, "column"));
      const names = headers.map((x) => x.name);
      select.append(option("", "Choose…"), ...headers.map((x) => option(x.name, `${x.letter} · ${x.name}`, x.name === rule.column && !custom.has(rule))));
      if (rule.column && !names.includes(rule.column) && !custom.has(rule)) select.append(option(rule.column, rule.column, true));
      select.append(option(CUSTOM, "Letter or pattern…", custom.has(rule)));
      const text = control(h("input", { class: "input mono gs-letter", value: rule.column || "", placeholder: "D or {step} RAM", hidden: !custom.has(rule) }),
        path, `${kind === "outbound" ? "Rule" : "Inbound rule"} ${n} column letter or pattern`, ctl(kind, i, "letter"));
      select.addEventListener("change", () => {
        if (select.value === CUSTOM) { custom.add(rule); rerender(ctl(kind, i, "letter")); return; }
        custom.delete(rule); rule.column = select.value; changed();
      });
      text.addEventListener("input", () => { rule.column = text.value.trim(); changed(); });
      const hint = kind === "outbound" && rule.step === "*" ? h("p", { class: "small muted", text: "{step} is replaced by each step id" }) : null;
      return h("td", {}, select, text, hint);
    };
    const drawOutbound = () => {
      const list = draft.outbound ||= [];
      outBody.replaceChildren(...list.map((rule, i) => {
        const n = i + 1, path = (k) => `outbound[${i}].${k}`;
        const step = control(h("select", { class: "input" }, option("*", "* every step", rule.step === "*"),
          state.steps.map((s) => option(s, s, rule.step === s))), path("step"), `Rule ${n} step`, ctl("outbound", i, "step"));
        if (rule.step && rule.step !== "*" && !state.steps.includes(rule.step)) step.append(option(rule.step, `${rule.step} (unknown)`, true));
        if (!rule.step) step.prepend(option("", "Choose…", true));
        step.addEventListener("change", () => { rule.step = step.value; rerender(); changed(); });
        const { base: fieldBase, key: fieldKey } = splitField(rule.field);
        const fieldSelect = control(h("select", { class: "input" }, fieldOptions()), path("field"), `Rule ${n} field`, ctl("outbound", i, "field"));
        if (![...fieldSelect.options].some((o) => o.value === fieldBase)) fieldSelect.prepend(option(fieldBase, fieldBase || "Choose…"));
        fieldSelect.value = fieldBase;
        const keyed = state.fields.find((f) => f.id === fieldBase)?.takes_key;
        fieldSelect.addEventListener("change", () => {
          const picked = state.fields.find((f) => f.id === fieldSelect.value);
          rule.field = picked?.takes_key ? `${fieldSelect.value.slice(0, -1)}${fieldKey}` : fieldSelect.value;
          if (rule.field === "template") rule.template ||= ""; else delete rule.template;
          rerender(); changed();
        });
        const keyInput = keyed ? control(h("input", { class: "input mono", value: fieldKey, list: keyLists[fieldBase], placeholder: "key" }),
          path("field"), `Rule ${n} ${fieldBase.slice(0, -2)} key`, ctl("outbound", i, "key")) : null;
        keyInput?.addEventListener("input", () => { rule.field = `${fieldBase.slice(0, -1)}${keyInput.value.trim()}`; changed(); });
        const format = control(h("select", { class: "input" }, state.formats.map((f) => option(f, f, (rule.format || "raw") === f))),
          path("format"), `Rule ${n} format`, ctl("outbound", i, "format"));
        format.addEventListener("change", () => { rule.format = format.value; changed(); });
        const when = rule.when ||= [];
        const label = () => when.length ? when.join(", ") : "Any status";
        const summaryNode = control(h("summary", { text: label() }), path("when"), `Rule ${n} when: ${label()}`, ctl("outbound", i, "when"));
        const whenBox = h("details", { class: "gs-when" }, summaryNode, h("div", { class: "gs-when-list" }, state.statuses.map((status) => {
          const box = h("input", { type: "checkbox", checked: when.includes(status) });
          box.addEventListener("change", () => {
            const at = when.indexOf(status);
            if (box.checked && at < 0) when.push(status); else if (!box.checked && at >= 0) when.splice(at, 1);
            summaryNode.textContent = label();
            summaryNode.setAttribute("aria-label", `Rule ${n} when: ${label()}`);
            changed();
          });
          return h("label", { class: "check" }, box, h("span", { text: status }));
        })));
        const template = rule.field === "template"
          ? control(h("input", { class: "input mono", value: rule.template || "", placeholder: "{status} in {duration_s}s" }), path("template"), `Rule ${n} template`, ctl("outbound", i, "template"))
          : null;
        template?.addEventListener("input", () => { rule.template = template.value; changed(); });
        return h("tr", {}, h("td", {}, step), columnCell("outbound", list, rule, i), h("td", {}, fieldSelect, keyInput),
          h("td", {}, format), h("td", {}, whenBox), h("td", { class: "gs-template" }, template), rowActions("outbound", list, i));
      }));
    };
    const drawInbound = () => {
      const list = draft.inbound ||= [];
      inBody.replaceChildren(...list.map((rule, i) => {
        const n = i + 1;
        const to = control(h("select", { class: "input" }, option("plan_column", "Plan column", rule.to !== "plan_cell"), option("plan_cell", "Step cell", rule.to === "plan_cell")),
          `inbound[${i}].to`, `Inbound rule ${n} destination type`, ctl("inbound", i, "to"));
        to.addEventListener("change", () => { rule.to = to.value; rerender(); changed(); });
        let target;
        if (rule.to === "plan_cell") {
          target = control(h("select", { class: "input" }, option("", "Choose…", !rule.step), state.steps.map((s) => option(s, s, rule.step === s))),
            `inbound[${i}].step`, `Inbound rule ${n} step`, ctl("inbound", i, "dest"));
          target.addEventListener("change", () => { rule.step = target.value; changed(); });
        } else {
          target = control(h("input", { class: "input mono", value: rule.plan_column || "", placeholder: "notes" }),
            `inbound[${i}].plan_column`, `Inbound rule ${n} plan column`, ctl("inbound", i, "dest"));
          target.addEventListener("input", () => { rule.plan_column = target.value.trim(); changed(); });
        }
        return h("tr", {}, columnCell("inbound", list, rule, i), h("td", {}, to), h("td", {}, target), rowActions("inbound", list, i));
      }));
    };
    const drawOptions = () => {
      const rows = draft.rows ||= {};
      const missing = control(h("select", { class: "input" }, option("skip", "Skip targets with no row", rows.missing_row !== "append"),
        option("append", "Append a row", rows.missing_row === "append")), "rows.missing_row", "Missing row", "opt.missing");
      missing.addEventListener("change", () => { rows.missing_row = missing.value; changed(); });
      const range = control(h("input", { class: "input mono", value: draft.read_range || "", placeholder: "A1:Z" }), "read_range", "Read range", "opt.range");
      range.addEventListener("input", () => { draft.read_range = range.value.trim(); changed(); });
      const verify = control(h("input", { type: "checkbox", checked: draft.verify_before_write !== false }), "verify_before_write", "Verify before write", "opt.verify");
      verify.addEventListener("change", () => { draft.verify_before_write = verify.checked; changed(); });
      const enabled = control(h("input", { type: "checkbox", role: "switch", checked: draft.enabled !== false }), "enabled", "Sync enabled", "opt.enabled");
      enabled.addEventListener("change", () => { draft.enabled = enabled.checked; changed(); });
      options.replaceChildren(field("Missing row", missing), field("Read range", range, "Optional, without a tab name."),
        h("label", { class: "check" }, verify, h("span", { text: "Verify cells before writing" })),
        h("label", { class: "check" }, enabled, h("span", { text: "Sync enabled" })));
      for (const node of options.querySelectorAll("[aria-label]")) if (node.closest("label") || node.labels?.length) node.removeAttribute("aria-label");
    };
    const showErrors = () => {
      body.querySelectorAll("[aria-invalid]").forEach((node) => node.removeAttribute("aria-invalid"));
      body.querySelectorAll(".gs-err").forEach((node) => node.remove());
      body.querySelectorAll("[data-gs-described]").forEach((node) => { node.removeAttribute("aria-describedby"); node.removeAttribute("data-gs-described"); });
      const items = errors.map((error) => {
        let node = body.querySelector(`[data-path="${CSS.escape(error.path)}"]:not([hidden])`);
        if (!node && /\[\d+\]$/.test(error.path)) node = body.querySelector(`[data-path^="${CSS.escape(error.path)}."]:not([hidden])`);
        if (node) {
          const id = uid("err");
          node.setAttribute("aria-invalid", "true");
          node.setAttribute("aria-describedby", id);
          node.setAttribute("data-gs-described", "");
          (node.closest("td, .field, label") || node.parentNode).append(h("p", { class: "gs-err small", id, text: error.message }));
        }
        return { error, node };
      });
      summary.replaceChildren(...items.length ? [h("p", { text: `${items.length} problem${items.length === 1 ? "" : "s"}` }),
        h("ul", {}, items.map(({ error, node }) => h("li", {}, node
          ? h("button", { type: "button", class: "link-btn", text: `${error.path}: ${error.message}`, onclick: () => { node.closest("details")?.setAttribute("open", ""); node.focus(); } })
          : h("span", { text: `${error.path}: ${error.message}` }))))] : []);
      if (save) {
        save.disabled = !canWrite || errors.length > 0;
        save.title = !canWrite ? OFF_TITLE : errors.length ? `Fix ${errors.length} problem${errors.length === 1 ? "" : "s"} first` : "";
      }
    };
    let sequence = 0;
    const validate = debounce(async () => {
      const mine = ++sequence;
      try {
        const result = await call(project, "validate", { mapping: cleanMapping(draft) });
        if (mine !== sequence || !body.isConnected) return;
        errors = result.errors || [];
        warn.replaceChildren(...(result.warnings || []).map((w) => h("p", { class: "small muted", text: w })));
      } catch (error) {
        if (mine !== sequence) return;
        errors = [{ path: "mapping", message: error.message }];
      }
      showErrors();
    }, 600);
    const warn = h("div", { class: "gs-warnings" });
    const addRule = h("button", { type: "button", class: "btn", text: "Add rule", "data-ctl": "outbound.add", onclick: () => {
      draft.outbound.push({ step: state.steps[0] || "*", column: "", field: "status" });
      rerender(ctl("outbound", draft.outbound.length - 1, "step")); changed();
    } });
    const addStatus = h("button", { type: "button", class: "btn", text: "Add status columns for all steps", onclick: () => {
      const extra = statusRules(draft.outbound, state.steps, headers);
      if (!extra.length) { api.toast("All steps already have a status column", "ok"); return; }
      draft.outbound.push(...extra); rerender(); changed();
      api.toast(`Added ${extra.length} rule${extra.length === 1 ? "" : "s"}`, "ok");
    } });
    const addInbound = h("button", { type: "button", class: "btn", text: "Add inbound rule", "data-ctl": "inbound.add", onclick: () => {
      draft.inbound.push({ column: "", to: "plan_column", plan_column: "" });
      rerender(ctl("inbound", draft.inbound.length - 1, "column")); changed();
    } });
    const table = (caption, columns, tbody) => h("div", { class: "grid-scroll gs-scroll", tabindex: "0", role: "region", "aria-label": `${caption}; scroll horizontally for more columns` },
      h("table", { class: "tbl small gs-rules" }, h("caption", { text: caption }),
        h("thead", {}, h("tr", {}, columns.map((c) => h("th", { scope: "col", text: c })))), tbody));
    const body = h("div", { class: "gs-editor" },
      h("div", { class: "callout", text: state.comments_notice || "Saving rewrites alfrd.gsheet.yaml; comments in it are removed." }),
      headerNote ? alertBox(headerNote) : null,
      h("fieldset", { class: "gs-group" }, h("legend", { text: "Outbound (Studio → sheet)" }),
        table("Outbound rules", ["Step", "Column", "Field", "Format", "When", "Template", "Actions"], outBody),
        h("div", { class: "row gap wrap" }, addRule, addStatus)),
      h("fieldset", { class: "gs-group" }, h("legend", { text: "Inbound (sheet → plan)" }),
        h("p", { class: "small muted", text: "A plan column must be an existing extra column; it cannot change a step, target, code, workdir or files column. A step cell accepts only todo or skip and never changes the unit about to run." }),
        table("Inbound rules", ["Column", "To", "Plan column or step", "Actions"], inBody),
        h("div", { class: "row gap wrap" }, addInbound)),
      h("fieldset", { class: "gs-group" }, h("legend", { text: "Options" }), options),
      datalists, warn, summary);
    let save = null;
    const handle = api.dialog({ title: "Google Sheet mapping", body, wide: true, actions: [
      { label: "Cancel", run: (d) => d.close() },
      { label: "Save", tone: "primary", async run(d) {
        validate.cancel();
        if (!await confirmRewrite(project, state, d.confirm)) return;
        let result;
        try { result = await call(project, "save", { mapping: cleanMapping(draft), base_sha256: base }); }
        catch (error) {
          if (!error.message.includes(CONFLICT)) throw error;
          const reload = await d.confirm("The file changed on disk. Reload it and discard your edits?", { tone: "danger", confirmLabel: "Reload", cancelLabel: "Keep editing" });
          if (!reload) return;
          const fresh = await call(project, "state");
          if (!fresh.mapping) { d.setDirty(false); d.close(); refresh(project); return; }
          Object.assign(state, fresh);
          base = fresh.mapping_sha256; draft = structuredClone(fresh.mapping); loaded = JSON.stringify(cleanMapping(draft));
          errors = []; d.setDirty(false); rerender(); validate();
          return;
        }
        if (!result.saved) { errors = result.errors || []; showErrors(); return; }
        base = result.mapping_sha256;
        d.setDirty(false);
        api.toast("Mapping saved", "ok");
        d.close();
        refresh(project);
      } },
    ] });
    save = handle.footer.querySelector(".btn.primary");
    rerender();
    validate.flush();
    return handle;
  }

  // -- preview / backfill --
  function openPreview(project, state, write) {
    const tabName = state.mapping?.worksheet ?? `gid ${state.mapping?.gid}`;
    const body = h("div", { class: "gs-preview" }, h("p", { class: "muted", role: "status", text: "Computing the cells that would change…" }));
    let preview = null, writeButton = null;
    const actions = [{ label: "Close", run: (d) => d.close() }];
    if (write) actions.push({ label: "Write cells…", tone: "primary", async run(d) {
      const total = preview?.total_cells || 0;
      if (!await d.confirm(`Write ${total} cell${total === 1 ? "" : "s"} to ${tabName}?`, { tone: "primary", confirmLabel: "Write" })) return;
      const result = await call(project, "backfill", { confirm: true });
      api.toast(result.dry_run ? `Dry run: nothing written (${result.total_cells} cells)` : `Wrote ${result.cells_written} cells (${result.result})`, result.result === "error" ? "fail" : "ok");
      d.close();
      refresh(project);
    } });
    const handle = api.dialog({ title: write ? "Backfill Google Sheet" : "Preview Google Sheet changes", body, wide: true, actions });
    writeButton = write ? handle.footer.querySelector(".btn.primary") : null;
    if (writeButton) writeButton.disabled = true;
    call(project, "preview").then((result) => {
      preview = result;
      const parts = [h("p", { role: "status" }, h("strong", { text: `${result.total_cells} cell${result.total_cells === 1 ? "" : "s"} would change in ${tabName}` }),
        result.truncated ? " (showing first 500)" : "")];
      if (result.conflicts?.length) parts.push(h("div", { class: "callout warn", text: `Changed in the sheet since the last sync, so left alone: ${result.conflicts.join(", ")}` }));
      if (!result.cells.length) parts.push(h("p", { text: "Nothing to write; the sheet is up to date." }));
      else parts.push(h("div", { class: "grid-scroll gs-scroll", tabindex: "0", role: "region", "aria-label": "Cells that would change" },
        h("table", { class: "tbl small gs-cells" }, h("caption", { text: "Cells that would change" }),
          h("thead", {}, h("tr", {}, ["Cell", "Target", "Step", "Column", "Old → New"].map((c) => h("th", { scope: "col", text: c })))),
          h("tbody", {}, result.cells.map((cell) => h("tr", {}, h("th", { scope: "row", class: "mono", text: cell.a1 }),
            h("td", { text: cell.target }), h("td", { text: cell.step }), h("td", { text: cell.column }),
            h("td", {}, h("del", { class: cell.old === "" ? "muted gs-empty" : "muted", text: cell.old === "" ? "(empty)" : String(cell.old) }), " → ", h("span", { text: String(cell.new) }))))))));
      if (write && state.dry_run) parts.push(h("p", { class: "small muted", text: "Dry run is on in Settings, so a backfill writes nothing." }));
      body.replaceChildren(...parts);
    }).catch((error) => body.replaceChildren(alertBox(error.message)))
      .finally(() => {
        if (writeButton) writeButton.disabled = !(preview?.total_cells > 0);
      });
    return handle;
  }

  // -- detach --
  function openDetach(project, state, preset = "disable") {
    const name = uid("detach");
    const choice = (value, title, text) => h("label", { class: "check gs-choice" },
      h("input", { type: "radio", name, value, checked: value === preset }), h("span", {}, h("strong", { text: title }), h("br"), h("small", { class: "muted", text })));
    const group = h("fieldset", { class: "gs-group" }, h("legend", { text: "How do you want to detach?" }),
      state.mapping ? choice("disable", "Disable sync (keep file)", "Nothing is written until you turn it back on.") : null,
      choice("delete", "Delete alfrd.gsheet.yaml", "Removes the mapping file. Sync history in .alfrd/gsheet stays."));
    const body = h("div", {}, group);
    const handle = api.dialog({ title: "Detach Google Sheet", body, actions: [
      { label: "Cancel", run: (d) => d.close() },
      { label: "Detach", tone: "primary", async run(d) {
        const mode = group.querySelector("input:checked")?.value || "disable";
        if (mode === "delete" && !await d.confirm("Delete alfrd.gsheet.yaml? This cannot be undone from Studio.", { tone: "danger", confirmLabel: "Delete" })) return;
        await call(project, "detach", { mode, base_sha256: state.mapping_sha256 });
        api.toast(mode === "delete" ? "Mapping deleted" : "Sync disabled", "ok");
        d.close();
        refresh(project);
      } },
    ] });
    const detach = handle.footer.querySelector(".btn.primary");
    const tone = () => {
      const del = group.querySelector("input:checked")?.value === "delete";
      detach.classList.toggle("danger", del);
      detach.classList.toggle("primary", !del);
    };
    if (!state.mapping) group.querySelector("input").checked = true;
    group.addEventListener("change", tone);
    tone();
    return handle;
  }

  api.registerProjectSection({ id: "gsheet", title: "Google Sheet", order: 50, render: renderSection });
  const withProject = (fn) => async () => {
    const project = api.project();
    if (!project) { api.toast("Select a project first.", "warn"); return; }
    try { await fn(project); } catch (error) { api.toast(error.message, "fail"); }
  };
  api.registerCommand({ id: "gsheet-editor", label: "Google Sheet: open mapping editor", run: withProject((project) => openEditor(project)) });
  api.registerCommand({ id: "gsheet-attach", label: "Google Sheet: attach…", run: withProject(async (project) => {
    const state = await call(project, "state");
    if (!state.credentials) { api.toast("Add a service-account key in Settings → Plugins → Google Sheet first.", "warn"); return; }
    if (!await policy()) { api.toast(OFF_TITLE, "warn"); return; }
    openAttach(project, state);
  }) });
  return { renderSection, openAttach, openEditor, editor, openPreview, openDetach };
}
