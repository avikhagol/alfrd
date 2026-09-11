from __future__ import annotations

import os
import subprocess
from typing import Mapping

from .models import StepExecution
from .protocols import RuntimeOperations
from .service import Status


class LocalSubprocessWorker:
    """Run exactly one step command locally without shell expansion."""

    def __init__(self, runtime: RuntimeOperations) -> None:
        self.runtime = runtime

    def execute(
        self,
        execution_id: str,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> StepExecution:
        execution = self.runtime.transition_execution(execution_id, Status.RUNNING)
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        try:
            completed = subprocess.run(
                execution.command_json,
                cwd=execution.cwd,
                env=process_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
            return self.runtime.transition_execution(
                execution_id,
                Status.FAILED,
                stdout=stdout,
                stderr=stderr,
                error=f"command timed out after {timeout} seconds",
            )
        except OSError as exc:
            return self.runtime.transition_execution(
                execution_id,
                Status.FAILED,
                error=str(exc),
            )
        status = Status.SUCCEEDED if completed.returncode == 0 else Status.FAILED
        return self.runtime.transition_execution(
            execution_id,
            status,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            error=None if completed.returncode == 0 else f"command exited with code {completed.returncode}",
        )
