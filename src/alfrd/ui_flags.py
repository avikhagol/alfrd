"""The single registry of Studio UI flags.

This module deliberately has no Flask or route dependency.  Routes can use the
registry later, while tests and other callers can use the same contract today.
Values are kept as small mappings by scope; a caller may persist those mappings
where its scope is authoritative.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from copy import deepcopy
from typing import Any, Mapping


Scope = str
SCOPES = ("browser", "project", "machine")
_MISSING = object()


@dataclass(frozen=True)
class Flag:
    key: str
    scope: Scope
    type: str
    default: Any
    label: str
    help: str
    dangerous: bool = False
    requires: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["requires"] = list(self.requires)
        return value


def _f(key: str, scope: Scope, type_: str, default: Any, label: str, help_: str,
       dangerous: bool = False, requires: tuple[str, ...] = ()) -> Flag:
    return Flag(key, scope, type_, default, label, help_, dangerous, requires)


# Every switch named by survey_ui.md is represented here.  Non-switch values
# from superuser.DEFAULTS and chat.DEFAULTS are included where they are user
# settings, so the registry remains the one inventory for Settings.
FLAGS: tuple[Flag, ...] = (
    _f("features.experimental", "machine", "bool", False, "Experimental features", "Enable experimental Studio features and Chat mode."),
    _f("studio.hidden_sections", "machine", "list[str]", [], "Hidden Studio sections", "Sections hidden from the workspace rail."),
    _f("studio.hidden_templates", "machine", "list[str]", [], "Hidden project templates", "Templates hidden from New project."),
    _f("studio.live_interval", "machine", "number|null", None, "Live update interval", "Seconds between busy-project checks."),
    _f("lockdown.read_only", "machine", "bool", False, "Read-only Studio", "Refuse changes while allowing safe stop operations.", True),
    # These names are part of the existing superuser contract and must not be renamed.
    *tuple(_f(f"lockdown.{key}", "machine", "bool", True, label, help_, True)
           for key, label, help_ in (
               ("plugins", "Allow plugin management", "Allow plugin install, update, removal and configuration."),
               ("plugin_actions", "Allow plugin actions", "Allow plugin actions that change files or external data."),
               ("project_create", "Allow creating and connecting projects", "Allow project creation and connection."),
               ("project_delete", "Allow forgetting and deleting projects", "Allow projects to be forgotten or deleted."),
               ("runs", "Allow workflow runs", "Allow starting, resuming and reviewing runs."),
               ("chat", "Allow chat", "Allow chat sessions, messages, decisions and replies."),
               ("file_edits", "Allow project file edits", "Allow edits to project files and task records."),
           )),
    _f("limits.chat_max_cost_usd", "machine", "number|null", None, "Chat cost cap", "Maximum cost per chat request."),
    _f("limits.chat_max_replans", "machine", "int|null", None, "Chat replan cap", "Maximum blocker replans per request."),
    _f("limits.chat_allowed_agents", "machine", "list[str]", [], "Allowed chat agents", "Restrict new chat work to these agents; empty means all."),
    _f("ui.live_updates", "browser", "bool", True, "Live updates", "Poll for changes while a project is open."),
    _f("ui.experimental_chat", "browser", "bool", False, "Experimental Chat mode", "Show the experimental Chat workspace.", requires=("features.experimental",)),
    _f("ui.browser_notifications", "browser", "bool", False, "Browser notifications", "Show notifications for relevant Studio events."),
    _f("ui.project_route_info", "browser", "bool", True, "Project route information", "Show project route details in notifications."),
    _f("ui.rows_per_page", "browser", "int", 25, "Rows per page", "Default table page size."),
    _f("ui.theme", "browser", "str", "obsidian-orbit", "Theme", "Selected Studio theme."),
    _f("ui.inspect", "browser", "bool", False, "Inspect panel", "Remember whether the Chat inspector is open."),
    _f("chat.final_summary", "project", "bool", False, "Final summary", "Ask the configured agent for a final summary per request."),
    _f("chat.native_skills", "project", "bool", True, "Native skills", "Use an adapter's native skills when supported."),
    _f("chat.native_plugins", "project", "bool", True, "Native plugins", "Use an adapter's native plugins when supported."),
    _f("chat.native_mcp", "project", "bool", True, "Native MCP", "Use an adapter's native MCP tools when supported."),
    _f("chat.saved_skills", "project", "bool", False, "Saved skills", "Inject selected saved skills into requests."),
    *tuple(_f(f"chat.agents.{agent}.lean", "project", "bool", default, f"{agent} lean startup",
              "Start this agent with its lean configuration.")
           for agent, default in (("claude", True), ("sonnet", True), ("codex", False), ("agy", False))),
)

REGISTRY = FLAGS
BY_KEY = {flag.key: flag for flag in FLAGS}

# The UI portion is intentionally flat and serialisable.  It is the shape a
# config loader can validate without importing the web application.
DEFAULTS: dict[str, Any] = {"ui": {flag.key: deepcopy(flag.default) for flag in FLAGS}}


class UIFlagError(ValueError):
    """Invalid UI flag data or an optimistic-concurrency conflict."""


class RevisionConflict(UIFlagError):
    status_code = 409


def _type_ok(flag: Flag, value: Any) -> bool:
    if flag.type == "bool":
        return isinstance(value, bool)
    if flag.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if flag.type == "number|null":
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))
    if flag.type == "int|null":
        return value is None or (isinstance(value, int) and not isinstance(value, bool))
    if flag.type == "str":
        return isinstance(value, str)
    if flag.type == "list[str]":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return False


def validate_ui(values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and default ``DEFAULTS['ui']``-shaped values.

    Unknown keys are rejected, and returned lists are copied so callers cannot
    mutate validated state behind the registry's back.
    """
    if values is None:
        values = {}
    if not isinstance(values, Mapping):
        raise UIFlagError("DEFAULTS['ui'] must be a mapping")
    unknown = set(values) - set(DEFAULTS["ui"])
    if unknown:
        raise UIFlagError(f"ui: unknown key(s): {', '.join(sorted(unknown))}")
    result = deepcopy(DEFAULTS["ui"])
    for key, value in values.items():
        flag = BY_KEY[key]
        if not _type_ok(flag, value):
            raise UIFlagError(f"{key} must have type {flag.type}")
        if key == "ui.rows_per_page" and value not in (10, 25, 50, 100):
            raise UIFlagError(f"{key} must be one of 10, 25, 50, 100")
        if key == "studio.live_interval" and value is not None and value < 0:
            raise UIFlagError(f"{key} must be non-negative")
        if key == "limits.chat_max_cost_usd" and value is not None and value < 0:
            raise UIFlagError(f"{key} must be non-negative")
        if key == "limits.chat_max_replans" and value is not None and value < 0:
            raise UIFlagError(f"{key} must be non-negative")
        result[key] = deepcopy(value)
    return result


def revision(values: Mapping[str, Any]) -> int:
    """A content revision of one scope's values: the same values give the same number."""
    return int(hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:12], 16)


def check_revision(current: int, expected: Any) -> None:
    """A form built from an older revision is stale: RevisionConflict (409). ``expected`` must be an int."""
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise UIFlagError("expected_revision must be an integer")
    if expected != current:
        raise RevisionConflict(f"stale expected_revision {expected}; current revision is {current}: reload the settings")


def validate_changes(scope: Scope, changes: Any) -> dict[str, Any]:
    """The changes of one section: every key belongs to ``scope`` and every value has its flag's type."""
    if scope not in SCOPES:
        raise UIFlagError(f"unknown scope {scope!r}")
    if not isinstance(changes, Mapping) or not changes:
        raise UIFlagError("changes must be a non-empty mapping")
    wrong = [key for key in changes if key not in BY_KEY or BY_KEY[key].scope != scope]
    if wrong:
        raise UIFlagError(f"{scope} cannot save: {', '.join(sorted(map(str, wrong)))}")
    validate_ui(changes)
    return deepcopy(dict(changes))


def _mapping(scope_values: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return scope_values if isinstance(scope_values, Mapping) else {}


def resolve(*, browser: Mapping[str, Any] | None = None,
            project: Mapping[str, Any] | None = None,
            machine: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Resolve flags using browser > project > machine > default precedence.

    ``machine`` may contain a ``locks`` mapping whose values are human-readable
    reasons; locked flags retain their effective machine/default value.
    """
    browser, project, machine = map(_mapping, (browser, project, machine))
    locks = machine.get("locks", {})
    locks = locks if isinstance(locks, Mapping) else {}
    out = []
    for flag in FLAGS:
        sources = (("browser", browser), ("project", project), ("machine", machine))
        value, source = flag.default, "default"
        for candidate, values in sources:
            if flag.key in values:
                value, source = values[flag.key], candidate
                break
        readonly = bool(machine.get("lockdown.read_only", False))
        locked = flag.key in locks or (readonly and flag.scope != "machine")
        reason = str(locks[flag.key]) if flag.key in locks else ("Read-only Studio" if readonly and flag.scope != "machine" else None)
        if flag.key in locks:
            value, source = flag.default, "default"
        missing = [key for key in flag.requires if not _effective_bool(key, browser, project, machine)]
        disabled = bool(missing)
        if disabled:
            reason = f"Requires {', '.join(missing)}"
        row = flag.as_dict()
        row.update(value=value, effective=value, source=source, locked=locked, disabled=disabled, reason=reason)
        out.append(row)
    return out


def _effective_bool(key: str, browser: Mapping[str, Any], project: Mapping[str, Any], machine: Mapping[str, Any]) -> bool:
    for values in (browser, project, machine):
        if key in values:
            return bool(values[key])
    return bool(BY_KEY[key].default) if key in BY_KEY else False


class UIFlagRegistry:
    """Small revisioned state holder used by a future settings route."""

    def __init__(self, *, browser: Mapping[str, Any] | None = None,
                 project: Mapping[str, Any] | None = None,
                 machine: Mapping[str, Any] | None = None) -> None:
        machine_input = dict(machine or {})
        self._locks = machine_input.pop("locks", {})
        self._values = {"browser": validate_ui(browser), "project": validate_ui(project),
                        "machine": validate_ui(machine_input)}
        self.revision = 0

    def values(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._values)

    def resolved(self) -> list[dict[str, Any]]:
        machine = dict(self._values["machine"])
        if self._locks:
            machine["locks"] = deepcopy(self._locks)
        return resolve(browser=self._values["browser"], project=self._values["project"], machine=machine)

    def save(self, scope: Scope, changes: Mapping[str, Any], *, expected_revision: int | None = None) -> int:
        if scope not in SCOPES:
            raise UIFlagError(f"unknown scope {scope!r}")
        if expected_revision is not None and expected_revision != self.revision:
            raise RevisionConflict(f"stale expected_revision {expected_revision}; current revision is {self.revision}")
        if not isinstance(changes, Mapping):
            raise UIFlagError("changes must be a mapping")
        wrong_scope = [key for key in changes if key not in BY_KEY or BY_KEY[key].scope != scope]
        if wrong_scope:
            raise UIFlagError(f"{scope} cannot save: {', '.join(sorted(map(str, wrong_scope)))}")
        candidate = dict(self._values[scope])
        candidate.update(changes)
        self._values[scope] = validate_ui(candidate)
        self.revision += 1
        return self.revision


__all__ = ["BY_KEY", "DEFAULTS", "FLAGS", "REGISTRY", "RevisionConflict", "UIFlagError",
           "UIFlagRegistry", "check_revision", "resolve", "revision", "validate_changes", "validate_ui"]
