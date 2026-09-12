from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path
from typing import Mapping

from .models import StepExecution
from .protocols import RuntimeOperations
from .service import Status


class LocalSubprocessWorker:
    """Run exactly one step command locally without shell expansion.

    Output is streamed to a per-execution log file under
    ``<cwd>/.alfrd/logs/<execution_id>.log`` so browser/CLI control surfaces
    can tail live output while the process runs, in addition to the
    stdout/stderr captured on the persisted ``StepExecution`` once it
    finishes.
    """

    def __init__(self, runtime: RuntimeOperations) -> None:
        self.runtime = runtime

    def _log_path(self, execution: StepExecution) -> Path:
        directory = Path(execution.cwd) / ".alfrd" / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{execution.id}.log"

    def execute(
        self,
        execution_id: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> StepExecution:
        execution = self.runtime.transition_execution(execution_id, Status.RUNNING)
        log_path = self._log_path(execution)
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        try:
            with log_path.open("w", encoding="utf-8") as log_stream:
                process = subprocess.Popen(
                    execution.command_json,
                    cwd=execution.cwd,
                    env=process_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                attach = getattr(self.runtime, "attach_process", None)
                if callable(attach):
                    try:
                        attach(execution.run_id, pid=process.pid, hostname=socket.gethostname())
                    except Exception:  # noqa: BLE001 - ownership metadata is best-effort
                        pass
                assert process.stdout is not None
                lines: list[str] = []
                try:
                    for line in process.stdout:
                        lines.append(line)
                        log_stream.write(line)
                        log_stream.flush()
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    combined = "".join(lines)
                    return self.runtime.transition_execution(
                        execution_id,
                        Status.FAILED,
                        stdout=combined,
                        error=f"command timed out after {timeout} seconds",
                        log_path=log_path,
                    )
        except OSError as exc:
            return self.runtime.transition_execution(
                execution_id,
                Status.FAILED,
                error=str(exc),
                log_path=log_path,
            )
        combined_output = "".join(lines)
        status = Status.SUCCEEDED if returncode == 0 else Status.FAILED
        return self.runtime.transition_execution(
            execution_id,
            status,
            exit_code=returncode,
            stdout=combined_output,
            error=None if returncode == 0 else f"command exited with code {returncode}",
            log_path=log_path,
        )


def run_workflow(
    runtime: RuntimeOperations,
    run_id: str,
    *,
    worker: LocalSubprocessWorker | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> Status:
    """Drive one pending run to completion using the local subprocess worker.

    This is the small orchestration loop the local worker abstraction needs on
    top of ``LocalSubprocessWorker.execute`` (which runs exactly one already
    claimed step). It transitions the run to running, executes pending steps
    in sequence, stops at the first failure, and marks the run finished.
    Intended to be invoked out-of-process (e.g. from a spawned CLI process),
    never synchronously inside a Flask request.
    """
    worker = worker or LocalSubprocessWorker(runtime)
    run = runtime.get_run(run_id)
    if run.status == Status.PENDING.value:
        run = runtime.transition_run(run_id, Status.RUNNING)
    final_status = Status.SUCCEEDED
    while True:
        execution = runtime.next_pending_execution(run_id)
        if execution is None:
            break
        result = worker.execute(execution.id, env=env, timeout=timeout)
        if result.status != Status.SUCCEEDED.value:
            final_status = Status.FAILED
            break
    if final_status == Status.FAILED:
        while True:
            remaining = runtime.next_pending_execution(run_id)
            if remaining is None:
                break
            runtime.transition_execution(
                remaining.id, Status.SKIPPED, error="skipped after an earlier step failed"
            )
    runtime.transition_run(
        run_id,
        final_status,
        error=None if final_status == Status.SUCCEEDED else "a step failed; see step execution details",
    )
    return final_status
