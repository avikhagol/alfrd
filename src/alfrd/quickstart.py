"""Setup forms declared in alfrd.yaml (``quickstart:``), usually by the project template.

.. code-block:: yaml

    quickstart:
      setup:                               # form id
        type: form
        title: Set up AVICA
        description: Values written to avica.inp in the project folder.
        target: {file: avica.inp}          # key = value file, or {file: alfrd.yaml}
        fields:
          folder_for_fits: {type: path, label: Raw FITS-IDI folder, required: true}
          use_local_antab: {type: toggle, default: true}
          rpicard.mpi_cores: {type: number, min: 1}

Field types: ``textbox`` (``text``), ``textarea``, ``number``, ``toggle`` /
``checkbox`` (True/False), ``path`` (the Studio offers the server folder
browser; ``pick: program`` checks the server's PATH instead), ``select``
(``options:``) and ``list`` (comma-separated). For a
``key = value`` file the field key is the key (``<step>.<param>`` allowed). For
``alfrd.yaml`` it is a dotted path; a list segment is an index or the ``name``
of an item, e.g. ``entrypoint.claude.model`` or ``workflows.0.repeat.iterations``.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

TYPES = {"textbox", "text", "textarea", "number", "toggle", "checkbox", "path", "select", "list"}
FIELD_KEYS = {"type", "label", "help", "required", "default", "options", "min", "max", "pick", "placeholder", "must_exist"}
MANIFEST = "alfrd.yaml"
_KEY = re.compile(r"[A-Za-z_][\w.-]*")


class QuickstartError(ValueError):
    def __init__(self, message: str, errors: Mapping[str, str] | None = None):
        super().__init__(message)
        self.errors = dict(errors or {})


def _form(form_id: str, spec: Any) -> dict[str, Any]:
    """Validate one form declaration; returns it normalized."""
    if not isinstance(spec, Mapping):
        raise QuickstartError(f"quickstart.{form_id} must be a mapping")
    if spec.get("type", "form") != "form":
        raise QuickstartError(f"quickstart.{form_id}.type must be form")
    target = spec.get("target") or {}
    if isinstance(target, str):
        target = {"file": target}
    file = str(target.get("file") or "")
    if not file or "/" in file or "\\" in file or file.startswith("."):
        raise QuickstartError(f"quickstart.{form_id}.target.file must be a file name in the project folder")
    raw = spec.get("fields")
    if not isinstance(raw, Mapping) or not raw:
        raise QuickstartError(f"quickstart.{form_id}.fields must map field keys to their type")
    fields = []
    for key, field in raw.items():
        key = str(key)
        field = {"type": field} if isinstance(field, str) else dict(field or {})
        kind = field.get("type", "textbox")
        if not _KEY.fullmatch(key):
            raise QuickstartError(f"quickstart.{form_id}: invalid field key {key!r}")
        if kind not in TYPES:
            raise QuickstartError(f"quickstart.{form_id}.{key}: type must be one of {', '.join(sorted(TYPES))}")
        unknown = set(field) - FIELD_KEYS
        if unknown:
            raise QuickstartError(f"quickstart.{form_id}.{key}: unknown settings {', '.join(sorted(unknown))}")
        if kind == "select" and not (isinstance(field.get("options"), list) and field["options"]):
            raise QuickstartError(f"quickstart.{form_id}.{key}: a select needs options")
        kind = {"text": "textbox", "checkbox": "toggle"}.get(kind, kind)
        fields.append({**field, "key": key, "type": kind, "label": str(field.get("label") or key),
                       "required": bool(field.get("required", False))})
    return {"id": form_id, "title": str(spec.get("title") or form_id), "description": str(spec.get("description") or ""),
            "target": {"file": file, "format": "manifest" if file == MANIFEST else "key_value"}, "fields": fields}


def declared(root: str | Path) -> dict[str, Any]:
    from alfrd.execution import merged_manifest

    data = merged_manifest(root).get("quickstart") or {}
    if not isinstance(data, Mapping):
        raise QuickstartError("quickstart must map form ids to forms")
    return dict(data)


def _segment(node: Any, part: str) -> tuple[Any, Any]:
    """(container key, child) of one path segment; KeyError when it does not exist."""
    if isinstance(node, list):
        if part.isdigit():
            index = int(part)
            if index >= len(node):
                raise KeyError(part)
            return index, node[index]
        for index, item in enumerate(node):
            if isinstance(item, Mapping) and str(item.get("name")) == part:
                return index, item
        raise KeyError(part)
    if isinstance(node, Mapping) and part in node:
        return part, node[part]
    raise KeyError(part)


def get_path(data: Any, key: str) -> Any:
    node = data
    for part in key.split("."):
        try:
            _, node = _segment(node, part)
        except KeyError:
            return None
    return node


def set_path(data: Any, key: str, value: Any) -> None:
    """Set (or with ``None`` remove) a dotted path; missing mappings are created, list items must exist."""
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        try:
            slot, child = _segment(node, part)
        except KeyError:
            if not isinstance(node, dict):
                raise QuickstartError(f"{key}: {part!r} is not in alfrd.yaml") from None
            if value is None:
                return
            node[part] = child = {}
            slot = part
        if child is None and isinstance(node, dict):
            node[slot] = child = {}
        node = child
    last = parts[-1]
    if isinstance(node, list):
        if not last.isdigit() or int(last) > len(node):
            raise QuickstartError(f"{key}: list position {last!r} does not exist")
        index = int(last)
        if value is None:
            if index < len(node):
                node.pop(index)
        elif index == len(node):
            node.append(value)
        else:
            node[index] = value
    elif isinstance(node, dict):
        if value is None:
            node.pop(last, None)
        else:
            node[last] = value
    else:
        raise QuickstartError(f"{key}: cannot set a value inside {type(node).__name__}")


def _manifest_text(root: Path) -> tuple[str, Path]:
    from alfrd.manifest_default import default_manifest_text
    from alfrd.studio_defs import manifest_file

    path = manifest_file(root)
    if path and path.is_file():
        return path.read_text(encoding="utf-8"), path
    return default_manifest_text(root) or "", root / MANIFEST


def current(root: Path, form: Mapping[str, Any]) -> dict[str, Any]:
    if form["target"]["format"] == "manifest":
        from alfrd.execution import merged_manifest

        data = merged_manifest(root)
        return {f["key"]: get_path(data, f["key"]) for f in form["fields"]}
    from alfrd.avica_layout import read_key_values

    values = read_key_values(root / form["target"]["file"])
    return {f["key"]: values.get(f["key"]) for f in form["fields"]}


def forms(root: str | Path) -> list[dict[str, Any]]:
    """Every declared form with each field's current value (or None)."""
    root = Path(root).resolve()
    out = []
    for form_id, spec in declared(root).items():
        try:
            form = _form(str(form_id), spec)
        except QuickstartError as exc:
            out.append({"id": str(form_id), "error": str(exc)})
            continue
        values = current(root, form)
        for field in form["fields"]:
            field["value"] = values.get(field["key"])
        out.append(form)
    return out


def coerce(field: Mapping[str, Any], value: Any, root: Path) -> tuple[Any, str | None]:
    """(value, warning) for a submitted value; raises ValueError with a message for the person."""
    kind = field["type"]
    if isinstance(value, str) and kind not in ("textarea",):
        value = value.strip()
    empty = value is None or value == "" or value == []
    if empty:
        if field["required"]:
            raise ValueError("required")
        return None, None
    if kind in ("textbox", "textarea", "path"):
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError("must be text")
        value = str(value)
        if kind == "textbox" and "\n" in value:
            raise ValueError("must be one line")
        if kind == "path" and field.get("pick") == "program":
            import shutil

            if not shutil.which(value):
                if field.get("must_exist"):
                    raise ValueError(f"{value} is not an executable program on the server")
                return value, f"{value} is not found on the server's PATH"
            return value, None
        if kind == "path":
            path = Path(value).expanduser()
            path = path if path.is_absolute() else root / path
            if not path.exists():
                if field.get("must_exist"):
                    raise ValueError(f"{path} does not exist")
                return value, f"{value} does not exist yet"
        return value, None
    if kind == "number":
        try:
            number = float(value) if not isinstance(value, bool) else None
        except (TypeError, ValueError):
            number = None
        if number is None or number != number or number in (float("inf"), float("-inf")):
            raise ValueError("must be a number")
        number = int(number) if number.is_integer() else number
        if field.get("min") is not None and number < field["min"]:
            raise ValueError(f"must be at least {field['min']}")
        if field.get("max") is not None and number > field["max"]:
            raise ValueError(f"must be at most {field['max']}")
        return number, None
    if kind == "toggle":
        if isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value, None
    if kind == "select":
        options = field["options"]
        match = next((o for o in options if o == value or str(o) == str(value)), None)
        if match is None:
            raise ValueError(f"must be one of {', '.join(map(str, options))}")
        return match, None
    if kind == "list":
        items = re.split(r"[,\n]", value) if isinstance(value, str) else value
        if not isinstance(items, list) or not all(isinstance(i, (str, int, float)) and not isinstance(i, bool) for i in items):
            raise ValueError("must be a list")
        items = [str(i).strip() for i in items if str(i).strip()]
        if not items:
            if field["required"]:
                raise ValueError("required")
            return None, None
        return items, None
    raise ValueError("unsupported field type")  # pragma: no cover - _form checked it


def apply(root: str | Path, form_id: str, values: Mapping[str, Any], *, source: str = "studio") -> dict[str, Any]:
    """Validate submitted values and write those that changed. Raises QuickstartError (``errors`` per field)."""
    from alfrd import history

    root = Path(root).resolve()
    spec = declared(root).get(form_id)
    if spec is None:
        raise QuickstartError(f"no quickstart form {form_id!r}")
    form = _form(form_id, spec)
    if not isinstance(values, Mapping):
        raise QuickstartError("values must map field keys to values")
    known = {f["key"]: f for f in form["fields"]}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise QuickstartError(f"unknown fields: {', '.join(unknown)}")
    before = current(root, form)
    changes: dict[str, Any] = {}
    errors: dict[str, str] = {}
    warnings: dict[str, str] = {}
    for key, field in known.items():
        if key not in values:
            if field["required"] and before.get(key) in (None, "", []):
                errors[key] = "required"
            continue
        try:
            value, warning = coerce(field, values[key], root)
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        if warning:
            warnings[key] = warning
        if value != before.get(key) and not (value is None and form["target"]["format"] == "key_value"):
            changes[key] = value
    if errors:
        raise QuickstartError("some values are not valid: " + "; ".join(f"{known[k]['label']}: {v}" for k, v in errors.items()), errors)
    file = form["target"]["file"]
    if changes and form["target"]["format"] == "key_value":
        from alfrd.studio_defs import update_key_values

        tracked = history.is_tracked(root, file)
        if tracked:
            history.ensure_baseline(root, file)
        update_key_values(root / file, changes)
        if tracked:
            history.record(root, file, source=source, message=f"quickstart {form_id}: " + ", ".join(sorted(changes))[:200])
    elif changes:
        _write_manifest(root, form_id, changes, source)
    return {"form": form_id, "file": file, "written": changes, "warnings": warnings}


def _write_manifest(root: Path, form_id: str, changes: Mapping[str, Any], source: str) -> None:
    import yaml

    from alfrd import history
    from alfrd.execution import ExecutionError, load_execution
    from alfrd.manifest import parse_manifest
    from alfrd.yaml_text import replace_sections

    text, path = _manifest_text(root)
    old = yaml.safe_load(text) or {}
    if not isinstance(old, dict):
        raise QuickstartError("alfrd.yaml is not a mapping")
    new = copy.deepcopy(old)
    from alfrd.execution import merged_manifest

    merged = merged_manifest(root)
    for key, value in changes.items():
        top = key.split(".", 1)[0]
        if top not in new and top in merged and not top.startswith("_"):
            new[top] = copy.deepcopy(merged[top])  # a template default the project has not copied yet
        set_path(new, key, value)
    try:
        parse_manifest(new)
    except Exception as exc:  # noqa: BLE001 - schema errors read well as they are
        raise QuickstartError(f"alfrd.yaml would not be valid: {exc}") from exc
    updated = replace_sections(text, old, new)
    history.save(root, MANIFEST, updated, source=source, message=f"quickstart {form_id}: " + ", ".join(sorted(changes))[:200], force=True)
    try:
        load_execution(root)
    except (ExecutionError, ValueError) as exc:
        history.save(root, MANIFEST, text, source=source, message=f"quickstart {form_id}: reverted", force=True)
        raise QuickstartError(f"alfrd.yaml would not load: {exc}") from exc
