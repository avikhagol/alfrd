"""Create project files and register them through RuntimeService."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from alfrd.agent_loop import DEFAULT_ITERATIONS, MAX_ITERATIONS, MAX_TURNS, expand_steps, is_sequence
from alfrd.manifest import parse_manifest
from alfrd.manifest_paths import MANIFEST_FILENAME, NESTED_MANIFEST
from alfrd.studio_defs import template_path
from alfrd.runtime.identity import project_identifier



def task_name(value: str) -> str:
    from alfrd.agent_loop import validate_target
    return validate_target(value)


def task_files(root: Path, steps, target: str, task: str) -> dict[str, str]:
    """Seed one task using the manifest's handoff layout (legacy paths stay valid)."""
    from alfrd.agent_loop import resolve_handoff
    target = task_name(target)
    if not isinstance(task, str) or not task.strip() or len(task.encode()) > 1024 * 1024:
        raise ValueError("task must be nonempty Markdown smaller than 1 MiB")
    first = steps[0]
    handoffs = [step.get("handoff", {}) if isinstance(step, dict) else step.handoff for step in steps]
    scoped = any("{target}" in v for handoff in handoffs for v in handoff.values())
    first_agent = (first.get("entrypoint") or first["id"]) if isinstance(first, dict) else first.entrypoint or first.base_step
    files = {f"{target}/task.md" if scoped else "task.md": task.rstrip() + "\n"}
    for handoff in handoffs:
        for filename in handoff.values():
            path = resolve_handoff(root, filename, target)
            files[path.relative_to(root).as_posix()] = f"# Awaiting the first {first_agent} plan\n"
    path = resolve_handoff(root, handoffs[0]["input"], target)
    files[path.relative_to(root).as_posix()] = task.rstrip() + "\n"
    return files


STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")  # mirrors web/js/data/workflow_edit.js STEP_ID
MAX_DRAFT_STEPS = 100
MAX_DRAFT_TARGETS = 200
_DRAFT_STEP_FIELDS = {"id", "label", "category", "description", "entrypoint", "cmd", "depends_on", "role"}


def _text(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\n" in value:
        raise ValueError(f"{what} must be one line of 1–{limit} characters")
    return value.strip()


def apply_draft(data: dict[str, Any], draft: Any, *, looping: bool) -> list[str]:
    """Apply a Studio canvas draft (``{steps?, targets?}``) to the template data; the initial target names.

    ``steps`` replaces the first workflow's steps (each ``{id, label?, category?, description?,
    entrypoint? | cmd?, role?, depends_on}``; dependencies are explicit, checked for unknown steps and
    cycles). An agent-loop template takes its agents as ``sequence`` instead, and at most one target:
    the first task's name (``loop.task_row``). Raises ValueError with every problem found first.
    """
    if draft is None:
        return []
    if not isinstance(draft, Mapping) or set(draft) - {"steps", "targets"}:
        raise ValueError("draft accepts steps and targets")
    from alfrd.runtime.plan_csv import check_target

    raw_targets = draft.get("targets") or []
    if not isinstance(raw_targets, list) or len(raw_targets) > MAX_DRAFT_TARGETS:
        raise ValueError(f"draft.targets must be a list of at most {MAX_DRAFT_TARGETS} names")
    targets: list[str] = []
    for value in raw_targets:
        if not isinstance(value, str):
            raise ValueError("draft.targets must be names")
        target = check_target(value)
        if "/" in target or "\\" in target:
            raise ValueError(f"target {target!r} cannot contain / or \\")
        if target in targets:
            raise ValueError(f"target {target!r} is listed twice")
        targets.append(target)
    if looping and targets:
        if len(targets) > 1:
            raise ValueError("an agent-loop project starts with one task; add more from the Target picker later")
        data.setdefault("loop", {})["task_row"] = task_name(targets[0])
    steps = draft.get("steps")
    if steps is None:
        return targets
    if looping:
        raise ValueError("an agent-loop project takes its agents as a sequence, not as draft steps")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_DRAFT_STEPS:
        raise ValueError(f"draft.steps must list 1–{MAX_DRAFT_STEPS} steps")
    entrypoints = {str(e.get("name")) for e in data.get("entrypoint") or [] if isinstance(e, Mapping) and e.get("name")}
    personas = set(((data.get("project_settings") or {}).get("personas") or {}))
    ids = [s.get("id") if isinstance(s, Mapping) else None for s in steps]
    errors: list[str] = []
    seen: set[str] = set()
    for sid in ids:
        if not isinstance(sid, str) or not STEP_ID.match(sid):
            errors.append(f"step id {sid!r} must be letters, digits, '.', '_' or '-' (starting with a letter or digit)")
        elif sid in seen:
            errors.append(f"step {sid!r} is listed twice")
        else:
            seen.add(sid)
    if errors:
        raise ValueError("; ".join(errors))
    defs = data.get("steps") if isinstance(data.get("steps"), dict) else {}
    items = []
    for raw in steps:
        sid = raw["id"]
        unknown = set(raw) - _DRAFT_STEP_FIELDS
        if unknown:
            errors.append(f"step {sid!r}: unknown field(s) {', '.join(sorted(unknown))}")
            continue
        spec = dict(defs.get(sid) or {})
        for field, limit in (("label", 120), ("category", 60)):
            if raw.get(field) not in (None, ""):
                spec[field] = _text(raw[field], f"step {sid!r} {field}", limit)
        if raw.get("description") not in (None, ""):
            if not isinstance(raw["description"], str) or len(raw["description"]) > 2000:
                errors.append(f"step {sid!r}: description must be text of at most 2000 characters")
            else:
                spec["description"] = raw["description"]
        if raw.get("entrypoint") and raw.get("cmd"):
            errors.append(f"step {sid!r}: give an entrypoint or a command, not both")
        if raw.get("entrypoint"):
            if raw["entrypoint"] not in entrypoints:
                errors.append(f"step {sid!r}: entrypoint {raw['entrypoint']!r} is not one of the template's "
                              f"({', '.join(sorted(entrypoints)) or 'none'})")
            spec["entrypoint"] = raw["entrypoint"]
            spec.pop("cmd", None)
        if raw.get("cmd"):
            cmd = raw["cmd"]
            if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) and a for a in cmd) or len(cmd) > 200:
                errors.append(f"step {sid!r}: cmd must be a non-empty list of arguments (no shell)")
            spec["cmd"] = cmd
            spec.pop("entrypoint", None)
        if raw.get("role"):
            if raw["role"] not in personas:
                errors.append(f"step {sid!r}: persona {raw['role']!r} is not in project_settings.personas")
            spec["role"] = raw["role"]
        deps = raw.get("depends_on") or []
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errors.append(f"step {sid!r}: depends_on must be a list of step ids")
            deps = []
        for dep in deps:
            if dep == sid:
                errors.append(f"step {sid!r} depends on itself")
            elif dep not in seen:
                errors.append(f"step {sid!r} depends on unknown step {dep!r}")
        if spec:
            defs[sid] = spec
        items.append({"id": sid, "depends_on": list(dict.fromkeys(deps))})
    if errors:
        raise ValueError("; ".join(errors))
    from alfrd.orchestration import find_cycle

    cycle = find_cycle({i["id"]: i["depends_on"] for i in items})
    if cycle:
        raise ValueError("dependency cycle: " + " → ".join(cycle))
    first = data.get("workflows")[0] if isinstance(data.get("workflows"), list) and data["workflows"] else {}
    name = first.get("name") if isinstance(first, Mapping) and first.get("name") else "main"
    data["workflows"] = [{**(first if isinstance(first, Mapping) else {}), "name": name, "steps": items}]
    data["steps"] = defs
    return targets


def create_project(service, path: str | Path, *, name: str | None = None,
                   template: str = "basic", task: str = "", iterations: int = DEFAULT_ITERATIONS,
                   sequence: list | None = None, manifest: str = NESTED_MANIFEST, draft: Any = None):
    """Create and register a project from a template.

    ``manifest`` is where the new alfrd.yaml goes, relative to the project:
    ``.alfrd/alfrd.yaml`` (the default, CLI and Studio) or a root ``alfrd.yaml``;
    see alfrd.manifest_paths for the precedence when reading. ``draft`` is the Studio's
    New-project canvas (see :func:`apply_draft`): initial workflow steps and targets.
    """
    if manifest not in (MANIFEST_FILENAME, NESTED_MANIFEST):
        raise ValueError(f"manifest must be {MANIFEST_FILENAME!r} or {NESTED_MANIFEST!r}")
    root = Path(path).expanduser().resolve()
    title = (name or root.name).strip()
    if not title or len(title) > 200:
        raise ValueError("project name must contain 1–200 characters")
    source = template_path(template)
    if source is None:
        raise ValueError(f"unknown project template: {template}")
    data = copy.deepcopy(yaml.safe_load(source.read_text()))
    data.update(version=1, name=title, template=template)
    looping = bool(data.get("workflows") and data["workflows"][0].get("repeat"))
    if looping:
        workflow = data["workflows"][0]
        limit = MAX_TURNS if is_sequence(workflow) else MAX_ITERATIONS
        if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= limit:
            raise ValueError(f"iterations must be an integer from 1 to {limit}")
        if not task.strip():
            raise ValueError("an agent-loop project needs an initial task")
        if sequence is not None:
            if not is_sequence(workflow):
                raise ValueError("this template does not take an agent sequence")
            names = {str(e.get("name")) for e in data.get("entrypoint") or [] if isinstance(e, dict)}
            names |= {str(st.get("id")) for st in workflow.get("steps") or [] if isinstance(st, dict)}
            flat = [a for p in (sequence if sequence and all(isinstance(p, list) for p in sequence) else [sequence]) for a in (p or [])]
            unknown = sorted({str(a) for a in flat} - names)
            if not flat or unknown:
                raise ValueError("sequence agents must be entrypoints: " + (", ".join(unknown) if unknown else "none given"))
            workflow["repeat"]["sequence"] = sequence
        workflow["repeat"].pop("passes", None)
        workflow["repeat"]["iterations"] = iterations
    targets = apply_draft(data, draft, looping=looping)
    if not looping:
        from alfrd.studio_defs import check_workflow_dependencies

        check_workflow_dependencies(data)
    parse_manifest(data)
    from alfrd.yaml_text import dump
    from alfrd.manifest_paths import MANIFEST_CANDIDATES
    files = {manifest: dump(data)}
    if looping:
        workflow = data["workflows"][0]
        if is_sequence(workflow):
            try:
                ordered = [{"id": key, **spec} for key, spec in expand_steps(workflow, {}).items()]
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        else:
            ordered = workflow["steps"]
        files.update(task_files(root, ordered, data.get("loop", {}).get("task_row", "task"), task))
    existing = [name for name in files if (root / name).exists() or (root / name).is_symlink()]
    existing += [rel for rel in MANIFEST_CANDIDATES if rel not in files and ((root / rel).exists() or (root / rel).is_symlink())]
    if existing:
        raise FileExistsError("project files already exist: " + ", ".join(existing))
    identifier = project_identifier(root, title)
    from alfrd.runtime import RuntimeNotFound
    try:
        service.get_project_by_identifier(identifier)
    except RuntimeNotFound:
        pass
    else:
        raise FileExistsError("project is already registered")
    taken = service.get_project_by_root(root)
    if taken is not None:
        raise FileExistsError(f"this folder already holds project {taken.name!r}; one folder holds one ALFRD project")
    was_dir = root.is_dir()
    root.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for filename, text in files.items():
            destination = root / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(text)
            written.append(destination)
        from alfrd.execution import load_execution
        from alfrd.runtime import plan_csv

        cfg = load_execution(root)
        if looping:
            if cfg.plan_csv.exists():
                raise FileExistsError(f"{cfg.plan_csv.name} already exists")
            plan_csv.create(cfg.plan_csv, [{"target": data.get("loop", {}).get("task_row", "task"), "files": "task.md"}], cfg.step_ids, cfg.step_ids,
                            key_column=cfg.key_column, files_column=cfg.files_column,
                            code_column=cfg.code_column, workdir_column=cfg.workdir_column)
            written.append(cfg.plan_csv)
        elif targets:
            from alfrd import targets_csv as tc

            spec = tc.load_spec(root)
            spec.csv.parent.mkdir(parents=True, exist_ok=True)
            with spec.csv.open("x", encoding="utf-8", newline="") as stream:
                stream.write(tc.dump(spec, [{"target": t, "files": "", "code": "", "extra": {}} for t in targets]))
            written.append(spec.csv)
        project, workflows = service.register_manifest(root / manifest, create_root=False)
    except Exception:
        for destination in reversed(written):
            destination.unlink(missing_ok=True)
        try:  # the .alfrd/ folder made for the manifest, when nothing else is in it
            (root / ".alfrd").rmdir()
        except OSError:
            pass
        if not was_dir:
            try:
                root.rmdir()
            except OSError:
                pass
        raise
    if looping and cfg.loop_workspace == "worktree" and any("{target}" in v for step in cfg.steps for v in step.handoff.values()):
        from alfrd import workspaces
        try:  # in an existing repository the first task gets its worktree now; otherwise at its first plan
            workspaces.ensure(root, data.get("loop", {}).get("task_row", "task"))
        except (ValueError, OSError):
            pass
    return project, workflows
