// Catalog and advanced confirmation dialogs, loaded only by Settings → Plugins.
import { $, esc, copyText } from "../utils/dom.js";
import { server } from "../data/server.js";
import { startJob } from "./plugin_jobs.js";
const key = encodeURIComponent;
export const kinds = p => (p.kinds || []).map(k => `<span class="badge tone-muted">${esc(k)}</span>`).join(" ");
export const button = (text, attr, cls = "") => `<button type="button" class="btn sm ${cls}" ${attr}>${text}</button>`;
const shellQuote = s => "'" + String(s).replaceAll("'", "'\\''") + "'";
const command = (action, source) => `alfrd plugin ${action} ${shellQuote(source)}`;
const trust = (user, advanced) => `<p class="callout warn plug-trust"><b>Plugins run as ${esc(user)} with full access to your files and projects.</b> Install only plugins you trust. ${advanced ? '<b>This package is not from a catalog and is not hash-checked.</b>' : "The download is checked against the SHA-256 above before anything runs."}</p>`;
const idValid = id => /^[a-z][a-z0-9_-]{0,40}$/.test(id);
export const validSource = (source, id) => Boolean(source.trim() && idValid(id));

// The modal host supports one dialog. Returning to Settings restores the opener's tab.
export function installDialog(ctx, catalog, p = null, action = "install", onReturn = () => {}) {
  const opener = document.activeElement;
  const advanced = !p, remove = action === "remove";
  let allowed = catalog.gui_install !== false && Boolean(server.session?.mutations_enabled);
  let policyCommand = null;
  let version = p?.versions?.find(v => v.version === p.latest) || p?.versions?.at(-1), busy = false;
  const heading = remove ? `Remove ${p.title || p.id}?` : advanced ? "Install from source" : action === "update" ? `Update ${p.title || p.id} to ${p.latest}` : `Install ${p.title || p.id}`;
  const fields = advanced ? `<label class="field">Package, wheel URL or local path<input class="input mono" data-source-input required></label><label class="field">Type the plugin id this package provides to confirm<input class="input" data-id autocomplete="off" spellcheck="false" pattern="[a-z][a-z0-9_-]{0,40}" required aria-describedby="plug-id-hint"></label><small id="plug-id-hint" class="muted">e.g. <code>markdown</code></small>` : remove ? '<p>Its files are deleted. Projects keep their data.</p>' : `<p>${esc(p.description || "")}</p><dl class="plug-facts"><dt>Version</dt><dd><select class="input" data-version aria-label="Version" ${action === "update" ? "disabled" : ""}>${(p.versions || []).map(v => `<option ${v.version === version?.version ? "selected" : ""}>${esc(v.version)}</option>`).join("")}</select></dd><dt>Source</dt><dd><span class="mono" data-wheel></span> ${button("Copy", 'data-copy="wheel"')}</dd><dt>SHA-256</dt><dd><span class="mono" data-hash></span> ${button("Copy", 'data-copy="sha256"')}</dd><dt>Contributes</dt><dd>${kinds(p)}</dd>${p.requires_bin?.length ? `<dt>Needs</dt><dd>${esc(p.requires_bin.join(", "))}</dd>` : ""}<dt data-packages-label hidden>Extra packages</dt><dd data-packages hidden></dd></dl>`;
  let returned = false;
  const returnFocus = () => { if (returned) return; returned = true; onReturn(); requestAnimationFrame(() => {
    if (opener?.isConnected) opener.focus();
    else {
      const attr = ["install", "plugMore", "source"].find(k => opener?.dataset && k in opener.dataset);
      const data = attr === "plugMore" ? "plug-more" : attr;
      if (data) [...document.querySelectorAll(`[data-${data}]`)].find(el => el.dataset[attr] === opener.dataset[attr])?.focus();
    }
  }); };
  const finish = close => { close(); returnFocus(); };
  ctx.modal(`<header class="modal-h"><h2>${esc(heading)}</h2></header><div class="modal-b">${fields}<div data-policy></div>${remove ? "" : trust(catalog.user || "the server account", advanced || !version)}<p class="callout warn" data-dialog-error role="alert" hidden></p><small class="muted" data-reason>${advanced ? "Type the plugin id to enable Install" : ""}</small><div class="row gap">${button(allowed ? "Cancel" : "Close", "data-cancel")}${button(allowed ? remove ? "Remove" : action === "update" ? "Update" : "Install" : "Copy command", "data-submit", remove ? "danger" : "primary")}</div></div>`, (root, close) => {
    root.addEventListener("beforeclose", () => setTimeout(returnFocus, 0), { once: true });
    const submit = $("[data-submit]", root), errorBox = $("[data-dialog-error]", root);
    const source = () => advanced ? $("[data-source-input]", root).value.trim() : action === "install" ? version?.wheel || p.id : p.id;
    const cmd = () => policyCommand || command(action, source());
    const update = () => {
      if (!advanced && !remove) {
        const wheel = $("[data-wheel]", root), hash = $("[data-hash]", root);
        wheel.textContent = version?.wheel || ""; wheel.title = version?.wheel || "";
        hash.textContent = version?.sha256 ? `${version.sha256.slice(0, 12)}…${version.sha256.slice(-8)}` : ""; hash.title = version?.sha256 || "";
        const packages = version?.requirements || [];
        $("[data-packages-label]", root).hidden = $("[data-packages]", root).hidden = !packages.length;
        $("[data-packages]", root).innerHTML = `<details><summary>${packages.length} packages</summary><pre class="mono small">${esc(packages.join("\n"))}</pre></details>`;
      }
      const valid = advanced ? validSource(source(), $("[data-id]", root).value) : action !== "install" || Boolean(version);
      submit.disabled = busy || !valid; submit.setAttribute("aria-disabled", String(submit.disabled));
      $("[data-reason]", root).textContent = advanced && !valid ? source() ? "Type the plugin id to enable Install" : "Enter a package source to enable Install" : "";
      if (!allowed) $("[data-policy]", root).innerHTML = `<p class="callout">Studio installs are turned off on this server. Run this in a terminal:</p><pre class="mono">${esc(cmd())}</pre>`;
    };
    root.oninput = update;
    root.onchange = e => { if (e.target.matches("[data-version]")) version = p.versions.find(v => v.version === e.target.value); update(); };
    root.onclick = async e => {
      if (e.target.closest("[data-cancel]")) return finish(close);
      const copy = e.target.closest("[data-copy]");
      if (copy) { ctx.toast(await copyText(version?.[copy.dataset.copy] || "") ? "Copied" : "Couldn’t copy", "ok"); return; }
      if (!e.target.closest("[data-submit]") || submit.disabled) return;
      if (!allowed) { ctx.toast(await copyText(cmd()) ? "Copied" : "Couldn’t copy", "ok"); return; }
      busy = true; update();
      try {
        const payload = advanced ? { source: source(), confirm_id: $("[data-id]", root).value } : action === "install" ? { id: p.id, version: version.version } : {};
        const result = await server.mutate(action === "install" ? "/studio/plugins/install" : `/studio/plugins/${key(p.id)}/${action}`, payload);
        startJob(result.job, ctx); finish(close);
      } catch (error) {
        if (error.body?.error?.reason === "gui_install") {
          allowed = false; submit.textContent = "Copy command";
          policyCommand = error.body.error.command;
        }
        const job = error.body?.error?.job;
        errorBox.textContent = error.status === 409 && job ? `Another plugin job is running (${job.action} ${job.target}). Wait for it to finish.` : error.message;
        errorBox.hidden = false;
      } finally { busy = false; update(); }
    };
    update();
    // The shared modal focuses after setup; defer to ensure Cancel is the default.
    requestAnimationFrame(() => $("[data-cancel]", root)?.focus());
  });
}

