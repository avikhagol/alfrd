from __future__ import annotations

from pathlib import Path

import pytest

from alfrd.config import BaseConfig, CONFIG_MAPPING, Config
from alfrd.manifest import ManifestError
from alfrd.repository import (
    RepositoryNotFoundError,
    RepositoryService,
    add_repository,
    inspect_repository,
    sync_repository,
)


def _consumer_repo(root: Path, *, name: str = "consumer") -> Path:
    repo = root / "consumer-repo"
    repo.mkdir(parents=True)
    (repo / "alfrd.yaml").write_text(
        f"""name: {name}
entrypoint:
  - name: runner
    cmd: [python, -m, consumer]
schema:
  - name: input
    definitions: [consumer.config.Settings]
""",
        encoding="utf-8",
    )
    (repo / "consumer.py").write_text("SENTINEL = True\n", encoding="utf-8")
    return repo


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_generic_config_preserves_avica_class_and_mapping_shape():
    assert issubclass(BaseConfig, type)

    class ConsumerConfig(Config):
        count = 3
        label = "default"

    CONFIG_MAPPING["count"] = "renamed_count"
    try:
        config = ConsumerConfig(count=4)
        assert config.data == {"renamed_count": 4, "label": "default"}
        assert config.to_dict() == config.data
    finally:
        CONFIG_MAPPING.clear()


def test_config_can_load_yaml_and_override_class_defaults(tmp_path: Path):
    class ConsumerConfig(Config):
        count = 1
        label = "default"

    path = tmp_path / "config.yaml"
    path.write_text("count: 7\n", encoding="utf-8")
    config = ConsumerConfig.from_yaml(path)
    assert config.count == 7
    assert config.label == "default"
    assert config.source == path.resolve()


def test_repository_add_sync_inspect_are_read_only_for_consumer(tmp_path: Path):
    repo = _consumer_repo(tmp_path)
    before = _snapshot(repo)
    service = RepositoryService()

    record = service.add(repo)
    assert record.name == "consumer"
    assert record.root == repo.resolve()
    assert record.manifest_path == (repo / "alfrd.yaml").resolve()
    assert record.manifest.name == "consumer"
    assert _snapshot(repo) == before

    inspected = service.inspect("consumer")
    assert inspected == record
    assert service.list() == (record,)

    (repo / "alfrd.yaml").write_text(
        (repo / "alfrd.yaml").read_text(encoding="utf-8").replace(
            "name: consumer", "name: renamed"
        ),
        encoding="utf-8",
    )
    before_sync = _snapshot(repo)
    synced = service.sync("consumer")
    assert synced.name == "renamed"
    assert _snapshot(repo) == before_sync
    assert service.inspect("renamed") == synced
    with pytest.raises(RepositoryNotFoundError):
        service.inspect("consumer")


def test_repository_function_api_and_persistent_index(tmp_path: Path):
    repo = _consumer_repo(tmp_path)
    assert inspect_repository(repo).name == "consumer"
    added = add_repository(repo)
    assert inspect_repository("consumer") == added
    assert sync_repository(repo).name == "consumer"

    reloaded = RepositoryService().inspect(repo)
    assert reloaded.root == repo.resolve()


def test_repository_rejects_duplicate_name_and_missing_manifest(tmp_path: Path):
    first = _consumer_repo(tmp_path / "first", name="same")
    second = _consumer_repo(tmp_path / "second", name="same")
    service = RepositoryService()
    service.add(first)

    with pytest.raises(ManifestError, match="already registered"):
        service.add(second)
    with pytest.raises(RepositoryNotFoundError):
        service.sync("missing")
