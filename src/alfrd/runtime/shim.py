"""Run one command and record how it ended, independent of the ALFRD runner.

``python -m alfrd.runtime.shim --exit-file X.exit -- cmd arg ...``

The runner starts this in a new session (so it is the process-group leader)
with stdout/stderr already pointing at the log file. No pipes are involved, so
the command keeps running and logging when the runner or ``alfrd serve`` stops.
On exit the shim writes ``X.exit`` (JSON: exit_code, signal, finished) with an
atomic rename; ``X.child`` holds the command's pid for re-adoption.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def proc_start(pid: int) -> str | None:
    """Kernel start time of ``pid`` (guards against pid reuse); None off Linux."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = text[text.rfind(")") + 2:].split()
    return fields[19] if len(fields) > 19 else None


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alfrd.runtime.shim")
    parser.add_argument("--exit-file", required=True)
    parser.add_argument("--stdin-file")
    parser.add_argument("--stdout-file")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    exit_file = Path(args.exit_file)
    if not command:
        _write_json(exit_file, {"exit_code": 2, "error": "no command", "finished": datetime.now().isoformat()})
        return 2
    signal.signal(signal.SIGHUP, signal.SIG_IGN)  # a closed terminal must not end the run
    started = time.time()
    env = os.environ.copy()
    original = env.pop("ALFRD_ORIG_PYTHONPATH", None)  # the command gets the user's PYTHONPATH back
    if original is not None:
        if original:
            env["PYTHONPATH"] = original
        else:
            env.pop("PYTHONPATH", None)
    try:
        from alfrd.runtime.usage import become_subreaper

        become_subreaper()  # daemonized ranks come back to us, so they are counted and reaped
    except Exception:  # noqa: BLE001
        pass
    try:
        with contextlib.ExitStack() as stack:
            stdin = stack.enter_context(open(args.stdin_file, "rb")) if args.stdin_file else subprocess.DEVNULL
            stdout = stack.enter_context(open(args.stdout_file, "wb")) if args.stdout_file else None
            child = subprocess.Popen(command, stdin=stdin, stdout=stdout, env=env)
    except OSError as exc:
        print(f"[alfrd] cannot start {command[0]!r}: {exc}", file=sys.stderr, flush=True)
        _write_json(exit_file, {"exit_code": 127, "error": str(exc), "finished": datetime.now().isoformat()})
        return 127
    _write_json(exit_file.with_suffix(".child"), {"pid": child.pid, "proc_start": proc_start(child.pid)})

    def forward(signum, _frame):
        try:
            child.send_signal(signum)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    code, rusage, usage, reaped_cpu = _wait(child, exit_file, env)
    print(f"\n[alfrd] exit code {code} after {time.time() - started:.0f} s", flush=True)
    data = {
        "exit_code": code,
        "signal": -code if code < 0 else None,
        "finished": datetime.now().isoformat(timespec="seconds"),
    }
    if code == LOST:
        data["error"] = "exit status lost"
    _write_json(exit_file, data)  # the exit code first: the usage summary (work dir size) can take a while
    if usage is not None:
        try:
            data["usage"] = usage.summary(rusage, launcher=os.environ.get("ALFRD_LAUNCHER"), child=child.pid,
                                          reaped_cpu=reaped_cpu)
        except Exception as exc:  # noqa: BLE001 - usage must never lose the exit code
            data["usage_error"] = str(exc)
        _write_json(exit_file, data)
    return code if code >= 0 else 128 - code


LOST = 255  # the child was reaped elsewhere: its exit status is unknown


ORPHAN_GRACE = 2.0  # seconds to keep reaping re-parented ranks after the command itself ended


def _wait(child: subprocess.Popen, exit_file: Path, env: dict) -> tuple[int, object, object, float]:
    """Wait for the command, sampling its resource usage (alfrd.runtime.usage) meanwhile.

    The shim is a child subreaper, so it also reaps descendants that daemonized
    (MPI ranks); their CPU time is summed into the last value.
    """
    try:
        interval = float(os.environ.get("ALFRD_USAGE_INTERVAL", "5") or 0)
    except ValueError:
        interval = 5.0
    sampler = None
    try:
        from alfrd.runtime.usage import Sampler

        out = exit_file.with_suffix(".usage.jsonl")
        sampler = Sampler(os.getpid(), out, interval, marker=_marker(),
                          workdir=os.environ.get("ALFRD_USAGE_DIR") or None)
    except Exception:  # noqa: BLE001
        sampler = None
    if not hasattr(os, "wait4"):  # pragma: no cover - not POSIX
        return child.wait(), None, sampler, 0.0
    reaped_cpu = 0.0
    result: tuple[int, object] | None = None
    ended_at = 0.0
    while True:
        try:
            pid, status, rusage = os.wait4(-1, os.WNOHANG)
        except ChildProcessError:  # nothing left to reap
            if result is None:
                return (child.returncode if child.returncode is not None else LOST), None, sampler, reaped_cpu
            return result[0], result[1], sampler, reaped_cpu
        except InterruptedError:
            continue
        if pid:
            reaped_cpu += float(rusage.ru_utime + rusage.ru_stime)
            if pid == child.pid:
                code = os.waitstatus_to_exitcode(status)
                child.returncode = code
                result = (code, rusage)
                ended_at = time.time()
            continue  # reap everything that is ready before sleeping
        if result is not None and time.time() - ended_at >= ORPHAN_GRACE:
            return result[0], result[1], sampler, reaped_cpu
        now = time.time()
        if sampler is not None and sampler.due(now):
            try:
                sampler.take(now)
            except Exception:  # noqa: BLE001
                sampler.enabled = False
        time.sleep(0.1 if sampler is None or not sampler.enabled else min(0.25, max(0.02, sampler.interval / 4)))


def _marker() -> str | None:
    unit = os.environ.get("ALFRD_UNIT")
    return f"ALFRD_UNIT={unit}" if unit else None


if __name__ == "__main__":
    sys.exit(main())
