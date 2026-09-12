from __future__ import annotations

import json
import importlib
import sys
from importlib import resources
from pathlib import Path

import pytest

from alfrd.manifest import (
    ManifestError,
    ManifestNotFoundError,
    ProjectManifest,
    discover_manifest,
    load_manifest,
    parse_manifest,
)


AVICA_MANIFEST = """
name: AVICA
entrypoint:
  - name: runner
    cmd: [python, -m, avica]
schema:
  - name: input
    definitions:
      - avica.config.InputConfig
      - avica.pipeline.PipelineConfig
"""


def test_discovery_walks_up_and_accepts_file_or_directory(tmp_path: Path):
    manifest_path = tmp_path / "alfrd.yaml"
    manifest_path.write_text(AVICA_MANIFEST, encoding="utf-8")
    nested = tmp_path / "src" / "avica"
    nested.mkdir(parents=True)
    source = nested / "module.py"
    source.write_text("", encoding="utf-8")

    assert discover_manifest(nested) == manifest_path
    assert discover_manifest(source) == manifest_path
    assert discover_manifest(manifest_path) == manifest_path


def test_discovery_has_a_specific_error(tmp_path: Path):
    with pytest.raises(ManifestNotFoundError, match="alfrd.yaml"):
        discover_manifest(tmp_path)


def test_load_legacy_avica_shape_without_importing_consumer(tmp_path: Path, monkeypatch):
    path = tmp_path / "alfrd.yaml"
    path.write_text(AVICA_MANIFEST, encoding="utf-8")

    real_import_module = importlib.import_module

    def guarded_import(name, package=None):
        if name.startswith("avica"):
            raise AssertionError("consumer code must not be imported")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", guarded_import)
    manifest = load_manifest(path)

    assert manifest.name == "AVICA"
    assert manifest.version == 1
    assert manifest.entrypoint[0].name == "runner"
    assert manifest.entrypoint[0].cmd == ("python", "-m", "avica")
    assert manifest.schema[0].definitions == (
        "avica.config.InputConfig",
        "avica.pipeline.PipelineConfig",
    )
    assert manifest.path == path.resolve()
    assert not any(name == "avica" or name.startswith("avica.") for name in sys.modules)


def test_manifest_round_trip_preserves_legacy_keys():
    manifest = parse_manifest(
        {
            "version": 1,
            "name": "consumer",
            "entrypoint": [{"name": "runner", "cmd": ["python", "run.py"]}],
            "schema": [{"name": "config", "definitions": ["pkg.config.Settings"]}],
        }
    )

    assert isinstance(manifest, ProjectManifest)
    assert manifest.to_dict() == {
        "version": 1,
        "name": "consumer",
        "entrypoint": [{"name": "runner", "cmd": ["python", "run.py"]}],
        "schema": [{"name": "config", "definitions": ["pkg.config.Settings"]}],
    }


def test_packaged_v1_schema_is_json_and_rejects_invalid_documents():
    schema_resource = resources.files("alfrd.schemas").joinpath("project-manifest-v1.schema.json")
    assert schema_resource.is_file()
    schema = json.loads(schema_resource.read_text(encoding="utf-8"))
    assert schema["$id"].endswith("project-manifest-v1.schema.json")

    with pytest.raises(ManifestError, match="entrypoint"):
        parse_manifest({"version": 1, "name": "bad", "entrypoint": "runner"})

    with pytest.raises(ManifestError, match="Unsupported manifest version"):
        parse_manifest({"version": 2, "name": "future"})


def test_yaml_errors_and_duplicate_names_are_reported(tmp_path: Path):
    malformed = tmp_path / "alfrd.yaml"
    malformed.write_text("name: [", encoding="utf-8")
    with pytest.raises(ManifestError, match="YAML"):
        load_manifest(malformed)

    with pytest.raises(ManifestError, match="runner"):
        parse_manifest(
            {
                "name": "duplicate",
                "entrypoint": [
                    {"name": "runner", "cmd": ["one"]},
                    {"name": "runner", "cmd": ["two"]},
                ],
            }
        )
