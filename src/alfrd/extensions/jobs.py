"""Studio plugin jobs: install, update and remove in a subprocess, one at a time.

The Studio starts a job with :func:`start`. The work runs in
``python -m alfrd.extensions.jobs <action> <json>`` (so a hang can be killed),
which uses :mod:`alfrd.extensions.installer` and its ``.install.lock`` exactly
like the CLI does. Its output goes to ``plugins/jobs/<id>.log``; the Studio
tails that log and hears about state changes through ``notify`` (the
``plugin_job`` live event). Every finished job appends one line to
``plugins/audit.jsonl``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from . import plugins_dir

#: Seconds before a job is killed (its installer call has the same limit).
TIMEOUT = 600
#: Extra installer arguments for every job (tests: ``--no-index --find-links …``).
EXTRA: tuple[str, ...] = ()
ACTIONS = ("install", "install-pinned", "update", "remove")
#: Plugin ids the user may type to confirm an advanced install (Q4.4).
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")


class JobBusy(RuntimeError):
    """A plugin job is already running."""


def jobs_dir() -> Path:
    return plugins_dir() / "jobs"


def log_path(job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise ValueError(f"invalid job id {job_id!r}")
    return jobs_dir() / f"{job_id}.log"


def audit(action: str, plugin_id: str | None, *, source: str | None = None, version: str | None = None,
          sha256: str | None = None, result: str, error: str | None = None) -> None:
    """Append ``{time, user, action, id, source, version, sha256, result, error}`` to ``plugins/audit.jsonl``."""
    line = json.dumps({"time": datetime.now(timezone.utc).isoformat(), "user": getpass.getuser(), "action": action,
                       "id": plugin_id, "source": source, "version": version, "sha256": sha256,
                       "result": result, "error": error}, sort_keys=True)
    plugins_dir().mkdir(parents=True, exist_ok=True)
    # One short O_APPEND write per line: concurrent writers never interleave.
    fd = os.open(plugins_dir() / "audit.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def _target(action: str, payload: dict) -> str | None:
    return payload.get("id") or payload.get("confirm_id")


class Runner:
    """One job at a time per server. ``notify(job)`` is called on start and finish."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._current: str | None = None
        self.last: str | None = None

    def running(self) -> dict | None:
        with self._lock:
            return dict(self._jobs[self._current]) if self._current else None

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def start(self, action: str, payload: dict, notify: Callable[[dict], None] | None = None) -> dict:
        if action not in ACTIONS:
            raise ValueError(f"unknown plugin job {action!r}")
        with self._lock:
            if self._current:
                raise JobBusy(f"a plugin job is already running ({self._jobs[self._current]['action']} "
                              f"{self._jobs[self._current]['target']})")
            job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
            jobs_dir().mkdir(parents=True, exist_ok=True)
            job = {"id": job_id, "action": action, "target": _target(action, payload), "status": "running",
                   "started": time.time(), "ended": None, "returncode": None, "restart_required": False}
            command = [sys.executable, "-u", "-m", "alfrd.extensions.jobs", action,
                       json.dumps({**payload, "extra": list(EXTRA)})]
            with log_path(job_id).open("wb") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           start_new_session=os.name != "nt")
            self._jobs[job_id] = job
            self._current = self.last = job_id
        threading.Thread(target=self._wait, args=(job_id, process, payload, notify), daemon=True,
                         name=f"plugin-job-{job_id}").start()
        _notify(notify, job)
        return dict(job)

    def _wait(self, job_id: str, process: subprocess.Popen, payload: dict, notify) -> None:
        try:
            code = process.wait(timeout=TIMEOUT)
            status = "done" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            _kill(process)
            code, status = process.wait(), "timeout"
            with log_path(job_id).open("a", encoding="utf-8") as log:
                log.write(f"\nStopped: the job took longer than {TIMEOUT} s.\n")
            # The killed child could not write its own audit line.
            entry = payload.get("version") or {}
            audit(self._jobs[job_id]["action"], _target("", payload), source=payload.get("source") or entry.get("wheel"),
                  version=entry.get("version"), sha256=entry.get("sha256"),
                  result="timeout", error=f"timed out after {TIMEOUT} s")
        with self._lock:
            job = self._jobs[job_id]
            # Python loads plugins once per process: any change needs a restart.
            job.update(status=status, returncode=code, ended=time.time(), restart_required=status == "done")
            self._current = None
            done = dict(job)
        _notify(notify, done)


def _kill(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass


def _notify(notify, job: dict) -> None:
    if notify is not None:
        try:
            notify(dict(job))
        except Exception:  # noqa: BLE001 - a broken event stream must not break the job
            pass


#: The server's runner (one per process).
runner = Runner()


def read_log(job_id: str, offset: int = 0) -> tuple[str, int]:
    """Text appended since ``offset`` and the offset to continue from."""
    with log_path(job_id).open("rb") as stream:
        stream.seek(max(0, offset))
        data = stream.read()
    return data.decode("utf-8", errors="replace"), max(0, offset) + len(data)


# -- the job process ----------------------------------------------------------

def _run(action: str, payload: dict) -> int:
    from . import installer

    extra = tuple(payload.get("extra") or ())
    plugin_id = _target(action, payload)
    source, version, sha256 = payload.get("source"), None, None
    print(f"Plugins run as {getpass.getuser()} with access to your files and projects.")
    try:
        if action == "install-pinned":
            entry = payload["version"]
            source, version, sha256 = entry["wheel"], entry["version"], entry["sha256"]
            print(f"Installing {plugin_id} {version} from the catalog")
            print(f"  wheel  {source}\n  sha256 {sha256}")
            records = installer.install_pinned(plugin_id, entry, extra=extra)
        elif action == "install":
            print(f"Installing {source} (expecting plugin {plugin_id!r})")
            records = installer.install(source, extra=extra, expect_id=plugin_id)
        elif action == "update":
            print(f"Updating {plugin_id}")
            records = installer.update(plugin_id, extra=extra)
        else:
            print(f"Removing {plugin_id}")
            installer.remove(plugin_id)
            records = []
    except Exception as exc:  # noqa: BLE001 - reported in the log and the audit
        print(f"\nFailed: {exc}")
        audit(action, plugin_id, source=source, version=version, sha256=sha256, result="failed", error=str(exc))
        return 1
    for record in records:
        print(f"{'removed' if action == 'remove' else 'installed'} {record['id']} {record['version']}")
        version, sha256 = record.get("version"), record.get("sha256")
        source = record.get("wheel") or record.get("source") or source
    if action == "remove":
        print(f"removed {plugin_id}")
    print("Done. Restart alfrd serve to load the change.")
    audit(action, plugin_id, source=source, version=version, sha256=sha256, result="ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] not in ACTIONS:
        print(f"usage: python -m alfrd.extensions.jobs {{{','.join(ACTIONS)}}} JSON", file=sys.stderr)
        return 2
    return _run(argv[0], json.loads(argv[1]))


if __name__ == "__main__":
    sys.exit(main())
