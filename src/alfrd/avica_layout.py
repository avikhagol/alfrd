"""Import-free reader for an AVICA reduction tree.

ALFRD never imports AVICA. This module only reads the files AVICA leaves on
disk next to an ``alfrd.yaml``::

    <root>/
      alfrd.yaml                 ALFRD project (name = ALFRD project)
      avica.inp                  AVICA config (key = value, ``<step>.<param>`` allowed)
      avica.summary.json         optional cache of ``avica pipe config --summary``
      avica.logs/                avica__log-*.log, avica_crash_<step>.json
      <target_dir>/              from the resolved AVICA config (default ``reductions/``)
        <TARGET>_result.csv      per-target step history
        <CODE>/wd/               AVICA project code (e.g. BV019, RDV41) work dir
          avica.meta/            JSON sidecars ``*.avica``, ``listobs.json``, ``*.out``
          input_template/        rPicard ``*.inp`` templates
          wd_<band>/             per-band work dirs
          wd_<band>_<TARGET>/    per-band, per-target work dirs (+ input_template_<band>_<TARGET>/)

Naming: an *ALFRD project* is what ``alfrd.yaml`` declares; an *AVICA project
code* is the observation code taken from the FITS files (the ``<CODE>`` folder).
One ALFRD project holds many targets and a target may appear under several
project codes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

AVICA_STEPS = (
    "preprocess_fitsidi",
    "fits_to_ms",
    "phaseshift",
    "avica_avg",
    "avicameta_ms",
    "avica_snr",
    "avica_fill_input",
    "avica_split_ms",
    "rpicard",
)

#: Values AVICA uses when neither avica.inp nor the summary provide one.
DEFAULT_CONFIG = {
    "target_dir": "reductions/",
    "picard_input_template_update": "",
}

SUMMARY_FILENAMES = ("avica.summary.json", "avica.summary.txt")
CONFIG_FILENAME = "avica.inp"
LOGS_DIRNAME = "avica.logs"
META_DIRNAME = "avica.meta"
RESULT_SUFFIX = "_result.csv"
PICARD_INP_FILES = ("array.inp", "observation.inp", "array_finetune.inp", "flagging.inp", "constants.inp")

_MAX_TEXT = 2 * 1024 * 1024
_BAND_TARGET = re.compile(r"^wd_(?P<band>[A-Z][A-Z0-9]*?)(?:_(?P<target>.+))?$")
_META_TARGET = re.compile(r"^(?P<kind>[a-z][a-z_]*?)_(?P<band>[A-Z][A-Z0-9]*)_(?P<target>.+)\.(?:avica|out)$")


# ---------------------------------------------------------------------------
# alfrd.yaml `avica:` block


def manifest_avica(root: str | Path) -> dict[str, Any]:
    """Return the optional ``avica:`` block of ``alfrd.yaml`` (or ``.alfrd.yaml``)."""
    import yaml

    for name in ("alfrd.yaml", ".alfrd.yaml"):
        path = Path(root) / name
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                return {}
            block = data.get("avica") if isinstance(data, dict) else None
            return dict(block) if isinstance(block, dict) else {}
    return {}


# ---------------------------------------------------------------------------
# key = value files


def _coerce(value: str) -> Any:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    low = text.lower()
    if low in {"true", "false"}:
        return low == "true"
    if low == "none":
        return None
    # Only plain numbers; keep rPicard words such as inf/nan/int as text (and JSON-safe).
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", text):
        return float(text)
    return text


def read_key_values(path: str | Path) -> dict[str, Any]:
    """Read AVICA/rPicard ``key = value`` files (``#`` comments ignored)."""
    source = Path(path)
    if not source.is_file():
        return {}
    values: dict[str, Any] = {}
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if "=" not in stripped:
            continue
        key, value = (part.strip() for part in stripped.split("=", 1))
        if key:
            values[key] = _coerce(value)
    return values


# ---------------------------------------------------------------------------
# `avica pipe config --summary`

_BOX = "│┃|"


def parse_config_summary(text: str) -> list[dict[str, Any]]:
    """Parse the table printed by ``avica pipe config --summary``.

    Handles rich's box drawing (``│``/``┃``) and plain ``|`` tables, blank
    step cells (continuation of the previous step) and values wrapped across
    several lines. Returns rows ``{"step", "parameter", "source", "value"}``.
    """
    rows: list[dict[str, Any]] = []
    step = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip()[:1] not in _BOX:
            continue
        cells = [cell.strip() for cell in re.split(r"[│┃|]", line)[1:-1]]
        if len(cells) != 4:
            continue
        s, param, source, value = cells
        if s.lower() == "step" and param.lower() == "parameter":
            continue  # header
        if not source and rows and not (s and param):
            # rich wraps long cells onto extra lines without a source cell.
            if param:
                rows[-1]["parameter"] = f"{rows[-1]['parameter']}{param}"
            if value:
                rows[-1]["value"] = f"{rows[-1]['value']}{value}"
            if s:
                rows[-1]["step"] = f"{rows[-1]['step']}{s}"
                step = rows[-1]["step"]
            continue
        if s:
            step = s
        rows.append({"step": step, "parameter": param, "source": source, "value": value})
    for row in rows:
        row["value"] = _coerce(str(row["value"]))
    return rows


def run_config_summary(root: str | Path, timeout: float = 60.0, command: list[str] | None = None) -> dict[str, Any]:
    """Run ``avica pipe config --summary`` in ``root`` and parse its table.

    Only invoked on explicit request (CLI ``alfrd avica summary`` or the
    loopback-only Studio endpoint). Never imports AVICA.
    """
    argv = [str(part) for part in (command or ["avica", "pipe", "config", "--summary"])]
    executable = shutil.which(argv[0])
    if executable is None:
        raise FileNotFoundError(f"`{argv[0]}` is not on PATH")
    env = {**os.environ, "COLUMNS": "400", "NO_COLOR": "1", "TERM": "dumb"}
    completed = subprocess.run(
        [executable, *argv[1:]],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[-2000:] or "avica pipe config failed")
    rows = parse_config_summary(completed.stdout)
    if not rows:
        raise RuntimeError("could not parse `avica pipe config --summary` output")
    return {"command": " ".join(argv), "rows": rows}


def write_summary_cache(root: str | Path, summary: Mapping[str, Any], filename: str | None = None) -> Path:
    target = Path(root) / (filename or SUMMARY_FILENAMES[0])
    target.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return target


def load_summary_cache(root: str | Path, filename: str | None = None) -> dict[str, Any] | None:
    base = Path(root)
    json_file = base / (filename or SUMMARY_FILENAMES[0])
    if json_file.is_file():
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("rows"), list):
                data.setdefault("file", json_file.name)
                return data
        except (OSError, json.JSONDecodeError):
            pass
    text_file = base / SUMMARY_FILENAMES[1]
    if text_file.is_file():
        rows = parse_config_summary(text_file.read_text(encoding="utf-8", errors="replace"))
        if rows:
            return {"command": "avica pipe config --summary", "rows": rows, "file": text_file.name}
    return None


@dataclass
class AvicaConfig:
    """Resolved AVICA configuration for one ALFRD project root."""

    values: dict[str, Any]
    sources: dict[str, str]
    summary: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def step_params(self) -> dict[str, dict[str, Any]]:
        """Parameters grouped by step (``other`` holds core parameters)."""
        grouped: dict[str, dict[str, Any]] = {}
        if self.summary:
            for row in self.summary["rows"]:
                grouped.setdefault(row["step"] or "other", {})[row["parameter"]] = row["value"]
            return grouped
        for key, value in self.values.items():
            step, _, param = key.partition(".")
            if param and step in AVICA_STEPS:
                grouped.setdefault(step, {})[param] = value
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {"values": self.values, "sources": self.sources, "summary": self.summary}


def resolve_config(root: str | Path, run_summary: bool = False) -> AvicaConfig:
    """Layer defaults ← alfrd.yaml ``avica:`` ← avica.inp ← summary (cached or freshly run)."""
    base = Path(root)
    block = manifest_avica(base)
    values = dict(DEFAULT_CONFIG)
    sources = dict.fromkeys(DEFAULT_CONFIG, "default")
    if block.get("target_dir"):
        values["target_dir"] = block["target_dir"]
        sources["target_dir"] = "alfrd.yaml"
    config_name = str(block.get("config") or CONFIG_FILENAME)
    inp = read_key_values(base / config_name)
    values.update(inp)
    sources.update(dict.fromkeys(inp, config_name))
    cache_name = str(block.get("config_summary_cache") or SUMMARY_FILENAMES[0])
    command = block.get("config_summary") if isinstance(block.get("config_summary"), list) else None
    summary = None
    if run_summary:
        summary = run_config_summary(base, command=command)
        write_summary_cache(base, summary, cache_name)
    else:
        summary = load_summary_cache(base, cache_name)
    if summary:
        for row in summary["rows"]:
            if row["step"] in ("", "other") or str(row.get("source", "")).endswith("/core"):
                values[row["parameter"]] = row["value"]
                sources[row["parameter"]] = f"summary:{row['source']}"
    return AvicaConfig(values=values, sources=sources, summary=summary)


# ---------------------------------------------------------------------------
# Tree scanning


def _within(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    resolved.relative_to(root.resolve())  # raises ValueError when outside
    return resolved


def resolve_dir(root: str | Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else Path(root) / path


#: Layout patterns used when alfrd.yaml's ``avica:`` block does not define them.
#: Placeholders: {target_dir} (resolved config value), {project_code}, {n} (integer),
#: {band} (e.g. X, S2), {target}. Patterns may be a string or a list of alternatives.
DEFAULT_PATTERNS: dict[str, Any] = {
    "workdir": ["{target_dir}/{project_code}/wd", "{target_dir}/{project_code}/wd_{n}"],
    "band_dir": ["wd_{band}", "wd_{band}_{target}"],
    "meta_dir": "avica.meta",
    "input_templates": ["input_template", "input_template_{n}", "wd_{band}_{target}/input_template_{band}_{target}"],
    "result_csv": "{target_dir}/{target}_result.csv",
}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_GROUPS = {
    "project_code": r"[^/]+",
    "n": r"\d+",
    "band": r"[A-Z][A-Z0-9]*?",
    "target": r"[^/]+?",
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def layout_patterns(root: str | Path) -> dict[str, list[str]]:
    """Patterns from alfrd.yaml (``avica:`` block and the ``result_csv`` artifact) or defaults."""
    block = manifest_avica(root)
    patterns = {key: _as_list(block.get(key, default)) for key, default in DEFAULT_PATTERNS.items()}
    import yaml

    manifest = Path(root) / "alfrd.yaml"
    if "result_csv" not in block and manifest.is_file():
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            for artifact in data.get("artifacts") or []:
                if isinstance(artifact, dict) and artifact.get("name") == "result_csv" and "{target}" in str(artifact.get("path_pattern", "")):
                    patterns["result_csv"] = [str(artifact["path_pattern"])]
        except yaml.YAMLError:
            pass
    return patterns


def pattern_regex(pattern: str, fixed: Mapping[str, str] | None = None) -> re.Pattern[str]:
    """Compile a layout pattern; repeated placeholders must match the same text."""
    fixed = fixed or {}
    seen: set[str] = set()
    out = []
    pos = 0
    for match in _PLACEHOLDER.finditer(pattern):
        out.append(re.escape(pattern[pos:match.start()]))
        name = match.group(1)
        if name in fixed:
            out.append(re.escape(str(fixed[name]).rstrip("/")))
        elif name in seen:
            out.append(f"(?P={name})")
        else:
            seen.add(name)
            out.append(f"(?P<{name}>{_GROUPS.get(name, r'[^/]+?')})")
        pos = match.end()
    out.append(re.escape(pattern[pos:]))
    return re.compile("^" + "".join(out) + "$")


def _match_any(regexes: list[re.Pattern[str]], text: str) -> re.Match[str] | None:
    for regex in regexes:
        match = regex.match(text)
        if match:
            return match
    return None


@dataclass
class ProjectCode:
    """One AVICA work dir: ``<target_dir>/<CODE>/<workdir>`` (e.g. wd, wd_1)."""

    id: str
    code: str
    wd: Path
    bands: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    meta_files: list[str] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "wd": os.path.relpath(self.wd, root),
            "workdir": self.wd.name,
            "bands": self.bands,
            "targets": self.targets,
            "meta_files": self.meta_files,
            "templates": self.templates,
        }


def _meta_targets(names: Iterable[str]) -> set[str]:
    found = set()
    for name in names:
        match = _META_TARGET.match(name)
        if match:
            found.add(match.group("target"))
    return found


def _iter_dirs(base: Path, depth: int) -> Iterable[Path]:
    if depth <= 0 or not base.is_dir():
        return
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and not child.name.endswith(".ms") and not child.name.startswith("."):
            yield child
            yield from _iter_dirs(child, depth - 1)


def scan_project_codes(root: str | Path, target_dir: Path, patterns: Mapping[str, list[str]] | None = None) -> list[ProjectCode]:
    """Find work dirs matching the ``workdir`` patterns without walking measurement sets."""
    base = Path(root).resolve()
    pats = patterns or {k: _as_list(v) for k, v in DEFAULT_PATTERNS.items()}
    rel_target = os.path.relpath(target_dir, base)
    workdir_res = [pattern_regex(p, {"target_dir": rel_target}) for p in pats["workdir"]]
    band_res = [pattern_regex(p) for p in pats["band_dir"]]
    tpl_res = [pattern_regex(p) for p in pats["input_templates"]]
    meta_dir = pats["meta_dir"][0]
    depth = max(p.count("/") for p in pats["workdir"]) - pats["workdir"][0].split("/").index("{target_dir}") if pats["workdir"] else 2
    found: list[tuple[str, Path]] = []
    for candidate in _iter_dirs(target_dir, max(1, depth)):
        match = _match_any(workdir_res, os.path.relpath(candidate, base))
        if match:
            found.append((match.groupdict().get("project_code") or candidate.parent.name, candidate))
    per_code: dict[str, int] = {}
    for code, _ in found:
        per_code[code] = per_code.get(code, 0) + 1
    codes: list[ProjectCode] = []
    for code, wd in found:
        item = ProjectCode(id=code if per_code[code] == 1 else f"{code}/{wd.name}", code=code, wd=wd)
        targets: set[str] = set()
        bands: set[str] = set()
        for child in _iter_dirs(wd, 2):
            rel = os.path.relpath(child, wd)
            match = _match_any(band_res, rel)
            if match:
                if match.groupdict().get("band"):
                    bands.add(match.group("band"))
                if match.groupdict().get("target"):
                    targets.add(match.group("target"))
            if _match_any(tpl_res, rel):
                item.templates.extend(sorted(f"{rel}/{p.name}" for p in child.glob("*.inp")))
        meta = wd / meta_dir
        if meta.is_dir():
            item.meta_files = sorted(p.name for p in meta.iterdir() if p.is_file())
            targets |= _meta_targets(item.meta_files)
        item.bands = sorted(bands)
        item.targets = sorted(targets)
        codes.append(item)
    return codes


def result_csvs(root: str | Path, target_dir: Path, patterns: Mapping[str, list[str]] | None = None) -> list[dict[str, str]]:
    """Result CSVs matching ``result_csv`` (default ``{target_dir}/{target}_result.csv``)."""
    base = Path(root).resolve()
    pats = patterns or {k: _as_list(v) for k, v in DEFAULT_PATTERNS.items()}
    regexes = [pattern_regex(p, {"target_dir": os.path.relpath(target_dir, base)}) for p in pats["result_csv"]]
    if not target_dir.is_dir():
        return []
    out = []
    for path in sorted(target_dir.rglob("*.csv")):
        if len(path.relative_to(target_dir).parts) > 3:
            continue
        match = _match_any(regexes, os.path.relpath(path, base))
        if match:
            out.append({"file": os.path.relpath(path, base), "target": match.groupdict().get("target") or ""})
    return out


def logs_listing(root: Path, dirname: str = LOGS_DIRNAME) -> list[dict[str, Any]]:
    logs = root / dirname
    if not logs.is_dir():
        return []
    items = []
    for path in sorted(logs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file():
            kind = "crash" if path.name.startswith("avica_crash_") else "log"
            step = path.stem.removeprefix("avica_crash_") if kind == "crash" else None
            items.append({"name": path.name, "size": path.stat().st_size, "mtime": path.stat().st_mtime, "kind": kind, "step": step})
    return items


def scan_layout(root: str | Path, config: AvicaConfig | None = None) -> dict[str, Any]:
    """Describe everything the Studio can show for one ALFRD project root."""
    base = Path(root).resolve()
    cfg = config or resolve_config(base)
    target_dir = resolve_dir(base, cfg.get("target_dir")) or base / "reductions"
    update_dir = resolve_dir(base, cfg.get("picard_input_template_update"))
    patterns = layout_patterns(base)
    codes = scan_project_codes(base, target_dir, patterns)
    detected_update = None
    if not update_dir:
        candidates = [p for p in base.iterdir() if p.is_dir() and re.search(r"input_temp.*update", p.name)]
        detected_update = os.path.relpath(candidates[0], base) if candidates else None
    return {
        "root": str(base),
        "config": cfg.to_dict(),
        "target_dir": os.path.relpath(target_dir, base) if target_dir.exists() else str(cfg.get("target_dir")),
        "target_dir_exists": target_dir.is_dir(),
        "result_csvs": result_csvs(base, target_dir, patterns),
        "patterns": patterns,
        "project_codes": [code.to_dict(base) for code in codes],
        "logs_dir": str(manifest_avica(base).get("logs") or LOGS_DIRNAME),
        "logs": logs_listing(base, str(manifest_avica(base).get("logs") or LOGS_DIRNAME)),
        "picard_input_template_update": os.path.relpath(update_dir, base) if update_dir and update_dir.is_dir() else None,
        "picard_input_template_update_detected": detected_update,
    }


def _read_meta_file(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    entry: dict[str, Any] = {"name": path.name, "size": size}
    match = _META_TARGET.match(path.name)
    if match:
        entry.update(kind=match.group("kind"), band=match.group("band"), target=match.group("target"))
    else:
        entry["kind"] = path.stem
    if size > _MAX_TEXT:
        entry["error"] = "file too large to preview"
        return entry
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix in {".avica", ".json"}:
        try:
            entry["data"] = json.loads(text)
            entry["format"] = "json"
            return entry
        except json.JSONDecodeError as error:
            entry["error"] = f"invalid JSON: {error}"
    entry["format"] = "text"
    entry["text"] = text
    return entry


def read_workdir(root: str | Path, code: str, target: str | None = None, config: AvicaConfig | None = None) -> dict[str, Any]:
    """Read ``avica.meta`` and rPicard templates for one work dir (id ``CODE`` or ``CODE/wd_1``)."""
    base = Path(root).resolve()
    cfg = config or resolve_config(base)
    target_dir = resolve_dir(base, cfg.get("target_dir")) or base / "reductions"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*(/[A-Za-z0-9][A-Za-z0-9._+-]*)?", code or ""):
        raise ValueError("invalid project code")
    patterns = layout_patterns(base)
    entry = next((c for c in scan_project_codes(base, target_dir, patterns) if c.id == code or (c.code == code and "/" not in code)), None)
    if entry is None:
        raise FileNotFoundError(f"no work dir for {code} under {target_dir}")
    wd = _within(base, entry.wd)
    meta_dir_name = patterns["meta_dir"][0]

    meta: list[dict[str, Any]] = []
    meta_dir = wd / meta_dir_name
    if meta_dir.is_dir():
        for path in sorted(meta_dir.iterdir(), key=lambda p: p.name):
            if not path.is_file():
                continue
            item = _read_meta_file(path)
            # Keep shared files and files for this target only.
            if target and item.get("target") and item["target"] != target:
                continue
            meta.append(item)

    update_dir = resolve_dir(base, cfg.get("picard_input_template_update"))
    updates = {}
    if update_dir and update_dir.is_dir():
        for name in PICARD_INP_FILES:
            values = read_key_values(update_dir / name)
            if values:
                updates[name] = values

    tpl_res = [pattern_regex(p, {"target": target} if target else None) for p in patterns["input_templates"]]
    templates = []
    for folder in _iter_dirs(wd, 2):
        if not _match_any(tpl_res, os.path.relpath(folder, wd)):
            continue
        for name in PICARD_INP_FILES:
            path = folder / name
            if path.is_file():
                templates.append({
                    "folder": os.path.relpath(folder, base),
                    "file": name,
                    "values": read_key_values(path),
                    "updated": dict(updates.get(name, {})),
                })
    return {
        "code": entry.code,
        "id": entry.id,
        "target": target,
        "wd": os.path.relpath(wd, base),
        "bands": entry.bands,
        "meta": meta,
        "templates": templates,
        "template_update": {"folder": os.path.relpath(update_dir, base) if update_dir and update_dir.is_dir() else None, "files": updates},
    }


def read_log(root: str | Path, name: str, tail: int = 400_000) -> str:
    base = Path(root).resolve()
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("invalid log name")
    path = _within(base, base / str(manifest_avica(base).get("logs") or LOGS_DIRNAME) / name)
    if not path.is_file():
        raise FileNotFoundError(name)
    with path.open("rb") as stream:
        size = path.stat().st_size
        if size > tail:
            stream.seek(size - tail)
        return stream.read().decode("utf-8", errors="replace")


def collect_studio_files(root: str | Path, log_tail: int = 64 * 1024) -> dict[str, Any]:
    """Collect the files the Studio reads for one ALFRD project root.

    Same selection as the browser's targeted folder scan: alfrd.yaml, the AVICA
    config and summary cache, root dataset tables, ``result_csv`` files, every
    work dir's ``avica.meta/`` and input templates (band dirs as markers),
    ``picard_input_template_update`` and ``avica.logs/`` (crash snapshots in
    full, the last ``log_tail`` bytes of each log). Import the JSON in the
    Studio when the reduction tree lives on another machine.
    """
    import datetime as _dt

    base = Path(root).resolve()
    manifest = next((base / n for n in ("alfrd.yaml", ".alfrd.yaml") if (base / n).is_file()), None)
    if manifest is None:
        raise FileNotFoundError(f"no alfrd.yaml in {base}")
    block = manifest_avica(base)
    files: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: Path, limit: int | None = _MAX_TEXT, tail: bool = False, hint: str | None = None) -> None:
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        if rel in seen or not path.is_file():
            return
        seen.add(rel)
        size = path.stat().st_size
        item: dict[str, Any] = {"rel": rel, "size": size}
        if hint:
            item["hint"] = hint
        if tail:
            with path.open("rb") as stream:
                if size > limit:
                    stream.seek(size - limit)
                item["text"] = stream.read().decode("utf-8", errors="replace")
        elif limit is None or size <= limit:
            item["text"] = path.read_text(encoding="utf-8", errors="replace")
        files.append(item)

    add(manifest)
    add(base / str(block.get("config") or CONFIG_FILENAME))
    for name in [block.get("config_summary_cache"), *SUMMARY_FILENAMES]:
        if name:
            add(base / str(name), hint="summary")
    for path in sorted(base.glob("*.csv")) + sorted(base.glob("*.tsv")):
        add(path)

    cfg = resolve_config(base)
    target_dir = resolve_dir(base, cfg.get("target_dir")) or base / "reductions"
    patterns = layout_patterns(base)
    for item in result_csvs(base, target_dir, patterns):
        add(base / item["file"])
    meta_dir = patterns["meta_dir"][0]
    band_res = [pattern_regex(p) for p in patterns["band_dir"]]
    for code in scan_project_codes(base, target_dir, patterns):
        for name in code.meta_files:
            add(code.wd / meta_dir / name, limit=_MAX_TEXT)
        for rel in code.templates:
            add(code.wd / rel, limit=512 * 1024)
        for child in _iter_dirs(code.wd, 2):
            if _match_any(band_res, os.path.relpath(child, code.wd).replace(os.sep, "/")):
                marker = os.path.relpath(child, base).replace(os.sep, "/") + "/.dir"
                files.append({"rel": marker, "size": 0, "marker": True})

    update_dir = resolve_dir(base, cfg.get("picard_input_template_update"))
    update_dirs = [update_dir] if update_dir else [p for p in base.iterdir() if p.is_dir() and re.search(r"input_temp.*update", p.name)]
    for folder in update_dirs:
        if folder and folder.is_dir():
            for path in sorted(folder.glob("*.inp")):
                add(path, limit=512 * 1024)

    logs = base / str(block.get("logs") or LOGS_DIRNAME)
    if logs.is_dir():
        for path in sorted(logs.iterdir()):
            if re.match(r"avica_crash_.*\.json$", path.name):
                add(path)
            elif path.suffix == ".log" and log_tail > 0:
                add(path, limit=log_tail, tail=True)

    # Step logs and log artifacts declared in alfrd.yaml (names/sizes; read on demand).
    from alfrd.studio_defs import collect_log_files, collect_ms_paths, studio_context

    studio = studio_context(base)
    by_rel = {f["rel"]: f for f in files}
    for item in collect_log_files(base, studio):
        info = {k: item[k] for k in ("groups", "steps", "band", "target", "workdir") if item.get(k)}
        if item["rel"] in by_rel:
            by_rel[item["rel"]]["log"] = info
            by_rel[item["rel"]]["mtime"] = item["mtime"]
        else:
            entry = {"rel": item["rel"], "size": item["size"], "mtime": item["mtime"], "log": info}
            files.append(entry)
            by_rel[item["rel"]] = entry
    return {
        "alfrd_avica_scan": 1,
        "root": str(base),
        "root_name": base.name,
        "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "target_dir": os.path.relpath(target_dir, base),
        "files": files,
        "ms_paths": collect_ms_paths(base, studio),
    }


__all__ = [
    "AVICA_STEPS",
    "AvicaConfig",
    "collect_studio_files",
    "DEFAULT_CONFIG",
    "DEFAULT_PATTERNS",
    "layout_patterns",
    "load_summary_cache",
    "manifest_avica",
    "pattern_regex",
    "parse_config_summary",
    "read_key_values",
    "read_log",
    "read_workdir",
    "resolve_config",
    "run_config_summary",
    "scan_layout",
    "scan_project_codes",
    "write_summary_cache",
]
