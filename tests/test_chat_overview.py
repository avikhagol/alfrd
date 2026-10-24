"""``R/overview`` projection and hash-checked ``S/artifacts/raw`` (L2, contract C22)."""
from __future__ import annotations

import hashlib

import pytest

from alfrd import chat
from alfrd import overview as ov

from tests.test_chat_orchestration import project, say  # noqa: F401  (the project fixture)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def client(project, tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    ident = service.list_projects()[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    test = app.test_client()
    test.base = f"/api/studio/projects/{ident}/chat/sessions/"
    return test


def task(row, *, criteria=(), artifacts=(), expected=(), evidence=None, status="done"):
    result = {"status": "done", "summary": "ok", "artifacts": list(artifacts)}
    if evidence is not None:
        result["evidence"] = evidence
    return {"row": row, "kind": "task", "owner": "codex", "status": status, "created_ns": 1,
            "task": {"id": row, "acceptance_criteria": list(criteria), "expected_outputs": list(expected)},
            "result": result}


def test_legacy_session_has_empty_criteria_and_files(project, client):
    sid = chat.create_session(project, "Legacy")["id"]
    body = client.get(client.base + sid + "/overview").get_json()
    assert body["criteria"] == [] and body["files"] == []
    assert body["goal"] == {"text": "", "source": None}
    assert body["rev"] == chat.session(project, sid).rev


def test_overview_goal_criteria_and_files_are_deduplicated(project, client):
    sid = chat.create_session(project, "Over")["id"]
    say(project, sid, "@codex build the report")
    store = chat.session(project, sid)
    (project / "out.txt").write_text("v1")
    ref = f"out.txt@{sha(b'v1')}"
    store.save_task(task("r0001.a", criteria=["It builds", "It is fast"], artifacts=[ref],
                         evidence=[{"criterion": 0, "verdict": "verified"}]))
    store.save_task(task("r0001.b", criteria=["It builds"], artifacts=[ref, f"gone.txt@{sha(b'x')}"],
                         expected=["later.txt"], status="queued"))
    body = client.get(client.base + sid + "/overview").get_json()
    assert body["goal"]["text"] == "@codex build the report" and body["goal"]["source"] == "request:r0001"
    status = {c["id"]: c["status"] for c in body["criteria"]}
    assert status == {"r0001.t1.c1": "unverified", "r0001.a.c1": "verified", "r0001.a.c2": "unverified",
                      "r0001.b.c1": "unverified"}  # t1 is the real @codex task: no evidence, so unverified
    files = {f["path"]: f for f in body["files"]}
    assert len([f for f in body["files"] if f["path"] == "out.txt"]) == 1
    assert files["out.txt"]["producer_rows"] == ["r0001.a", "r0001.b"]
    assert files["out.txt"]["state"] == "recorded" and files["out.txt"]["bytes"] == 2
    assert files["gone.txt"]["state"] == "missing"
    assert files["later.txt"]["state"] == "expected"
    (project / "out.txt").write_text("v2")
    changed = {f["path"]: f["state"] for f in client.get(client.base + sid + "/overview").get_json()["files"]}
    assert changed["out.txt"] == "changed"


def test_raw_returns_bytes_and_409_on_stale_hash(project, client):
    sid = chat.create_session(project, "Raw")["id"]
    (project / "a.txt").write_bytes(b"hello")
    url = client.base + sid + "/artifacts/raw?ref="
    ok = client.get(url + f"a.txt@{sha(b'hello')}")
    assert ok.status_code == 200 and ok.data == b"hello"
    assert ok.headers["X-Content-Type-Options"] == "nosniff"
    (project / "a.txt").write_bytes(b"changed")
    assert client.get(url + f"a.txt@{sha(b'hello')}").status_code == 409
    assert client.get(url + f"missing.txt@{sha(b'')}").status_code == 404
    assert client.get(url + "not-a-ref").status_code == 400


def test_raw_refuses_traversal(project, client, tmp_path):
    sid = chat.create_session(project, "Trav")["id"]
    (tmp_path / "secret.txt").write_text("s")
    url = client.base + sid + "/artifacts/raw?ref="
    digest = sha(b"s")
    for path in ("../secret.txt", "/etc/passwd", "sub/../../secret.txt", "~/x"):
        assert client.get(url + f"{path}@{digest}").status_code in (400, 403), path


def test_raw_refuses_symlinks(project, client, tmp_path):
    sid = chat.create_session(project, "Sym")["id"]
    (project / "real.txt").write_text("data")
    (project / "link.txt").symlink_to(project / "real.txt")  # inside the root, still refused
    (tmp_path / "outside.txt").write_text("data")
    (project / "out.txt").symlink_to(tmp_path / "outside.txt")
    (project / "realdir").mkdir()
    (project / "realdir" / "f.txt").write_text("data")
    (project / "linkdir").symlink_to(project / "realdir")
    url = client.base + sid + "/artifacts/raw?ref="
    digest = sha(b"data")
    for path in ("link.txt", "out.txt", "linkdir/f.txt"):
        assert client.get(url + f"{path}@{digest}").status_code == 403, path
    assert client.get(url + f"real.txt@{digest}").status_code == 200


def test_raw_caps_at_20_mb(project, client, monkeypatch):
    sid = chat.create_session(project, "Big")["id"]
    monkeypatch.setattr(ov, "MAX_RAW_BYTES", 1024)
    (project / "big.bin").write_bytes(b"x" * 1025)
    (project / "fit.bin").write_bytes(b"x" * 1024)
    url = client.base + sid + "/artifacts/raw?ref="
    assert client.get(url + f"big.bin@{sha(b'x' * 1025)}").status_code == 413
    assert client.get(url + f"fit.bin@{sha(b'x' * 1024)}").status_code == 200


def test_cap_constant_is_20_mb():
    assert ov.MAX_RAW_BYTES == 20 * 1024 * 1024


def test_unknown_session_is_404(project, client):
    assert client.get(client.base + "nosuch/overview").status_code == 404
    assert client.get(client.base + f"nosuch/artifacts/raw?ref=a@{sha(b'')}").status_code == 404
