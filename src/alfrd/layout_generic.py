"""Template-driven project layout and Metadata panels (no pipeline names in code).

A template (or alfrd.yaml) describes its folder hierarchy and the panels of the
Metadata view::

    hierarchy:
      - {level: project_code, dir: "{target_dir}/{project_code}"}
      - {level: workdir, dir: ["{target_dir}/{project_code}/wd", "{target_dir}/{project_code}/wd_{n}"]}
      - {level: band, dir: ["wd_{band}", "wd_{band}_{target}"]}
    views:
      vars: {meta_dir: "{workdir}/avica.meta"}
      metadata:
        - {panel: file_status, title: Step outputs, scope: workdir, files: from_steps}
        - {panel: json_fields, source: "{meta_dir}/listobs.json", fields: [observer, "spw[*].ref_freq"]}
        - {panel: csv_table, source: result_csv, scope: target}
        - {panel: text, source: "{meta_dir}/*.out"}
        - {panel: image, source: "{workdir}/wd_{band}_{target}/diagnostics_*/*.png"}

``dir`` is one pattern or a list ("any of"). A pattern that starts with
``{target_dir}`` is relative to the project root, any other to the parent
level's folder. A level's value is its own placeholder when the pattern has one,
else the folder name. ``{from: "avica.workdir"}`` takes a pattern list from the
``avica:`` block (so alfrd.yaml overrides keep working).

Panel types (code): ``file_status``, ``json_fields``, ``csv_table``, ``text``,
``image``. ``scope`` (a level name, or ``target``) decides where a panel is
evaluated: once per folder of that level, or once per target across all its
folders (so e.g. bands are per target, not per project code).

JSON Schema of both blocks: ``alfrd/schemas/template_views.v1.json``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

#: Server panel kinds (derived from :data:`PANELS`).
PANEL_TYPES: tuple[str, ...] = ()
#: Panels the Studio renders itself from data it already reads (a template opts in by naming them).
CLIENT_PANELS: tuple[str, ...] = ()
DEFAULT_BASE = "{meta_dir}"
MAX_FILES = 200
MAX_PARSE = 256 * 1024
MAX_TEXT = 64 * 1024
MAX_ROWS = 500
MAX_IMAGES = 24
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


# ---------------------------------------------------------------------------
# Spec (template + alfrd.yaml)


def spec_for(root: str | Path) -> dict[str, Any]:
    """``{hierarchy, views, steps, step_order, errors}`` of a project (merged with its template)."""
    from alfrd.studio_defs import studio_manifest

    merged = studio_manifest(root)
    spec = {"hierarchy": merged.get("hierarchy") or [], "views": merged.get("views") or {},
            "steps": merged.get("steps") or {}, "step_order": merged.get("step_order") or []}
    spec["errors"] = validate({"hierarchy": spec["hierarchy"], "views": spec["views"]})
    return spec


def schema() -> dict[str, Any]:
    return json.loads(resources.files("alfrd.schemas").joinpath("template_views.v1.json").read_text(encoding="utf-8"))


def validate(block: Mapping[str, Any]) -> list[str]:
    """JSON Schema errors of ``{hierarchy, views}`` (empty when valid)."""
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - a declared dependency
        return []
    validator = jsonschema.Draft202012Validator(schema())
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '(top)'}: {e.message}" for e in validator.iter_errors(dict(block))]
    views = block.get("views")
    for name, panels in (views.items() if isinstance(views, Mapping) else ()):
        for i, panel in enumerate(panels if isinstance(panels, list) else ()):
            kind = panel.get("panel") if isinstance(panel, Mapping) else None
            if isinstance(kind, str) and kind and kind not in PANELS:
                errors.append(f"views/{name}/{i}/panel: unknown panel {kind!r} (known: {', '.join(sorted(PANELS))})")
    return errors


def _patterns(root: Path, value: Any) -> list[str]:
    if isinstance(value, Mapping) and value.get("from"):
        from alfrd.avica_layout import layout_patterns

        block, _, key = str(value["from"]).partition(".")
        if block != "avica":
            return []
        return list(layout_patterns(root).get(key) or [])
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value or []]


def _vars(root: Path, spec: Mapping[str, Any]) -> dict[str, str]:
    out = {}
    for name, value in ((spec.get("views") or {}).get("vars") or {}).items():
        if isinstance(value, Mapping) and value.get("from"):
            found = _patterns(root, value)
            prefix = str(value.get("prefix") or "")
            out[str(name)] = prefix + found[0] if found else ""
        else:
            out[str(name)] = str(value)
    return out


def base_values(root: Path) -> dict[str, str]:
    """Values every pattern may use: ``target_dir`` (relative), ``logs``."""
    from alfrd.avica_layout import manifest_avica, resolve_config, resolve_dir

    values: dict[str, str] = {}
    try:
        cfg = resolve_config(root)
        target_dir = resolve_dir(root, cfg.get("target_dir"))
        if target_dir is not None:
            rel = os.path.relpath(target_dir, root).replace(os.sep, "/")
            values["target_dir"] = "." if rel in ("", ".") else rel
        values["logs"] = str(manifest_avica(root).get("logs") or "avica.logs")
    except Exception:  # noqa: BLE001 - not an AVICA layout: {target_dir} = the root
        values.setdefault("target_dir", ".")
    return values


# ---------------------------------------------------------------------------
# Hierarchy walk


def _dirs(base: Path, depth: int) -> Iterable[Path]:
    from alfrd.avica_layout import _iter_dirs

    yield from _iter_dirs(base, depth)


def _specificity(pattern: str) -> tuple[int, int]:
    literal = _PLACEHOLDER.sub("", pattern).replace("*", "")
    return (len(literal), -pattern.count("*"))


def _targets_from(base: Path, folder: Path, value: Any) -> set[str]:
    """Targets named by the files of ``folder``; each file counts once, for its most specific pattern.

    With ``["{target}.json", "{target}_*.json"]``, ``M31_bias.json`` names M31 (not ``M31_bias``).
    """
    from alfrd.studio_defs import expand_pattern

    claimed: dict[str, str] = {}
    for pattern in sorted(_patterns(base, value), key=_specificity, reverse=True):
        for rel, groups in expand_pattern(folder, pattern, {}):
            if groups.get("target") and rel not in claimed:
                claimed[rel] = groups["target"]
    return set(claimed.values())


def walk(root: str | Path, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The folders of each hierarchy level: nested nodes ``{level, value, path, values, targets, children}``."""
    from alfrd.avica_layout import pattern_regex

    base = Path(root).resolve()
    spec = spec or spec_for(base)
    levels = [lv for lv in spec.get("hierarchy") or [] if isinstance(lv, Mapping) and lv.get("level")]
    start = base_values(base)

    def level_nodes(i: int, parent_path: Path, values: dict[str, str]) -> list[dict[str, Any]]:
        if i >= len(levels):
            return []
        name = str(levels[i]["level"])
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pattern in _patterns(base, levels[i].get("dir")):
            root_rel = pattern.startswith("{target_dir}")
            origin = base if root_rel else parent_path
            regex = pattern_regex(pattern, values)
            effective = pattern
            if root_rel:
                td = str(values.get("target_dir") or ".").strip("/")
                effective = pattern.replace("{target_dir}/", "") if td in ("", ".") else pattern.replace("{target_dir}", td)
            for folder in _dirs(origin, effective.count("/") + 1):
                rel = os.path.relpath(folder, origin).replace(os.sep, "/")
                match = regex.match(rel)
                if not match:
                    continue
                key = os.path.relpath(folder, base).replace(os.sep, "/")
                if key in seen:
                    continue
                seen.add(key)
                found = {k: v for k, v in match.groupdict().items() if v is not None}
                value = found.get(name) or folder.name
                node_values = {**values, **found, name: value}
                if name == "workdir":
                    node_values["workdir_path"] = key
                children = level_nodes(i + 1, folder, node_values)
                targets = set(filter(None, [found.get("target")])) | {t for c in children for t in c["targets"]}
                targets |= _targets_from(base, folder, levels[i].get("targets_from"))
                out.append({"level": name, "value": value, "path": key, "values": node_values,
                            "targets": sorted(targets), "children": children})
        return sorted(out, key=lambda n: n["path"])

    return level_nodes(0, base, start)


def flatten(nodes: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(flatten(node.get("children") or []))
    return out


# ---------------------------------------------------------------------------
# Panels


def _fill(pattern: str, values: Mapping[str, Any]) -> str:
    from alfrd.studio_defs import fill

    return fill(pattern, {k: v for k, v in values.items() if v not in (None, "")})


def _pattern_values(node_values: Mapping[str, Any], target: str | None, varmap: Mapping[str, str]) -> dict[str, Any]:
    values = dict(node_values)
    if values.get("workdir_path"):
        values["workdir"] = values["workdir_path"]
    if target:
        values["target"] = target
    for name, template in varmap.items():
        values[name] = _fill(template, values)
    return values


def _expand(root: Path, pattern: str, values: Mapping[str, Any]) -> list[tuple[str, dict[str, str]]]:
    from alfrd.studio_defs import expand_pattern

    text = _fill(pattern, values)
    if text.startswith("./"):
        text = text[2:]
    return expand_pattern(root, text, {})


def _json_get(data: Any, path: str) -> Any:
    """``a.b``, ``a[0]``, ``a[*].b`` (lists flatten)."""
    parts = re.findall(r"[^.\[\]]+|\[\*\]|\[\d+\]", str(path))
    items = [data]
    for part in parts:
        nxt = []
        for item in items:
            if part == "[*]":
                if isinstance(item, list):
                    nxt.extend(item)
                elif isinstance(item, Mapping):
                    nxt.extend(item.values())
            elif part.startswith("["):
                idx = int(part[1:-1])
                if isinstance(item, list) and -len(item) <= idx < len(item):
                    nxt.append(item[idx])
            elif isinstance(item, Mapping) and part in item:
                nxt.append(item[part])
        items = nxt
    if "[*]" in str(path):
        return items
    return items[0] if items else None


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return None, "file too large"
        return json.loads(path.read_text(encoding="utf-8", errors="replace")), None
    except (OSError, ValueError) as exc:
        return None, f"invalid JSON: {exc}"


def _files_from_steps(spec: Mapping[str, Any], base: str | None = None) -> list[dict[str, Any]]:
    """Metadata files the steps declare, as patterns under ``base`` (the panel's ``base``, default ``{meta_dir}``)."""
    prefix = str(base or DEFAULT_BASE).rstrip("/")
    prefix = prefix + "/" if prefix else ""
    out = []
    for sid in spec.get("step_order") or []:
        for item in (spec.get("steps") or {}).get(sid, {}).get("metadata") or []:
            if isinstance(item, Mapping) and item.get("file"):
                out.append({"step": sid, "path": prefix + str(item["file"]), "label": item.get("label") or item["file"],
                            "expect": {"require": list(item.get("require") or [])}})
    return out


def _file_status(root: Path, panel: Mapping[str, Any], values: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    files = panel.get("files")
    specs = _files_from_steps(spec, panel.get("base")) if files == "from_steps" else [f for f in files or [] if isinstance(f, Mapping)]
    entries = []
    for item in specs:
        expect = dict(item.get("expect") or {})
        require = list(expect.get("require") or []) + ([expect["json_key"]] if expect.get("json_key") else [])
        hits = _expand(root, str(item.get("path") or ""), values)
        found = []
        for rel, groups in hits:
            status, note = "ok", None
            if require:
                data, error = _read_json(root / rel)
                if error:
                    status, note = "invalid", error
                else:
                    missing = [k for k in require if _empty(_json_get(data, k))]
                    if missing:
                        status, note = "invalid", "missing or empty: " + ", ".join(missing)
            found.append({"rel": rel, "name": rel.rsplit("/", 1)[-1], "status": status, "note": note,
                          **({"band": groups["band"]} if groups.get("band") else {})})
        status = "missing" if not found else "invalid" if any(f["status"] != "ok" for f in found) else "ok"
        entries.append({"step": item.get("step"), "label": item.get("label") or item.get("path"), "pattern": item.get("path"),
                        "require": require, "status": status, "files": found})
    counts = {s: sum(e["status"] == s for e in entries) for s in ("ok", "invalid", "missing")}
    return {"entries": entries, "counts": counts}


def _json_fields(root: Path, panel: Mapping[str, Any], values: Mapping[str, Any], spec: Mapping[str, Any] | None = None) ->dict[str, Any]:
    hits = _expand(root, str(panel.get("source") or ""), values)
    if not hits:
        return {"source": None, "fields": [], "missing": True}
    rel = hits[0][0]
    data, error = _read_json(root / rel)
    fields = [{"field": str(f), "value": None if error else _json_get(data, str(f))} for f in panel.get("fields") or []]
    return {"source": rel, "fields": fields, "error": error}


def _csv_table(root: Path, panel: Mapping[str, Any], values: Mapping[str, Any], spec: Mapping[str, Any] | None = None) ->dict[str, Any]:
    source = str(panel.get("source") or "")
    tables = []
    if source == "result_csv":
        from alfrd.avica_layout import result_csv_matches

        if values.get("target"):
            try:
                matches = result_csv_matches(root, values["target"], values.get("project_code", ""),
                                             Path(values.get("workdir_path") or "").name if values.get("workdir_path") else "")
            except Exception:  # noqa: BLE001
                matches = []
            rels = [m["file"] for m in matches]
        else:
            rels = []
    else:
        rels = [rel for rel, _ in _expand(root, source, values)]
    for rel in rels[:5]:
        try:
            text = (root / rel).read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for i, row in enumerate(reader):
            if i >= MAX_ROWS:
                break
            rows.append({k: v for k, v in row.items() if k is not None})
        columns = [c for c in (panel.get("columns") or reader.fieldnames or [])]
        tables.append({"rel": rel, "columns": columns, "rows": rows})
    return {"tables": tables}


def _text(root: Path, panel: Mapping[str, Any], values: Mapping[str, Any], spec: Mapping[str, Any] | None = None) ->dict[str, Any]:
    out = []
    for rel, _ in _expand(root, str(panel.get("source") or ""), values)[: int(panel.get("limit") or 5)]:
        try:
            with open(root / rel, "rb") as stream:
                raw = stream.read(MAX_TEXT + 1)
        except OSError:
            continue
        out.append({"rel": rel, "text": raw[:MAX_TEXT].decode("utf-8", errors="replace"), "truncated": len(raw) > MAX_TEXT})
    return {"files": out}


def _image(root: Path, panel: Mapping[str, Any], values: Mapping[str, Any], spec: Mapping[str, Any] | None = None) ->dict[str, Any]:
    hits = _expand(root, str(panel.get("source") or ""), values)
    limit = min(int(panel.get("limit") or 12), MAX_IMAGES)
    images = [{"rel": rel, "name": rel.rsplit("/", 1)[-1]} for rel, _ in hits if re.search(r"\.(png|jpe?g|gif|webp)$", rel, re.I)]
    return {"images": images[:limit], "more": max(0, len(images) - limit)}


def _owner_regexes(spec: Mapping[str, Any], values: Mapping[str, Any], base: str | None = None) -> list[tuple[re.Pattern[str], re.Pattern[str], dict]]:
    """Per declared step file: (regex with this target, regex with any target, spec)."""
    from alfrd.studio_defs import pattern_to_regex

    out = []
    for item in _files_from_steps(spec, base):
        with_target = _fill(item["path"], values)
        any_target = _fill(item["path"], {k: v for k, v in values.items() if k != "target"})
        out.append((pattern_to_regex(with_target), pattern_to_regex(any_target), item))
    return out


def _files(root: Path, panel: Mapping[str, Any], values: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Files of a folder with their content (JSON parsed), each with the step that writes it.

    A file that a step declares for another target is left out; files no step
    declares are listed as shared.
    """
    owners = _owner_regexes(spec, values, panel.get("base"))
    target = values.get("target")
    out = []
    for rel, _groups in _expand(root, str(panel.get("source") or ""), values):
        owner = None
        other = False
        for mine, anyone, item in owners:
            if mine.match(rel):
                owner = item
                break
            m = anyone.match(rel)
            if m and target and m.groupdict().get("target") not in (None, target):
                other = True
        if other and owner is None:
            continue
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entry: dict[str, Any] = {"rel": rel, "name": rel.rsplit("/", 1)[-1], "size": size,
                                 "step": owner["step"] if owner else None, "label": owner["label"] if owner else None}
        band = next((m.groupdict().get("band") for mine, _a, _i in owners for m in [mine.match(rel)] if m), None)
        if band:
            entry["band"] = band
        if size > MAX_PARSE:
            entry["error"] = "file too large to preview"
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            if text.lstrip()[:1] in ("{", "["):
                try:
                    entry.update(format="json", data=json.loads(text))
                except ValueError as exc:
                    entry.update(format="text", text=text[:MAX_TEXT], error=f"invalid JSON: {exc}")
            else:
                entry.update(format="text", text=text[:MAX_TEXT])
        out.append(entry)
        if len(out) >= MAX_FILES:
            break
    return {"files": out}


Evaluator = Callable[[Path, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]

#: Panel kind -> ``evaluate(root, panel, values, spec)``; ``None`` = a client panel (the browser renders it).
PANELS: dict[str, Evaluator | None] = {
    "file_status": _file_status, "files": _files, "json_fields": _json_fields, "csv_table": _csv_table,
    "text": _text, "image": _image, "avica_config": None, "avica_inputs": None,
}
_BUILTIN_PANELS = frozenset(PANELS)
_KIND = re.compile(r"^[a-z][a-z0-9_]*$")


def _sync_kinds() -> None:
    global PANEL_TYPES, CLIENT_PANELS
    PANEL_TYPES = tuple(k for k, fn in PANELS.items() if fn is not None)
    CLIENT_PANELS = tuple(k for k, fn in PANELS.items() if fn is None)


_sync_kinds()


def register_panel(kind: str, evaluate: Evaluator | None = None, client: bool = False) -> None:
    """Add a panel kind: a server ``evaluate(root, panel, values, spec)``, or ``client=True`` (browser only).

    Built-in kinds can't be replaced; registering a plugin kind again replaces it.
    """
    if not isinstance(kind, str) or not _KIND.match(kind):
        raise ValueError(f"invalid panel kind {kind!r} (lowercase letters, digits, _)")
    if kind in _BUILTIN_PANELS:
        raise ValueError(f"panel kind {kind!r} is built in")
    if evaluate is None and not client:
        raise ValueError(f"panel {kind!r} needs an evaluate function or client=True")
    if evaluate is not None and not callable(evaluate):
        raise ValueError(f"panel {kind!r}: evaluate is not callable")
    PANELS[kind] = evaluate
    _sync_kinds()


def unregister_panel(kind: str) -> None:
    """Drop a plugin panel kind (built-ins stay)."""
    if kind not in _BUILTIN_PANELS and kind in PANELS:
        del PANELS[kind]
        _sync_kinds()


def _evaluate(root: Path, panel: Mapping[str, Any], values: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    kind = panel.get("panel")
    if kind not in PANELS:
        return {"error": f"unknown panel type {kind!r}"}
    evaluate = PANELS[kind]
    if evaluate is None:
        return {"client": True}
    if kind in _BUILTIN_PANELS:
        return evaluate(root, panel, values, spec)
    try:  # a plugin's evaluator never breaks the view
        result = evaluate(root, panel, values, spec)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{kind}: {exc}"}
    return dict(result) if isinstance(result, Mapping) else {"error": f"{kind}: evaluator returned {type(result).__name__}"}


def view(root: str | Path, entity: Mapping[str, Any], *, name: str = "metadata") -> dict[str, Any]:
    """Panels of ``views.<name>`` evaluated for an entity path (target, project_code, workdir …)."""
    base = Path(root).resolve()
    spec = spec_for(base)
    nodes = walk(base, spec)
    flat = flatten(nodes)
    varmap = _vars(base, spec)
    target = entity.get("target")
    levels = [str(lv.get("level")) for lv in spec.get("hierarchy") or [] if isinstance(lv, Mapping)]

    def selected(level: str) -> list[Mapping[str, Any]]:
        out = []
        for node in flat:
            if node["level"] != level:
                continue
            vals = node["values"]
            if any(entity.get(k) not in (None, "") and vals.get(k) not in (None, "") and str(vals.get(k)) != str(entity[k])
                   for k in levels if k != level):
                continue
            if entity.get(level) and str(node["value"]) != str(entity[level]):
                continue
            if target and node["targets"] and target not in node["targets"]:
                continue  # a folder of other targets (unknown targets: kept)
            out.append(node)
        return out

    panels = []
    for i, panel in enumerate((spec.get("views") or {}).get(name) or []):
        if not isinstance(panel, Mapping):
            continue
        scope = str(panel.get("scope") or ("workdir" if "workdir" in levels else levels[-1] if levels else "target"))
        item = {"index": i, "panel": panel.get("panel"), "title": panel.get("title") or str(panel.get("panel")),
                "scope": scope, "instances": []}
        if scope == "target" or scope not in levels:
            where = {k: v for k, v in entity.items() if k != "project"}
            scoped = selected("workdir") if "workdir" in levels else selected(levels[-1]) if levels else []
            if panel.get("panel") == "csv_table" and str(panel.get("source")) == "result_csv":
                # One table per work dir of the target (result CSVs are per target × code × work dir).
                for node in scoped or [{"values": {}}]:
                    values = _pattern_values(node["values"], target, varmap)
                    item["instances"].append({"where": {**where, **_where(node, levels)}, **_evaluate(base, panel, values, spec)})
            else:
                values = _pattern_values({}, target, varmap)
                merged = _merged(base, panel, [(_pattern_values(n["values"], target, varmap)) for n in scoped] or [values], spec)
                item["instances"].append({"where": where, **merged})
        else:
            for node in selected(scope):
                values = _pattern_values(node["values"], target, varmap)
                result = _evaluate(base, panel, values, spec)
                item["instances"].append({"where": _where(node, levels), "path": node["path"], **result})
        panels.append(item)
    return {"schema": "alfrd.view/1", "view": name, "entity": dict(entity), "levels": levels, "panels": panels,
            "nodes": [_summary(n) for n in nodes], "errors": spec["errors"]}


def _merged(root: Path, panel: Mapping[str, Any], value_sets: Sequence[Mapping[str, Any]], spec: Mapping[str, Any]) -> dict[str, Any]:
    """A target-scope panel over several folders: one result, lists concatenated."""
    results = [_evaluate(root, panel, values, spec) for values in value_sets]
    if not results:
        return {}
    if panel.get("panel") == "json_fields":  # one file's fields: the first readable one
        found = [r for r in results if r.get("source")]
        return dict(next((r for r in found if not r.get("error")), found[0] if found else results[0]))
    first = dict(results[0])
    for result in results[1:]:
        for key, value in result.items():
            if isinstance(value, list) and isinstance(first.get(key), list):
                if key == "entries":  # file_status: same entries per folder, merge their files
                    for a, b in zip(first[key], value):
                        a["files"] = a["files"] + b["files"]
                        a["status"] = "missing" if not a["files"] else "invalid" if any(f["status"] != "ok" for f in a["files"]) else "ok"
                else:
                    seen = {item.get("rel") for item in first[key] if isinstance(item, Mapping)}
                    first[key] = first[key] + [item for item in value
                                               if not (isinstance(item, Mapping) and item.get("rel") and item["rel"] in seen)]
    if "entries" in first:
        first["counts"] = {s: sum(e["status"] == s for e in first["entries"]) for s in ("ok", "invalid", "missing")}
    return first


def _where(node: Mapping[str, Any], levels: Sequence[str]) -> dict[str, Any]:
    return {k: node["values"][k] for k in levels if node["values"].get(k) not in (None, "")}


def _summary(node: Mapping[str, Any]) -> dict[str, Any]:
    return {"level": node["level"], "value": node["value"], "path": node["path"], "targets": node["targets"],
            "children": [_summary(c) for c in node.get("children") or []]}


def panel_file(root: str | Path, entity: Mapping[str, Any], index: int, rel: str) -> Path:
    """An image of panel ``index`` (only files that panel lists are served)."""
    base = Path(root).resolve()
    result = view(base, entity)
    for panel in result["panels"]:
        if panel["index"] != index or panel["panel"] != "image":
            continue
        for inst in panel["instances"]:
            if any(img["rel"] == rel for img in inst.get("images") or []):
                path = (base / rel).resolve()
                path.relative_to(base)
                return path
    raise PermissionError(f"{rel} is not an image of this panel")


__all__ = ["CLIENT_PANELS", "PANELS", "PANEL_TYPES", "flatten", "register_panel", "unregister_panel", "panel_file", "schema", "spec_for", "validate", "view", "walk"]
