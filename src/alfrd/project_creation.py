"""Create project files and register them through RuntimeService."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from alfrd.agent_loop import DEFAULT_ITERATIONS, MAX_ITERATIONS, project_file
from alfrd.manifest import parse_manifest
from alfrd.studio_defs import template_path
from alfrd.runtime.identity import project_identifier


def create_project(service, path: str | Path, *, name: str | None = None,
                   template: str = "basic", task: str = "", iterations: int = DEFAULT_ITERATIONS):
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
        if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= MAX_ITERATIONS:
            raise ValueError(f"iterations must be an integer from 1 to {MAX_ITERATIONS}")
        if not task.strip():
            raise ValueError("an agent-loop project needs an initial task")
        data["workflows"][0]["repeat"]["iterations"] = iterations
    parse_manifest(data)
    files = {"alfrd.yaml": yaml.safe_dump(data, sort_keys=False)}
    if looping:
        ordered = data["workflows"][0]["steps"]
        first = ordered[0]
        first_agent = first.get("entrypoint") or first["id"]
        files["task.md"] = task.rstrip() + "\n"
        for step in ordered:
            for filename in step.get("handoff", {}).values():
                project_file(root, filename)
                files[filename] = f"# Awaiting the first {first_agent} plan\n"
        files[first["handoff"]["input"]] = task.rstrip() + "\n"
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
            plan_csv.create(cfg.plan_csv, [{"target": "task"}], cfg.step_ids, cfg.step_ids,
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
    return project, workflows
