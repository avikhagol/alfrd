# Plugins and themes

A plugin is a normal Python package that adds Studio themes, metadata panels,
file viewers, file converters or `alfrd <id> …` commands. Plugins are installed
for the whole alfrd installation, not per project.

## Trust

**A plugin runs as the user who runs `alfrd`, with access to all your files
and projects.** Its Python code runs inside `alfrd serve` and the CLI; its
browser code runs in the Studio's origin. Install only packages you
trust. `alfrd plugin install` prints this warning and asks before installing
(`--yes` skips the question).

A drop-in theme contains CSS only and needs no package install. A Python package that contributes a theme is still a full-trust plugin. Hash verification proves the downloaded bytes match the chosen catalog; it does not prove that the code is safe.

## Commands

```bash
alfrd plugin list                      # id, version, kinds, enabled, status, source package
alfrd plugin info <id>                 # manifest, contributions, last error + traceback tail
alfrd plugin install <spec> [--yes]    # a PyPI name, a local wheel/sdist or a folder
alfrd plugin remove <id> [--yes]
alfrd plugin update [<id>]             # reinstall from the recorded source (all if no id)
alfrd plugin enable|disable <id>
alfrd plugin theme [<id>]              # list themes (* = current) or select one
alfrd plugin new <id> [--kind viewer|converter|theme|panel] [--dir .]
```

Errors print one line and exit with code 1, never a traceback.

**Restart or reload?** `install`, `remove`, `update`, `enable` and `disable`
take effect when `alfrd serve` restarts: the server loads plugins once at start.
Python contributions need that restart. Existing browser-only enable/disable changes offer an explicit Studio reload; the Studio never reloads drafts automatically. Selecting a theme in Settings applies immediately and saves the choice; selecting one in the CLI needs a Studio reload.

`status` is one of `ok`, `disabled`, `error` (import failed, wrong manifest or
id; `info` shows the error), `missing binary: <name>` (a `requires_bin`
program isn't on `PATH`; the plugin is listed but not applied),
`incompatible API` or `skipped (safe mode)`.

## Safe mode

A plugin that fails to load is recorded and skipped; it never stops
`alfrd serve` or the CLI. If a plugin misbehaves after loading, start without
any plugin:

```bash
alfrd serve --safe-mode          # or: ALFRD_NO_PLUGINS=1 alfrd serve
ALFRD_NO_PLUGINS=1 alfrd plugin list
```

Safe mode also ignores plugin-packaged themes (built-in and drop-in themes
still apply). `--safe-mode` is an option of `serve` and `gui`; for other
commands use `ALFRD_NO_PLUGINS=1`. `alfrd plugin …` itself never mounts plugin
commands, so you can always disable or remove a broken plugin.

## Where things live

| path | what |
|---|---|
| `~/.local/share/alfrd/plugins/site/` | installed plugins and their dependencies (`user_data_dir("alfrd")`) |
| `~/.local/share/alfrd/plugins/installed.json` | id, distribution, version, source, sha256 (local files), install time, owned distributions |
| `~/.local/share/alfrd/plugins/.install.lock` | one install/remove/update at a time |
| `~/.local/share/alfrd/themes/<id>/` | drop-in themes |
| `~/.config/alfrd/plugins.json` | `disabled`, `theme`, `catalog_url`, `gui_install` |
| `~/.local/share/alfrd/plugins/catalog.json` | validated catalog cache, normally valid for 24 hours |
| `~/.local/share/alfrd/plugins/jobs/<id>.log` | Studio job output, retained after restart |
| `~/.local/share/alfrd/plugins/audit.jsonl` | one record per finished Studio install/update/remove, including failures and timeouts |

Paths follow `XDG_DATA_HOME` / `XDG_CONFIG_HOME` (macOS and Windows: the
platform's user data and config folders).

## How installs work

The plugin folder is separate from alfrd's own environment, so it works the
same whether alfrd was installed with `uv tool`, pipx, a venv or pip, and a
running server never rewrites its own environment.

- `install` runs `uv pip install --target <plugins/site> --python <alfrd's python>`,
  or `python -m pip install --target …` when `uv` isn't on `PATH`.
- Every package in alfrd's environment is pinned with `--constraint` to its
  installed version. A plugin can't upgrade or downgrade flask, pandas or alfrd
  itself; a conflict fails the install with the resolver's message.
- The plugin folder goes on `sys.path` *after* alfrd's site-packages, so
  alfrd's own packages always win.
- `--target` installs a copy of dependencies alfrd already has (e.g. typer).
  This costs disk space and download time, not correctness: the pinned
  versions are the same and alfrd's copy is imported first.
- The install runs in a staged copy of the plugin folder and replaces it only
  when it succeeds, so a failed install leaves the folder as it was.
- `remove` deletes the files listed in each owned distribution's `RECORD`
  (refusing any path outside the plugin folder), keeps distributions or files
  another plugin still needs, and cleans up `__pycache__` and empty folders.

A plugin distribution must declare exactly one `alfrd.plugins` entry point;
two installed plugins can't share an id.

## Writing a plugin

`alfrd plugin new myviewer` scaffolds `alfrd-myviewer/` with a `pyproject.toml`,
the manifest, `web/index.js` (viewer/panel kinds), a test and a README with the
install and test commands.

```toml
[project]
name = "alfrd-myviewer"
dependencies = []     # not alfrd: the host provides it; `alfrd_api` declares compatibility

[project.entry-points."alfrd.plugins"]
myviewer = "alfrd_myviewer:plugin"     # the name must equal Plugin.id
```

```python
# alfrd_myviewer/__init__.py
from alfrd.extensions import Plugin, PanelSpec

def evaluate(root, panel, values, spec):
    return {"rows": [...]}              # JSON for the browser; raise to report an error

plugin = Plugin(
    id="myviewer", version="0.1.0", alfrd_api=">=1,<2",
    title="My viewer", requires_bin=[],
    panels=[PanelSpec("myviewer_table", evaluate=evaluate)],
)
```

Manifest fields (`alfrd.extensions.Plugin`, plugin API version `1`):

| field | meaning |
|---|---|
| `id` | lower case letters, digits, `-`/`_`; also the entry point name and the `alfrd <id>` command |
| `version`, `title`, `description` | shown by `list` / `info` |
| `alfrd_api` | supported plugin API versions, e.g. `">=1,<2"` (`>= <= > < == !=`, comma-separated) |
| `requires_bin` | programs that must be on `PATH` (e.g. `["gs"]`) |
| `panels` | `PanelSpec(kind, evaluate=None, client=False, title="")`: a `views.metadata` panel kind; `evaluate(root, panel, values, spec)` runs on the server |
| `cli` | a `typer.Typer`, mounted as `alfrd <id> …` (core command names win; a clash is reported in `list`) |
| `theme` | package-relative path of a `theme.css` |
| `web`, `viewers` | package folder with `index.js`, and the viewer ids it registers (loaded by the Studio) |
| `converters` | `Converter(src=[".ps"], to="pdf", run=…)`; `run(src: Path, dest: Path, *, timeout: float)` writes `dest` or raises. The Studio runs it in a worker process with a timeout and caches the output in `<project>/.alfrd/cache/convert/` |

A panel kind registered by a plugin is accepted in `views.metadata`
(see [template-views.md](template-views.md)) and may take its own keys; the
built-in kinds stay strictly validated. An unknown kind is reported with the
list of known kinds. An evaluator that raises shows `{"error": "<kind>: …"}`
in that panel only.

Test a plugin without the installer: `uv pip install -e alfrd-myviewer` into
alfrd's environment, then `alfrd plugin info myviewer`.

## Themes

Two themes are built in: **Obsidian Orbit** (`obsidian-orbit`, dark, the
default) and **Daylight Orbit** (`daylight-orbit`, light).

```bash
alfrd plugin theme daylight-orbit    # then reload the Studio
alfrd plugin theme obsidian-orbit    # back to the default look
```

A theme overrides the colour tokens in `studio.css`'s `:root` and sets
`color-scheme`; nothing else. Make one with `alfrd plugin new my-theme --kind theme`:
it creates `my-theme/theme.css` (every colour token, ready to edit) and
`theme.json` (`{"title", "color_scheme": "light"|"dark"}`). Copy the folder to
`~/.local/share/alfrd/themes/` (`alfrd plugin theme` prints the path) and
select it. Theme ids use lower case letters, digits and `-`.

Keep text at WCAG AA contrast (4.5:1) against its surface, including labels on
accent and status fills (`--on-accent`, `--on-ok`).

The Studio loads built-in and drop-in CSS from `/studio/theme.css` (no cache).
For an enabled plugin theme, the boot hook adds its authenticated
`/studio/plugins/<id>/theme.css` link afterward. Resolution prefers built-in
ids, then drop-in folders, then enabled plugin themes. An unknown
or broken theme falls back to Obsidian Orbit; it never breaks the page. A
built-in id can't be shadowed by a drop-in or plugin.

## Studio installation

Open **Settings → Plugins**. Installed shows enable switches, Diagnostics,
managed-plugin Update/Remove actions and the theme picker. Development installs
are visible but have no Update/Remove menu: manage those in their own environment.
Browse offers search, kind filters, compatibility badges and available updates.

Choose **Install…**, review the version, wheel source, SHA-256, contributions,
required programs and extra packages, then select Install. The trust note names
the account running the server. Missing external programs are shown before
installation; installing the package does not install programs such as `gs`.

**Install from source…** accepts a package name, wheel URL or local path. This
path has no catalog hash pin. Type the plugin id the package provides; the worker
checks that id before promoting any staged files. A wrong id fails with the
actual id in the job log and leaves the installation unchanged.

Only one plugin job runs at a time. Progress and **Show log** appear above both
tabs; the Jobs tray links a running plugin job back to Plugins. Jobs stop after
10 minutes. Success offers **Restart now / Later**. Restart now restarts the
same server command, keeps the login token and session secret, waits for health
to return, and reloads the Studio. Unsaved workflow edits require confirmation.
`--debug`, Windows and servers without restart support require a terminal
restart. A running plugin job prevents restarting.

Studio mutations require the authenticated access session, a CSRF token and a
loopback client. To allow viewing but prevent Studio install/update/remove:

```bash
alfrd serve --no-gui-install
```

Alternatively set `"gui_install": false` in `plugins.json`. Dialogs then show a
copyable terminal command. This policy does not disable CLI administration.

Each completed Studio job appends an audit JSON object with `time`, `user`,
`action`, `id`, `source`, `version`, `sha256`, `result` and `error`. Results are
`ok`, `failed` or `timeout`. The CLI currently does not append these records.
Job status is in memory; a restarted server forgets job ids, while logs and audit
records remain on disk.

## Catalog

**There is no default catalog URL.** Choose the publisher you trust and merge
this setting into `~/.config/alfrd/plugins.json` (preserve existing settings):

```json
{
  "catalog_url": "https://your-publisher.example/plugins/index.json",
  "gui_install": true
}
```

A local `file:///absolute/path/index.json` also works. The server fetches and
validates the catalog, with a 2 MiB limit. Browse Refresh fetches again. If a
refresh fails, a previously validated copy for the same URL remains usable and
is labelled **Offline copy** with the error. Without a cached copy, Browse shows
retry guidance. Switching the URL never reuses another publisher's cache.

Catalog format (`schema_version: 1`; see
[the schema](../src/alfrd/schemas/plugin_catalog.v1.json)):

```json
{
  "schema_version": 1,
  "name": "My plugin catalog",
  "plugins": [{
    "id": "myviewer",
    "title": "My viewer",
    "description": "Read a custom text format",
    "kinds": ["viewer"],
    "alfrd_api": ">=1,<2",
    "requires_bin": [],
    "homepage": "https://your-publisher.example/myviewer",
    "versions": [{
      "version": "0.1.0",
      "wheel": "https://your-publisher.example/alfrd_myviewer-0.1.0-py3-none-any.whl",
      "sha256": "REPLACE_WITH_THE_64_LOWERCASE_HEX_DIGITS_OF_THE_WHEEL_HASH"
    }]
  }]
}
```

The hash above is a placeholder; compute the real value before publishing.
Wheel URLs must use `https://` or `file://` and end in `.whl`. Catalog versions
use one to four numeric components (for example `0.1.0`), not prerelease labels.
Duplicate ids and equivalent versions such as `1.0` / `1.0.0` are rejected.

Every non-host dependency must be explicitly pinned and hashed in that
version's optional `requirements` array, for example
`"some-package==1.2.3 --hash=sha256:<64 lowercase hex digits>"`.
Include transitive dependencies too. Catalog installs use
`--require-hashes --no-deps`; the host provides alfrd and its constrained
packages. The installer checks the wheel's hash before installation, then
checks its plugin id and version before replacing the staged folder. Catalog
updates select the newest published version and repeat these checks. Advanced
source updates reinstall the recorded source without a catalog hash pin.

## Browser API (version 1)

Declare `web="web"` in the Python manifest and package `web/index.js` (ES module)
and optional `web/index.css`. Export `activate(api)`; it may return a Promise.
The Studio loads each enabled, active plugin once during server-mode boot. A
failure appears in Diagnostics and the Log Stream without blocking other
plugins. Browser assets require the same access session as project data.

```javascript
export function activate(api) {
  api.registerViewer({
    id: "myviewer",
    match: [".mytext"],
    render(file, host) {
      host.innerHTML = api.sanitize(`<pre>${escapeText(file.text)}</pre>`);
    }
  });
}

function escapeText(text) {
  return String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
```

| method | contract |
|---|---|
| `registerViewer({id, match, render})` | `match` contains file extensions, MIME types or wildcards such as `image/*`; `render(file, host)` writes DOM and may return a Promise. Newest matching registration wins. |
| `registerPanel(kind, render)` | Register a client panel renderer `render(inst, ctx, target, extra) → HTML or Promise<HTML>`; `extra` contains `view`, `panel`, `id`, `formatFile`. Declare `PanelSpec(kind, client=True)` in Python. |
| `registerCommand({id, label, run, ...})` | Adds a command under Plugins in the command palette; a repeated id replaces its earlier registration. |
| `toast(text, tone, options?)` | Uses the Studio toast; tone examples: `ok`, `warn`, `fail`. |
| `fetchJSON(path, {method="GET", body}={})` | Same-origin API path such as `/studio/plugins` or `/api/studio/plugins`; mutations retain loopback and CSRF checks. |
| `sanitize(html)` | Synchronous DOMPurify HTML sanitization; use it before inserting untrusted HTML. No sandbox is created. |
| `convert(path, to="pdf", project?)` | Returns a Promise of a Blob; defaults to the selected project. Revoke object URLs you create when their content closes. |

Viewer files contain `project`, `path`, `name`, `mime`, `url`, and `text` for text
viewers. Extensions are tried before exact MIME types, then MIME wildcards.
Use `textContent` for plain text. The Markdown reference plugin sanitizes the
HTML generated by its bundled Markdown parser.

The file endpoint serves only paths allowed by the project's manifest. To open
Markdown, declare it as a log or as a `views` text/image panel source; an
arbitrary undeclared `.md` path returns 403. See
[template views](template-views.md). Plugins do not expand this file boundary.

## Converters and reference plugins

```bash
alfrd plugin install ./examples/plugins/alfrd-markdown
alfrd plugin install ./examples/plugins/alfrd-ps2pdf
```

These folders are in the source repository. Restart `alfrd serve` after
installation. Markdown provides `.md` rendering; ps2pdf uses Ghostscript (`gs`
on `PATH`) for `.ps` / `.eps` → PDF. If `gs` is missing, Diagnostics explains the
requirement and the converter is inactive. Declared source files offer
**Convert to PDF**, followed by a PDF viewer.

A converter callback is `run(src: Path, dest: Path, *, timeout: float)`.
It runs in a worker process, with a default 120-second timeout, a 200 MiB source
limit and a 500 MiB output limit. Its PDF cache is under
`<project>/.alfrd/cache/convert/`, keyed by source bytes, plugin id and version.
Source paths must stay inside the project and its declared file set. Worker
isolation and limits protect server responsiveness; the plugin remains full
trust and is not sandboxed.

## Packaging and publishing

Start with `alfrd plugin new myviewer --kind viewer`, then edit the generated
manifest, renderer and test. Keep the entry-point name equal to `Plugin.id` and
ship exactly one `alfrd.plugins` entry point per distribution. Include browser
assets and CSS in the wheel using your build backend's package-data settings;
inspect the built archive before publishing.

Run the generated README's test command, build a wheel with your chosen backend,
and test both folder and wheel installation into a separate alfrd environment.
The scaffolds use Hatchling; `uv build ./alfrd-myviewer --wheel` builds the wheel
when that backend is available. For local development, an editable install in
alfrd's environment works; the Studio treats it as a development install.

Publish the wheel to your package index or HTTPS host. For catalog distribution,
compute its SHA-256, add the exact URL/version/hash and all pinned non-host
dependencies to the catalog, then validate against the schema. Publish changed
bytes as a new version. Reference plugins are supplied as examples here;
separate registry publication is not part of the alfrd release.

## API version policy

`ALFRD_PLUGIN_API = 1` is the plugin contract version, separate from alfrd's
package version. Declare the supported range in `alfrd_api` (`">=1,<2"` is the
normal version-1 range). The host accepts comparison clauses `>=`, `<=`, `>`,
`<`, `==`, `!=`, separated by commas. Unsupported ranges are listed as
incompatible and are not activated. Catalog Browse shows compatibility before
installation.

Version 1 covers manifests, panel/viewer registration, converters, themes,
commands and the browser methods above. Breaking contract changes require a
new plugin API version; adding optional capabilities should preserve existing
version-1 plugins. Plugins must use the public API instead of depending on
private Studio module paths.
