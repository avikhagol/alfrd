"""Plugins Phase 4: Studio install/update/remove jobs, the audit log, and restart."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from http.cookiejar import CookieJar

import pytest

from alfrd import extensions
from alfrd.extensions import installer as ins, jobs
from plugin_wheel import build_wheel
from test_studio import studio_app  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def fresh_runner(tmp_path, monkeypatch):
    """A runner per test, offline installs, and a private uv cache."""
    monkeypatch.setattr(jobs, "runner", jobs.Runner())
    monkeypatch.setattr(jobs, "EXTRA", ("--no-index", "--find-links", str(tmp_path / "wheels")))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    yield
    for name in list(sys.modules):
        if name.startswith("alfrd_testplug"):
            sys.modules.pop(name)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog(tmp_path, *wheels: Path, sha: str | None = None):
    versions = [{"version": w.name.split("-")[1], "wheel": w.as_uri(), "sha256": sha or _sha(w)} for w in wheels]
    data = {"schema_version": 1, "plugins": [{"id": "testplug", "title": "Test plug", "kinds": ["viewer"],
                                              "alfrd_api": ">=1", "versions": versions}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data))
    extensions.state_file().parent.mkdir(parents=True, exist_ok=True)
    extensions.state_file().write_text(json.dumps({"catalog_url": path.as_uri()}))


def _csrf(client):
    return {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}


def _wait(job_id: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.runner.get(job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still running")


def _audit() -> list[dict]:
    path = extensions.plugins_dir() / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_catalog_install_update_remove_jobs_stream_logs_and_audit(studio_app, tmp_path):
    app, _ = studio_app
    client = app.test_client()
    first = build_wheel(tmp_path / "wheels")
    _catalog(tmp_path, first)
    reply = client.post("/api/studio/plugins/install", json={"id": "testplug"}, headers=_csrf(client))
    assert reply.status_code == 202, reply.get_json()
    job = _wait(reply.get_json()["job"]["id"])
    log = client.get(f"/api/studio/plugins/jobs/{job['id']}?offset=0")
    assert job["status"] == "done" and job["restart_required"], log.get_data(as_text=True)
    text = log.get_data(as_text=True)
    assert "installed testplug 0.1" in text and _sha(first) in text and log.headers["X-Job-Status"] == "done"
    tail = client.get(f"/api/studio/plugins/jobs/{job['id']}?offset={log.headers['X-Offset']}")
    assert tail.get_data(as_text=True) == "" and tail.headers["X-Offset"] == log.headers["X-Offset"]
    assert ins.installed()["testplug"]["source"] == "catalog:testplug"
    assert client.get("/api/studio/plugins").get_json()["job"]["id"] == job["id"]

    second = build_wheel(tmp_path / "wheels", version="0.2")
    _catalog(tmp_path, first, second)
    extensions.catalog.cache_file().unlink()  # the 24 h cache would hide the new version
    job = _wait(client.post("/api/studio/plugins/testplug/update", headers=_csrf(client)).get_json()["job"]["id"])
    assert job["status"] == "done" and ins.installed()["testplug"]["version"] == "0.2"

    job = _wait(client.post("/api/studio/plugins/testplug/remove", headers=_csrf(client)).get_json()["job"]["id"])
    assert job["status"] == "done" and ins.installed() == {}
    assert not any(p.is_file() for p in extensions.site_dir().rglob("*"))
    audit = _audit()
    assert [(a["action"], a["id"], a["result"]) for a in audit] == [
        ("install-pinned", "testplug", "ok"), ("update", "testplug", "ok"), ("remove", "testplug", "ok")]
    assert audit[0]["sha256"] == _sha(first) and audit[0]["version"] == "0.1" and audit[0]["user"]


def test_hash_mismatch_job_fails_cleanly_and_is_audited(studio_app, tmp_path):
    app, _ = studio_app
    client = app.test_client()
    _catalog(tmp_path, build_wheel(tmp_path / "wheels"), sha="0" * 64)
    job = _wait(client.post("/api/studio/plugins/install", json={"id": "testplug"},
                            headers=_csrf(client)).get_json()["job"]["id"])
    assert job["status"] == "failed" and not job["restart_required"]
    assert "Hash mismatch" in jobs.read_log(job["id"])[0]
    assert ins.installed() == {} and _audit()[-1]["result"] == "failed" and "Hash mismatch" in _audit()[-1]["error"]


def test_advanced_install_needs_the_typed_id(studio_app, tmp_path):
    app, _ = studio_app
    client = app.test_client()
    wheel = build_wheel(tmp_path / "wheels")
    bad = client.post("/api/studio/plugins/install", json={"source": str(wheel), "confirm_id": "Not An Id"},
                      headers=_csrf(client))
    assert bad.status_code == 400 and "type the plugin id" in bad.get_json()["error"]["message"]
    job = _wait(client.post("/api/studio/plugins/install", json={"source": str(wheel), "confirm_id": "other"},
                            headers=_csrf(client)).get_json()["job"]["id"])
    assert job["status"] == "failed" and "provides testplug, not 'other'" in jobs.read_log(job["id"])[0]
    assert ins.installed() == {}
    job = _wait(client.post("/api/studio/plugins/install", json={"source": str(wheel), "confirm_id": "testplug"},
                            headers=_csrf(client)).get_json()["job"]["id"])
    assert job["status"] == "done" and ins.installed()["testplug"]["source"] == str(wheel)


def test_gates_csrf_then_policy_then_one_job_at_a_time(studio_app, tmp_path):
    app, _ = studio_app
    client = app.test_client()
    assert client.post("/api/studio/plugins/install", json={"id": "testplug"}).status_code == 403  # no CSRF
    remote = app.test_client()
    remote.environ_base["REMOTE_ADDR"] = "10.0.0.5"
    assert remote.post("/api/studio/plugins/nope/remove").status_code == 403
    app.config["PLUGINS_GUI_INSTALL"] = False  # alfrd serve --no-gui-install
    off = client.post("/api/studio/plugins/nope/remove", headers=_csrf(client))
    assert off.status_code == 403 and off.get_json()["error"]["command"] == "alfrd plugin remove nope"
    assert "Studio installs are turned off" in off.get_json()["error"]["message"]
    app.config["PLUGINS_GUI_INSTALL"] = True
    extensions.state_file().parent.mkdir(parents=True, exist_ok=True)
    extensions.state_file().write_text('{"gui_install": false}')
    assert client.post("/api/studio/plugins/install", json={"id": "x"}, headers=_csrf(client)).status_code == 403
    extensions.state_file().unlink()
    assert client.post("/api/studio/plugins/nope/remove", headers=_csrf(client)).status_code == 404
    assert client.post("/api/studio/plugins/install", json={"id": "x"}, headers=_csrf(client)).status_code == 404
    jobs.runner._jobs["busy"] = {"id": "busy", "action": "install", "target": "a", "status": "running"}
    jobs.runner._current = "busy"
    busy = client.post("/api/studio/plugins/install", json={"source": "x", "confirm_id": "x"}, headers=_csrf(client))
    assert busy.status_code == 409 and busy.get_json()["error"]["job"]["id"] == "busy"
    assert client.post("/api/studio/restart", headers=_csrf(client)).status_code == 409


def test_job_timeout_kills_the_process_and_events_are_sent(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "TIMEOUT", 0.5)
    real = subprocess.Popen
    monkeypatch.setattr(jobs.subprocess, "Popen",
                        lambda cmd, **kw: real([sys.executable, "-c", "import time; time.sleep(60)"], **kw))
    events: list[dict] = []
    job = jobs.runner.start("remove", {"id": "slow"}, events.append)
    with pytest.raises(jobs.JobBusy):
        jobs.runner.start("remove", {"id": "other"})
    done = _wait(job["id"], timeout=10)
    assert done["status"] == "timeout" and "longer than 0.5 s" in jobs.read_log(job["id"])[0]
    assert [e["status"] for e in events] == ["running", "timeout"]
    assert _audit()[-1]["result"] == "timeout" and jobs.runner.running() is None
    with pytest.raises(ValueError):
        jobs.log_path("../x")


def test_job_log_route_validates(studio_app):
    app, _ = studio_app
    client = app.test_client()
    assert client.get("/api/studio/plugins/jobs/x").status_code == 404
    assert client.get("/api/studio/plugins/jobs/20261008-120000-deadbeef").status_code == 404


def test_restart_route_waits_for_mutations_and_needs_a_server(studio_app):
    from alfrd.gui import studio

    app, _ = studio_app
    client = app.test_client()
    assert client.get("/api/studio/session").get_json()["can_restart"] is False
    assert client.post("/api/studio/restart", headers=_csrf(client)).status_code == 409
    called = []
    app.config["STUDIO_RESTART"] = lambda: called.append(time.monotonic())
    assert client.post("/api/studio/restart").status_code == 403  # CSRF
    studio._INFLIGHT["count"] = 1  # a mutation still running
    start = time.monotonic()
    assert client.post("/api/studio/restart", headers=_csrf(client)).status_code == 202
    time.sleep(0.6)
    assert not called
    studio._INFLIGHT["count"] = 0
    deadline = time.monotonic() + 5
    while not called and time.monotonic() < deadline:
        time.sleep(0.05)
    assert called and called[0] - start >= 0.6


def _free_port() -> int:
    for port in range(5072, 5090):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    pytest.skip("no free port in 5072–5089")


def test_real_server_restart_keeps_the_session(tmp_path):
    """`alfrd serve` re-executes itself: same token and secret, the old cookie still works."""
    pytest.importorskip("waitress")
    from alfrd.gui import auth

    port = _free_port()
    env = {**os.environ, "ALFRD_TOKEN": "", "ALFRD_SECRET_KEY": ""}
    env.pop("ALFRD_TOKEN")
    env.pop("ALFRD_SECRET_KEY")
    log = (tmp_path / "serve.log").open("wb")
    server = subprocess.Popen([sys.executable, "-c", "from alfrd.cli import main; main()", "serve",
                               "--port", str(port), "--no-browser",
                               "--runtime-db", str(tmp_path / "runtime.sqlite")],
                              cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"

    def server_file(starts=1):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            data = auth.read_server_file(port)
            if data and (tmp_path / "serve.log").read_text().count("ALFRD Studio:") >= starts:
                try:
                    urllib.request.urlopen(f"{base}/api/health", timeout=2)
                    return data
                except OSError:
                    pass
            time.sleep(0.2)
        raise AssertionError((tmp_path / "serve.log").read_text())

    try:
        first = server_file()
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.open(f"{base}/studio/?token={first['token']}", timeout=5)
        session = json.loads(opener.open(f"{base}/api/studio/session", timeout=5).read())
        assert session["can_restart"] is True
        request = urllib.request.Request(f"{base}/api/studio/restart", method="POST", data=b"{}",
                                         headers={"X-CSRF-Token": session["csrf_token"], "Origin": base,
                                                  "Content-Type": "application/json"})
        assert opener.open(request, timeout=5).status == 202
        second = server_file(starts=2)
        assert "Restarting alfrd serve" in (tmp_path / "serve.log").read_text()
        # execv keeps the pid; the new image wrote the token file again with the same secrets.
        assert second["pid"] == first["pid"] == server.pid and second["token"] == first["token"]
        assert second["secret_key"] == first["secret_key"]
        again = opener.open(f"{base}/api/studio/session", timeout=5)
        assert again.status == 200 and json.loads(again.read())["csrf_token"]
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
