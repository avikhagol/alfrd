"""Studio definitions read from ``alfrd.yaml`` (plus an optional template).

ALFRD Studio is not tied to AVICA: step labels, stages, categories,
the metadata files each step writes, the log files each step leaves behind and
the Overview "MS Storage Path" column are all declared in ``alfrd.yaml``.
A manifest may say ``template: avica`` to start from the defaults shipped in
``alfrd/web/assets/templates/avica.yaml``; anything alfrd.yaml sets wins.

This module holds the pieces the server needs (the browser has a mirror in
``web/js/data/defs.js``):

* :func:`studio_manifest` - alfrd.yaml merged with its template;
* :func:`expand_pattern` - list files/folders matching a pattern without
  walking measurement sets;
* :func:`collect_log_files` / :func:`collect_ms_paths` - what the Logs view
  and the Overview need;
* :func:`allowed_file` - whether the server may hand a file to the Studio;
* :func:`save_manifest` / :func:`update_key_values` - the two writes the
  Studio can ask for (alfrd.yaml from Project settings, avica.inp from the
  Workflow inspector).
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

MANIFEST_NAMES = ("alfrd.yaml", ".alfrd.yaml")
_TOKEN = re.compile(r"(\{\w+\}|\*|\?)")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_GROUPS = {
    "project_code": r"[^/]+",
    "n": r"\d+",
    "band": r"[A-Z][A-Z0-9]*?",
    "target": r"[^/]+?",
    "workdirname": r"wd(?:_\d+)?",
}
#: Folders a wildcard never descends into (thousands of files, nothing to show).
_NO_DESCEND = re.compile(r"(\.ms|\.ms\..+|\.flagversions)$|^(raw|tmp_files|tmp_fringe_testing|calibration_tables|__pycache__|\..+)$")
_MAX_HITS = 5000
#: Logs written by `alfrd plan` runs (see alfrd.runtime.scheduler).
_PLAN_LOG = re.compile(r"^\.alfrd/plans/[^/]+/(?:logs/[^/]+\.(?:log|usage\.jsonl)|runner\.log)$")


# ---------------------------------------------------------------------------
# Manifest + template


def template_path(name: str) -> Path | None:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name or ""):
        return None
    try:
        from alfrd.web import web_root

        path = web_root() / "assets" / "templates" / f"{name}.yaml"
    except FileNotFoundError:  # pragma: no cover
        return None
    return path if path.is_file() else None


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    from alfrd.avica_layout import load_yaml_cached

    try:
        data = load_yaml_cached(path) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def manifest_file(root: str | Path) -> Path | None:
    """The folder's own alfrd.yaml (the default manifest is not a file of the folder)."""
    for name in MANIFEST_NAMES:
        path = Path(root) / name
        if path.is_file():
            return path
    return None


def template_name(manifest: Mapping[str, Any]) -> str | None:
    """``template:`` from alfrd.yaml; an ``avica:`` block implies ``avica``."""
    name = manifest.get("template")
    if isinstance(name, str) and name:
        return name
    if isinstance(manifest.get("avica"), dict):
        return "avica"
    return None


def _step_list(manifest: Mapping[str, Any]) -> list[Any]:
    workflows = manifest.get("workflows")
    if isinstance(workflows, list) and workflows:
        first = workflows[0]
        steps = first.get("steps") if isinstance(first, dict) else first
        return list(steps) if isinstance(steps, list) else []
    if isinstance(workflows, dict) and workflows:
        first = next(iter(workflows.values()))
        steps = first.get("steps") if isinstance(first, dict) else first
        return list(steps) if isinstance(steps, list) else []
    return []


def _step_id(step: Any) -> str:
    if isinstance(step, str):
        return step
    if isinstance(step, dict):
        return str(step.get("id") or step.get("key") or step.get("name") or "")
    return ""


def resolve_alias(name: str, aliases: Mapping[str, Any]) -> str:
    """alfrd.yaml ``project_settings.field_aliases`` (``"old*": "new*"`` rewrites a prefix)."""
    if name in aliases:
        return str(aliases[name])
    for old, new in aliases.items():
        old, new = str(old), str(new)
        if old.endswith("*") and name.lower().startswith(old[:-1].lower()):
            return new.rstrip("*") + name[len(old) - 1:]
    return name


def studio_manifest(root: str | Path, *, strict: bool = False) -> dict[str, Any]:
    """alfrd.yaml merged with its template (steps as a ``{id: definition}`` mapping).

    ``strict``: an alfrd.yaml that does not parse raises ManifestError (see manifest_data).
    """
    from alfrd.manifest_default import manifest_data

    manifest, _path, _default = manifest_data(root, strict=strict)  # local alfrd.yaml, else the default one
    name = template_name(manifest)
    template = _load_yaml(template_path(name)) if name and template_path(name) else {}
    merged: dict[str, Any] = copy.deepcopy(template)
    for key, value in manifest.items():
        if key in {"project_settings", "overview", "results", "step_defaults", "views", "quickstart"} and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}
        elif key == "artifacts" and isinstance(value, list):
            names = {a.get("name") for a in value if isinstance(a, dict)}
            merged[key] = [a for a in merged.get("artifacts") or [] if a.get("name") not in names] + value
        else:
            merged[key] = copy.deepcopy(value)
    defs = {k: dict(v) if isinstance(v, dict) else {} for k, v in (template.get("steps") or {}).items()}
    # alfrd.yaml's own ``steps:`` mapping overrides the template's step definitions key by key.
    own = manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {}
    for sid, spec in own.items():
        if isinstance(spec, dict):
            defs[sid] = {**defs.get(sid, {}), **spec}
    aliases = (merged.get("project_settings") or {}).get("field_aliases") or {}
    steps: dict[str, dict[str, Any]] = {}
    raw_steps = _step_list(manifest) or _step_list(template) or list(defs)
    order = [resolve_alias(_step_id(s), aliases) for s in raw_steps]
    for raw in raw_steps:
        sid = resolve_alias(_step_id(raw), aliases)
        if not sid:
            continue
        base = dict(defs.get(sid) or {})
        if isinstance(raw, dict):
            base.update({k: v for k, v in raw.items() if k not in {"id", "key", "name"}})
        steps[sid] = base
    # ``skip: true`` (on a workflow step or in ``steps:``) leaves a template step out of the workflow.
    skipped = [sid for sid in order if (steps.get(sid) or {}).get("skip") is True]
    steps = {sid: spec for sid, spec in steps.items() if sid not in skipped}
    order = [sid for sid in order if sid not in skipped]
    for spec in steps.values():
        for key in ("depends_on", "needs", "after"):
            if isinstance(spec.get(key), list):
                spec[key] = [d for d in spec[key] if d not in skipped]
    merged["steps"] = steps
    merged["step_order"] = order
    merged["skipped_steps"] = skipped
    merged["template"] = name
    workflows = merged.get("workflows") or []
    workflow = workflows[0] if isinstance(workflows, list) and workflows else next(iter(workflows.values()), {}) if isinstance(workflows, dict) else {}
    from alfrd.agent_loop import expand_steps, is_sequence, sequence_passes

    if isinstance(workflow, dict) and is_sequence(workflow):
        # Sequence items name workflow steps or entrypoints; labels come from top-level ``steps``.
        labels = {**defs, **(manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {})}
        own = {resolve_alias(_step_id(s), aliases): s for s in workflow.get("steps") or [] if isinstance(s, dict)}
        base: dict[str, dict[str, Any]] = {}
        for item in dict.fromkeys(i for p in sequence_passes(workflow["repeat"]) for i in p):
            base[item] = {**(labels.get(item) if isinstance(labels.get(item), dict) else {}),
                          **{k: v for k, v in own.get(item, {}).items() if k not in {"id", "key", "name"}}}
        merged["_base_steps"] = base
        merged["steps"] = expand_steps(workflow, base)
        merged["step_order"] = list(merged["steps"])
    elif isinstance(workflow, dict) and (workflow.get("repeat") is not None or "roles" in workflow or any("role" in s for s in steps.values())):
        merged["_base_steps"] = steps
        merged["steps"] = expand_steps(workflow, steps)
        merged["step_order"] = list(merged["steps"])
    return merged


# ---------------------------------------------------------------------------
# Patterns


def normalize_pattern(pattern: str) -> str:
    """Legacy ``{target_dir}/{project_code}/{workdir}`` → ``{workdir}``."""
    text = str(pattern).strip()
    return text.replace("{target_dir}/{project_code}/{workdir}", "{workdir}")


def fill(pattern: str, fixed: Mapping[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        value = fixed.get(match.group(1))
        return str(value).strip("/") if value not in (None, "") else match.group(0)

    text = _PLACEHOLDER.sub(repl, pattern)
    # A root target_dir (".") leaves "./" segments behind; drop them.
    return re.sub(r"(?:^|(?<=/))\.(?:/|$)", "", text)


def _segment_regex(seg: str, known: Mapping[str, str]) -> re.Pattern[str]:
    out = []
    seen: set[str] = set()
    for token in _TOKEN.split(seg):
        if not token:
            continue
        if token == "*":
            out.append(r"[^/]*")
        elif token == "?":
            out.append(r"[^/]")
        elif token.startswith("{") and token.endswith("}"):
            name = token[1:-1]
            if name in known:
                out.append(re.escape(known[name]))
            elif name in seen:
                out.append(f"(?P={name})")
            else:
                seen.add(name)
                out.append(f"(?P<{name}>{_GROUPS.get(name, r'[^/]+?')})")
        else:
            out.append(re.escape(token))
    return re.compile("^" + "".join(out) + "$")


def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Whole-path regex (placeholders → one folder level, ``*`` → no slash)."""
    out = []
    seen: set[str] = set()
    for token in _TOKEN.split(pattern.strip("/")):
        if not token:
            continue
        if token == "*":
            out.append(r"[^/]*")
        elif token == "?":
            out.append(r"[^/]")
        elif token.startswith("{") and token.endswith("}"):
            name = token[1:-1]
            if name in seen:
                out.append(f"(?P={name})")
            else:
                seen.add(name)
                out.append(f"(?P<{name}>{_GROUPS.get(name, r'[^/]+?')})")
        else:
            out.append(re.escape(token))
    return re.compile("^" + "".join(out) + "$")


def expand_pattern(root: str | Path, pattern: str, fixed: Mapping[str, Any] | None = None, kind: str = "file") -> list[tuple[str, dict[str, str]]]:
    """Paths under ``root`` matching ``pattern`` (relative, with captured placeholders).

    Literal segments are opened directly; only segments with a placeholder or a
    wildcard list one folder. Measurement sets and scratch folders are never
    entered unless the pattern names them literally.
    """
    base = Path(root).resolve()
    text = fill(normalize_pattern(pattern), fixed or {})
    if {"workdir", "meta_dir", "logs", "target_dir"} & set(_PLACEHOLDER.findall(text)):
        return []  # context not known (e.g. no AVICA work dirs)
    segs = [s for s in text.split("/") if s and s != "."]
    if not segs or ".." in segs:
        return []
    frontier: list[tuple[Path, dict[str, str]]] = [(base, {})]
    for i, seg in enumerate(segs):
        last = i == len(segs) - 1
        want_dir = (not last) or kind == "directory"
        nxt: list[tuple[Path, dict[str, str]]] = []
        for path, groups in frontier:
            if not _TOKEN.search(seg):
                cand = path / seg
                if (cand.is_dir() if want_dir else cand.is_file()):
                    nxt.append((cand, groups))
                continue
            regex = _segment_regex(seg, groups)
            try:
                entries = sorted(os.scandir(path), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                if is_dir != want_dir:
                    continue
                if is_dir and not last and _NO_DESCEND.search(entry.name):
                    continue
                match = regex.match(entry.name)
                if match:
                    found = {k: v for k, v in match.groupdict().items() if v is not None}
                    nxt.append((Path(entry.path), {**groups, **found}))
        frontier = nxt[:_MAX_HITS]
        if not frontier:
            break
    return [(os.path.relpath(p, base).replace(os.sep, "/"), g) for p, g in frontier]


# ---------------------------------------------------------------------------
# Context (work dirs, logs dir, meta dir)


def studio_context(root: Path) -> dict[str, Any]:
    from alfrd.avica_layout import LOGS_DIRNAME, layout_patterns, manifest_avica, resolve_config, resolve_dir, scan_project_codes

    manifest = studio_manifest(root)
    block = manifest_avica(root)
    ctx: dict[str, Any] = {"manifest": manifest, "logs": str(block.get("logs") or LOGS_DIRNAME), "workdirs": [], "meta_dir": "avica.meta"}
    if manifest.get("template") == "avica":
        cfg = resolve_config(root)
        target_dir = resolve_dir(root, cfg.get("target_dir")) or root / "reductions"
        patterns = layout_patterns(root)
        ctx["target_dir"] = os.path.relpath(target_dir, root).replace(os.sep, "/")
        ctx["meta_dir"] = patterns["meta_dir"][0]
        ctx["workdirs"] = [
            {"rel": os.path.relpath(code.wd, root).replace(os.sep, "/"), "id": code.id, "code": code.code}
            for code in scan_project_codes(root, target_dir, patterns)
        ]
    return ctx


def log_patterns(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every log pattern the Studio shows: ``{pattern, group, step}``."""
    out: list[dict[str, Any]] = []
    defaults = [str(p) for p in (manifest.get("step_defaults") or {}).get("logs") or []]
    for sid, step in (manifest.get("steps") or {}).items():
        for pattern in [*(step.get("logs") or []), *defaults]:
            out.append({"pattern": normalize_pattern(str(pattern)), "group": f"step:{sid}", "step": sid})
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict) or not artifact.get("path_pattern"):
            continue
        if artifact.get("kind") == "log" or artifact.get("viewer") == "log" or artifact.get("show_in_logs"):
            out.append({"pattern": normalize_pattern(str(artifact["path_pattern"])), "group": f"artifact:{artifact.get('name')}", "step": None})
    return out


def _expansions(root: Path, pattern: str, ctx: Mapping[str, Any], fixed: Mapping[str, Any], kind: str = "file") -> Iterable[tuple[str, dict[str, str], dict[str, Any] | None]]:
    base = {"logs": ctx["logs"], "target_dir": ctx.get("target_dir"), **fixed}
    if "{workdir}" in pattern or "{meta_dir}" in pattern:
        for wd in ctx["workdirs"]:
            values = {**base, "workdir": wd["rel"], "meta_dir": f"{wd['rel']}/{ctx['meta_dir']}"}
            for rel, groups in expand_pattern(root, pattern, values, kind):
                yield rel, groups, wd
    else:
        for rel, groups in expand_pattern(root, pattern, base, kind):
            yield rel, groups, None


def collect_log_files(root: str | Path, ctx: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Log files matching alfrd.yaml's step ``logs`` and log artifacts (names and sizes only)."""
    base = Path(root).resolve()
    ctx = ctx or studio_context(base)
    files: dict[str, dict[str, Any]] = {}
    for spec in log_patterns(ctx["manifest"]):
        fixed = {"step": spec["step"]} if spec["step"] else {}
        if "{step}" in spec["pattern"] and not spec["step"]:
            continue
        for rel, groups, wd in _expansions(base, spec["pattern"], ctx, fixed):
            path = base / rel
            try:
                stat = path.stat()
            except OSError:
                continue
            item = files.setdefault(rel, {"rel": rel, "size": stat.st_size, "mtime": stat.st_mtime, "groups": []})
            if spec["group"] not in item["groups"]:
                item["groups"].append(spec["group"])
            for key in ("band", "target"):
                if groups.get(key):
                    item[key] = groups[key]
            if spec["step"]:
                item.setdefault("steps", [])
                if spec["step"] not in item["steps"]:
                    item["steps"].append(spec["step"])
            if wd:
                item["workdir"] = wd["id"]
    return sorted(files.values(), key=lambda f: -f["mtime"])


def collect_ms_paths(root: str | Path, ctx: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Folders matching ``overview.ms_path`` with the target they belong to."""
    base = Path(root).resolve()
    ctx = ctx or studio_context(base)
    patterns = (ctx["manifest"].get("overview") or {}).get("ms_path") or []
    out = []
    for order, pattern in enumerate(patterns if isinstance(patterns, list) else [patterns]):
        for rel, groups, wd in _expansions(base, normalize_pattern(str(pattern)), ctx, {}, kind="directory"):
            out.append({"rel": rel, "target": groups.get("target"), "band": groups.get("band"), "workdir": wd["id"] if wd else None, "order": order})
    return out


def allowed_file(root: str | Path, rel: str) -> Path:
    """Resolve ``rel`` if it is a file the Studio may read (a declared log, inside root)."""
    base = Path(root).resolve()
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
        raise ValueError("invalid path")
    path = (base / rel).resolve()
    path.relative_to(base)  # ValueError when outside
    if not path.is_file():
        raise FileNotFoundError(rel)
    rel_norm = os.path.relpath(path, base).replace(os.sep, "/")
    if _PLAN_LOG.match(rel_norm):  # `alfrd plan` command and runner logs
        return path
    ctx = studio_context(base)
    for spec in log_patterns(ctx["manifest"]):
        fixed = {"step": spec["step"]} if spec["step"] else {}
        values = {"logs": ctx["logs"], "target_dir": ctx.get("target_dir"), **fixed}
        candidates = [fill(spec["pattern"], values)]
        if "{workdir}" in spec["pattern"] or "{meta_dir}" in spec["pattern"]:
            candidates = [fill(spec["pattern"], {**values, "workdir": wd["rel"], "meta_dir": f"{wd['rel']}/{ctx['meta_dir']}"}) for wd in ctx["workdirs"]]
        if any(pattern_to_regex(c).match(rel_norm) for c in candidates if "{workdir}" not in c):
            return path
    raise PermissionError(f"{rel} is not a log declared in alfrd.yaml")


def read_tail(path: Path, tail: int = 400_000) -> str:
    return read_range(path, None, tail)["text"]


def _complete_utf8(data: bytes) -> int:
    """Length of ``data`` without a trailing, incomplete UTF-8 sequence."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte & 0xC0 == 0x80:  # continuation byte: keep looking for the lead byte
            continue
        need = 2 if byte & 0xE0 == 0xC0 else 3 if byte & 0xF0 == 0xE0 else 4 if byte & 0xF8 == 0xF0 else 1
        return len(data) - back if need > back else len(data)
    return len(data)


def file_id(stat: os.stat_result) -> str:
    """Identity of a file across polls: a new inode means the log was rotated or replaced."""
    return f"{stat.st_dev}:{stat.st_ino}"


def read_range(path: Path, offset: int | None = None, tail: int = 400_000, file: str | None = None) -> dict[str, Any]:
    """Read a growing log from byte ``offset`` (``None``: the last ``tail`` bytes).

    Returns ``text``, the byte ``offset`` to ask for next time, ``size``, the
    file ``id`` and ``reset``. ``reset`` is true when the text replaces what the
    caller has: first read, the file was truncated or replaced (``file`` is the
    id the caller saw), or more than ``tail`` bytes were appended since. A
    UTF-8 character split at the end is left for the next read.
    """
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        size = stat.st_size
        ident = file_id(stat)
        reset = offset is None or offset < 0 or offset > size or (file is not None and file != ident) or size - offset > tail
        start = max(0, size - tail) if reset else offset
        stream.seek(start)
        data = stream.read(size - start)
    if reset and start > 0:
        # Started mid-file: drop a partial line (and any partial character).
        cut = data.find(b"\n")
        data = data[cut + 1:] if 0 <= cut < 4096 else data
        start = size - len(data)
    end = _complete_utf8(data)
    return {
        "text": data[:end].decode("utf-8", errors="replace"),
        "offset": start + end,
        "size": size,
        "id": ident,
        "mtime": stat.st_mtime,
        "reset": reset,
    }


# ---------------------------------------------------------------------------
# Writes requested by the Studio (loopback + CSRF only; see gui/studio.py)


def save_manifest(root: str | Path, text: str) -> Path:
    """Validate and write alfrd.yaml (previous version kept as ``alfrd.yaml.bak``)."""
    import yaml

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("alfrd.yaml must be a YAML mapping")
    if not str(data.get("name") or "").strip():
        raise ValueError("alfrd.yaml needs a `name`")
    path = manifest_file(root) or Path(root) / MANIFEST_NAMES[0]
    if path.is_file():
        path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, (list, tuple, dict)):
        import json

        return json.dumps(value)
    return str(value)


def update_key_values(path: str | Path, changes: Mapping[str, Any]) -> dict[str, Any]:
    """Set ``key = value`` lines in an AVICA-style file, keeping comments and order."""
    target = Path(path)
    lines = target.read_text(encoding="utf-8").splitlines() if target.is_file() else []
    pending = {str(k): v for k, v in changes.items() if re.fullmatch(r"[A-Za-z_][\w.]*", str(k))}
    written: dict[str, Any] = {}
    for i, line in enumerate(lines):
        body, hash_, comment = line.partition("#")
        if "=" not in body:
            continue
        key = body.split("=", 1)[0].strip()
        if key in pending:
            indent = line[: len(line) - len(line.lstrip())]
            tail = f"  #{comment}" if hash_ else ""
            lines[i] = f"{indent}{key} = {_format_value(pending[key])}{tail}"
            written[key] = pending.pop(key)
    for key, value in pending.items():
        lines.append(f"{key} = {_format_value(value)}")
        written[key] = value
    if target.is_file():
        target.with_name(target.name + ".bak").write_bytes(target.read_bytes())
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


__all__ = [
    "studio_context",
    "allowed_file",
    "collect_log_files",
    "collect_ms_paths",
    "expand_pattern",
    "log_patterns",
    "manifest_file",
    "normalize_pattern",
    "pattern_to_regex",
    "read_tail",
    "save_manifest",
    "studio_manifest",
    "template_name",
    "update_key_values",
]
