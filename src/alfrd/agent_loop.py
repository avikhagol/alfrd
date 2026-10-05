"""Finite workflow expansion and immutable Markdown handoffs.

Plan JSON/CSV is authoritative; no second agent scheduler or runtime database.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Mapping


DEFAULT_ITERATIONS = 10
MAX_ITERATIONS = 100
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
REQUIRED_HEADINGS = ("Goal", "What was completed", "Files changed", "Checks and results",
                     "Instructions for the next agent", "Acceptance criteria", "Blockers")


class ResponseValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class HandoffConflictError(ValueError):
    """The outgoing handoff changed during the turn; the edit is kept."""


def validate_response(text: str, required=REQUIRED_HEADINGS) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return ["Response is empty"]
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        return ["Response exceeds 10 MiB"]
    headings = {
        re.sub(r"\s+\([^()\n]+\)$", "", m.group(1).strip().lower())
        for m in re.finditer(r"^#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", text, re.M)
    }
    return [f"Missing heading: ## {h}" for h in required if h.lower() not in headings]


def resolve_roles(schedule, turn, spec):
    roles = schedule[turn % len(schedule)] if schedule else spec.get("role")
    return [roles] if isinstance(roles, str) else [] if roles is None else roles


MAX_TURNS = 2 * MAX_ITERATIONS
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
DEFAULT_HANDOFF = "{target}/next-step-{agent}.md"


def is_sequence(workflow: Mapping[str, Any] | None) -> bool:
    """``repeat.sequence`` workflows count turns; legacy ``repeat`` workflows count passes."""
    repeat = (workflow or {}).get("repeat")
    return isinstance(repeat, Mapping) and "sequence" in repeat


def sequence_passes(repeat: Mapping[str, Any]) -> list[list[str]]:
    seq = repeat.get("sequence")
    if not isinstance(seq, list) or not seq:
        raise ValueError("repeat.sequence must be a nonempty list of agents")
    passes = [seq] if all(isinstance(item, str) for item in seq) else seq
    if not all(isinstance(p, list) and p and all(isinstance(i, str) and AGENT_NAME.fullmatch(i) for i in p) for p in passes):
        raise ValueError("repeat.sequence lists agent names, or lists of agent names (one list per pass)")
    return passes


def sequence_agents(repeat: Mapping[str, Any]) -> list[str]:
    """The agent of every turn, plus the agent that would receive the final handoff.

    ``iterations`` is the total number of turns; ``passes`` is shorthand for
    whole passes. The last listed pass repeats.
    """
    passes = sequence_passes(repeat)
    if ("iterations" in repeat) == ("passes" in repeat):
        raise ValueError("repeat.sequence needs exactly one of iterations (total turns) or passes")

    def pass_at(n: int) -> list[str]:
        return passes[min(n, len(passes) - 1)]

    if "passes" in repeat:
        count = repeat["passes"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_ITERATIONS:
            raise ValueError(f"repeat.passes must be an integer from 1 to {MAX_ITERATIONS}")
        total = sum(len(pass_at(i)) for i in range(count))
    else:
        total = repeat["iterations"]
    if isinstance(total, bool) or not isinstance(total, int) or not 1 <= total <= MAX_TURNS:
        raise ValueError(f"repeat.iterations (total turns) must be an integer from 1 to {MAX_TURNS}")
    agents: list[str] = []
    n = 0
    while len(agents) <= total:
        agents.extend(pass_at(n))
        n += 1
    return agents[:total + 1]


def workflow_turns(workflow: Mapping[str, Any]) -> int:
    """The workflow's turn count in its own unit: turns for sequences, passes for legacy repeats."""
    repeat = workflow.get("repeat") or {}
    return len(sequence_agents(repeat)) - 1 if is_sequence(workflow) else int(repeat.get("iterations") or 0)


def turn_overrides(workflow: Mapping[str, Any], step_id: str, turn: int) -> dict[str, Any]:
    """``workflow.turns`` settings for one expanded turn, keyed by turn number or step id."""
    turns = workflow.get("turns") or {}
    if not isinstance(turns, Mapping):
        raise ValueError("workflow.turns must map turn numbers or step ids to settings")
    result: dict[str, Any] = {}
    for key in (turn, str(turn), step_id):
        value = turns.get(key)
        if value is not None:
            if not isinstance(value, Mapping) or set(value) - TURN_SETTINGS:
                raise ValueError(f"workflow.turns.{key} accepts only {', '.join(sorted(TURN_SETTINGS))}")
            result.update(value)
    return result


TURN_SETTINGS = {"human_review", "manual", "model", "fallback_models", "after", "role", "roles"}


def expand_steps(workflow: Mapping[str, Any], steps: Mapping[str, Any]) -> dict[str, Any]:
    repeat = workflow.get("repeat")
    schedule = workflow.get("roles", [])
    if not isinstance(schedule, list):
        raise ValueError("workflow.roles must be a list of turn roles")
    if repeat is None:
        return {key: {**raw, "turn": i + 1, "roles": resolve_roles(schedule, i, raw)} for i, (key, raw) in enumerate(steps.items())}
    if not isinstance(repeat, Mapping):
        raise ValueError("workflow.repeat must contain iterations")
    if is_sequence(workflow):
        return expand_sequence(workflow, steps)
    count = repeat.get("iterations")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_ITERATIONS:
        raise ValueError(f"repeat.iterations must be an integer from 1 to {MAX_ITERATIONS}")
    if not steps:
        raise ValueError("a repeated workflow needs steps")
    out = {}
    for iteration in range(1, count + 1):
        for key, raw in steps.items():
            spec = copy.deepcopy(raw)
            sid = f"i{iteration:03d}-{key}"
            spec.update(turn_overrides(workflow, sid, len(out) + 1))
            spec.update(turn=len(out) + 1, roles=resolve_roles(schedule, len(out), spec))
            spec.update(iteration=iteration, iterations=count, base_step=key)
            spec["label"] = f"{iteration}/{count} · {spec.get('label') or key}"
            out[sid] = spec
    return out


def expand_sequence(workflow: Mapping[str, Any], steps: Mapping[str, Any]) -> dict[str, Any]:
    """One step per turn; handoffs follow the agents, so any order of agents works."""
    repeat = workflow["repeat"]
    schedule = workflow.get("roles", [])
    agents = sequence_agents(repeat)
    total = len(agents) - 1
    pattern = repeat.get("handoff", DEFAULT_HANDOFF)
    if not isinstance(pattern, str) or "{agent}" not in pattern or not pattern.endswith(".md"):
        raise ValueError("repeat.handoff must be a Markdown path containing {agent}")

    def entry(item: str) -> str:
        return str((steps.get(item) or {}).get("entrypoint") or item)

    out: dict[str, Any] = {}
    seen: set[tuple[str, str]] = set()
    for turn in range(1, total + 1):
        item = agents[turn - 1]
        sid = f"t{turn:03d}-{item}"
        spec = {k: copy.deepcopy(v) for k, v in (steps.get(item) or {}).items() if k not in {"id", "key", "name", "handoff"}}
        spec.update(turn_overrides(workflow, sid, turn))
        roles = resolve_roles(schedule, turn - 1, spec)
        introduce = (entry(item), json_key(roles)) not in seen
        seen.add((entry(item), json_key(roles)))
        spec.update(entrypoint=entry(item), turn=turn, roles=roles, iteration=turn, iterations=total, base_step=item,
                    introduce_roles=introduce,
                    handoff={"input": pattern.replace("{agent}", entry(item)),
                             "output": pattern.replace("{agent}", entry(agents[turn]))})
        spec.pop("role", None)
        spec["label"] = f"{turn}/{total} · {spec.get('label') or item}"
        out[sid] = spec
    return out


def json_key(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def task_iterations(options: Mapping[str, Any], workflow: Mapping[str, Any]) -> int | None:
    """A task's ``.alfrd-task.json`` count in the project's unit.

    Version 1 files (no ``version``) count passes; version 2 counts turns.
    """
    value = options.get("iterations")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("task iterations must be a positive integer")
    turns_file = options.get("version") == 2
    if is_sequence(workflow) and not turns_file:
        first = sequence_passes(workflow["repeat"])
        return sum(len(first[min(i, len(first) - 1)]) for i in range(value))
    if not is_sequence(workflow) and turns_file:
        width = max(1, len(workflow.get("steps") or []))
        return -(-value // width)
    return value


def task_options(workflow: Mapping[str, Any], iterations: int) -> dict[str, Any]:
    """What a new ``.alfrd-task.json`` holds for this project."""
    return {"iterations": iterations, "version": 2} if is_sequence(workflow) else {"iterations": iterations}


def project_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("handoff files must be inside the project")
    return path



def validate_target(target: str) -> str:
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}", target):
        raise ValueError("task name must be 1–64 letters, digits, dots, underscores or hyphens, and cannot start with a dot")
    return target


def resolve_handoff(root: Path, name: str, target: str) -> Path:
    if "{target}" in name:
        name = name.replace("{target}", validate_target(target))
    return project_file(root, name)


def compact_input(text: str, limit: int, archive: Path) -> str:
    """Keep actionable sections intact; archive the unabridged incoming handoff."""
    return compact_report(text, limit, archive)[0]


def compact_report(text: str, limit: int, archive: Path) -> tuple[str, dict[str, Any]]:
    """``compact_input`` plus what it did: raw and trimmed lengths and the shortened sections.

    ``sections_truncated`` is ``[]`` when nothing was shortened; text before the
    first heading is reported as ``"(preamble)"``.
    """
    raw_chars = len(text)
    text = re.sub(r"\A\s*<!-- ALFRD plan=.*?-->[ \t]*\n?", "", text, count=1, flags=re.S)
    if len(text) <= limit:
        return text, {"raw_input_chars": raw_chars, "trimmed_handoff_chars": len(text), "sections_truncated": []}
    atomic_text(archive, text)
    marker = f"\n…[truncated by ALFRD; full text in {archive}]\n"
    parts = re.split(r"(?=^## [^\n]+$)", text, flags=re.M)
    protected = {"Goal", "Instructions for the next agent", "Acceptance criteria", "Blockers"}
    keep = []
    names = []
    for part in parts:
        heading = re.match(r"## ([^\n]+)", part)
        names.append(heading.group(1).strip() if heading else "(preamble)")
        keep.append(bool(heading and heading.group(1).strip() in protected))
    fixed = sum(len(part) for part, required in zip(parts, keep) if required)
    optional = sum(not required and bool(part) for part, required in zip(parts, keep))
    if fixed + optional * len(marker) > limit:
        raise ValueError("loop.max_input_chars is too small to preserve the required handoff sections")
    budget = limit - fixed - optional * len(marker)
    result = []
    truncated = []
    for part, required, name in zip(parts, keep, names):
        if required or not part:
            result.append(part)
        else:
            allowance = min(1500, budget // optional)
            excerpt = part[:allowance]
            if len(part) > allowance:
                truncated.append(name)
            result.append(excerpt + marker if len(part) > allowance else part)
            budget -= len(excerpt)
            optional -= 1
    out = "".join(result)
    return out, {"raw_input_chars": raw_chars, "trimmed_handoff_chars": len(out), "sections_truncated": truncated}

def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def progress_file(options: Mapping[str, Any], target: str, own_workspace: bool) -> str | None:
    """The checklist file an agent keeps in its working folder (``loop.progress``; false = off).

    Default: ``PROGRESS.md`` in a task's own worktree, ``PROGRESS-<task>.md`` when tasks share one folder.
    """
    value = options.get("progress", True)
    if value is False or value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip().replace("{target}", target)
    return "PROGRESS.md" if own_workspace else f"PROGRESS-{target}.md"


def retry_note(previous: Mapping[str, Any] | None, attempt: int, progress: str | None) -> str:
    """What a retried turn is told about the attempt before it (its partial work is still on disk)."""
    if not previous:
        return ""
    how = {"interrupted": "was interrupted", "cancelled": "was cancelled", "failed": "failed"}.get(str(previous.get("status")), "did not finish")
    minutes = ""
    try:
        from datetime import datetime

        spent = (datetime.fromisoformat(str(previous["finished"])) - datetime.fromisoformat(str(previous["started"]))).total_seconds()
        minutes = f" after {max(1, round(spent / 60))} min"
    except (KeyError, TypeError, ValueError):
        pass
    error = str(previous.get("error") or previous.get("outcome_reason") or "").strip().splitlines()
    look = f"read {progress} and " if progress else ""
    return (f"\nRETRY: this is attempt {attempt} of this turn. The previous attempt ({previous.get('id')}) {how}{minutes}"
            + (f" ({error[0][:200]})" if error else "") + ". "
            f"Its unfinished work may already be in the files: {look}check `git status` / `git diff` first, "
            "keep what is correct, and continue from where it stopped instead of starting over.\n")


def prepare(root: Path, archive: Path, step, plan_id: str, unit_id: str, target: str = "task", *,
            progress: str | None = None, retry: str = "", timeout: float | None = None) -> dict[str, Any]:
    """Snapshot at launch, never at plan creation: the preceding agent supplies it.

    ``progress``: checklist file to keep (see progress_file); ``retry``: retry_note() text;
    ``timeout``: the turn's time limit in seconds, told to the agent so it hands off in time.
    """
    source = resolve_handoff(root, step.handoff["input"], target)
    destination = resolve_handoff(root, step.handoff["output"], target)
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"{source.name} is empty")
    recipient = step.entrypoint or step.base_step or step.id
    final = step.iteration == step.iterations and step.final_turn
    options = getattr(step, "loop_options", {})
    text, sizes = compact_report(text, options.get("max_input_chars", 40000), archive / "input.md")
    required = options.get("headings", REQUIRED_HEADINGS)
    roles = getattr(step, "roles", ())
    role_header = " role=" + "+".join(r["label"] for r in roles) if roles else ""
    contract = (
        f"\n\n---\nALFRD turn: plan={plan_id}, unit={unit_id}, "
        f"iteration={step.iteration}/{step.iterations}, agent={recipient}{role_header}.\n"
        "Use these exact final-response Markdown headings: "
        + "; ".join(f"## {h}" for h in required) + ". " +
        "ALFRD publishes your response; do not edit next-step files. "
        "Keep the user's scope and repository instructions. Do not commit or push.\n"
    )
    if roles:
        contract += "\nYour role(s) this turn:\n" + "\n".join(
            f"- {r['label']}:\n{r['instructions']}" if getattr(step, "introduce_roles", step.iteration == 1) else
            "- " + r["label"] + ": " + (r.get("summary") or re.split(r"(?<=[.!?])\s+", r["instructions"].strip(), maxsplit=1)[0]) for r in roles) + "\n"
        if not step.final_turn:
            contract += f"Next turn: {step.next_agent}" + (" as " + " + ".join(step.next_roles) if step.next_roles else "") + ".\n"
    phase = "final" if final else "first" if step.iteration == 1 and step.first_turn else "middle"
    defaults = {
        "final": "Execute the incoming task and report files changed and checks performed. This is the final turn: provide a closing report; do not invent another task.",
        "first": "This is the first turn: plan the initial task for the next agent. Inspect as needed; leave implementation and file creation to the next agent.",
        "middle": "Execute the incoming task, report files changed and checks performed, then plan the next concrete task for the other agent.",
    }
    contract += options.get("contract", {}).get(phase, defaults[phase]) + "\n"
    if progress:
        contract += (f"\nKeep {progress} in your working folder as a checklist of the overall plan (create it if missing): "
                     "read it first, continue from the first unchecked item, and tick items as you finish them. "
                     "Do what fits in this turn, update the checklist, then hand off; the next turn continues from it.\n")
    if timeout:
        contract += f"This turn is stopped after {max(1, round(timeout / 60))} min: save progress and hand off before then.\n"
    contract += retry
    snapshot = archive / "prompt.md"
    atomic_text(snapshot, text + contract)
    return {"roles": [r["label"] for r in roles], "headings": list(required), "target": target, "input": str(source.relative_to(root.resolve())), "output": str(destination.relative_to(root.resolve())),
            "prompt_chars": len(text + contract), **sizes,
            "prompt_file": str(snapshot), "response_file": str(archive / "response.md"),
            "input_sha256": sha256(text), "prompt_sha256": sha256(text + contract),
            "output_base_hash": sha256(destination.read_text(encoding="utf-8")) if destination.exists() else None}


def publish(root: Path, handoff: Mapping[str, Any], *, plan_id: str, unit_id: str,
            iteration: int, agent: str) -> dict[str, Any]:
    response = Path(handoff["response_file"])
    if response.stat().st_size > MAX_RESPONSE_BYTES:
        raise ValueError("handoff exceeds 10 MiB")
    text = response.read_text(encoding="utf-8")
    errors = validate_response(text, handoff.get("headings", REQUIRED_HEADINGS))
    if errors:
        raise ResponseValidationError(errors)
    metadata = f"<!-- ALFRD plan={plan_id} unit={unit_id} iteration={iteration} sender={agent} -->\n"
    destination = resolve_handoff(root, handoff["output"], handoff.get("target", "task"))
    from alfrd.runtime.plan_csv import locked
    from alfrd import history

    with locked(root / ".alfrd" / "locks" / "handoff.lock"):
        current = destination.read_text(encoding="utf-8") if destination.exists() else None
        if current != metadata + text and (sha256(current) if current is not None else None) != handoff.get("output_base_hash"):
            raise HandoffConflictError("outgoing handoff was edited during this turn; preserving the edit")
        history.ensure_baseline(root, handoff["output"])
        if history.is_tracked(root, handoff["output"]):
            history.record(root, handoff["output"], metadata + text, source="cli", message=f"handoff {unit_id}")
        atomic_text(destination, metadata + text)
    return {"sha256": sha256(text), "path": handoff["output"], "size_bytes": response.stat().st_size}


def turn_phase(unit):
    if unit.get("status") == "running":
        if unit.get("review_status") == "pending":
            return "awaiting_review"
        if unit.get("manual"):
            return "awaiting_response"
    return unit.get("status", "pending")


def status(plan: Mapping[str, Any], units: list[dict[str, Any]]) -> dict[str, Any] | None:
    definition = plan.get("loop")
    if not definition:
        return None
    current = next((u for u in reversed(units) if u.get("status") == "running"), None)
    current = current or next((u for u in reversed(units) if u.get("iteration")), {})
    handoff = current.get("handoff") or {}
    steps = plan.get("steps") or []
    step = (current.get("steps") or [None])[0]
    turn = steps.index(step) + 1 if step in steps else 0
    return {"turn": turn, "turns": len(steps), "started": current.get("started"), "finished": current.get("finished"), "iterations": definition["iterations"], "iteration": current.get("iteration", 0),
            "iteration_unit": definition.get("unit") or "iterations",
            "agent": current.get("agent"), "roles": current.get("roles", handoff.get("roles", [])), "phase": turn_phase(current),
            "model": current.get("model"), "requested_model": current.get("requested_model"),
            "input": handoff.get("input"), "output": handoff.get("output"),
            "unit": current.get("id"), "artifact": current.get("artifact")}


def submit_response(root: Path, plan_id: str, unit_id: str, text: str) -> None:
    from alfrd.runtime.scheduler import PlanDir
    from alfrd.runtime.plan_csv import locked

    if not re.fullmatch(r"[A-Za-z0-9+._-]+", unit_id):
        raise ValueError("invalid unit id")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id):
        raise ValueError("invalid plan id")
    folder = PlanDir(root, plan_id)
    unit = next((u for u in folder.units() if u["id"] == unit_id), None)
    if not unit or not unit.get("manual") or unit.get("status") != "running":
        raise ValueError("this turn is not awaiting a manual response")
    errors = validate_response(text, unit["handoff"].get("headings", REQUIRED_HEADINGS))
    if errors:
        raise ResponseValidationError(errors)
    with locked(folder.path / "manual-response.lock"):
        response = Path(unit["handoff"]["response_file"])
        if response.exists():
            raise FileExistsError("a response was already submitted")
        atomic_text(response, text)


def reject_response(root: Path, plan_id: str, unit_id: str, reason: str = "") -> None:
    """Refuse a held handoff: nothing is published and the plan stops like a failed turn."""
    import json
    from alfrd.runtime.scheduler import PlanDir
    from alfrd.runtime.plan_csv import locked

    if not isinstance(reason, str) or len(reason) > 2000:
        raise ValueError("reason must be text of at most 2000 characters")
    folder = PlanDir(root, plan_id)
    with locked(folder.path / "review.lock"):
        unit = next((u for u in folder.units() if u["id"] == unit_id), None)
        if not unit or unit.get("status") != "running" or unit.get("review_gate") != "runner":
            raise ValueError("this turn is not awaiting human review")
        exit_path = project_file(root, unit["exit_file"])
        if not exit_path.with_suffix(".review.json").is_file():
            raise ValueError("this turn is not awaiting human review")
        if exit_path.with_suffix(".approval.json").exists() or exit_path.with_suffix(".rejection.json").exists():
            raise FileExistsError("this response was already reviewed")
        atomic_text(exit_path.with_suffix(".rejection.json"), json.dumps({"reason": reason.strip()}))


def approve_response(root: Path, plan_id: str, unit_id: str, text: str, base_hash: str) -> None:
    """Review the archived agent response; the runner (or, for older turns, the shim) publishes only after approval."""
    import json
    from alfrd.runtime.scheduler import PlanDir
    from alfrd.runtime.plan_csv import locked
    from alfrd import history

    folder = PlanDir(root, plan_id)
    with locked(folder.path / "review.lock"):
        unit = next((u for u in folder.units() if u["id"] == unit_id), None)
        if not unit or not unit.get("human_review") or unit.get("status") != "running":
            raise ValueError("this turn is not awaiting human review")
        errors = validate_response(text, unit["handoff"].get("headings", REQUIRED_HEADINGS))
        if errors:
            raise ResponseValidationError(errors)
        exit_path = project_file(root, unit["exit_file"])
        ready = exit_path.with_suffix(".review.json")
        approval = exit_path.with_suffix(".approval.json")
        # The runner holds the handoff after the command exits; older turns wait inside the shim.
        if not ready.is_file() or (exit_path.is_file() and unit.get("review_gate") != "runner"):
            raise ValueError("this turn is not awaiting human review")
        if approval.exists() or exit_path.with_suffix(".rejection.json").exists():
            raise FileExistsError("this response was already reviewed")
        response = project_file(root, str(Path(unit["handoff"]["response_file"]).relative_to(root)))
        current = response.read_text(encoding="utf-8")
        if sha256(current) != base_hash:
            raise history.Conflict(str(response.relative_to(root)), current, text)
        original = response.with_name("response-agent.md")
        if not original.exists():
            atomic_text(original, current)
        atomic_text(response, text)
        atomic_text(approval, json.dumps({"hash": sha256(text), "original_hash": sha256(current)}))
