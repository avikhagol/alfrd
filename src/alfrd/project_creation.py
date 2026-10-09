"""Create project files and register them through RuntimeService."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from alfrd.agent_loop import DEFAULT_ITERATIONS, MAX_ITERATIONS, MAX_TURNS, expand_steps, is_sequence
from alfrd.manifest import parse_manifest
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


def create_project(service, path: str | Path, *, name: str | None = None,
                   template: str = "basic", task: str = "", iterations: int = DEFAULT_ITERATIONS,
                   sequence: list | None = None):
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
    parse_manifest(data)
    from alfrd.yaml_text import dump
    files = {"alfrd.yaml": dump(data)}
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
    if (root / ".alfrd.yaml").exists():
        existing.append(".alfrd.yaml")
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
        project, workflows = service.register_manifest(root / "alfrd.yaml", create_root=False)
    except Exception:
        for destination in reversed(written):
            destination.unlink(missing_ok=True)
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
