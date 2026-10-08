"""Shared plugin installer. Full-trust packages live outside alfrd's environment.

All mutations take a per-user lock. Resolution happens in a staged copy, so a
failed install leaves the current plugin site and inventory untouched.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import io
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from . import GROUP, plugins_dir, site_dir


class InstallError(RuntimeError):
    """A clean, user-facing installer failure (including the resolver output)."""


class InstallerBusy(InstallError):
    """Another process is changing the same plugin site."""


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def installed() -> dict[str, dict]:
    path = plugins_dir() / "installed.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise InstallError(f"Cannot read plugin inventory: {exc}") from exc
    if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
        raise InstallError("Invalid plugin inventory; restore installed.json before changing plugins")
    return data


def _write_inventory(data: dict) -> None:
    parent = plugins_dir()
    fd, name = tempfile.mkstemp(prefix=".installed-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(name, parent / "installed.json")
    finally:
        Path(name).unlink(missing_ok=True)


def build_command(spec: str, *, site: Path, constraints: Path, upgrade: bool = False,
                  extra: tuple[str, ...] = (), requirements: Path | None = None) -> list[str]:
    """With ``requirements`` (a hashed requirements file) the install is ``--require-hashes --no-deps -r`` it."""
    uv = shutil.which("uv")
    command = ([uv, "pip", "install", "--target", str(site), "--python", sys.executable]
               if uv else [sys.executable, "-m", "pip", "install", "--target", str(site)])
    command += ["--constraint", str(constraints)]
    if upgrade:
        command.append("--upgrade")
    if requirements is not None:
        return command + list(extra) + ["--require-hashes", "--no-deps", "-r", str(requirements)]
    return command + list(extra) + [spec]


def write_constraints(path: Path) -> Path:
    """Pin only distributions belonging to the host environment, never plugins."""
    site = site_dir().resolve()
    pins = {}
    for dist in metadata.distributions():
        if Path(dist.locate_file("")).resolve().is_relative_to(site):
            continue
        name = dist.metadata.get("Name")
        if name and dist.version:
            pins[_name(name)] = dist.version
    path.write_text("".join(f"{name}=={version}\n" for name, version in sorted(pins.items())), encoding="utf-8")
    return path


@contextmanager
def install_lock():
    parent = plugins_dir()
    parent.mkdir(parents=True, exist_ok=True)
    with (parent / ".install.lock").open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallerBusy("another install or remove is running") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _distributions(site: Path) -> dict[str, metadata.Distribution]:
    return {_name(d.metadata["Name"]): d for d in metadata.distributions(path=[str(site)]) if d.metadata.get("Name")}


def _fingerprint(dist: metadata.Distribution) -> str:
    return hashlib.sha256((str(dist.version) + (dist.read_text("METADATA") or "") +
                           (dist.read_text("entry_points.txt") or "")).encode()).hexdigest()


def _source_name(spec: str) -> str:
    path = Path(spec)
    if path.is_file() and path.suffix == ".whl":
        with zipfile.ZipFile(path) as wheel:
            member = next((n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")), None)
            if member:
                for line in wheel.read(member).decode().splitlines():
                    if line.startswith("Name: "):
                        return _name(line[6:])
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[|[<>=!~ @]|$)", spec)
    return _name(match[1]) if match else ""


def _owned(dist: metadata.Distribution, dists: dict) -> set[str]:
    """Include installed dependency closure for shared-file ownership tracking."""
    todo, out = [_name(dist.metadata["Name"])], set()
    while todo:
        name = todo.pop()
        if name in out or name not in dists:
            continue
        out.add(name)
        for req in dists[name].requires or ():
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9_.-]*)", req)
            if match:
                todo.append(_name(match[1]))
    return out


def _checked(site: Path, relative: str) -> Path:
    path = site / relative
    if not path.resolve().is_relative_to(site.resolve()):
        raise InstallError(f"Distribution RECORD points outside plugin site: {relative}")
    return path


def _record_files(dist: metadata.Distribution) -> list[str]:
    # Distribution.files may silently discard missing paths. Validate the raw
    # RECORD instead, including malicious or already-missing entries.
    record = dist.read_text("RECORD")
    if record is None:
        raise InstallError(f"Cannot remove {dist.metadata['Name']}: distribution has no RECORD")
    try:
        return [row[0] for row in csv.reader(io.StringIO(record)) if row]
    except csv.Error as exc:
        raise InstallError(f"Invalid distribution RECORD: {exc}") from exc


def _delete_dists(site: Path, names: set[str], retained: set[str]) -> None:
    dists = _distributions(site)
    # A retained distribution may own the same namespace file as one being removed.
    protect = {str(_checked(site, str(file))) for name in retained if name in dists
               for file in _record_files(dists[name])}
    paths = []
    for name in names:
        if name not in dists:
            continue
        files = _record_files(dists[name])
        paths += [_checked(site, str(file)) for file in files]
    # Validate every path before touching any file.
    for path in paths:
        if str(path) not in protect:
            path.unlink(missing_ok=True)
            if path.suffix == ".py":
                cache = path.parent / "__pycache__"
                if cache.is_dir() and not cache.is_symlink():
                    for pyc in cache.glob(f"{path.stem}.*.pyc"):
                        _checked(site, str(pyc.relative_to(site))).unlink(missing_ok=True)
    for path in sorted(site.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError:
                pass


def _promote(stage: Path, inventory: dict) -> None:
    site = site_dir()
    backup = stage.parent / "previous"
    had_site = site.exists()
    if had_site:
        site.rename(backup)
    try:
        stage.rename(site)
        _write_inventory(inventory)
    except BaseException:
        if site.exists():
            shutil.rmtree(site)
        if had_site:
            backup.rename(site)
        raise
    # The new site and inventory are in place; a failed cleanup must not report a failed install.
    shutil.rmtree(backup, ignore_errors=True)


def _install(spec: str, inventory: dict, *, upgrade: bool, extra: tuple[str, ...],
             requirements: Path | None = None, pinned: dict | None = None,
             expect_id: str | None = None) -> list[dict]:
    site = site_dir()
    previously_owned = {name for r in inventory.values() for name in r.get("dists", [])}
    with tempfile.TemporaryDirectory(prefix=".install-", dir=plugins_dir()) as work:
        root = Path(work)
        stage = root / "site"
        if site.exists():
            shutil.copytree(site, stage, symlinks=True)
        else:
            stage.mkdir()
        before = {name: _fingerprint(d) for name, d in _distributions(stage).items()}
        constraints = write_constraints(root / "constraints.txt")
        command = build_command(spec, site=stage, constraints=constraints, upgrade=upgrade, extra=extra,
                                requirements=requirements)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError(f"Plugin installation failed: {exc}") from exc
        if result.returncode:
            tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-30:])
            raise InstallError(tail or f"Installer exited with status {result.returncode}")
        # uv's target lock is an installer artifact, not part of a package.
        if not (site / ".lock").exists():
            (stage / ".lock").unlink(missing_ok=True)
        after = _distributions(stage)
        changed = {name for name, d in after.items() if before.get(name) != _fingerprint(d)}
        source_name = _source_name(spec)
        candidates = changed | {source_name} | {r.get("distribution", "") for r in inventory.values() if r.get("source") == spec}
        records = []
        for name in sorted(candidates):
            if name not in after:
                continue
            dist = after[name]
            eps = [ep for ep in dist.entry_points if ep.group == GROUP]
            if len(eps) > 1:
                raise InstallError(f"{name} declares multiple alfrd.plugins entry points; a plugin distribution must declare one")
            for ep in eps:
                previous = inventory.get(ep.name)
                if previous and previous.get("distribution") != name:
                    raise InstallError(f"Plugin id {ep.name!r} is already installed from {previous.get('distribution')}")
                if previous and previous.get("source") != spec and name != source_name:
                    # Another installed plugin changed as a dependency of this spec: keep its own source.
                    inventory[ep.name] = {**previous, "version": dist.version}
                    continue
                source = Path(spec)
                record = dict(id=ep.name, distribution=name, version=dist.version, source=spec,
                              sha256=hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None,
                              installed_at=datetime.now(timezone.utc).isoformat(),
                              dists=sorted(_owned(dist, after) | changed))
                # Don't assign another plugin's newly installed dist to this plugin.
                record["dists"] = [n for n in record["dists"] if n == name or not any(e.group == GROUP for e in after[n].entry_points)]
                inventory[ep.name] = record
                records.append(record)
        if not records:
            raise InstallError("Package has no alfrd.plugins entry point; nothing was installed")
        if expect_id is not None and [r["id"] for r in records] != [expect_id]:
            # Advanced Studio installs: the user typed the id they expect this source to provide.
            raise InstallError(f"This package provides {', '.join(r['id'] for r in records)}, "
                               f"not {expect_id!r}; nothing was installed")
        if pinned is not None:
            # A catalog install must be the plugin the catalog names; record where it came from.
            if [r["id"] for r in records] != [pinned["id"]]:
                raise InstallError(f"The catalog wheel for {pinned['id']!r} provides "
                                   f"{', '.join(r['id'] for r in records)}; nothing was installed")
            records[0].update(source=f"catalog:{pinned['id']}", wheel=pinned["wheel"], sha256=pinned["sha256"])
            if records[0]["version"] != pinned["version"]:
                raise InstallError(f"The catalog wheel for {pinned['id']} {pinned['version']} "
                                   f"is version {records[0]['version']}; nothing was installed")
        retained = {name for r in inventory.values() for name in r.get("dists", [])}
        _delete_dists(stage, previously_owned - retained, retained)
        _promote(stage, inventory)
        return records


def install(spec: str, *, extra: tuple[str, ...] = (), upgrade: bool = False,
            expect_id: str | None = None) -> list[dict]:
    """``expect_id``: abort before anything changes unless the package provides exactly that plugin."""
    if not spec or spec.startswith("-"):
        raise InstallError("Specify a package name, URL, or local wheel")
    try:
        # Updates must work from any working directory, including a GUI job.
        source = Path(spec)
        if source.exists():
            spec = str(source.resolve())
        with install_lock():
            return _install(spec, installed(), upgrade=upgrade, extra=extra, expect_id=expect_id)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise InstallError(str(exc)) from exc


#: Catalog wheels are downloaded (and hash-checked) by us before uv sees them.
MAX_WHEEL = 200 * 1024 * 1024


def _download(url: str, sha256: str, folder: Path) -> Path:
    from .catalog import CatalogError, open_url

    name = Path(url.rsplit("/", 1)[-1].split("?", 1)[0]).name
    if not name.endswith(".whl") or name.startswith("."):
        raise InstallError(f"Catalog wheel URL must end in a .whl file name: {url}")
    dest, digest, size = folder / name, hashlib.sha256(), 0
    try:
        with open_url(url, MAX_WHEEL, timeout=60) as source, dest.open("wb") as out:
            while chunk := source.read(1 << 20):
                size += len(chunk)
                if size > MAX_WHEEL:
                    raise InstallError(f"Catalog wheel is larger than {MAX_WHEEL >> 20} MiB: {url}")
                digest.update(chunk)
                out.write(chunk)
    except (CatalogError, OSError, ValueError) as exc:
        raise InstallError(f"Cannot download {url}: {exc}") from exc
    if digest.hexdigest() != sha256:
        raise InstallError(f"Hash mismatch for {name}: the catalog says sha256 {sha256}, "
                           f"the download is {digest.hexdigest()}; nothing was installed")
    return dest


def install_pinned(plugin_id: str, version: dict, *, extra: tuple[str, ...] = ()) -> list[dict]:
    """Install one catalog version: the wheel pinned to its sha256 plus its hashed ``requirements``.

    ``version`` is a catalog version entry (``version``, ``wheel``, ``sha256``,
    optional ``requirements``). Host dependencies (alfrd, flask, …) come from
    alfrd's environment, so the install runs ``--no-deps``.
    """
    try:
        with install_lock():
            return _install_pinned(plugin_id, version, installed(), extra)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise InstallError(str(exc)) from exc


def _install_pinned(plugin_id: str, version: dict, inventory: dict, extra: tuple[str, ...]) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix=".download-", dir=plugins_dir()) as work:
        wheel = _download(version["wheel"], version["sha256"], Path(work))
        req = Path(work) / "requirements.txt"
        lines = [f"{wheel} --hash=sha256:{version['sha256']}", *version.get("requirements", [])]
        req.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        pinned = {"id": plugin_id, **{k: version[k] for k in ("version", "wheel", "sha256")}}
        return _install(str(wheel), inventory, upgrade=True, extra=extra, requirements=req, pinned=pinned)


def remove(plugin_id: str) -> None:
    try:
        with install_lock():
            inventory = installed()
            if plugin_id not in inventory:
                raise InstallError(f"Plugin {plugin_id!r} was not installed by alfrd")
            record = inventory.pop(plugin_id)
            retained = {name for r in inventory.values() for name in r.get("dists", [])}
            # Multiple entry points in one distribution are an indivisible install.
            if record["distribution"] in retained:
                raise InstallError("This distribution also provides another installed plugin; remove them together is not yet supported")
            with tempfile.TemporaryDirectory(prefix=".remove-", dir=plugins_dir()) as work:
                stage = Path(work) / "site"
                if site_dir().exists():
                    shutil.copytree(site_dir(), stage, symlinks=True)
                else:
                    stage.mkdir()
                _delete_dists(stage, set(record.get("dists", [])) - retained, retained)
                _promote(stage, inventory)
    except OSError as exc:
        raise InstallError(str(exc)) from exc


def update(plugin_id: str | None = None, *, extra: tuple[str, ...] = ()) -> list[dict]:
    try:
        with install_lock():
            inventory = installed()
            ids = [plugin_id] if plugin_id else list(inventory)
            out = []
            for ident in ids:
                if ident not in inventory:
                    raise InstallError(f"Plugin {ident!r} was not installed by alfrd")
                if str(inventory[ident].get("source", "")).startswith("catalog:"):
                    # Catalog installs update to the newest catalog version, pinned again.
                    from . import catalog

                    try:
                        _, version = catalog.find(catalog.get()["catalog"], ident)
                    except catalog.CatalogError as exc:
                        raise InstallError(str(exc)) from exc
                    out.extend(_install_pinned(ident, version, inventory, extra))
                    continue
                out.extend(_install(inventory[ident]["source"], inventory, upgrade=True, extra=extra))
            return out
    except (OSError, ValueError) as exc:
        raise InstallError(str(exc)) from exc
