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


DEFAULT_ITERATIONS = 5
MAX_ITERATIONS = 100
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
REQUIRED_HEADINGS = ("Goal", "What was completed", "Files changed", "Checks and results",
                     "Instructions for the next agent", "Acceptance criteria", "Blockers")


class ResponseValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


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


def expand_steps(workflow: Mapping[str, Any], steps: Mapping[str, Any]) -> dict[str, Any]:
    repeat = workflow.get("repeat")
    if repeat is None:
        return dict(steps)
    if not isinstance(repeat, Mapping):
        raise ValueError("workflow.repeat must contain iterations")
    count = repeat.get("iterations")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_ITERATIONS:
        raise ValueError(f"repeat.iterations must be an integer from 1 to {MAX_ITERATIONS}")
    if not steps:
        raise ValueError("a repeated workflow needs steps")
    out = {}
    for iteration in range(1, count + 1):
        for key, raw in steps.items():
            spec = copy.deepcopy(raw)
            spec.update(iteration=iteration, iterations=count, base_step=key)
            spec["label"] = f"{iteration}/{count} · {spec.get('label') or key}"
            out[f"i{iteration:03d}-{key}"] = spec
    return out


def project_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("handoff files must be inside the project")
    return path


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


def prepare(root: Path, archive: Path, step, plan_id: str, unit_id: str) -> dict[str, Any]:
    """Snapshot at launch, never at plan creation: the preceding agent supplies it."""
    source = project_file(root, step.handoff["input"])
    destination = project_file(root, step.handoff["output"])
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"{source.name} is empty")
    recipient = step.entrypoint or step.base_step or step.id
    final = step.iteration == step.iterations and step.final_turn
    options = getattr(step, "loop_options", {})
    required = options.get("headings", REQUIRED_HEADINGS)
    contract = (
        f"\n\n---\nALFRD turn: plan={plan_id}, unit={unit_id}, "
        f"iteration={step.iteration}/{step.iterations}, agent={recipient}.\n"
        "Your final response must use these Markdown headings exactly: "
        + "; ".join(f"## {h}" for h in required) + ". " +
        "ALFRD publishes the response; do not edit the next-step files yourself. "
        "Preserve the user's scope and repository instructions. Do not commit or push.\n"
    )
    phase = "final" if final else "first" if step.iteration == 1 and step.first_turn else "middle"
    defaults = {
        "final": "Execute the incoming task and report files changed and checks performed. This is the final turn: provide a closing report; do not invent another task.",
        "first": "This is the first turn: plan the initial task for the next agent. Inspect as needed; leave implementation and file creation to the next agent.",
        "middle": "Execute the incoming task, report files changed and checks performed, then plan the next concrete task for the other agent.",
    }
    contract += options.get("contract", {}).get(phase, defaults[phase]) + "\n"
    snapshot = archive / "prompt.md"
    atomic_text(snapshot, text + contract)
    return {"headings": list(required), "input": step.handoff["input"], "output": step.handoff["output"],
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
    destination = project_file(root, handoff["output"])
    from alfrd.runtime.plan_csv import locked
    from alfrd import history

    with locked(root / ".alfrd" / "locks" / "handoff.lock"):
        current = destination.read_text(encoding="utf-8") if destination.exists() else None
        if current != metadata + text and (sha256(current) if current is not None else None) != handoff.get("output_base_hash"):
            raise ValueError("outgoing handoff was edited during this turn; preserving the edit")
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
            "agent": current.get("agent"), "phase": turn_phase(current),
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


def approve_response(root: Path, plan_id: str, unit_id: str, text: str, base_hash: str) -> None:
    """Review the archived agent response; the waiting shim publishes only after approval."""
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
        if not ready.is_file() or exit_path.is_file():
            raise ValueError("this turn is not awaiting human review")
        if approval.exists():
            raise FileExistsError("this response was already approved")
        response = project_file(root, str(Path(unit["handoff"]["response_file"]).relative_to(root)))
        current = response.read_text(encoding="utf-8")
        if sha256(current) != base_hash:
            raise history.Conflict(str(response.relative_to(root)), current, text)
        original = response.with_name("response-agent.md")
        if not original.exists():
            atomic_text(original, current)
        atomic_text(response, text)
        atomic_text(approval, json.dumps({"hash": sha256(text), "original_hash": sha256(current)}))
