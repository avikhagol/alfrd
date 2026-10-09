"""Plugin settings: the values a user enters in Settings → Plugins, per plugin.

Stored in ``user_config_dir("alfrd")/plugin-settings.json`` (``{"<plugin id>": {"<key>": value}}``,
mode 0600), never in ``alfrd.yaml``. A plugin reads its values with :func:`values`; the Studio
shows them with :func:`public`, where a ``secret`` field only says whether it is set.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from . import Plugin, SettingField

#: Longest accepted value (characters); tokens and ids are far shorter.
MAX_VALUE = 4096


def settings_file() -> Path:
    from platformdirs import user_config_dir

    return Path(user_config_dir("alfrd")) / "plugin-settings.json"


def _read_all() -> dict[str, Any]:
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, Any]) -> None:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".plugin-settings.", suffix=".json", dir=path.parent)  # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def values(plugin_id: str) -> dict[str, Any]:
    """The saved values of one plugin (``{}`` when there are none or the file is broken)."""
    entry = _read_all().get(plugin_id)
    return dict(entry) if isinstance(entry, dict) else {}


def _filled(value: Any) -> bool:
    return value not in (None, "")


def public(plugin: Plugin) -> list[dict[str, Any]]:
    """The form: every field with its value, except a secret, which only reports ``set``."""
    saved = values(plugin.id)
    out = []
    for field in plugin.settings:
        item = {"key": field.key, "label": field.label or field.key, "kind": field.kind, "required": field.required,
                "help": field.help, "pattern": field.pattern, "placeholder": field.placeholder}
        if field.kind == "secret":
            item["set"] = _filled(saved.get(field.key))
        else:
            item["value"] = saved.get(field.key, False if field.kind == "bool" else "")
        out.append(item)
    return out


def missing(plugin: Plugin, saved: Mapping[str, Any] | None = None) -> list[str]:
    """Labels of the required fields that have no value."""
    saved = values(plugin.id) if saved is None else saved
    return [f.label or f.key for f in plugin.settings if f.required and not _filled(saved.get(f.key))]


def _coerce(field: SettingField, value: Any) -> Any:
    label = field.label or field.key
    if field.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{label}: expected true or false")
        return value
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{label}: expected text")
    text = str(value).strip()
    if len(text) > MAX_VALUE:
        raise ValueError(f"{label}: longer than {MAX_VALUE} characters")
    if text and field.kind == "number":
        try:
            float(text)
        except ValueError:
            raise ValueError(f"{label}: expected a number") from None
    if text and field.pattern and not re.fullmatch(field.pattern, text):
        # Never echo the value: it may be a secret pasted into the wrong field.
        raise ValueError(f"{label}: not in the expected format" + (f" ({field.help})" if field.help else ""))
    return text


def save(plugin: Plugin, changes: Mapping[str, Any], clear: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Validate and store ``changes`` (``{key: value}``) and remove the keys in ``clear``.

    An empty secret keeps the saved one (the form never shows it); use ``clear`` to remove it.
    Raises ``ValueError`` (with a message that never contains a value) on an unknown key, a bad
    value, or a required field left empty by a save that sets others. Returns the new values.
    """
    fields = {f.key: f for f in plugin.settings}
    unknown = sorted((set(changes) | set(clear)) - set(fields))
    if unknown:
        raise ValueError(f"unknown setting: {', '.join(unknown)}")
    saved = values(plugin.id)
    for key, value in changes.items():
        coerced = _coerce(fields[key], value)
        if fields[key].kind == "secret" and coerced == "":
            continue
        saved[key] = coerced
    for key in clear:
        saved.pop(key, None)
    saved = {k: v for k, v in saved.items() if k in fields and (_filled(v) or isinstance(v, bool))}
    lacking = missing(plugin, saved)
    # Removing values (``clear``) or every value un-configures the plugin; a save must be complete.
    if lacking and not clear and any(_filled(v) for v in saved.values()):

        raise ValueError(f"required: {', '.join(lacking)}")
    data = _read_all()
    data[plugin.id] = saved
    _write_all(data)
    return dict(saved)


__all__ = ["MAX_VALUE", "missing", "public", "save", "settings_file", "values"]
