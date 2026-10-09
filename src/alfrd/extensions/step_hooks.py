"""Calling plugins' :class:`~alfrd.extensions.StepHooks` from the plan runner.

The runner (:class:`alfrd.runtime.scheduler.Runner`) calls :func:`call_before` right
before it spawns a unit's command and :func:`call_after` after it saved the finished
unit. Units that never launch (skipped, blocked, cannot start) get no call.

A hook can't fail, hang or change a step: each call runs in a daemon worker thread
that is abandoned after the hook's ``timeout``; an exception or timeout comes back as
a one-line :class:`Failure` the runner writes to ``runner.log`` and as a
``plugin.hook_failed`` event. Safe mode (``ALFRD_NO_PLUGINS=1``) and disabled
plugins give no hooks. Plugins are loaded once per process (:func:`alfrd.extensions.load`);
the disabled list is re-read on every call, so disabling applies from the next unit.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from alfrd.extensions import StepContext, StepHooks

_ERROR_LIMIT = 300


@dataclass(frozen=True)
class Failure:
    plugin: str
    phase: str  # before | after
    error: str  # one line, no traceback

    def line(self, unit_id: str) -> str:
        return f"{unit_id}: plugin {self.plugin} {self.phase} hook failed: {self.error}"


def hooks() -> list[tuple[str, StepHooks]]:
    """``[(plugin_id, StepHooks)]`` of the enabled, loaded plugins (empty in safe mode or on any load error)."""
    from alfrd import extensions

    if extensions.safe_mode():
        return []
    try:
        records = extensions.load()
        return [(r.id, r.plugin.step_hooks) for r in records
                if r.plugin is not None and r.plugin.step_hooks is not None and extensions.active(r)]
    except Exception:  # noqa: BLE001 - plugin discovery never stops a run
        return []


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    text = f"{type(exc).__name__}: {text}" if not text.startswith(type(exc).__name__) else text
    return text if len(text) <= _ERROR_LIMIT else text[:_ERROR_LIMIT - 1] + "…"


def run_isolated(fn: Callable[..., Any], args: tuple, timeout: float, name: str = "alfrd-hook") -> tuple[Any, str | None]:
    """``(value, None)`` or ``(None, error)``: ``fn(*args)`` in a daemon thread, abandoned after ``timeout`` seconds."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001 - SystemExit from a hook too
            box["error"] = _one_line(exc)

    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, f"timed out after {timeout:g} s"
    if "error" in box:
        return None, box["error"]
    return box.get("value"), None


def _copy(ctx: StepContext) -> StepContext:
    import copy

    return copy.deepcopy(ctx)


def call_before(ctx: StepContext, active: list[tuple[str, StepHooks]] | None = None) -> tuple[dict[str, Any], list[Failure]]:
    """Run every ``before`` hook: ``({plugin_id: state}, failures)``; a failed hook's state is ``None``."""
    states: dict[str, Any] = {}
    failures: list[Failure] = []
    for plugin_id, spec in hooks() if active is None else active:
        if spec.before is None:
            continue
        value, error = run_isolated(spec.before, (_copy(ctx),), float(spec.timeout), f"alfrd-hook-{plugin_id}")
        states[plugin_id] = value
        if error:
            failures.append(Failure(plugin_id, "before", error))
    return states, failures


def call_after(ctx: StepContext, states: Mapping[str, Any] | None,
               active: list[tuple[str, StepHooks]] | None = None) -> list[Failure]:
    """Run every ``after`` hook with its plugin's ``before`` state (``None`` when there is none)."""
    failures: list[Failure] = []
    for plugin_id, spec in hooks() if active is None else active:
        if spec.after is None:
            continue
        state = (states or {}).get(plugin_id)
        _, error = run_isolated(spec.after, (_copy(ctx), state), float(spec.timeout), f"alfrd-hook-{plugin_id}")
        if error:
            failures.append(Failure(plugin_id, "after", error))
    return failures


__all__ = ["Failure", "call_after", "call_before", "hooks", "run_isolated"]
