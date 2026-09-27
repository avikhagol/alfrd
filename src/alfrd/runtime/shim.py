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
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, env=env)
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
    code = child.wait()
    print(f"\n[alfrd] exit code {code} after {time.time() - started:.0f} s", flush=True)
    _write_json(exit_file, {
        "exit_code": code,
        "signal": -code if code < 0 else None,
        "finished": datetime.now().isoformat(timespec="seconds"),
    })
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
