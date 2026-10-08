# Plugins and themes

> **Draft (plugins Phase 1–2).** This page covers what ships now: discovery,
> the `alfrd plugin` commands, the installer and Studio themes. The browser
> plugin API (`activate(api)`), Settings → Plugins, installs from the Studio,
> converter routes and the Diagnostics section come later (Phases 3–4); they
> are marked *later* below.

A plugin is a normal Python package that adds Studio themes, metadata panels,
file viewers, file converters or `alfrd <id> …` commands. Plugins are installed
for the whole alfrd installation, not per project.

## Trust

**A plugin runs as the user who runs `alfrd`, with access to all your files
and projects.** Its Python code runs inside `alfrd serve` and the CLI; its
browser code (later) runs in the Studio's origin. Install only packages you
trust. `alfrd plugin install` prints this warning and asks before installing
(`--yes` skips the question).

A theme is CSS only: no code runs. A drop-in theme folder needs no install.

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
`theme` takes effect when you reload the Studio page; no restart needed.

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
| `~/.config/alfrd/plugins.json` | `{"disabled": [...], "theme": "<id>"}` |

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
| `web`, `viewers` | package folder with `index.js`, and the viewer ids it registers (*later*: loaded by the Studio in Phase 3) |
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

The Studio loads the theme from `/studio/theme.css` (no cache), resolved in
this order: built-in, drop-in folder, an enabled plugin's `theme`. An unknown
or broken theme falls back to Obsidian Orbit; it never breaks the page. A
built-in id can't be shadowed by a drop-in or plugin.

## Later

- **Phase 3:** browser plugin loading (`web/index.js` → `activate(api)` with
  `registerViewer`, `registerPanel`, `sanitize`), Settings → Plugins with a
  theme picker, a Diagnostics section, the convert route and cache, and the
  reference plugins `alfrd-markdown` and `alfrd-ps2pdf`.
- **Phase 4:** a catalog, hash-pinned installs and install/update/remove from
  the Studio.
