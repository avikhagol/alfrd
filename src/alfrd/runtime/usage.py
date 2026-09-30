"""Resource usage of a command and everything it starts, from /proc (Linux; no psutil).

The shim samples its command every ``interval`` seconds (``execution.usage_interval``).
Which processes count (MPI launchers move ranks out of the command's process
group, so the group alone undercounts):

* every descendant of the shim; the shim is a *child subreaper*, so ranks that
  daemonize (double fork, ``setsid``) are re-parented to it instead of to init;
* every process in the shim's session;
* every process whose environment carries this command's ``ALFRD_UNIT`` marker
  (ranks started through a local daemon that inherited the environment).

CPU is counted exactly once: each live process's own time, plus the
``cutime``/``cstime`` its reaped children left it (the shim's own cutime holds
whatever it reaped), plus the last reading of processes that ended without a
counted parent to reap them. Memory is the summed PSS
(``/proc/<pid>/smaps_rollup``; RSS where that is not readable), I/O the summed
``read_bytes``/``write_bytes``. One JSON line per sample goes to
``<unit>.usage.jsonl``; the summary (peak memory, CPU seconds, average cores,
I/O, wall time) into the exit file. Off Linux only the wall time is known
(``limited: true``). MPI ranks on other nodes are not seen.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROC = Path("/proc")
try:
    TICKS = os.sysconf("SC_CLK_TCK")
except (AttributeError, ValueError, OSError):  # pragma: no cover - not POSIX
    TICKS = 100


def available() -> bool:
    return sys.platform.startswith("linux") and (PROC / "self" / "stat").exists()


def _stat(pid: int) -> list[str] | None:
    try:
        text = (PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    return text[text.rfind(")") + 2:].split()


def _uid() -> int:
    try:
        return os.getuid()
    except AttributeError:  # pragma: no cover
        return -1


def scan() -> dict[int, dict[str, Any]]:
    """Every process: state, ppid, pgrp, session, own and children CPU (s), start time."""
    out: dict[int, dict[str, Any]] = {}
    for entry in os.scandir(PROC):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        fields = _stat(pid)
        if not fields or len(fields) < 20:
            continue
        try:
            out[pid] = {"state": fields[0], "ppid": int(fields[1]), "pgrp": int(fields[2]), "sid": int(fields[3]),
                        "own": (int(fields[11]) + int(fields[12])) / TICKS,
                        "children": (int(fields[13]) + int(fields[14])) / TICKS, "start": fields[19]}
        except ValueError:
            continue
    return out


def _has_marker(pid: int, marker: bytes) -> bool:
    try:
        return marker in (PROC / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError:
        return False


class Tracker:
    """The processes of one command across samples, with CPU accounting that survives exits."""

    def __init__(self, root: int, *, marker: str | None = None) -> None:
        self.root = root
        self.marker = marker.encode() if marker else None
        # The session counts only when the shim leads it (the runner starts it with a new session);
        # otherwise it is e.g. the terminal's and holds unrelated processes.
        try:
            self.sid = root if os.getsid(root) == root else None
        except (AttributeError, OSError):
            self.sid = None
        self.members: dict[tuple[int, str], dict[str, Any]] = {}   # (pid, start) -> last reading
        self.gone_cpu = 0.0          # ended processes no counted parent reaped
        self.gone_read = self.gone_write = 0
        self.env_checked: dict[tuple[int, str], bool] = {}
        self.best_cpu = 0.0

    def _tracked(self, procs: dict[int, dict[str, Any]]) -> set[int]:
        children: dict[int, list[int]] = {}
        for pid, info in procs.items():
            children.setdefault(info["ppid"], []).append(pid)
        found: set[int] = set()
        todo = [self.root]
        while todo:  # descendants of the shim
            for kid in children.get(todo.pop(), []):
                if kid not in found:
                    found.add(kid)
                    todo.append(kid)
        uid = _uid()
        for pid, info in procs.items():
            if pid in found or pid == self.root:
                continue
            if self.sid is not None and info["sid"] == self.sid:
                found.add(pid)
                continue
            if self.marker:
                key = (pid, info["start"])
                if key not in self.env_checked:
                    try:
                        mine = os.stat(PROC / str(pid)).st_uid == uid
                    except OSError:
                        mine = False
                    self.env_checked[key] = mine and _has_marker(pid, self.marker)
                if self.env_checked[key]:
                    found.add(pid)
        return found

    def sample(self) -> dict[str, Any]:
        procs = scan()
        tracked = self._tracked(procs)
        now: dict[tuple[int, str], dict[str, Any]] = {}
        mem = rss = read = write = 0
        count = 0
        for pid in tracked:
            info = procs[pid]
            key = (pid, info["start"])
            entry = {"ppid": info["ppid"], "own": info["own"], "children": info["children"], "read": 0, "write": 0}
            if info["state"] not in ("Z", "X"):
                count += 1
                m, r = _memory(pid)
                mem += m
                rss += r
                entry["read"], entry["write"] = _io(pid)
            else:  # a zombie keeps its times but no longer has io / memory
                prev = self.members.get(key)
                if prev:
                    entry["read"], entry["write"] = prev["read"], prev["write"]
            now[key] = entry
            read += entry["read"]
            write += entry["write"]
        counted = {pid for pid, _ in self.members} | {self.root}
        for key, prev in self.members.items():
            if key in now:
                continue
            # Ended since the last sample. If its parent was counted, the parent (or, once that
            # ended too, whoever reaped the parent, up to the shim) holds its time in cutime.
            # Otherwise keep what we last saw.
            if prev["ppid"] not in counted:
                self.gone_cpu += prev["own"] + prev["children"]
            self.gone_read += prev["read"]
            self.gone_write += prev["write"]
        self.members = now
        root = procs.get(self.root)
        cpu = sum(e["own"] + e["children"] for e in now.values()) + (root["children"] if root else 0.0) + self.gone_cpu
        self.best_cpu = max(self.best_cpu, cpu)  # a process between exit and reap can hide for a moment
        return {"cpu_s": round(self.best_cpu, 2), "mem": mem, "rss": rss, "read_bytes": read + self.gone_read,
                "write_bytes": write + self.gone_write, "procs": count}


def group_pids(pgid: int) -> list[int]:
    """Processes whose process group is ``pgid``."""
    return [pid for pid, info in scan().items() if info["pgrp"] == pgid and info["state"] not in ("Z", "X")]


def become_subreaper() -> bool:
    """Orphaned descendants (daemonized MPI ranks) are re-parented to this process (Linux)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
    except (OSError, AttributeError):
        return False


def _memory(pid: int) -> tuple[int, int]:
    """(pss, rss) in bytes; pss falls back to rss."""
    pss = rss = 0
    try:
        for line in (PROC / str(pid) / "smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                pss = int(line.split()[1]) * 1024
            elif line.startswith("Rss:"):
                rss = int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    if not rss:
        try:
            for line in (PROC / str(pid) / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                    break
        except (OSError, ValueError, IndexError):
            pass
    return (pss or rss), rss


def _io(pid: int) -> tuple[int, int]:
    read = write = 0
    try:
        for line in (PROC / str(pid) / "io").read_text().splitlines():
            key, _, value = line.partition(":")
            if key == "read_bytes":
                read = int(value)
            elif key == "write_bytes":
                write = int(value)
    except (OSError, ValueError):
        pass
    return read, write


def sample(root: int | None = None, marker: str | None = None) -> dict[str, Any]:
    """One reading of ``root``'s processes (default: this process's)."""
    return Tracker(root or os.getpid(), marker=marker).sample()


def cgroup_memory_peak(pid: int) -> int | None:
    """``memory.peak`` of the process's cgroup (v2), e.g. the systemd-run unit; None when missing."""
    try:
        line = next(l for l in (PROC / str(pid) / "cgroup").read_text().splitlines() if l.startswith("0::"))
        return int((Path("/sys/fs/cgroup") / line[3:].lstrip("/") / "memory.peak").read_text().strip())
    except (OSError, StopIteration, ValueError):
        return None


def dir_size(path: str | Path, budget: float = 10.0) -> dict[str, Any] | None:
    """Bytes below ``path`` (no symlinks followed), giving up after ``budget`` seconds."""
    root = Path(path)
    if not root.is_dir():
        return None
    total = files = 0
    deadline = time.monotonic() + budget
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in names:
            try:
                total += os.lstat(os.path.join(base, name)).st_size
                files += 1
            except OSError:
                pass
        if time.monotonic() > deadline:
            return {"bytes": total, "files": files, "partial": True}
    return {"bytes": total, "files": files}


class Sampler:
    """Samples a command's processes (see :class:`Tracker`) and writes them as JSON lines."""

    def __init__(self, root: int, out: Path | None, interval: float, *, marker: str | None = None,
                 workdir: str | None = None) -> None:
        self.root = root
        self.out = out
        self.interval = float(interval)
        self.enabled = available() and self.interval > 0
        self.tracker = Tracker(root, marker=marker) if available() else None
        self.started = time.time()
        self.last_t = 0.0
        self.last_cpu = 0.0
        self.max_cpu = 0.0
        self.peak_mem = 0
        self.peak_rss = 0
        self.max_read = 0
        self.max_write = 0
        self.samples = 0
        self.workdir = workdir
        self.workdir_start: dict[str, Any] | None = None
        if workdir:
            threading.Thread(target=self._size_at_start, daemon=True).start()

    def _size_at_start(self) -> None:
        self.workdir_start = dir_size(self.workdir) if self.workdir else None

    def due(self, now: float) -> bool:
        """A first sample after half a second (short steps get one too), then every ``interval``."""
        if not self.enabled:
            return False
        if self.samples == 0:
            return now - self.started >= min(0.5, self.interval)
        return now - self.started - self.last_t >= self.interval

    def take(self, now: float | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        now = time.time() if now is None else now
        reading = self.tracker.sample()
        t = now - self.started
        cpu = max(reading["cpu_s"], self.max_cpu)
        span = t - self.last_t
        cores = (cpu - self.last_cpu) / span if span > 0 else 0.0
        self.last_t, self.last_cpu, self.max_cpu = t, cpu, cpu
        self.peak_mem = max(self.peak_mem, reading["mem"])
        self.peak_rss = max(self.peak_rss, reading["rss"])
        self.max_read = max(self.max_read, reading["read_bytes"])
        self.max_write = max(self.max_write, reading["write_bytes"])
        self.samples += 1
        row = {"t": round(t, 1), "cores": round(max(0.0, cores), 2), **reading, "cpu_s": round(cpu, 2)}
        if self.out is not None:
            try:
                with open(self.out, "a", encoding="utf-8") as stream:
                    import json

                    stream.write(json.dumps(row) + "\n")
            except OSError:
                pass
        return row

    def summary(self, rusage: Any = None, *, launcher: str | None = None, child: int | None = None,
                reaped_cpu: float = 0.0) -> dict[str, Any]:
        """``rusage``: of the command (wait4); ``reaped_cpu``: CPU s of everything the shim reaped (orphans too)."""
        wall = time.time() - self.started
        cpu = self.max_cpu
        if self.tracker is not None:
            try:
                cpu = max(cpu, self.tracker.sample()["cpu_s"])  # the shim's cutime now holds what it reaped
            except Exception:  # noqa: BLE001
                pass
        cpu = max(cpu, reaped_cpu)
        max_rss = 0
        if rusage is not None:
            cpu = max(cpu, float(rusage.ru_utime + rusage.ru_stime))
            max_rss = int(rusage.ru_maxrss) * 1024 if sys.platform.startswith("linux") else int(rusage.ru_maxrss)
        sampled = self.samples > 0
        out: dict[str, Any] = {
            "wall_s": round(wall, 1), "cpu_s": round(cpu, 2), "avg_cores": round(cpu / wall, 2) if wall > 0 else 0.0,
            # Sampled group PSS when there are samples (sums processes, may miss a short spike);
            # else the largest single process's peak RSS from the kernel.
            "peak_mem": self.peak_mem if sampled else max_rss, "peak_mem_kind": "pss" if sampled else "maxrss",
            "peak_rss": self.peak_rss, "max_rss": max_rss,
            "read_bytes": self.max_read, "write_bytes": self.max_write, "samples": self.samples,
            "interval_s": self.interval, "limited": not available(),
            "counted": "descendants (subreaper), session, ALFRD_UNIT environment",
        }
        if launcher == "systemd-run" and child:
            unit_peak = cgroup_memory_peak(os.getpid())
            if unit_peak:
                out["cgroup_memory_peak"] = unit_peak
        if self.workdir:
            out["workdir"] = self.workdir
            out["workdir_start"] = self.workdir_start
            out["workdir_end"] = dir_size(self.workdir, budget=5.0)
        return out


__all__ = ["Sampler", "Tracker", "available", "become_subreaper", "cgroup_memory_peak", "dir_size", "group_pids", "sample", "scan"]
