# Plugins and themes (ALFRD)

A plugin is a trusted Python package (entry point `alfrd.plugins`) adding
themes, `views.metadata` panels, file viewers, converters or `alfrd <id> …`
commands; installed per alfrd installation, not per project. `install|remove|
update|enable|disable` apply on the next `alfrd serve` restart. Status values:
`ok disabled error`, `missing binary: X`, `incompatible API`, `skipped (safe mode)`.
Themes: built-in `obsidian-orbit` (dark, default) and `daylight-orbit`; drop-in
CSS themes in `~/.local/share/alfrd/themes/<id>/`. CLI theme selection needs a
Studio reload; Settings applies at once. Scaffold: `alfrd plugin new <id>
[--kind viewer|converter|theme|panel]`. Details: `docs/plugins.md`.
Plugins may declare `settings` (form in Settings → Plugins → Configure; values in `~/.config/alfrd/plugin-settings.json`, 0600, read with `alfrd.extensions.settings.values(id)`), a `check` (Test) and `services` (`alfrd <cmd>` children of `alfrd serve`: Start/Stop, log, *Start with the Studio*; exit 2 = don't restart).
Example `examples/plugins/alfrd-telegram`: token + allowed chat id in its settings; the bot runs as its service or `alfrd telegram run` (`/status /runs /log /help`, `/pause /resume` after Yes/No).

Writing one (start from `alfrd plugin new`, don't hand-roll the layout):

- `pyproject.toml`: `dependencies` must **not** list `alfrd` (the host provides
  it); exactly one `[project.entry-points."alfrd.plugins"]` entry, whose name
  equals `Plugin.id` (`myid = "alfrd_myid:plugin"`).
- `from alfrd.extensions import Plugin, PanelSpec, Converter`; `plugin = Plugin(id=…,
  version=…, alfrd_api=">=1,<2", panels=[…], converters=[…], cli=typer_app,
  theme="theme.css", web="web", viewers=[…], requires_bin=[…])`.
- `PanelSpec(kind, evaluate=fn)`: `fn(root, panel, values, spec)` returns JSON
  for the browser or raises. `Converter(src=[".ps"], to="pdf", run=fn)`:
  `fn(src, dest, *, timeout)` writes `dest` or raises.
- Theme only: `--kind theme` gives `theme.css` (colour tokens) + `theme.json`;
  copy the folder to the drop-in themes path, no package needed.
- Test: `uv pip install -e ./alfrd-myid` into alfrd's env, then
  `alfrd plugin info myid` (shows the load error if any); restart `alfrd serve`.
