"""Execution settings from alfrd.yaml: which command runs a workflow step.

Import-free (reads YAML only). Commands come from ``entrypoint:`` (``name`` +
``cmd`` argv list) and the workflow / its steps pick one::

    entrypoint:
      - {name: avica-step, cmd: [avica, pipe, run, --t, "{target}", --f, "{FILENAMES}", "{step}"]}
    execution:
      mode: step            # step | target | batch
      plan_csv: alfrd.plan.csv
    workflows:
      - name: avica
        entrypoint: avica-step
        steps:
          - {id: rpicard, timeout: 172800}

A template (``template: avica``) supplies defaults for ``entrypoint`` (merged by
name) and ``execution`` (merged key by key); alfrd.yaml always wins. Nothing
here is AVICA specific.

Placeholders are filled per argv item; there is no shell and no word
splitting. A placeholder without a value is an error before anything starts.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

MODES = ("step", "target", "batch")
ON_FAILURE = ("stop_target", "continue", "stop_plan")
STATUS_FROM = ("exit_code", "result_csv", "both")
LAUNCHERS = ("detach", "systemd-run")
AUTO_RESUME = ("adopt", "always", "never")
SERIALIZE_MATCH = ("all", "any")

DEFAULT_EXECUTION: dict[str, Any] = {
    "cwd": ".",                    # relative to alfrd.yaml
    "plan_csv": "alfrd.plan.csv",  # relative to alfrd.yaml
    "mode": "step",
    "concurrency": 1,
    "on_failure": "stop_target",
    "status_from": "exit_code",    # the avica template sets "both"
    "launcher": "detach",
    "auto_resume": "adopt",        # restart the runner on load only to re-adopt live steps
    "timeout": None,               # seconds per command
    "kill_grace": 30,              # SIGTERM → SIGKILL after this many seconds
    "heartbeat": 10,
    "env": {},
    "target_entrypoint": None,     # mode: target
    "batch_entrypoint": None,      # mode: batch
    "key_column": None,            # default: alfrd.yaml primary_key, else TARGET_NAME
    "files_column": "FILENAMES",
    "code_column": "PROJECT_CODE",
    "workdir_column": "WORKDIR",
    # concurrency > 1: rows that share a value in the listed columns run one at a
    # time. Names: ``target`` (the key column), ``files`` (the file column, compared
    # as a set) or any plan CSV column. ``serialize_match: all`` = conflict only when
    # every listed column is shared, ``any`` = when one is. [] = only the work dir lock.
    "serialize_on": [],
    "serialize_match": "all",
    "usage_interval": 5,           # seconds between resource samples (0 = off)
}

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _arg(part: Any) -> str:
    """YAML turns ``true``/``no``/``1.0`` into non-strings; argv wants the text back."""
    if isinstance(part, bool):
        return "true" if part else "false"
    return str(part)


class ExecutionError(ValueError):
    """alfrd.yaml does not describe how to run something."""


class RenderError(ExecutionError):
    """A command template has placeholders without a value."""

    def __init__(self, missing: Sequence[str], argv: Sequence[str]) -> None:
        self.missing = list(dict.fromkeys(missing))
        self.argv = list(argv)
        super().__init__("no value for " + ", ".join("{" + m + "}" for m in self.missing))


@dataclass(frozen=True)
class StepCommand:
    id: str
    argv: tuple[str, ...] | None       # template; None when no entrypoint applies
    entrypoint: str | None
    timeout: float | None = None
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass
class ExecutionConfig:
    root: Path
    settings: dict[str, Any]
    entrypoints: dict[str, tuple[str, ...]]
    workflow: str
    steps: list[StepCommand]
    aliases: dict[str, Any]
    primary_key: str

    @property
    def step_ids(self) -> list[str]:
        return [s.id for s in self.steps]

    def step(self, step_id: str) -> StepCommand:
        for item in self.steps:
            if item.id == step_id:
                return item
        raise ExecutionError(f"unknown step {step_id!r}; workflow {self.workflow!r} has {', '.join(self.step_ids)}")

    @property
    def mode(self) -> str:
        return str(self.settings["mode"])

    @property
    def key_column(self) -> str:
        return str(self.settings.get("key_column") or self.primary_key or "TARGET_NAME")

    @property
    def files_column(self) -> str:
        return str(self.settings["files_column"])

    @property
    def code_column(self) -> str:
        return str(self.settings["code_column"])

    @property
    def workdir_column(self) -> str:
        return str(self.settings["workdir_column"])

    @property
    def cwd(self) -> Path:
        path = Path(str(self.settings.get("cwd") or ".")).expanduser()
        return path if path.is_absolute() else (self.root / path).resolve()

    @property
    def plan_csv(self) -> Path:
        path = Path(str(self.settings.get("plan_csv") or DEFAULT_EXECUTION["plan_csv"])).expanduser()
        return path if path.is_absolute() else self.root / path

    def unit_entrypoint(self, mode: str | None = None) -> tuple[str, tuple[str, ...]] | None:
        """Entrypoint for ``target``/``batch`` modes (one command per target / plan)."""
        mode = mode or self.mode
        name = self.settings.get(f"{mode}_entrypoint")
        if not name:
            return None
        if name not in self.entrypoints:
            raise ExecutionError(f"execution.{mode}_entrypoint {name!r} is not an entrypoint in alfrd.yaml")
        return str(name), self.entrypoints[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "settings": {k: v for k, v in self.settings.items() if k != "env"},
            "env": sorted((self.settings.get("env") or {}).keys()),
            "entrypoints": {k: list(v) for k, v in self.entrypoints.items()},
            "workflow": self.workflow,
            "steps": [
                {"id": s.id, "entrypoint": s.entrypoint, "argv": list(s.argv) if s.argv else None, "timeout": s.timeout}
                for s in self.steps
            ],
            "key_column": self.key_column,
            "files_column": self.files_column,
            "code_column": self.code_column,
            "workdir_column": self.workdir_column,
            "plan_csv": str(self.plan_csv),
            "cwd": str(self.cwd),
        }


def _serialize_on(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ExecutionError("execution.serialize_on must be a list of column names (target, files, PROJECT_CODE, ...)")
    return list(dict.fromkeys(x.strip() for x in raw))


def _entrypoints(raw: Any) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for item in raw or []:
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        cmd = item.get("cmd")
        if isinstance(cmd, str):
            raise ExecutionError(f"entrypoint {item['name']!r}: cmd must be a list (no shell), got a string")
        if not isinstance(cmd, (list, tuple)) or not cmd:
            continue
        out[str(item["name"])] = tuple(_arg(part) for part in cmd)
    return out


def merged_manifest(root: str | Path) -> dict[str, Any]:
    """alfrd.yaml merged with its template, entrypoints merged by name."""
    from alfrd.manifest_default import manifest_data
    from alfrd.studio_defs import _load_yaml, studio_manifest, template_name, template_path

    merged = studio_manifest(root)
    manifest, _path, _default = manifest_data(root)
    name = template_name(manifest)
    template = _load_yaml(template_path(name)) if name and template_path(name) else {}
    entries = {**_entrypoints(template.get("entrypoint")), **_entrypoints(manifest.get("entrypoint"))}
    merged["entrypoint"] = [{"name": k, "cmd": list(v)} for k, v in entries.items()]
    merged["execution"] = {**copy.deepcopy(template.get("execution") or {}), **copy.deepcopy(manifest.get("execution") or {})}
    workflows = manifest.get("workflows") or template.get("workflows")
    merged["_workflow"] = workflows[0] if isinstance(workflows, list) and workflows and isinstance(workflows[0], Mapping) else {}
    if isinstance(workflows, Mapping) and workflows:
        key, first = next(iter(workflows.items()))
        merged["_workflow"] = {"name": key, **(first if isinstance(first, Mapping) else {})}
    return merged


def load_execution(root: str | Path) -> ExecutionConfig:
    base = Path(root).expanduser().resolve()
    merged = merged_manifest(base)
    settings = {**DEFAULT_EXECUTION, **(merged.get("execution") or {})}
    for key, allowed in (("mode", MODES), ("on_failure", ON_FAILURE), ("status_from", STATUS_FROM),
                         ("launcher", LAUNCHERS), ("auto_resume", AUTO_RESUME),
                         ("serialize_match", SERIALIZE_MATCH)):
        if settings[key] not in allowed:
            raise ExecutionError(f"execution.{key} must be one of {', '.join(allowed)} (got {settings[key]!r})")
    try:
        settings["concurrency"] = max(1, int(settings["concurrency"]))
    except (TypeError, ValueError) as exc:
        raise ExecutionError("execution.concurrency must be an integer") from exc
    settings["serialize_on"] = _serialize_on(settings.get("serialize_on"))
    try:
        settings["usage_interval"] = max(0.0, float(settings.get("usage_interval") or 0))
    except (TypeError, ValueError) as exc:
        raise ExecutionError("execution.usage_interval must be a number of seconds") from exc
    entrypoints = _entrypoints(merged.get("entrypoint"))
    workflow = merged.get("_workflow") or {}
    default_entry = workflow.get("entrypoint") or settings.get("step_entrypoint")
    if default_entry and default_entry not in entrypoints:
        raise ExecutionError(f"workflow entrypoint {default_entry!r} is not an entrypoint in alfrd.yaml")
    steps = []
    for sid in merged.get("step_order") or list((merged.get("steps") or {}).keys()):
        spec = (merged.get("steps") or {}).get(sid) or {}
        entry = spec.get("entrypoint") or default_entry
        if spec.get("cmd") is not None:
            cmd = spec["cmd"]
            if isinstance(cmd, str) or not isinstance(cmd, (list, tuple)) or not cmd:
                raise ExecutionError(f"step {sid!r}: cmd must be a non-empty list (no shell)")
            argv: tuple[str, ...] | None = tuple(_arg(p) for p in cmd)
            entry = None
        elif entry:
            if entry not in entrypoints:
                raise ExecutionError(f"step {sid!r}: entrypoint {entry!r} is not an entrypoint in alfrd.yaml")
            argv = entrypoints[entry]
        else:
            argv = None
        timeout = spec.get("timeout", settings.get("timeout"))
        steps.append(StepCommand(
            id=sid, argv=argv, entrypoint=entry,
            timeout=float(timeout) if timeout not in (None, "", 0) else None,
            env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
        ))
    aliases = (merged.get("project_settings") or {}).get("field_aliases") or {}
    return ExecutionConfig(
        root=base, settings=settings, entrypoints=entrypoints,
        workflow=str(workflow.get("name") or "workflow"), steps=steps,
        aliases=dict(aliases), primary_key=str(merged.get("primary_key") or "TARGET_NAME"),
    )


def placeholders(argv: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(m.group(1) for part in argv for m in _PLACEHOLDER.finditer(part)))


def render(argv: Sequence[str], values: Mapping[str, Any]) -> list[str]:
    """Fill ``{name}`` in each argv item. Empty and missing values are errors."""
    missing: list[str] = []

    def repl(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value if str(v))
        if value is None or str(value) == "":
            missing.append(match.group(1))
            return match.group(0)
        return str(value)

    out = [_PLACEHOLDER.sub(repl, str(part)) for part in argv]
    if missing:
        raise RenderError(missing, argv)
    return out


def split_files(value: Any) -> str:
    """FITS file lists may be written ``a,b`` / ``a;b`` / ``a b``; commands get ``a,b``."""
    parts = re.split(r"[,;\s]+", str(value or "").strip())
    return ",".join(p for p in parts if p)


__all__ = [
    "DEFAULT_EXECUTION",
    "ExecutionConfig",
    "ExecutionError",
    "RenderError",
    "StepCommand",
    "load_execution",
    "merged_manifest",
    "placeholders",
    "render",
    "split_files",
]
