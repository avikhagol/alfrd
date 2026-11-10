"""Run all Studio tests with bounded concurrency and process-tree cleanup.

Use the repository Python environment; Node and Playwright retain their normal
PATH / ALFRD_PLAYWRIGHT / ALFRD_CHROMIUM_EXECUTABLE configuration.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], timeout: float) -> int:
    # Keep output streaming so CI and agents can see which test is stuck.
    process = subprocess.Popen(command, cwd=ROOT, start_new_session=os.name == "posix")
    try:
        return process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait()
        print(f"Studio test run stopped: {type(exc).__name__} after {timeout}s", file=sys.stderr)
        return 124 if isinstance(exc, subprocess.TimeoutExpired) else 130


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("files", nargs="*", help="Optional targeted .test.mjs paths")
    args = parser.parse_args()
    if args.timeout <= 0 or args.concurrency <= 0:
        parser.error("timeout and concurrency must be positive")
    files = args.files or [str(p) for p in sorted((ROOT / "tests/studio_js").glob("*.test.mjs"))]
    return run(["node", "--test", f"--test-concurrency={args.concurrency}", *files], args.timeout)


if __name__ == "__main__":
    sys.exit(main())
