"""Which plugins apply to which project types.

A project's type is its ``template:`` (an ``avica:`` block implies ``avica``; see
:func:`alfrd.studio_defs.template_name`). It is free text, so custom templates work;
a manifest without one counts as ``basic``, the default template.

The rule for a plugin, first match wins:

1. the project's override in its *active* alfrd.yaml::

       plugins:
         potter: {enabled: false}                 # off here, whatever the types say
         other: {project_types: [avica, custom]}  # or "all"

2. the global override in ``plugins.json`` (``"project_types": {"<id>": [...] | "all"}``);
3. the plugin's default, ``Plugin.project_types`` (``None`` = all project types).

An inapplicable plugin contributes no panels, project actions, project checks or
step hooks for that project, and the Studio hides its pages, sections and commands.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

ALL = "all"
DEFAULT_TYPE = "basic"
_SOURCES = {"default": "the plugin's default", "global": "the global plugin setting",
            "project": "this project's alfrd.yaml"}


def parse_types(value: Any) -> tuple[str, ...] | None | bool:
    """``None`` for all types (``"all"``), a tuple of names, or ``False`` when ``value`` is not a rule."""
    if isinstance(value, str):
        return None if value.strip().lower() in (ALL, "*") else (value.strip(),) if value.strip() else False
    if isinstance(value, (list, tuple)):
        names = [v.strip() for v in value if isinstance(v, str)]
        if not names or len(names) != len(value) or not all(names):
            return False
        if any(n.lower() in (ALL, "*") for n in names):
            return None
        return tuple(dict.fromkeys(names))
    return False


def project_type(root: str | Path) -> str:
    """The project's type: its ``template:`` (free text), ``avica`` for an ``avica:`` block, else ``basic``."""
    from alfrd.manifest_default import manifest_data
    from alfrd.studio_defs import template_name

    try:
        data, _path, _default = manifest_data(root)
    except Exception:  # noqa: BLE001 - an unreadable manifest is an untyped (basic) project
        data = {}
    return template_name(data) or DEFAULT_TYPE


def project_overrides(root: str | Path) -> dict[str, dict[str, Any]]:
    """``plugins:`` of the project's active alfrd.yaml (only well-formed entries)."""
    from alfrd.manifest_default import local_manifest, manifest_data

    if local_manifest(root) is None:
        return {}
    try:
        data, _path, _default = manifest_data(root)
    except Exception:  # noqa: BLE001
        return {}
    block = data.get("plugins")
    if not isinstance(block, Mapping):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, entry in block.items():
        if not isinstance(entry, Mapping):
            continue
        rule: dict[str, Any] = {}
        if isinstance(entry.get("enabled"), bool):
            rule["enabled"] = entry["enabled"]
        if "project_types" in entry:
            parsed = parse_types(entry["project_types"])
            if parsed is not False:
                rule["project_types"] = parsed
        if rule:
            out[str(key)] = rule
    return out


def _label(types: tuple[str, ...] | None) -> str:
    return "all project types" if types is None else ", ".join(types)


def resolve(plugin_id: str, default: tuple[str, ...] | None, ptype: str, *,
            project: Mapping[str, Any] | None = None, global_types: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The effective rule for one plugin and one project type (pure: callers pass the overrides).

    ``{"available", "reason", "source", "project_type", "project_types", "default", "global", "project"}``
    where ``project_types`` is the effective list (``None`` = all) and ``source`` says where it came from.
    """
    from alfrd import extensions

    if global_types is None:
        global_types = extensions.read_state()["project_types"]
    rule = dict(project or {})
    has_global = plugin_id in global_types
    out: dict[str, Any] = {"project_type": ptype, "default": list(default) if default is not None else None,
                           "global": (list(global_types[plugin_id]) if global_types[plugin_id] is not None else ALL) if has_global else None,
                           "project": {k: (list(v) if isinstance(v, tuple) else ALL if v is None and k == "project_types" else v)
                                       for k, v in rule.items()} or None}
    if rule.get("enabled") is False:
        return {**out, "available": False, "source": "project", "project_types": None,
                "reason": f"Turned off for this project in alfrd.yaml (plugins.{plugin_id}.enabled: false)."}
    if "project_types" in rule:
        types, source = rule["project_types"], "project"
    elif has_global:
        types, source = global_types[plugin_id], "global"
    else:
        types, source = default, "default"
    if rule.get("enabled") is True:  # an explicit opt-in for this project wins over the type list
        return {**out, "available": True, "source": "project", "project_types": list(types) if types is not None else None,
                "reason": None}
    available = types is None or ptype in types
    reason = None if available else (f"Not for {ptype} projects: {_SOURCES[source]} limits it to {_label(types)}.")
    return {**out, "available": available, "source": source, "project_types": list(types) if types is not None else None,
            "reason": reason}


def for_root(plugin_id: str, default: tuple[str, ...] | None, root: str | Path) -> dict[str, Any]:
    """:func:`resolve` for a project folder (reads its type and its ``plugins:`` block)."""
    return resolve(plugin_id, default, project_type(root), project=project_overrides(root).get(plugin_id))


def _default(plugin_id: str) -> tuple[str, ...] | None:
    from alfrd import extensions

    rec = next((r for r in extensions.loaded() if r.id == plugin_id), None)
    return rec.plugin.project_types if rec is not None and rec.plugin is not None else None


def available(plugin_id: str, root: str | Path | None) -> bool:
    """Whether a plugin applies to the project at ``root`` (``None``: no project, always true). Never raises."""
    if root is None:
        return True
    try:
        return bool(for_root(plugin_id, _default(plugin_id), root)["available"])
    except Exception:  # noqa: BLE001 - a broken rule never hides a plugin by accident
        return True


def known_types() -> list[str]:
    """Project types offered in the UI: the packaged templates (custom names can be typed)."""
    try:
        from alfrd.web import web_root

        return sorted(p.stem for p in (web_root() / "assets" / "templates").glob("*.yaml"))
    except (OSError, FileNotFoundError):
        return [DEFAULT_TYPE]


__all__ = ["ALL", "DEFAULT_TYPE", "available", "for_root", "known_types", "parse_types", "project_overrides", "project_type", "resolve"]
