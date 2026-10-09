"""Plugins: manifest types, discovery, loading and the enabled/theme state.

A plugin is a Python distribution with an entry point in the ``alfrd.plugins``
group whose name is the plugin id and whose object is a :class:`Plugin`::

    [project.entry-points."alfrd.plugins"]
    markdown = "alfrd_markdown:plugin"

Plugins run with full trust (they are Python code in the alfrd process); a
broken one never stops ``alfrd serve`` or the CLI: :func:`load` records the
error and goes on. ``ALFRD_NO_PLUGINS=1`` (``--safe-mode``) skips them all.

Where things live (resolved per call, so XDG changes in tests apply):
``plugins_dir()/site`` (installed plugins, after site-packages on ``sys.path``),
``themes_dir()/<id>/theme.css`` (drop-in themes), ``state_file()``
(``{"disabled": [...], "theme": "obsidian-orbit", "catalog_url": ..., "gui_install": true,
"plugin_actions": true, "autostart": ["<plugin>:<service>"]}``).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ALFRD_PLUGIN_API = 1
GROUP = "alfrd.plugins"
DEFAULT_THEME = "obsidian-orbit"
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_ACTION = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
THEME_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")  # `_` too: a plugin's id names its theme


# ---------------------------------------------------------------------------
# Manifest


def _tuple(value: Any) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    return tuple(value)


@dataclass(frozen=True)
class Converter:
    """``run(src: Path, dest: Path, *, timeout: float)`` turns a file with one of the ``src`` extensions into ``to``.

    It writes ``dest`` or raises; ``timeout`` (seconds) bounds any subprocess it starts. The
    Studio calls it in a worker process (:mod:`alfrd.extensions.convert`) and caches the output.
    """

    src: tuple[str, ...]
    to: str
    run: Callable

    def __post_init__(self) -> None:
        object.__setattr__(self, "src", _tuple(self.src))


@dataclass(frozen=True)
class PanelSpec:
    """A ``views.metadata`` panel kind: a server ``evaluate(root, panel, values, spec)`` and/or a browser renderer.

    ``auto(root) -> dict | None``: when it returns a panel instance (e.g. ``{"title": "…", "scope": "project"}``)
    for a project, the panel is added to its metadata view as if ``views.metadata`` listed it. It must be cheap
    (filesystem only, no network); an exception is logged and ignored.
    """

    kind: str
    evaluate: Callable | None = None
    client: bool = False
    title: str = ""
    auto: Callable[[Path], dict | None] | None = None

    def __post_init__(self) -> None:
        if self.auto is not None and not callable(self.auto):
            raise TypeError(f"panel {self.kind!r}: auto must be callable")


@dataclass(frozen=True)
class ProjectAction:
    """A Studio call on one project: ``run(root, settings_values, payload) -> dict`` (JSON-safe).

    ``POST /studio/projects/<project>/plugins/<id>/actions/<action>`` runs it in a worker thread for at
    most ``timeout`` seconds. A ``ValueError`` message is shown to the user; any other error shows only its
    type. A ``mutating`` action (writes files, or calls external APIs that write) is audited, and
    ``"plugin_actions": false`` in ``plugins.json`` or ``alfrd serve --no-plugin-actions`` refuses it.
    """

    id: str
    run: Callable[[Path, dict, dict], dict]
    mutating: bool = False
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _ACTION.match(self.id):
            raise ValueError(f"invalid project action id {self.id!r}")
        if not callable(self.run):
            raise TypeError(f"project action {self.id!r}: run must be callable")
        timeout = self.timeout
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= 600:
            raise ValueError(f"project action {self.id!r}: timeout must be between 0 and 600 seconds")


#: Setting kinds: ``text`` and ``number`` are shown; a ``secret`` is write-only (never sent back to a browser).
SETTING_KINDS = ("text", "number", "secret", "bool")


@dataclass(frozen=True)
class SettingField:
    """One user setting of a plugin, edited in Settings → Plugins and read with :func:`alfrd.extensions.settings.values`.

    ``pattern`` (a regular expression the whole value must match) and ``required`` are checked on save.
    """

    key: str
    label: str = ""
    kind: str = "text"
    required: bool = False
    help: str = ""
    pattern: str = ""
    placeholder: str = ""

    def __post_init__(self) -> None:
        if not _KEY.match(self.key):
            raise ValueError(f"invalid setting key {self.key!r}")
        if self.kind not in SETTING_KINDS:
            raise ValueError(f"setting {self.key!r}: kind must be one of {', '.join(SETTING_KINDS)}")
        if self.pattern:
            re.compile(self.pattern)


@dataclass(frozen=True)
class Service:
    """A long-running ``alfrd <command…>`` that ``alfrd serve`` starts as a child process on request.

    ``command`` is the arguments after ``alfrd`` (usually the plugin's own CLI, e.g. ``("telegram", "run")``).
    The child gets ``ALFRD_RUNTIME_DB`` (the server's runtime database) and is stopped with SIGINT.
    """

    id: str
    command: tuple[str, ...]
    title: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", _tuple(self.command))
        if not _KEY.match(self.id):
            raise ValueError(f"invalid service id {self.id!r}")
        if not self.command or not all(isinstance(a, str) and a for a in self.command):
            raise ValueError(f"service {self.id!r}: command must be a non-empty list of strings")


@dataclass(frozen=True)
class StepContext:
    """The public, JSON-friendly view of one launched unit that :class:`StepHooks` receive.

    ``rows`` is a list of ``{"key", "target", "code", "workdir"}``. The fields after
    ``rows`` are filled for ``after`` only (``None`` / empty in ``before``): ``log_path``
    is absolute, ``usage`` holds :func:`alfrd.runtime.usage.summary` keys, and ``cells`` is
    ``{row_key: {step: plan-CSV cell}}`` for the unit's rows and steps after the unit.
    Each hook call gets its own copy, so a hook can't change the runner's records.
    """

    project_root: str
    plan_id: str
    unit_id: str
    mode: str
    steps: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    status: str | None = None
    exit_code: int | None = None
    error: str | None = None
    started: str | None = None
    finished: str | None = None
    duration_s: float | None = None
    log_path: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    agent_usage: dict[str, Any] | None = None
    total_cost_usd: float | None = None
    model: str | None = None
    outcome: str | None = None
    results: dict[str, Any] = field(default_factory=dict)
    cells: dict[str, dict[str, str]] = field(default_factory=dict)
    readopted: bool = False  # ``after`` for a unit an earlier runner launched (its before-state is ``None``)

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", _tuple(self.steps))
        object.__setattr__(self, "rows", _tuple(self.rows))

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        out = asdict(self)
        out["steps"], out["rows"] = list(self.steps), [dict(r) for r in self.rows]
        return out


@dataclass(frozen=True)
class StepHooks:
    """Calls around each *launched* unit (never for a skipped, blocked or never-started one).

    ``before(ctx)`` runs after the unit is recorded and its first step's plan cells are
    ``running``, right before the command is spawned; it blocks the launch for at most
    ``timeout`` seconds and its return value (opaque state) is passed to ``after``.
    ``after(ctx, state)`` runs after the unit record is saved when it ends (``state`` is
    ``None`` for a unit re-adopted after a runner restart, or when ``before`` failed).
    Each call runs in a worker thread: an exception or a timeout is logged and recorded
    as a ``plugin.hook_failed`` event, and never changes the step's status.
    """

    before: Callable[[StepContext], Any] | None = None
    after: Callable[[StepContext, Any], None] | None = None
    timeout: float = 30.0

    def __post_init__(self) -> None:
        for name in ("before", "after"):
            if getattr(self, name) is not None and not callable(getattr(self, name)):
                raise TypeError(f"step_hooks.{name} must be callable")
        if not isinstance(self.timeout, (int, float)) or not 0 < float(self.timeout) <= 600:
            raise ValueError("step_hooks.timeout must be between 0 and 600 seconds")


@dataclass(frozen=True)
class Plugin:
    id: str
    version: str
    alfrd_api: str = ">=1,<2"
    title: str = ""
    description: str = ""
    requires_bin: tuple[str, ...] = ()
    web: str | None = None  # package-relative folder with index.js (browser code: Phase 3)
    viewers: tuple[str, ...] = ()  # viewer ids the browser code registers
    panels: tuple[PanelSpec, ...] = ()
    converters: tuple[Converter, ...] = ()
    theme: str | None = None  # package-relative theme.css
    cli: Any = None  # a typer.Typer, mounted as `alfrd <id> …`
    settings: tuple[SettingField, ...] = ()  # Settings → Plugins form; values in settings.settings_file()
    services: tuple[Service, ...] = ()  # child processes `alfrd serve` can run (Start/Stop in Settings)
    check: Callable | None = None  # check(values) -> str: a "Test" button; raise ValueError with a safe message
    step_hooks: StepHooks | None = None  # before/after each launched unit, in the plan runner
    check_project: Callable | None = None  # check_project(root, values) -> (level, text): protected Studio check
    project_actions: tuple[ProjectAction, ...] = ()  # Studio calls on one project (see ProjectAction)

    def __post_init__(self) -> None:
        for name in ("requires_bin", "viewers", "panels", "converters", "settings", "services", "project_actions"):
            object.__setattr__(self, name, _tuple(getattr(self, name)))
        if not all(isinstance(a, ProjectAction) for a in self.project_actions):
            raise TypeError("project_actions must be alfrd.extensions.ProjectAction values")
        ids = [a.id for a in self.project_actions]
        if len(ids) != len(set(ids)):
            raise ValueError("project action ids must be unique")
        if self.step_hooks is not None and not isinstance(self.step_hooks, StepHooks):
            raise TypeError("step_hooks must be an alfrd.extensions.StepHooks")
        if self.check_project is not None and not callable(self.check_project):
            raise TypeError("check_project must be callable")

    @property
    def kinds(self) -> list[str]:
        out = []
        if self.viewers or self.web:
            out.append("viewer")
        if self.panels:
            out.append("panel")
        if self.converters:
            out.append("converter")
        if self.theme:
            out.append("theme")
        if self.cli is not None:
            out.append("cli")
        if self.settings:
            out.append("settings")
        if self.services:
            out.append("service")
        if self.step_hooks is not None:
            out.append("step_hooks")
        return out


def _version(text: str) -> tuple[int, ...]:
    parts = text.strip().split(".")
    if not parts or not all(p.isdigit() for p in parts):
        raise ValueError(f"bad version {text!r}")
    return tuple(int(p) for p in parts)


_OPS: dict[str, Callable[[tuple, tuple], bool]] = {
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b, ">": lambda a, b: a > b, "<": lambda a, b: a < b,
}


def api_ok(spec: str, version: int | str = ALFRD_PLUGIN_API) -> bool:
    """Whether ``version`` satisfies ``spec`` (``">=1,<2"``; comma = and). A bad spec raises ``ValueError``."""
    have = _version(str(version))
    clauses = [c.strip() for c in str(spec or "").split(",") if c.strip()]
    if not clauses:
        raise ValueError(f"empty alfrd_api specifier {spec!r}")
    for clause in clauses:
        op = next((o for o in (">=", "<=", "==", "!=", ">", "<") if clause.startswith(o)), None)
        if op is None:
            raise ValueError(f"bad alfrd_api specifier {clause!r}")
        want = _version(clause[len(op):])
        width = max(len(have), len(want))
        if not _OPS[op](have + (0,) * (width - len(have)), want + (0,) * (width - len(want))):
            return False
    return True


# ---------------------------------------------------------------------------
# Paths and state


def _data_dir() -> Path:
    from platformdirs import user_data_dir

    return Path(user_data_dir("alfrd"))


def plugins_dir() -> Path:
    return _data_dir() / "plugins"


def site_dir() -> Path:
    return plugins_dir() / "site"


def themes_dir() -> Path:
    return _data_dir() / "themes"


def state_file() -> Path:
    from platformdirs import user_config_dir

    return Path(user_config_dir("alfrd")) / "plugins.json"


def _raw_state() -> dict[str, Any]:
    try:
        data = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def catalog_url_ok(url: Any) -> bool:
    """Only ``https://`` and ``file://`` catalogs; there is no default URL."""
    return isinstance(url, str) and url.lower().startswith(("https://", "file://")) and len(url) <= 2048


def read_state() -> dict[str, Any]:
    """``{"disabled": [ids], "theme": id, "catalog_url": url | None, "gui_install": bool, "plugin_actions": bool,
    "autostart": [keys]}``.

    A missing or broken file (or key) gives the defaults; a catalog URL that is
    not https/file is ignored.
    """
    state: dict[str, Any] = {"disabled": [], "theme": DEFAULT_THEME, "catalog_url": None, "gui_install": True,
                             "plugin_actions": True, "autostart": []}
    data = _raw_state()
    if isinstance(data.get("disabled"), list):
        state["disabled"] = sorted({str(x) for x in data["disabled"]})
    if isinstance(data.get("theme"), str) and THEME_ID.match(data["theme"]):
        state["theme"] = data["theme"]
    if catalog_url_ok(data.get("catalog_url")):
        state["catalog_url"] = data["catalog_url"]
    if isinstance(data.get("gui_install"), bool):
        state["gui_install"] = data["gui_install"]
    if isinstance(data.get("plugin_actions"), bool):
        state["plugin_actions"] = data["plugin_actions"]
    if isinstance(data.get("autostart"), list):
        state["autostart"] = sorted({str(x) for x in data["autostart"]})
    return state


def write_state(state: dict[str, Any]) -> None:
    """Write ``plugins.json`` atomically (temp file + replace)."""
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".plugins.", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _update_state(**changes: Any) -> dict[str, Any]:
    # Rewrite only the changed keys: hand-edited ones (catalog_url, gui_install,
    # even unknown or invalid values) stay exactly as the user wrote them.
    data = _raw_state()
    data.update(changes)
    write_state(data)
    return read_state()


def set_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
    disabled = set(read_state()["disabled"])
    (disabled.discard if enabled else disabled.add)(plugin_id)
    return _update_state(disabled=sorted(disabled))


def set_autostart(key: str, on: bool) -> dict[str, Any]:
    """Start the service ``"<plugin>:<service>"`` with ``alfrd serve`` (or not)."""
    keys = set(read_state()["autostart"])
    (keys.add if on else keys.discard)(key)
    return _update_state(autostart=sorted(keys))


def set_theme(theme_id: str) -> dict[str, Any]:
    if not THEME_ID.match(theme_id or ""):
        raise ValueError(f"invalid theme id {theme_id!r}")
    return _update_state(theme=theme_id)


def add_site() -> None:
    """Put the plugin site after site-packages on ``sys.path`` (alfrd's own packages always win)."""
    site = str(site_dir())
    if Path(site).is_dir() and site not in sys.path:
        sys.path.append(site)
        importlib.invalidate_caches()


# ---------------------------------------------------------------------------
# Discovery and loading


@dataclass
class Record:
    id: str
    source: str  # entry_point | builtin | theme_dir
    kinds: list[str] = field(default_factory=list)
    entry_point: str = ""
    dist: str = ""
    version: str = ""
    title: str = ""
    enabled: bool = True
    status: str = "not loaded"
    error: str = ""
    traceback_tail: str = ""
    missing_bin: list[str] = field(default_factory=list)
    path: str = ""  # theme.css of a theme
    plugin: Plugin | None = field(default=None, repr=False)
    ep: Any = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k not in ("plugin", "ep")}
        if self.plugin is not None:
            out["description"] = self.plugin.description
            out["alfrd_api"] = self.plugin.alfrd_api
            out["viewers"] = list(self.plugin.viewers)
            out["panels"] = [p.kind for p in self.plugin.panels]
            out["converters"] = [{"src": list(c.src), "to": c.to} for c in self.plugin.converters]
            out["settings"] = [f.key for f in self.plugin.settings]
            out["services"] = [s.id for s in self.plugin.services]
            out["step_hooks"] = self.plugin.step_hooks is not None
            out["project_actions"] = [a.id for a in self.plugin.project_actions]
        return out


def _entry_points() -> list[Any]:
    try:
        return list(importlib.metadata.entry_points(group=GROUP))
    except Exception:  # noqa: BLE001 - a broken dist-info never breaks discovery
        return []


def _theme_title(folder: Path, theme_id: str) -> str:
    try:
        data = json.loads((folder / "theme.json").read_text(encoding="utf-8"))
        return str(data.get("title") or theme_id) if isinstance(data, dict) else theme_id
    except (OSError, ValueError):
        return theme_id


def _theme_records() -> list[Record]:
    from alfrd.web import web_root

    out: dict[str, Record] = {}
    for source, base in (("builtin", web_root() / "css" / "themes"), ("theme_dir", themes_dir())):
        try:
            folders = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
        for folder in folders:
            css = folder / "theme.css"
            if not THEME_ID.match(folder.name) or not css.is_file() or folder.name in out:
                continue  # a drop-in never shadows a built-in theme
            out[folder.name] = Record(id=folder.name, source=source, kinds=["theme"], title=_theme_title(folder, folder.name),
                                      status="ok", path=str(css))
    return list(out.values())


def discover() -> list[Record]:
    """Every plugin (entry points, not imported) and theme, with its enabled state."""
    disabled = set(read_state()["disabled"])
    records = []
    for ep in _entry_points():
        dist = getattr(ep, "dist", None)
        records.append(Record(id=ep.name, source="entry_point", entry_point=ep.value, ep=ep,
                              dist=getattr(dist, "name", "") or "", version=getattr(dist, "version", "") or "",
                              enabled=ep.name not in disabled, status="not loaded" if ep.name not in disabled else "disabled"))
    return records + _theme_records()


_LOADED: list[Record] | None = None
_APPLIED_PANELS: list[str] = []


def safe_mode() -> bool:
    return os.environ.get("ALFRD_NO_PLUGINS", "").strip().lower() not in ("", "0", "false", "no")


def _tail(limit: int = 5) -> str:
    return "".join(traceback.format_exc().rstrip().splitlines(keepends=True)[-limit:])


def _unapply() -> None:
    from alfrd import layout_generic

    for kind in _APPLIED_PANELS:
        layout_generic.unregister_panel(kind)
    _APPLIED_PANELS.clear()


def _apply(plugin: Plugin) -> None:
    from alfrd import layout_generic

    done = []
    try:
        for spec in plugin.panels:
            if spec.kind in layout_generic.PANELS:
                raise ValueError(f"panel kind {spec.kind!r} is already registered")
            layout_generic.register_panel(spec.kind, spec.evaluate, client=spec.client,
                                          auto=_auto(plugin.id, spec.auto) if spec.auto else None)
            done.append(spec.kind)
    except BaseException:
        for kind in done:
            layout_generic.unregister_panel(kind)
        raise
    _APPLIED_PANELS.extend(done)


def _auto(plugin_id: str, auto: Callable) -> Callable:
    """A panel's ``auto`` that adds nothing once its plugin is disabled (before a restart) or in safe mode."""
    def run(root: Path) -> dict | None:
        if safe_mode() or plugin_id in read_state()["disabled"]:
            return None
        return auto(root)
    return run


def _load_one(rec: Record, seen: set[str]) -> None:
    try:
        obj = rec.ep.load()
        if not isinstance(obj, Plugin):
            raise TypeError(f"entry point {rec.entry_point!r} is a {type(obj).__name__}, not alfrd.extensions.Plugin")
        if obj.id != rec.id:
            raise ValueError(f"entry point name {rec.id!r} does not match Plugin.id {obj.id!r}")
        if not _ID.match(obj.id):
            raise ValueError(f"invalid plugin id {obj.id!r}")
        if obj.id in seen:
            raise ValueError(f"another plugin already uses the id {obj.id!r}")
        rec.plugin, rec.title, rec.kinds = obj, obj.title or obj.id, obj.kinds
        rec.version = rec.version or obj.version
        if not api_ok(obj.alfrd_api):
            rec.status = f"incompatible API: needs {obj.alfrd_api} (alfrd provides {ALFRD_PLUGIN_API})"
            return
        rec.missing_bin = [b for b in obj.requires_bin if shutil.which(b) is None]
        if rec.missing_bin:
            rec.status = "missing binary: " + ", ".join(rec.missing_bin)
            return
        _apply(obj)
        seen.add(obj.id)
        rec.status = "ok"
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 - SystemExit from a plugin too: never stop alfrd
        rec.status, rec.error, rec.traceback_tail = "error", f"{type(exc).__name__}: {exc}", _tail()


def load(force: bool = False) -> list[Record]:
    """Discover and apply the enabled plugins, once per process (``force`` re-reads everything)."""
    global _LOADED
    if _LOADED is not None and not force:
        return _LOADED
    _unapply()
    add_site()
    records = discover()
    safe = safe_mode()
    seen: set[str] = set()
    for rec in records:
        if rec.source != "entry_point":
            continue
        if safe:
            rec.status = "skipped (safe mode)"
        elif rec.enabled:
            _load_one(rec, seen)
    _LOADED = records
    return records


def loaded() -> list[Record]:
    """The records of the last :func:`load` (empty before it)."""
    return list(_LOADED or [])


def get(plugin_id: str) -> Record | None:
    return next((r for r in (_LOADED if _LOADED is not None else discover()) if r.id == plugin_id), None)


def module_dir(record: Record) -> Path | None:
    """The package folder of a loaded plugin (where ``web`` and ``theme`` are relative to)."""
    if record.plugin is None or not record.entry_point:
        return None
    module = sys.modules.get(record.entry_point.split(":", 1)[0])
    filename = getattr(module, "__file__", None)
    return Path(filename).resolve().parent if filename else None


def web_dir(record: Record) -> Path | None:
    """The plugin's declared ``web`` folder, confined to its package folder; ``None`` if it has none."""
    base = module_dir(record)
    web = record.plugin.web if record.plugin is not None else None
    if base is None or not web or Path(web).is_absolute():
        return None
    try:
        path = (base / web).resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.is_relative_to(base) and path.is_dir() else None


def active(record: Record) -> bool:
    """Loaded in this process and not disabled since (disable applies to the browser at once)."""
    return record.source == "entry_point" and record.status == "ok" and record.id not in read_state()["disabled"]


def reset() -> None:
    """Forget the loaded plugins and undo their registrations (tests; a fresh ``load()`` follows)."""
    global _LOADED
    _unapply()
    _LOADED = None


__all__ = [
    "ALFRD_PLUGIN_API", "Converter", "DEFAULT_THEME", "GROUP", "PanelSpec", "Plugin", "ProjectAction", "Record", "SETTING_KINDS", "Service",
    "SettingField", "StepContext", "StepHooks", "active", "add_site", "api_ok", "discover", "get", "load", "loaded", "module_dir", "plugins_dir",
    "read_state", "reset", "safe_mode", "set_autostart", "set_enabled", "set_theme",
    "site_dir", "state_file", "themes_dir", "web_dir", "write_state",
]
