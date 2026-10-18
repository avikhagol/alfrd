"""Studio New-project canvas: ``POST /api/studio/projects/create`` with a ``draft`` (initial steps and targets).

Everything is validated before a file is written; files land in ``.alfrd/`` unless a root alfrd.yaml is asked for.
"""
from __future__ import annotations

import pytest
import yaml

from alfrd.project_creation import apply_draft

pytest.importorskip("flask")


@pytest.fixture
def client(tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    c = app.test_client()
    c.h = {"X-CSRF-Token": c.get("/api/studio/session").get_json()["csrf_token"]}
    return c


def create(client, path, **payload):
    return client.post("/api/studio/projects/create", json={"path": str(path), "name": path.name, **payload}, headers=client.h)


def test_basic_draft_writes_steps_dependencies_and_targets(client, tmp_path):
    draft = {"steps": [{"id": "fetch", "label": "Fetch data", "cmd": ["echo", "fetch"], "depends_on": []},
                       {"id": "build", "label": "Build", "cmd": ["echo", "build"], "depends_on": ["fetch"]}],
             "targets": ["alpha", "beta"]}
    made = create(client, tmp_path / "canvas", draft=draft)
    assert made.status_code == 201, made.get_json()
    assert made.get_json()["targets"] == ["alpha", "beta"]
    root = tmp_path / "canvas"
    data = yaml.safe_load((root / ".alfrd" / "alfrd.yaml").read_text())
    assert not (root / "alfrd.yaml").exists()
    assert data["workflows"][0]["steps"] == [{"id": "fetch", "depends_on": []}, {"id": "build", "depends_on": ["fetch"]}]
    assert data["steps"]["build"] == {"label": "Build", "cmd": ["echo", "build"]}
    assert "alpha" in (root / "alfrd.targets.csv").read_text()
    from alfrd.execution import load_execution

    assert [s.id for s in load_execution(root).steps] == ["fetch", "build"]


def test_root_manifest_option_is_kept(client, tmp_path):
    made = create(client, tmp_path / "rooted", manifest="alfrd.yaml",
                  draft={"steps": [{"id": "a", "depends_on": []}], "targets": ["t1"]})
    assert made.status_code == 201 and (tmp_path / "rooted" / "alfrd.yaml").is_file()
    assert not (tmp_path / "rooted" / ".alfrd" / "alfrd.yaml").exists()


@pytest.mark.parametrize("draft, message", [
    ({"steps": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}]}, "cycle"),
    ({"steps": [{"id": "a", "depends_on": ["a"]}]}, "depends on itself"),
    ({"steps": [{"id": "a", "depends_on": ["zz"]}]}, "unknown step"),
    ({"steps": [{"id": "a"}, {"id": "a"}]}, "listed twice"),
    ({"steps": [{"id": "bad id"}]}, "step id"),
    ({"steps": [{"id": "a", "entrypoint": "nope"}]}, "entrypoint"),
    ({"steps": [{"id": "a", "cmd": "echo hi"}]}, "cmd"),
    ({"steps": [{"id": "a", "role": "ghost"}]}, "persona"),
    ({"steps": [{"id": "a", "shell": True}]}, "unknown field"),
    ({"steps": []}, "1–100"),
    ({"targets": ["#x"]}, "comment"),
    ({"targets": ["a", "a"]}, "listed twice"),
    ({"targets": ["a/b"]}, "cannot contain"),
    ({"other": 1}, "draft accepts"),
])
def test_invalid_drafts_write_nothing(client, tmp_path, draft, message):
    made = create(client, tmp_path / "bad", draft=draft)
    assert made.status_code == 400 and message in made.get_json()["error"]["message"]
    assert not (tmp_path / "bad").exists()


def test_avica_draft_keeps_template_step_definitions(client, tmp_path):
    draft = {"steps": [{"id": "preprocess_fitsidi", "depends_on": []}, {"id": "fits_to_ms", "depends_on": ["preprocess_fitsidi"]},
                       {"id": "check", "label": "Check", "entrypoint": "avica-step", "depends_on": ["fits_to_ms"]}],
             "targets": ["J0001+0001"]}
    made = create(client, tmp_path / "av", template="avica", draft=draft)
    assert made.status_code == 201, made.get_json()
    data = yaml.safe_load((tmp_path / "av" / ".alfrd" / "alfrd.yaml").read_text())
    assert [s["id"] for s in data["workflows"][0]["steps"]] == ["preprocess_fitsidi", "fits_to_ms", "check"]
    assert data["steps"]["fits_to_ms"]["label"] == "Convert FITS to CASA Measurement Set"
    assert data["steps"]["check"]["entrypoint"] == "avica-step"


def test_agent_loop_draft_names_the_first_task(client, tmp_path):
    made = create(client, tmp_path / "loop", template="agent-loop", task="Do things", sequence=["claude", "codex"],
                  iterations=4, draft={"targets": ["first-task"]})
    assert made.status_code == 201, made.get_json()
    root = tmp_path / "loop"
    assert (root / "first-task" / "task.md").read_text().startswith("Do things")
    bad = create(client, tmp_path / "loop2", template="agent-loop", task="x", draft={"steps": [{"id": "a"}]})
    assert bad.status_code == 400 and "sequence" in bad.get_json()["error"]["message"]
    two = create(client, tmp_path / "loop3", template="agent-loop", task="x", draft={"targets": ["a", "b"]})
    assert two.status_code == 400 and not (tmp_path / "loop3").exists()


def test_apply_draft_without_draft_changes_nothing():
    data = {"steps": {}, "workflows": []}
    assert apply_draft(data, None, looping=False) == [] and data == {"steps": {}, "workflows": []}
