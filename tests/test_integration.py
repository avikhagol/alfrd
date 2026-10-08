from __future__ import annotations

from pathlib import Path

from alfrd import ArtifactDefinition
from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.manifest import load_manifest
from alfrd.runtime import RuntimeService, RuntimeStore


def test_manifest_runtime_and_web_catalog_integration(tmp_path: Path):
    project_root = tmp_path / "consumer"
    project_root.mkdir()
    manifest_path = project_root / "alfrd.yaml"
    manifest_path.write_text(
        """\
name: integrated
entrypoint:
  - name: build
    cmd: [python, -m, consumer]
artifacts:
  - name: report
    path_pattern: products/{dataset_id}.txt
    description: Build report
    media_type: text/plain
""",
        encoding="utf-8",
    )

    manifest = load_manifest(project_root)
    assert manifest.artifacts == (
        ArtifactDefinition(
            name="report",
            path_pattern="products/{dataset_id}.txt",
            description="Build report",
            media_type="text/plain",
        ),
    )

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(manifest_path)

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog-shell.sqlite'}",
            "CATALOG_READER": RuntimeCatalogReader(service),
        }
    )
    client = app.test_client()
    assert client.get("/api/projects").get_json()["projects"][0]["name"] == "integrated"
    assert client.get("/api/projects/integrated/manifest").get_json()["validation"]["valid"]
    assert client.get("/api/projects/integrated/workflows/build").get_json()["sequence"] == [
        "build"
    ]
    artifact = client.get(
        "/api/projects/integrated/artifact-definitions/report"
    ).get_json()
    assert artifact["path_pattern"] == "products/{dataset_id}.txt"
