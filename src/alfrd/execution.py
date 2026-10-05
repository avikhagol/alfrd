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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
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
    "max_runtime": None,          # optional plan wall-clock limit, including pauses
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
    stdin_file: str | None = None
    output_file: str | None = None
    output_capture: str = "file"
    cwd: str | None = None
    handoff: Mapping[str, str] = field(default_factory=dict)
    iteration: int = 0
    iterations: int = 0
    base_step: str = ""
    first_turn: bool = False
    final_turn: bool = False
    manual: bool = False
    human_review: bool = False
    model: str | None = None
    claude_stream: bool = False
    agent: bool = False                # an agent step (claude/codex command, or category: Agent) outside a loop too
    roles: tuple[Mapping[str, str], ...] = ()
    next_roles: tuple[str, ...] = ()
    next_agent: str = ""
    loop_options: Mapping[str, Any] = field(default_factory=dict)
    introduce_roles: bool = False      # full persona instructions (first appearance) instead of the summary
    adapter: str = "generic"
    model_option: str | None = None    # a generic agent's model flag (entrypoint ``model_option``)
    fallback_models: tuple[str, ...] = ()
    after: float | None = None         # seconds to wait after the previous step finishes
    at: str | None = None              # or a clock time: "02:00" (next 02:00) / "2026-10-07 02:00"
    turn: int = 0


@dataclass
class ExecutionConfig:
    root: Path
    settings: dict[str, Any]
    entrypoints: dict[str, tuple[str, ...]]
    workflow: str
    steps: list[StepCommand]
    aliases: dict[str, Any]
    primary_key: str
    loop_max: int = 0               # project maximum: turns (sequence) or passes (legacy repeat)
    loop_unit: str = ""             # "turns" or "iterations" (legacy passes); "" without a loop
    loop_workspace: str = "shared"  # "worktree": each task works in {target}/workspace

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
                {"id": s.id, "entrypoint": s.entrypoint, "argv": list(s.argv) if s.argv else None, "timeout": s.timeout,
                 "handoff": dict(s.handoff), "iteration": s.iteration, "manual": s.manual,
                 "human_review": s.human_review, "model": s.model, "roles": [dict(r) for r in s.roles],
                 "turn": s.turn, "adapter": s.adapter, "fallback_models": list(s.fallback_models), "after": s.after}
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

    merged = studio_manifest(root, strict=True)  # a broken alfrd.yaml is an error, not "no steps"
    manifest, _path, _default = manifest_data(root)
    name = template_name(manifest)
    template = _load_yaml(template_path(name)) if name and template_path(name) else {}
    entries = {str(e["name"]): copy.deepcopy(e) for e in template.get("entrypoint") or [] if isinstance(e, Mapping) and e.get("name")}
    entries.update({str(e["name"]): copy.deepcopy(e) for e in manifest.get("entrypoint") or [] if isinstance(e, Mapping) and e.get("name")})
    merged["entrypoint"] = list(entries.values())
    merged["execution"] = {**copy.deepcopy(template.get("execution") or {}), **copy.deepcopy(manifest.get("execution") or {})}
    workflows = manifest.get("workflows") or template.get("workflows")
    merged["_workflow"] = workflows[0] if isinstance(workflows, list) and workflows and isinstance(workflows[0], Mapping) else {}
    if isinstance(workflows, Mapping) and workflows:
        key, first = next(iter(workflows.items()))
        merged["_workflow"] = {"name": key, **(first if isinstance(first, Mapping) else {})}
    return merged


def load_execution(root: str | Path, *, iterations: int | None = None, cap: bool = False) -> ExecutionConfig:
    """``iterations`` overrides the loop length (a task's own count); ``cap`` also refuses more than the project maximum."""
    base = Path(root).expanduser().resolve()
    try:
        merged = merged_manifest(base)
    except ValueError as exc:
        raise ExecutionError(str(exc)) from exc
    if iterations is not None:
        from alfrd.agent_loop import expand_steps, is_sequence, workflow_turns
        workflow = copy.deepcopy(merged.get("_workflow") or {})
        if not workflow.get("repeat"):
            raise ExecutionError("task iterations require a repeated workflow")
        try:
            maximum = workflow_turns(workflow)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        from alfrd.agent_loop import MAX_ITERATIONS, MAX_TURNS
        limit = maximum if cap else MAX_TURNS if is_sequence(workflow) else MAX_ITERATIONS
        if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= limit:
            unit = "turns" if is_sequence(workflow) else "iterations"
            raise ExecutionError(f"task {unit} must be an integer from 1 to {'the project maximum of ' if cap else ''}{limit}")
        workflow["repeat"]["iterations"] = iterations
        workflow["repeat"].pop("passes", None)
        originals = merged.get("_base_steps")
        if originals is None:
            originals = {}
            for key, step in merged["steps"].items():
                if step.get("iteration") == 1:
                    spec = copy.deepcopy(step)
                    spec["label"] = re.sub(r"^1/\d+ · ", "", spec.get("label", ""))
                    originals[spec["base_step"]] = spec
        try:
            merged["steps"] = expand_steps(workflow, originals)
        except ValueError as exc:
            raise ExecutionError(str(exc)) from exc
        merged["step_order"] = list(merged["steps"])
        merged["_project_workflow"] = merged.get("_workflow")
        merged["_workflow"] = workflow
    project_settings = merged.get("project_settings", {})
    if not isinstance(project_settings, Mapping):
        raise ExecutionError("project_settings must be a mapping")
    personas = project_settings.get("personas", {})
    if not isinstance(personas, Mapping):
        raise ExecutionError("project_settings.personas must be a mapping")
    for key, persona in personas.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", key):
            raise ExecutionError("persona key must match [a-z0-9][a-z0-9_-]{0,39}")
        if not isinstance(persona, Mapping) or (not {"label", "instructions"} <= set(persona) or set(persona) - {"label", "instructions", "summary"}):
            raise ExecutionError(f"persona {key!r} needs only label and instructions, with optional summary")
        label = persona["label"]
        if not isinstance(label, str) or not label.strip() or len(label) > 60 or any(c in label for c in "\r\n"):
            raise ExecutionError(f"persona {key!r} label must be 1–60 characters on one line")
        if not isinstance(persona["instructions"], str) or len(persona["instructions"]) > 4000:
            raise ExecutionError(f"persona {key!r} instructions must be a string of at most 4000 characters")

    for key, persona in personas.items():
        if "summary" in persona and (not isinstance(persona["summary"], str) or len(persona["summary"]) > 1000):
            raise ExecutionError(f"persona {key!r} summary must be a string of at most 1000 characters")

    def role_keys(raw):
        keys = [raw] if isinstance(raw, str) else [] if raw is None else raw
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ExecutionError("role must be a persona key, a list of keys or null")
        for key in keys:
            if key not in personas:
                raise ExecutionError(f"unknown persona {key!r}")
        return list(dict.fromkeys(keys))
    access = (merged.get("project_settings") or {}).get("agent_access") or {}
    if not isinstance(access, Mapping):
        raise ExecutionError("project_settings.agent_access must be a mapping")
    for key in ("folders", "bash_commands"):
        values = access.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            raise ExecutionError(f"agent_access.{key} must be a list of nonempty strings")
    if access.get("sandbox") not in (None, "read-only", "workspace-write"):
        raise ExecutionError("agent_access.sandbox must be read-only or workspace-write")
    access = dict(access)
    access["folders"] = [str((base / Path(p).expanduser()).resolve()) for p in access.get("folders", [])]
    loop_options = merged.get("loop") or {}
    if not isinstance(loop_options, Mapping):
        raise ExecutionError("loop must be a mapping")
    if "task_row" in loop_options and (not isinstance(loop_options["task_row"], str) or not loop_options["task_row"].strip()):
        raise ExecutionError("loop.task_row must be a nonempty string")
    limit = loop_options.get("max_input_chars", 40000)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ExecutionError("loop.max_input_chars must be a positive integer")
    headings = loop_options.get("headings")
    if headings is not None and (not isinstance(headings, list) or not headings or
                                not all(isinstance(h, str) and h.strip() and "\n" not in h for h in headings)):
        raise ExecutionError("loop.headings must be a nonempty list of heading names")
    contract = loop_options.get("contract", {})
    if not isinstance(contract, Mapping) or any(k not in ("first", "middle", "final") or not isinstance(v, str) for k, v in contract.items()):
        raise ExecutionError("loop.contract accepts first, middle and final text")
    progress = loop_options.get("progress", True)
    if not (isinstance(progress, bool) or (isinstance(progress, str) and progress.strip() and "\n" not in progress
                                           and not Path(progress.strip()).is_absolute() and ".." not in Path(progress.strip()).parts)):
        raise ExecutionError("loop.progress must be true, false or a file name in the agent's folder (e.g. PROGRESS.md)")
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
    if settings.get("max_runtime") is not None:
        try:
            import math

            settings["max_runtime"] = float(settings["max_runtime"])
            if not math.isfinite(settings["max_runtime"]) or settings["max_runtime"] <= 0:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ExecutionError("execution.max_runtime must be positive seconds") from exc
    try:
        settings["usage_interval"] = max(0.0, float(settings.get("usage_interval") or 0))
    except (TypeError, ValueError) as exc:
        raise ExecutionError("execution.usage_interval must be a number of seconds") from exc
    entrypoints = _entrypoints(merged.get("entrypoint"))
    workflow = merged.get("_workflow") or {}
    schedule = workflow.get("roles", [])
    if not isinstance(schedule, list):
        raise ExecutionError("workflow.roles must be a list of turn roles")
    for role in schedule:
        role_keys(role)
    raw_steps = workflow.get("steps", [])
    for spec in raw_steps.values() if isinstance(raw_steps, Mapping) else raw_steps:
        if isinstance(spec, Mapping):
            role_keys(spec.get("role"))
    default_entry = workflow.get("entrypoint") or settings.get("step_entrypoint")
    if default_entry and default_entry not in entrypoints:
        raise ExecutionError(f"workflow entrypoint {default_entry!r} is not an entrypoint in alfrd.yaml")
    steps = []
    entries = {e["name"]: e for e in merged.get("entrypoint") or []}
    review_default = (merged.get("project_settings") or {}).get("human_review", False)
    if not isinstance(review_default, bool):
        raise ExecutionError("project_settings.human_review must be true or false")
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
        timeout = spec.get("timeout", entries.get(entry, {}).get("timeout", settings.get("timeout")))
        io = {**entries.get(entry, {}), **spec}
        for key in ("stdin_file", "output_file", "cwd"):
            if io.get(key) is not None and (not isinstance(io[key], str) or not io[key]):
                raise ExecutionError(f"{key} must be a nonempty string")
        if "manual" in io and not isinstance(io["manual"], bool):
            raise ExecutionError("manual must be true or false")
        if io.get("output_capture") == "stdout" and not io.get("output_file"):
            raise ExecutionError("output_capture: stdout requires output_file")
        handoff = io.get("handoff") or {}
        if not isinstance(handoff, Mapping) or (handoff and set(handoff) != {"input", "output"}):
            raise ExecutionError("handoff needs input and output file names")
        if handoff:
            from alfrd.agent_loop import project_file

            for value in handoff.values():
                if not isinstance(value, str) or not value.endswith(".md"):
                    raise ExecutionError("handoff files must be Markdown (.md)")
                project_file(base, value.replace("{target}", "task"))
                if "{" in value.replace("{target}", "") or "}" in value.replace("{target}", ""):
                    raise ExecutionError("handoff supports only the {target} placeholder")
        if io.get("output_capture", "file") not in ("file", "stdout"):
            raise ExecutionError("output_capture must be file or stdout")
        review = io.get("human_review", review_default)
        if not isinstance(review, bool):
            raise ExecutionError("human_review must be true or false")
        model = io.get("model")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ExecutionError("model must be a nonempty string or null")
        from alfrd.agent_io import adapter_for, agent_command

        fallbacks = io.get("fallback_models") or []
        if not isinstance(fallbacks, list) or not all(isinstance(m, str) and m.strip() for m in fallbacks):
            raise ExecutionError(f"step {sid!r}: fallback_models must be a list of model names")
        if io.get("model_option") is not None and (not isinstance(io["model_option"], str) or not re.fullmatch(r"--?[A-Za-z0-9][A-Za-z0-9-]*", io["model_option"])):
            raise ExecutionError(f"step {sid!r}: model_option must be a command-line option such as --model")
        try:
            adapter = adapter_for(argv, io.get("adapter"), io.get("model_option")) if argv else adapter_for(None, "generic")
        except ValueError as exc:
            raise ExecutionError(f"step {sid!r}: {exc}") from exc
        try:
            at = clock_time(io.get("after")) if is_clock(io.get("after")) else None
            after = parse_delay(io.get("after")) if is_delay(io.get("after")) and at is None else None
        except ValueError as exc:
            raise ExecutionError(f"step {sid!r}: {exc}") from exc
        native = fallbacks[0] if fallbacks and adapter.native_fallback else None
        from alfrd.agent_io import SUPPORTED_AGENTS

        # Agent steps in any workflow get what loop turns get: token usage (Claude's stream), retry notes.
        is_agent = bool(handoff) or adapter.name in SUPPORTED_AGENTS or str(spec.get("category") or "").strip().lower() == "agent"
        stream = (bool(handoff) or (is_agent and bool(io.get("output_file")))) and not io.get("manual", False)
        argv, model, claude_stream = agent_command(argv, model, stream=stream, access=access,
                                                   adapter=adapter, fallback=native) if argv else (None, model, False)
        steps.append(StepCommand(
            id=sid, argv=argv, entrypoint=entry,
            timeout=float(timeout) if timeout not in (None, "", 0) else None,
            env={str(k): str(v) for k, v in {**(entries.get(entry, {}).get("env") or {}), **(spec.get("env") or {})}.items()},
            stdin_file=io.get("stdin_file"), output_file=io.get("output_file"),
            output_capture=io.get("output_capture", "file"), cwd=io.get("cwd"), handoff=dict(handoff),
            iteration=int(spec.get("iteration") or 0), iterations=int(spec.get("iterations") or 0),
            base_step=spec.get("base_step", sid),
            first_turn=sid == (merged.get("step_order") or [None])[0],
            final_turn=sid == (merged.get("step_order") or [None])[-1],
            manual=bool(io.get("manual", False)),
            roles=tuple({"key": key, **personas[key]} for key in role_keys(spec.get("roles", spec.get("role")))),
            human_review=review, model=model, claude_stream=claude_stream, loop_options=dict(loop_options),
            introduce_roles=bool(spec.get("introduce_roles", int(spec.get("iteration") or 0) == 1)),
            adapter=adapter.name, fallback_models=tuple(fallbacks), after=after, at=at, agent=is_agent, turn=int(spec.get("turn") or 0),
            model_option=io.get("model_option"),
        ))
    steps = [replace(step, next_roles=tuple(r["label"] for r in steps[i + 1].roles),
                     next_agent=steps[i + 1].entrypoint or steps[i + 1].base_step)
             if i + 1 < len(steps) else step for i, step in enumerate(steps)]
    if workflow.get("repeat") is not None:
        if settings["mode"] != "step" or settings["concurrency"] != 1:
            raise ExecutionError("repeated workflows require mode: step and concurrency: 1")
        if settings["on_failure"] != "stop_plan" or settings["status_from"] != "exit_code":
            raise ExecutionError("repeated workflows require on_failure: stop_plan and status_from: exit_code")
        if not all(s.handoff and s.stdin_file and s.output_file for s in steps):
            raise ExecutionError("repeated agent steps need handoff, stdin_file and output_file")
        if not all(s.stdin_file == "{prompt_file}" and s.output_file == "{response_file}" for s in steps):
            raise ExecutionError("repeated steps must use {prompt_file} and {response_file} snapshots")
        if any(s.cwd for s in steps):
            raise ExecutionError("agent loops use execution.cwd for the shared workspace")
    aliases = (merged.get("project_settings") or {}).get("field_aliases") or {}
    loop_max, loop_unit = 0, ""
    if workflow.get("repeat") is not None:
        from alfrd.agent_loop import is_sequence, workflow_turns

        project = merged.get("_project_workflow") or workflow
        loop_max, loop_unit = workflow_turns(project), "turns" if is_sequence(project) else "iterations"
    workspace = loop_options.get("workspace", "shared")
    if workspace not in ("shared", "worktree"):
        raise ExecutionError("loop.workspace must be shared or worktree")
    return ExecutionConfig(
        root=base, settings=settings, entrypoints=entrypoints,
        workflow=str(workflow.get("name") or "workflow"), steps=steps,
        aliases=dict(aliases), primary_key=str(merged.get("primary_key") or "TARGET_NAME"),
        loop_max=loop_max, loop_unit=loop_unit, loop_workspace=workspace,
    )


def load_task_execution(root: str | Path, target: str | None, *, cap: bool = False) -> ExecutionConfig:
    """The project's configuration with one task's own turn count (``{target}/.alfrd-task.json``)."""
    import json

    cfg = load_execution(root)
    if not target or not any("{target}" in v for step in cfg.steps for v in step.handoff.values()):
        return cfg
    from alfrd.agent_loop import project_file, task_iterations, validate_target

    path = project_file(cfg.root, f"{validate_target(target)}/.alfrd-task.json")
    if not path.is_file():
        return cfg
    try:
        options = json.loads(path.read_text(encoding="utf-8"))
        count = task_iterations(options if isinstance(options, dict) else {}, merged_manifest(cfg.root).get("_workflow") or {})
    except ValueError as exc:
        raise ExecutionError(f"{target}/.alfrd-task.json: {exc}") from exc
    return load_execution(root, iterations=count, cap=cap) if count is not None else cfg


MAX_DELAY = 7 * 24 * 3600


_DELAY = re.compile(r"\+?\s*(?:\d+(?:\.\d+)?|(?:\d+d)?(?:\d+h)?(?:\d+m)?(?:\d+s)?)", re.I)


_CLOCK = re.compile(r"(?:(\d{4}-\d{2}-\d{2})[ T])?(\d{1,2}):(\d{2})(?::(\d{2}))?")


def is_clock(value: Any) -> bool:
    """``after: "02:00"`` / ``"2026-10-07 02:00"`` (quoted: YAML reads a bare 02:00 as the number 120)."""
    if isinstance(value, datetime):
        return True
    return isinstance(value, str) and bool(_CLOCK.fullmatch(value.strip()))


def clock_time(value: Any) -> str:
    """A clock ``after`` / start time, checked: ``HH:MM`` or ``YYYY-MM-DD HH:MM`` (at most seven days ahead)."""
    if isinstance(value, datetime):
        value = value.strftime("%Y-%m-%d %H:%M:%S")
    match = _CLOCK.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError("a start time looks like 02:00 or 2026-10-07 02:00")
    day, hour, minute, second = match.groups()
    if int(hour) > 23 or int(minute) > 59 or int(second or 0) > 59:
        raise ValueError(f"{value!s} is not a time of day")
    text = f"{int(hour):02d}:{minute}" + (f":{second}" if second else "")
    if day:
        try:
            when = datetime.strptime(f"{day} {text}", "%Y-%m-%d %H:%M" + (":%S" if second else ""))
        except ValueError as exc:
            raise ValueError(f"{value!s} is not a date") from exc
        if (when - datetime.now()).total_seconds() > MAX_DELAY:
            raise ValueError("a start time must be at most 7 days ahead")
        return f"{day} {text}"
    return text


def next_clock(value: str, after: datetime) -> datetime:
    """When a clock time is reached: a date-time as given; ``HH:MM`` = its first occurrence after ``after``."""
    day, hour, minute, second = _CLOCK.fullmatch(value.strip()).groups()
    if day:
        return datetime.strptime(day, "%Y-%m-%d").replace(hour=int(hour), minute=int(minute), second=int(second or 0))
    when = after.replace(hour=int(hour), minute=int(minute), second=int(second or 0), microsecond=0)
    return when if when > after else when + timedelta(days=1)


def is_delay(value: Any) -> bool:
    """``after:`` is a delay (a number, a duration or a clock time); a step name (or list) means "depends on"."""
    if isinstance(value, bool):
        return False
    if is_clock(value):
        return True
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and bool(value.strip()) and bool(_DELAY.fullmatch(value.strip().replace(" ", "")))


def parse_delay(value: Any) -> float | None:
    """``+1h``, ``90m``, ``2h30m``, ``45s`` or seconds → seconds; at most seven days."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("after must be a duration such as +1h, 90m or 2h30m")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip().lstrip("+").replace(" ", "").lower()
        match = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text)
        if not text or not match or not any(match.groups()):
            if not re.fullmatch(r"\d+(?:\.\d+)?", text):
                raise ValueError("after must be a duration such as +1h, 90m or 2h30m")
            seconds = float(text)
        else:
            d, h, m, sec = (int(g or 0) for g in match.groups())
            seconds = float(d * 86400 + h * 3600 + m * 60 + sec)
    if not 0 <= seconds <= MAX_DELAY:
        raise ValueError("after must be between 0 and 7 days")
    return seconds or None


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
    "clock_time",
    "is_clock",
    "load_execution",
    "next_clock",
    "merged_manifest",
    "placeholders",
    "render",
    "split_files",
]
