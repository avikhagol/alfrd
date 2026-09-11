"""Read-only consumer repository registration and inspection services."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alfrd import get_alfrd_dir
from alfrd.manifest import ManifestError, ProjectManifest, discover_manifest, load_manifest


class RepositoryNotFoundError(KeyError):
    """A repository is not present in ALFRD's local index."""


@dataclass(frozen=True)
class RepositoryRecord:
    """A consumer repository and its last explicitly added/synced manifest."""

    name: str
    root: Path
    manifest_path: Path
    manifest: ProjectManifest


class RepositoryService:
    """Maintain an ALFRD-owned index without writing into consumer repositories."""

    def __init__(self, index_path: str | Path | None = None) -> None:
        self.index_path = (
            Path(index_path).expanduser().resolve()
            if index_path is not None
            else get_alfrd_dir().expanduser().resolve() / "repositories" / "index.json"
        )

    def add(self, repository: str | Path) -> RepositoryRecord:
        manifest_path = discover_manifest(repository)
        root = manifest_path.parent.resolve()
        manifest = load_manifest(manifest_path)
        index = self._read_index()

        for existing_name, item in index.items():
            existing_root = Path(item["root"]).resolve()
            if existing_name == manifest.name and existing_root != root:
                raise ManifestError(
                    f"Repository name {manifest.name!r} is already registered at {existing_root}"
                )
            if existing_root == root and existing_name != manifest.name:
                del index[existing_name]
                break

        index[manifest.name] = self._encode(root, manifest_path, manifest)
        self._write_index(index)
        return self._record(root, manifest_path, manifest)

    def sync(self, repository: str | Path) -> RepositoryRecord:
        index = self._read_index()
        old_name, item = self._resolve(repository, index)
        manifest_path = Path(item["manifest_path"]).resolve()
        manifest = load_manifest(manifest_path)
        root = Path(item["root"]).resolve()

        conflict = index.get(manifest.name)
        if manifest.name != old_name and conflict is not None:
            conflict_root = Path(conflict["root"]).resolve()
            if conflict_root != root:
                raise ManifestError(
                    f"Repository name {manifest.name!r} is already registered at {conflict_root}"
                )
        if old_name != manifest.name:
            del index[old_name]
        index[manifest.name] = self._encode(root, manifest_path, manifest)
        self._write_index(index)
        return self._record(root, manifest_path, manifest)

    def inspect(self, repository: str | Path) -> RepositoryRecord:
        index = self._read_index()
        try:
            _, item = self._resolve(repository, index)
        except RepositoryNotFoundError:
            requested = Path(repository).expanduser()
            if not requested.exists():
                raise
            manifest_path = discover_manifest(requested)
            manifest = load_manifest(manifest_path)
            return self._record(manifest_path.parent.resolve(), manifest_path, manifest)
        manifest_path = Path(item["manifest_path"]).resolve()
        manifest_data = item.get("manifest")
        if isinstance(manifest_data, dict):
            from alfrd.manifest import parse_manifest

            manifest = parse_manifest(manifest_data, source=manifest_path)
        else:  # Forward compatibility with an index written by an early build.
            manifest = load_manifest(manifest_path)
        return self._record(Path(item["root"]).resolve(), manifest_path, manifest)

    def list(self) -> tuple[RepositoryRecord, ...]:
        return tuple(self.inspect(name) for name in sorted(self._read_index()))

    def _resolve(
        self, repository: str | Path, index: dict[str, dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        key = str(repository)
        if key in index:
            return key, index[key]

        requested = Path(repository).expanduser().resolve()
        for name, item in index.items():
            root = Path(item["root"]).resolve()
            manifest_path = Path(item["manifest_path"]).resolve()
            if requested in {root, manifest_path}:
                return name, item
        raise RepositoryNotFoundError(f"Repository is not registered: {repository}")

    def _read_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Could not read repository index {self.index_path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ManifestError(f"Unsupported repository index: {self.index_path}")
        repositories = data.get("repositories")
        if not isinstance(repositories, dict):
            raise ManifestError(f"Invalid repository index: {self.index_path}")
        return repositories

    def _write_index(self, repositories: dict[str, dict[str, Any]]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": 1, "repositories": repositories}, indent=2, sort_keys=True
        ) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.index_path.name}.", dir=self.index_path.parent, text=True
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.index_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _encode(
        root: Path, manifest_path: Path, manifest: ProjectManifest
    ) -> dict[str, Any]:
        return {
            "root": str(root),
            "manifest_path": str(manifest_path),
            "manifest": manifest.to_dict(),
        }

    @staticmethod
    def _record(
        root: Path, manifest_path: Path, manifest: ProjectManifest
    ) -> RepositoryRecord:
        return RepositoryRecord(
            name=manifest.name,
            root=root,
            manifest_path=manifest_path,
            manifest=manifest,
        )


# Concise service-function API for callers that do not need dependency injection.
def add_repository(repository: str | Path) -> RepositoryRecord:
    return RepositoryService().add(repository)


def sync_repository(repository: str | Path) -> RepositoryRecord:
    return RepositoryService().sync(repository)


def inspect_repository(repository: str | Path) -> RepositoryRecord:
    return RepositoryService().inspect(repository)


Repository = RepositoryService

__all__ = [
    "Repository",
    "RepositoryNotFoundError",
    "RepositoryRecord",
    "RepositoryService",
    "add_repository",
    "inspect_repository",
    "sync_repository",
]
