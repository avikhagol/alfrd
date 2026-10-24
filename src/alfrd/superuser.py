"""Superuser settings: what every `alfrd serve` / `alfrd gui` of this user shows and allows.

One YAML file per user, ``user_config_dir("alfrd")/superuser.yaml`` (``ALFRD_SUPERUSER_FILE``
overrides the path). Every server and every runner of this user reads it; a change applies on the
next request, no restart needed. Edited from the Studio page ``/superuser`` (loopback browsers
only) or with ``alfrd su``.

Command-line flags still win where they are stricter: ``--experimental-features`` turns the
experimental features on for that server; ``--no-gui-install`` and ``--no-plugin-actions`` turn
those off whatever this file says.

    version: 1
    features:
      experimental: false        # Chat mode preference and the agent-loop template
    studio:
      hidden_sections: []        # workflow, metadata, results, logs, chat, config
      hidden_templates: []       # basic, avica, agent-loop (New project)
      live_interval: null        # seconds between folder checks while busy (0: off; null: the server's)
    lockdown:
      read_only: false           # refuse every change (stopping running work stays allowed)
      plugins: true              # install / update / remove / enable / disable / configure plugins
      plugin_actions: true       # plugins' actions that change files or external data
      project_create: true       # create, connect and rediscover projects; new folders
      project_delete: true       # forget and delete projects
      runs: true                 # start or resume workflow runs, reviews and turn overrides
      chat: true                 # chat sessions, messages, decisions, retries, mailbox replies
      file_edits: true           # alfrd.yaml, tasks, targets, notes, handoffs, setup forms, history restore
    limits:
      chat_max_cost_usd: null    # caps chat.bounds.max_cost_usd of every project
      chat_max_replans: null     # caps chat.bounds.max_replans
      chat_allowed_agents: []    # only these chat agents may run (empty: all)
"""
from __future__ import annotations

import copy
import math
import os
import threading
from pathlib import Path
from typing import Any, Mapping

import yaml

VERSION = 1
SECTIONS = ("workflow", "metadata", "results", "logs", "chat", "config")  # Overview always stays
TEMPLATES = ("basic", "avica", "agent-loop")
LOCKS = ("plugins", "plugin_actions", "project_create", "project_delete", "runs", "chat", "file_edits")

DEFAULTS: dict[str, Any] = {
    "version": VERSION,
    "features": {"experimental": False},
    "studio": {"hidden_sections": [], "hidden_templates": [], "live_interval": None},
    "lockdown": {"read_only": False, **{name: True for name in LOCKS}},
    "limits": {"chat_max_cost_usd": None, "chat_max_replans": None, "chat_allowed_agents": []},
}

# Human labels (Studio page, `alfrd su show`).
LABELS = {
    "features.experimental": "Experimental features (Chat mode, agent-loop template)",
    "studio.hidden_sections": "Hidden Studio sections",
    "studio.hidden_templates": "Hidden New-project templates",
    "studio.live_interval": "Live update interval (s)",
    "lockdown.read_only": "Read-only Studio",
    "lockdown.plugins": "Plugin install and management",
    "lockdown.plugin_actions": "Plugin actions that change data",
    "lockdown.project_create": "Create and connect projects",
    "lockdown.project_delete": "Forget and delete projects",
    "lockdown.runs": "Start and resume workflow runs",
    "lockdown.chat": "Chat sessions and messages",
    "lockdown.file_edits": "Edit project files",
    "limits.chat_max_cost_usd": "Chat: max cost per request (USD)",
    "limits.chat_max_replans": "Chat: max replans per request",
    "limits.chat_allowed_agents": "Chat: allowed agents",
}

_cache: dict[str, Any] = {"key": None, "data": None, "error": None, "good": None}
_lock = threading.Lock()


class SuperuserError(ValueError):
    """Invalid superuser settings (the message names the key)."""


def path() -> Path:
    override = os.environ.get("ALFRD_SUPERUSER_FILE")
    if override:
        return Path(override).expanduser()
    from platformdirs import user_config_dir

    return Path(user_config_dir("alfrd")) / "superuser.yaml"


def _words(value: Any, key: str, allowed: tuple[str, ...] | None = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise SuperuserError(f"{key} must be a list of names")
    out = []
    for item in (v.strip() for v in value):
        if allowed is not None and item not in allowed:
            raise SuperuserError(f"{key}: {item!r} is not one of {', '.join(allowed)}")
        if item not in out:
            out.append(item)
    return out


def _number(value: Any, key: str, *, integer: bool = False, minimum: float = 0) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise SuperuserError(f"{key} must be a number or null")
    try:
        number = int(value) if integer and float(value).is_integer() else float(value)
    except (TypeError, ValueError):
        raise SuperuserError(f"{key} must be a number or null") from None
    if not math.isfinite(number):
        raise SuperuserError(f"{key} must be a finite number or null")
    if integer and not isinstance(number, int):
        raise SuperuserError(f"{key} must be a whole number or null")
    if number < minimum:
        raise SuperuserError(f"{key} must be at least {minimum}")
    return number


def validate(raw: Any) -> dict[str, Any]:
    """The full settings (defaults filled in) from ``raw``; SuperuserError names the bad key."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise SuperuserError("the superuser file must be a mapping")
    unknown = set(raw) - set(DEFAULTS)
    if unknown:
        raise SuperuserError(f"unknown section(s): {', '.join(sorted(unknown))}")
    if raw.get("version", VERSION) != VERSION:
        raise SuperuserError(f"version must be {VERSION}")
    out = copy.deepcopy(DEFAULTS)
    for section in ("features", "studio", "lockdown", "limits"):
        value = raw.get(section) or {}
        if not isinstance(value, Mapping):
            raise SuperuserError(f"{section} must be a mapping")
        extra = set(value) - set(DEFAULTS[section])
        if extra:
            raise SuperuserError(f"{section}: unknown key(s) {', '.join(sorted(extra))}")
        for key, item in value.items():
            name = f"{section}.{key}"
            default = DEFAULTS[section][key]
            if isinstance(default, bool):
                if not isinstance(item, bool):
                    raise SuperuserError(f"{name} must be true or false")
                out[section][key] = item
            elif key == "hidden_sections":
                out[section][key] = _words(item, name, SECTIONS)
            elif key == "hidden_templates":
                out[section][key] = _words(item, name, TEMPLATES)
            elif key == "chat_allowed_agents":
                out[section][key] = _words(item, name)
            elif key == "chat_max_replans":
                out[section][key] = _number(item, name, integer=True)
            else:
                out[section][key] = _number(item, name)
    return out


def load(*, strict: bool = False) -> dict[str, Any]:
    """The current settings, re-read when the file changes. A broken file gives the defaults
    (and ``error()`` says why) unless ``strict``."""
    file = path()
    try:
        stat = file.stat()
        key = (str(file), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = (str(file), None, None)
    with _lock:
        if _cache["key"] == key and not strict:
            return copy.deepcopy(_cache["data"])
        error = None
        try:
            data = validate(yaml.safe_load(file.read_text(encoding="utf-8")) if key[1] is not None else {})
        except (OSError, yaml.YAMLError, SuperuserError) as exc:
            if strict:
                raise SuperuserError(f"{file}: {exc}") from exc
            # A broken file must not unlock anything: keep the last good settings, else fail closed (read-only).
            good = _cache.get("good")
            data = copy.deepcopy(good[1]) if good and good[0] == str(file) else _closed()
            error = f"{file}: {exc}"
        else:
            _cache["good"] = (str(file), copy.deepcopy(data))
        _cache.update(key=key, data=data, error=error)
        return copy.deepcopy(data)


def _closed() -> dict[str, Any]:
    data = copy.deepcopy(DEFAULTS)
    data["lockdown"]["read_only"] = True
    return data


def error() -> str | None:
    load()
    return _cache["error"]


def _header() -> str:
    return ("# ALFRD superuser settings: read by every `alfrd serve` / `alfrd gui` of this user.\n"
            "# Edit here, on the Studio page /superuser, or with `alfrd su set key=value`.\n")


def save(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and write the whole file atomically; returns the saved settings."""
    from alfrd.yaml_text import dump

    clean = validate(data)
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(f".{file.name}.{os.getpid()}.tmp")
    tmp.write_text(_header() + dump(clean), encoding="utf-8")
    os.replace(tmp, file)
    with _lock:
        _cache["key"] = None
    return clean


def merge(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Apply ``{"section": {"key": value}}`` or ``{"section.key": value}`` over the current file and save.
    A broken file is never overwritten from guesses: SuperuserError (fix it, ``save`` a whole set, or ``reset``)."""
    current = load(strict=True)
    for key, value in changes.items():
        if "." in str(key):
            section, name = str(key).split(".", 1)
            if section not in DEFAULTS or not isinstance(DEFAULTS[section], dict) or name not in DEFAULTS[section]:
                raise SuperuserError(f"unknown setting {key!r}")
            current[section][name] = value
        elif key in ("features", "studio", "lockdown", "limits"):
            if not isinstance(value, Mapping):
                raise SuperuserError(f"{key} must be a mapping")
            current[key].update(value)
        elif key == "version":
            continue
        else:
            raise SuperuserError(f"unknown setting {key!r}")
    return save(current)


def reset() -> dict[str, Any]:
    file = path()
    file.unlink(missing_ok=True)
    with _lock:
        _cache["key"] = None
    return copy.deepcopy(DEFAULTS)


def parse_value(key: str, text: str) -> Any:
    """``alfrd su set key=value``: true/false, null, numbers and comma lists by the key's type."""
    section, _, name = key.partition(".")
    if section not in DEFAULTS or not isinstance(DEFAULTS[section], dict) or name not in DEFAULTS[section]:
        raise SuperuserError(f"unknown setting {key!r} (see `alfrd su show`)")
    default, raw = DEFAULTS[section][name], text.strip()
    if isinstance(default, bool):
        lowered = raw.lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        raise SuperuserError(f"{key} takes true or false")
    if isinstance(default, list):
        return [] if raw.lower() in ("", "none", "null", "[]") else [p.strip() for p in raw.split(",") if p.strip()]
    return None if raw.lower() in ("", "none", "null") else raw


def flat(data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``{"section.key": value}``, in file order."""
    data = data or load()
    return {f"{s}.{k}": data[s][k] for s in ("features", "studio", "lockdown", "limits") for k in DEFAULTS[s]}


# --- effective values (file + server flags) -------------------------------------------------


def allowed(category: str, data: Mapping[str, Any] | None = None) -> bool:
    """A lockdown category (``LOCKS``) is allowed: not read-only and not switched off."""
    data = data or load()
    lock = data["lockdown"]
    return not lock["read_only"] and bool(lock.get(category, True))


def cap_chat_settings(conf: dict[str, Any], data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Apply the chat limits to effective chat settings (in place; also returned)."""
    data = data or load()
    limits = data["limits"]
    bounds = conf.setdefault("bounds", {})
    if limits["chat_max_cost_usd"] is not None and bounds.get("max_cost_usd") is not None:
        bounds["max_cost_usd"] = min(float(bounds["max_cost_usd"]), float(limits["chat_max_cost_usd"]))
    if limits["chat_max_replans"] is not None and bounds.get("max_replans") is not None:
        bounds["max_replans"] = min(int(bounds["max_replans"]), int(limits["chat_max_replans"]))
    # Configured agents stay (running rows keep working); chat.usable() limits new work to these.
    conf["allowed_agents"] = list(limits["chat_allowed_agents"]) or None
    return conf
