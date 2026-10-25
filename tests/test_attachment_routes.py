"""Attachment routes and message attachments / client_message_id (A2, contracts C11, C14)."""
from __future__ import annotations

import io
import struct
import zlib

import pytest

from alfrd import attachments as att
from alfrd import chat
from alfrd.gui import create_app
from alfrd.gui.services import RuntimeCatalogReader
from alfrd.runtime import RuntimeService, RuntimeStore

from tests.test_chat_orchestration import project  # noqa: F401  (the project fixture)


def png(width=2, height=2, extra=b""):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"") + extra)


@pytest.fixture
def web(project, tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    ident = service.list_projects()[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    base = f"/api/studio/projects/{ident}/chat"
    sid = chat.create_session(project, "A2")["id"]
    return client, headers, base, sid, project


def upload(web, data, name="a.png", mime="image/png", sid=None, headers=True):
    client, hdr, base, own, _ = web
    return client.post(f"{base}/sessions/{sid or own}/attachments", headers=hdr if headers else {},
                       data={"file": (io.BytesIO(data), name, mime)}, content_type="multipart/form-data")


def test_upload_201_and_content_roundtrip(web):
    client, _, base, sid, _ = web
    data = png(3, 5)
    resp = upload(web, data, name="../shot.png")
    assert resp.status_code == 201
    body = resp.get_json()
    assert set(body) == {"ref", "sha256", "media_type", "bytes", "width", "height", "name"}
    assert (body["media_type"], body["bytes"], body["width"], body["height"], body["name"]) == (
        "image/png", len(data), 3, 5, "shot.png")
    got = client.get(f"{base}/sessions/{sid}/attachments/{body['ref']}/content")
    assert got.status_code == 200 and got.data == data and got.mimetype == "image/png"
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    assert upload(web, data).get_json()["ref"] == body["ref"]  # same bytes, same ref


def test_upload_rejections(web):
    assert upload(web, png(), name="a.svg", mime="image/svg+xml").status_code == 415
    assert upload(web, b"<svg xmlns='http://www.w3.org/2000/svg'/>").status_code == 415
    assert upload(web, png(), mime="image/jpeg").status_code == 415  # declared type does not match the bytes
    assert upload(web, png(extra=b"\0" * att.MAX_IMAGE_BYTES)).status_code == 413
    assert upload(web, png(8001, 1)).status_code == 413
    assert upload(web, b"").status_code == 400
    client, _, base, sid, _ = web
    assert client.post(f"{base}/sessions/{sid}/attachments", headers=web[1]).status_code == 400  # no file
    assert upload(web, png(), sid="nosuchsession").status_code == 404


def test_upload_keeps_csrf_check(web):
    assert upload(web, png(), headers=False).status_code == 403


def test_session_quota_error(web, monkeypatch):
    monkeypatch.setattr(att, "SESSION_QUOTA_BYTES", len(png(2, 2)) + 10)
    assert upload(web, png(2, 2)).status_code == 201
    resp = upload(web, png(3, 3))
    assert resp.status_code == 413 and resp.get_json()["error"]["reason"] == "quota"
    assert upload(web, png(2, 2)).status_code == 201  # already stored: not counted twice


def test_cross_session_refs_are_rejected(web):
    client, hdr, base, sid, root = web
    other = chat.create_session(root, "Other")["id"]
    ref = upload(web, png(), sid=other).get_json()["ref"]
    assert client.get(f"{base}/sessions/{sid}/attachments/{ref}/content").status_code == 404
    resp = client.post(f"{base}/sessions/{sid}/messages", headers=hdr, json={"text": "see", "attachments": [ref]})
    assert resp.status_code == 404
    assert chat.session(root, sid).messages() == []


def test_message_with_image_and_empty_text(web):
    client, hdr, base, sid, root = web
    ref = upload(web, png()).get_json()["ref"]
    resp = client.post(f"{base}/sessions/{sid}/messages", headers=hdr, json={"text": "", "attachments": [ref]})
    assert resp.status_code == 201
    message = resp.get_json()["message"]
    assert message["text"] == "" and [a["ref"] for a in message["attachments"]] == [ref]
    state = client.get(f"{base}/sessions/{sid}").get_json()
    assert state["messages"][0]["attachments"][0]["media_type"] == "image/png"
    assert client.post(f"{base}/sessions/{sid}/messages", headers=hdr, json={"text": ""}).status_code == 400


def test_message_limits_per_message(web, monkeypatch):
    client, hdr, base, sid, root = web
    refs = [upload(web, png(2 + i, 2)).get_json()["ref"] for i in range(3)]
    monkeypatch.setattr(att, "MAX_IMAGES_PER_MESSAGE", 2)
    resp = client.post(f"{base}/sessions/{sid}/messages", headers=hdr, json={"text": "x", "attachments": refs})
    assert resp.status_code == 413 and resp.get_json()["error"]["reason"] == "too_many"
    monkeypatch.setattr(att, "MAX_IMAGES_PER_MESSAGE", 8)
    monkeypatch.setattr(att, "MAX_BYTES_PER_MESSAGE", 10)
    resp = client.post(f"{base}/sessions/{sid}/messages", headers=hdr, json={"text": "x", "attachments": refs[:1]})
    assert resp.status_code == 413 and resp.get_json()["error"]["reason"] == "too_large"
    assert chat.session(root, sid).messages() == []


def test_client_message_id_is_idempotent_and_conflicts(web):
    client, hdr, base, sid, root = web
    url = f"{base}/sessions/{sid}/messages"
    first = client.post(url, headers=hdr, json={"text": "@claude hi", "client_message_id": "c-1"})
    assert first.status_code == 201
    again = client.post(url, headers=hdr, json={"text": "@claude hi", "client_message_id": "c-1"})
    assert again.status_code == 200
    assert again.get_json()["message"] == first.get_json()["message"]
    assert again.get_json()["request"]["id"] == first.get_json()["request"]["id"]
    assert len(chat.session(root, sid).messages()) == 1 and len(chat.session(root, sid).requests()) == 1
    clash = client.post(url, headers=hdr, json={"text": "@claude other", "client_message_id": "c-1"})
    assert clash.status_code == 409
    ref = upload(web, png()).get_json()["ref"]
    assert client.post(url, headers=hdr, json={"text": "@claude hi", "attachments": [ref],
                                               "client_message_id": "c-1"}).status_code == 409
    assert client.post(url, headers=hdr, json={"text": "x", "client_message_id": "bad id!"}).status_code == 400
    assert len(chat.session(root, sid).messages()) == 1


def test_text_only_post_message_is_unchanged(project):
    sid = chat.create_session(project, "Old")["id"]
    out = chat.post_message(project, sid, "@claude hello", spawn=False)
    assert "attachments" not in out["message"] and "client_message_id" not in out["message"]
    assert "replayed" not in out
