"""Per-task git worktrees for agent loops (``loop.workspace: worktree``).

Each task folder ``{target}/`` holds its handoffs; ``{target}/workspace`` is a
git worktree of the project's repository on branch ``alfrd/<target>``, so
tasks can run in parallel without editing the same files. ALFRD never
commits, merges or pushes there. A project outside git keeps one shared
working tree, with a warning.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

WORKSPACE_DIR = "workspace"
BRANCH_PREFIX = "alfrd/"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check, timeout=60)


def repository(root: Path) -> Path | None:
    """The top level of the git repository containing ``root``, if any."""
    try:
        result = _git(root, "rev-parse", "--show-toplevel", check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def workspace_path(root: Path, target: str) -> Path:
    return Path(root).resolve() / target / WORKSPACE_DIR


def agent_cwd(root: Path, target: str) -> Path | None:
    """Where a task's agents run: the project's folder inside the task's worktree, when it exists."""
    root = Path(root).resolve()
    path = workspace_path(root, target)
    if not (path / ".git").exists():
        return None
    top = repository(root)
    inner = root.relative_to(top) if top and root.is_relative_to(top) else Path()
    cwd = path / inner
    return cwd if cwd.is_dir() else None


def _exclude(top: Path, entry: str) -> None:
    """Keep the worktree out of the main checkout's ``git status`` without editing a tracked file."""
    path = Path(_git(top, "rev-parse", "--git-path", "info/exclude").stdout.strip())
    path = path if path.is_absolute() else top / path
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if entry not in lines:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(("\n" if lines and lines[-1] else "") + entry + "\n")


def _branch(top: Path, target: str) -> str:
    """``alfrd/<target>``, or a numbered name when that branch already exists (never reset one)."""
    name, n = BRANCH_PREFIX + target, 2
    while _git(top, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", check=False).returncode == 0:
        name, n = f"{BRANCH_PREFIX}{target}-{n}", n + 1
    return name


def ensure(root: Path, target: str) -> dict[str, Any]:
    """Create the task's worktree if it is missing.

    Returns ``{"workspace": path, "branch": name}``, or ``{"workspace": None,
    "warning": text}`` when the project is not in a git repository.
    """
    root = Path(root).resolve()
    path = workspace_path(root, target)
    top = repository(root)
    if top is None:
        return {"workspace": None, "warning": "the project is not in a git repository; tasks share one working tree"}
    if (path / ".git").exists():
        return {"workspace": str(path), "branch": branch_of(path)}
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"{path} exists and is not a git worktree; move it away first")
    head = _git(top, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    if head.returncode != 0:
        return {"workspace": None, "warning": "the repository has no commit yet; tasks share one working tree"}
    inner = root.relative_to(top).as_posix()
    if inner != "." and _git(top, "cat-file", "-e", f"HEAD:{inner}", check=False).returncode != 0:
        # A worktree holds only committed files: without the project folder it would hold none of its code.
        return {"workspace": None, "warning": f"{inner} is not committed in the repository; commit it for per-task worktrees "
                                              "(tasks share one working tree until then)"}
    branch = _branch(top, target)
    rel = path.relative_to(top).as_posix()
    _exclude(top, f"/{rel}/")
    try:
        _git(top, "worktree", "add", "-b", branch, str(path), "HEAD")
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"cannot create the task worktree: {(exc.stderr or exc.stdout).strip()}") from exc
    return {"workspace": str(path), "branch": branch}


def branch_of(path: Path) -> str | None:
    result = _git(path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    return result.stdout.strip() or None if result.returncode == 0 else None


def repair(root: Path, target: str) -> None:
    """Re-link a worktree after its task folder moved (task rename)."""
    path = workspace_path(root, target)
    top = repository(Path(root).resolve())
    if top is None or not (path / ".git").exists():
        return
    _git(top, "worktree", "repair", str(path), check=False)
    _exclude(top, f"/{path.relative_to(top).as_posix()}/")


def describe(root: Path, target: str) -> dict[str, Any] | None:
    """Branch and uncommitted change count of a task's worktree, for the overview."""
    path = workspace_path(root, target)
    if not (path / ".git").exists():
        return None
    status = _git(path, "status", "--porcelain", check=False)
    changed = len([line for line in status.stdout.splitlines() if line.strip()]) if status.returncode == 0 else None
    return {"path": str(path), "branch": branch_of(path), "changed": changed}
