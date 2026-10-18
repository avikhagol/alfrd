"""Server-side workflow dependency validation on every alfrd.yaml save (Studio, history, CLI helpers)."""

from __future__ import annotations

import pytest

from alfrd import history
from alfrd.studio_defs import save_manifest, workflow_dependency_errors

CYCLE = (
    "name: demo\n"
    "workflows:\n"
    "  - name: main\n"
    "    steps:\n"
    "      - {id: a, cmd: [echo, a]}\n"
    "      - {id: b, cmd: [echo, b], depends_on: [c]}\n"
    "      - {id: c, cmd: [echo, c], depends_on: [b]}\n"
)


def test_dependency_errors_cover_cycles_self_and_unknown_steps():
    wf = lambda steps: {"name": "x", "workflows": [{"name": "m", "steps": steps}]}  # noqa: E731
    assert workflow_dependency_errors(wf(["a", {"id": "b", "depends_on": ["a"]}])) == []
    assert workflow_dependency_errors(wf(["a", "b", "c"])) == []  # implicit chain
    assert workflow_dependency_errors(wf([{"id": "a", "depends_on": ["c"]}, {"id": "b", "after": "a"}, {"id": "c", "needs": "b"}])) == ["workflow 'm': dependency cycle a → b → c → a"]
    # A reordered list may contradict the implicit previous-step chain; that is not an error.
    assert workflow_dependency_errors(wf([{"id": "b", "depends_on": ["a"]}, "a"])) == []
    assert "depends on itself" in workflow_dependency_errors(wf([{"id": "a", "after": "a"}]))[0]
    assert "unknown step 'zz'" in workflow_dependency_errors(wf(["a", {"id": "b", "needs": "zz"}]))[0]
    # A delay in `after` is not a dependency; a skipped step drops out of the graph.
    assert workflow_dependency_errors(wf(["a", {"id": "b", "after": "+30m"}])) == []
    assert workflow_dependency_errors(wf(["a", {"id": "b", "skip": True}, {"id": "c", "depends_on": ["b"]}])) == []
    # Agent loops order their turns themselves.
    assert workflow_dependency_errors({"name": "x", "workflows": [{"name": "loop", "repeat": {"sequence": ["a"]}, "steps": [{"id": "a", "depends_on": ["a"]}]}]}) == []
    # Template workflows are valid as shipped.
    assert workflow_dependency_errors({"name": "x", "template": "avica", "workflows": [{"name": "avica", "steps": ["preprocess_fitsidi", "fits_to_ms", "avica_avg"]}]}) == []


def test_history_save_rejects_a_cycle_before_writing(tmp_path):
    (tmp_path / "alfrd.yaml").write_text("name: demo\n")
    with pytest.raises(ValueError, match=r"dependency cycle b → c → b"):
        history.save(tmp_path, "alfrd.yaml", CYCLE, source="studio")
    assert (tmp_path / "alfrd.yaml").read_text() == "name: demo\n"
    assert not any("depends_on: [c]" in p.read_text() for p in tmp_path.rglob("*") if p.is_file())  # nothing recorded
    with pytest.raises(ValueError, match="dependency cycle"):
        save_manifest(tmp_path, CYCLE)


def test_studio_manifest_save_returns_400_for_a_cycle(tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    root = tmp_path / "proj"
    root.mkdir()
    (root / "alfrd.yaml").write_text("name: demo\n")
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, root)
    pid = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEFAULT_PROJECT": pid, "STUDIO_DEMO": False})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    resp = client.post(f"/api/studio/projects/{pid}/manifest", json={"text": CYCLE}, headers=h)
    assert resp.status_code == 400
    assert "dependency cycle b → c → b" in resp.get_json()["error"]["message"]
    assert (root / "alfrd.yaml").read_text() == "name: demo\n"
    ok = CYCLE.replace("depends_on: [b]", "depends_on: [a]")
    assert client.post(f"/api/studio/projects/{pid}/manifest", json={"text": ok}, headers=h).status_code == 200
