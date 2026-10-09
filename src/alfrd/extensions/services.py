"""Plugin services: long-running ``alfrd <command…>`` children of ``alfrd serve``.

Settings → Plugins starts and stops them; ``autostart`` (in ``plugins.json``) starts them
with the server. A child runs ``python -m alfrd.extensions.services <command…>`` (this
module's :func:`main`, then the ``alfrd`` CLI) in its own session with ``ALFRD_RUNTIME_DB``
set, writes to ``plugins/services/<plugin>-<service>.log`` and is stopped with SIGINT
(then SIGKILL after :data:`STOP_GRACE`). On Linux it also dies with the server
(``PR_SET_PDEATHSIG``). No ``preexec_fn``: Python code between fork and exec can
deadlock a threaded server, so the child sets itself up after exec.

A child that exits on its own is restarted after a growing delay; after
:data:`MAX_FAILURES` exits within :data:`FAILURE_WINDOW` seconds it stays ``failed``
until the user starts it again. Exit code :data:`CONFIG_ERROR` (2) means "fix the
settings first" (e.g. a revoked token): no restart at all.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import Plugin, Service, plugins_dir

STOP_GRACE = 5.0
#: A service exits with this code when retrying can't help (bad settings; also typer's usage errors).
CONFIG_ERROR = 2
MAX_FAILURES = 5
FAILURE_WINDOW = 600.0
#: Seconds before restart n (1-based); the last value repeats.
BACKOFF = (2.0, 5.0, 10.0, 30.0)
#: A log is cut to its last half when it grows past this at start.
LOG_LIMIT = 1 << 20


def key(plugin_id: str, service_id: str) -> str:
    return f"{plugin_id}:{service_id}"


def log_path(plugin_id: str, service_id: str) -> Path:
    return plugins_dir() / "services" / f"{plugin_id}-{service_id}.log"


def tail(plugin_id: str, service_id: str, lines: int = 40) -> str:
    """The last ``lines`` lines of a service log ('' when there is none)."""
    try:
        with log_path(plugin_id, service_id).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            data = stream.read()
    except OSError:
        return ""
    return "\n".join(data.decode("utf-8", errors="replace").splitlines()[-lines:])


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size > LOG_LIMIT:
            data = path.read_bytes()[-LOG_LIMIT // 2:]
            path.write_bytes(data[data.find(b"\n") + 1:])
    except OSError:
        pass


class _Proc:
    def __init__(self, plugin_id: str, service: Service) -> None:
        self.plugin_id, self.service = plugin_id, service
        self.process: subprocess.Popen | None = None
        self.status = "stopped"  # stopped | starting | running | restarting | failed
        self.wanted = False
        self.started: float | None = None
        self.exit_code: int | None = None
        self.failures: list[float] = []
        self.wake = threading.Event()
        self.generation = 0


class Supervisor:
    """The server's services (one per process). ``notify(status)`` is called on every change."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[str, _Proc] = {}
        self.env: dict[str, str] = {}
        self.notify: Callable[[dict], None] | None = None
        #: The child is ``prefix + service.command`` (tests: ``[..., "-c"]`` runs a stand-in script).
        self.prefix: list[str] = [sys.executable, "-u", "-m", "alfrd.extensions.services"]

    # -- queries ---------------------------------------------------------------

    def status(self, plugin_id: str, service: Service) -> dict[str, Any]:
        from . import read_state

        k = key(plugin_id, service.id)
        with self._lock:
            proc = self._procs.get(k)
            state = {"status": proc.status if proc else "stopped",
                     "pid": proc.process.pid if proc and proc.process and proc.status == "running" else None,
                     "started": proc.started if proc and proc.status == "running" else None,
                     "exit_code": proc.exit_code if proc else None,
                     "failures": len(proc.failures) if proc else 0}
        return {"id": service.id, "key": k, "title": service.title or service.id, "description": service.description,
                "command": ["alfrd", *service.command], "autostart": k in read_state()["autostart"], **state}

    def running(self, plugin_id: str) -> list[str]:
        """Ids of this plugin's services that are running or about to restart."""
        with self._lock:
            return [p.service.id for p in self._procs.values()
                    if p.plugin_id == plugin_id and p.wanted]

    # -- control ---------------------------------------------------------------

    def start(self, plugin_id: str, service: Service) -> None:
        k = key(plugin_id, service.id)
        with self._lock:
            proc = self._procs.get(k)
            if proc and proc.wanted:
                return
            if proc is None:
                proc = self._procs[k] = _Proc(plugin_id, service)
            proc.service, proc.wanted, proc.failures = service, True, []
            proc.status, proc.exit_code = "starting", None
            proc.generation += 1
            generation = proc.generation
            proc.wake = threading.Event()
        # The watcher thread forks the child: PR_SET_PDEATHSIG follows the forking *thread*,
        # and this one lives exactly as long as the child it waits for.
        threading.Thread(target=self._watch, args=(proc, generation), daemon=True, name=f"plugin-service-{k}").start()
        self._changed(proc)

    def stop(self, plugin_id: str, service_id: str) -> None:
        with self._lock:
            proc = self._procs.get(key(plugin_id, service_id))
            if proc is None:
                return
            proc.wanted = False
            proc.generation += 1
            proc.wake.set()
            process = proc.process
        if process is not None:
            _terminate(process)
        with self._lock:
            proc.status = "stopped"
        self._changed(proc)

    def restart(self, plugin_id: str, service: Service) -> None:
        self.stop(plugin_id, service.id)
        self.start(plugin_id, service)

    def stop_all(self) -> None:
        with self._lock:
            procs = [(p.plugin_id, p.service.id) for p in self._procs.values() if p.wanted or p.process]
        for plugin_id, service_id in procs:
            self.stop(plugin_id, service_id)

    def autostart(self, plugins: list[tuple[str, Plugin]]) -> list[str]:
        """Start the ``autostart`` services of these (active) plugins; returns the keys started."""
        from . import read_state, settings

        wanted = set(read_state()["autostart"])
        started = []
        for plugin_id, plugin in plugins:
            if settings.missing(plugin):  # not configured yet: Start in Settings says what is missing
                continue
            for service in plugin.services:
                if key(plugin_id, service.id) in wanted:
                    self.start(plugin_id, service)
                    started.append(key(plugin_id, service.id))
        return started

    # -- internals -------------------------------------------------------------

    def _spawn(self, proc: _Proc) -> None:
        """Start the child (``self._lock`` held)."""
        path = log_path(proc.plugin_id, proc.service.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _trim(path)
        command = [*self.prefix, *proc.service.command]
        env = {**os.environ, **self.env, "PYTHONUNBUFFERED": "1", "ALFRD_SERVICE_PARENT": str(os.getpid())}
        posix = os.name != "nt"
        with path.open("ab") as log:
            log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start: alfrd {' '.join(proc.service.command)}\n".encode())
            log.flush()
            proc.process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                start_new_session=posix,
            )
        proc.status, proc.started, proc.exit_code = "running", time.time(), None

    def _watch(self, proc: _Proc, generation: int) -> None:
        while True:
            with self._lock:
                if proc.generation != generation or not proc.wanted:
                    return
                try:
                    self._spawn(proc)
                except OSError as exc:
                    proc.status, proc.wanted, proc.process = "failed", False, None
                    _note(proc, f"could not start: {exc}")
                process = proc.process
            self._changed(proc)
            if process is None:
                return
            code = process.wait()
            with self._lock:
                proc.exit_code = code
                if proc.generation != generation or not proc.wanted:
                    return
                now = time.time()
                proc.failures = [t for t in proc.failures if now - t < FAILURE_WINDOW] + [now]
                _note(proc, f"exited with code {code}")
                if code == CONFIG_ERROR:
                    proc.status, proc.wanted = "failed", False
                    _note(proc, "not restarting: fix the settings, then Start")
                elif len(proc.failures) >= MAX_FAILURES:
                    proc.status, proc.wanted = "failed", False
                    _note(proc, f"stopped restarting after {MAX_FAILURES} exits in {int(FAILURE_WINDOW)} s")
                else:
                    proc.status = "restarting"
                delay = BACKOFF[min(len(proc.failures), len(BACKOFF)) - 1]
                failed = proc.status == "failed"
            self._changed(proc)
            if failed or proc.wake.wait(delay):
                return

    def _changed(self, proc: _Proc) -> None:
        if self.notify is None:
            return
        try:
            self.notify(self.status(proc.plugin_id, proc.service))
        except Exception:  # noqa: BLE001 - a broken event stream must not break the service
            pass


def _note(proc: _Proc, text: str) -> None:
    try:
        with log_path(proc.plugin_id, proc.service.id).open("a", encoding="utf-8") as log:
            log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    posix = os.name != "nt"
    try:
        if posix:
            os.killpg(process.pid, signal.SIGINT)
        else:
            process.terminate()
        process.wait(timeout=STOP_GRACE)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL) if posix else process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=STOP_GRACE)
        except subprocess.TimeoutExpired:
            pass


#: The server's supervisor (one per process).
supervisor = Supervisor()

def _setup_child() -> None:
    """In the child, after exec: stop on SIGINT, and (Linux) die with the server."""
    # `alfrd serve &` starts with SIGINT ignored, and exec keeps "ignored": Stop must still
    # reach the command as KeyboardInterrupt instead of waiting for SIGKILL.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    parent = os.environ.pop("ALFRD_SERVICE_PARENT", "")
    if not sys.platform.startswith("linux") or not parent.isdigit():
        return
    try:
        import ctypes

        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001 - best effort; the server still stops children on a clean exit
        return
    if os.getppid() != int(parent):  # the server died before prctl: nobody would stop us
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """``python -m alfrd.extensions.services <alfrd args…>`` (or ``-c CODE …`` for a stand-in)."""
    argv = list(sys.argv[1:] if argv is None else argv)
    _setup_child()
    if argv[:1] == ["-c"]:
        sys.argv = argv[1:]
        exec(compile(argv[1], "<service>", "exec"), {"__name__": "__main__"})  # noqa: S102 - the plugin's own command
        return
    from alfrd.cli import main as cli

    sys.argv = ["alfrd", *argv]
    cli()


__all__ = ["BACKOFF", "CONFIG_ERROR", "FAILURE_WINDOW", "MAX_FAILURES", "STOP_GRACE", "Supervisor", "key", "log_path", "main", "supervisor", "tail"]


if __name__ == "__main__":
    main()
