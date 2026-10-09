"""Plugin settings (``plugin-settings.json``), services (children of ``alfrd serve``) and their Studio routes."""
from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path

import pytest
from test_plugin_routes import _csrf, plugin_pkgs  # noqa: F401  (fixture)
from test_studio import studio_app  # noqa: F401  (fixture)

from alfrd import extensions
from alfrd.extensions import Plugin, Service, SettingField, services, settings

TOKEN = "123456:SECRET-value-never-shown"
PLUGIN = Plugin(id="demo", version="1", settings=[
    SettingField("token", "Token", kind="secret", required=True, pattern=r"[0-9]+:[A-Za-z0-9_-]+"),
    SettingField("chat_id", "Chat", required=True, pattern=r"-?[0-9]+"),
    SettingField("verbose", "Verbose", kind="bool"),
])


def test_field_and_service_validation():
    with pytest.raises(ValueError, match="invalid setting key"):
        SettingField("Bad-Key")
    with pytest.raises(ValueError, match="kind must be"):
        SettingField("x", kind="password")
    with pytest.raises(ValueError, match="non-empty"):
        Service("bot", ())
    assert Plugin(id="p", version="1", settings=[SettingField("a")], services=[Service("s", "run")]).kinds == ["settings", "service"]


def test_save_public_missing_and_secret_handling():
    assert settings.missing(PLUGIN) == ["Token", "Chat"]
    with pytest.raises(ValueError, match="required: Token"):
        settings.save(PLUGIN, {"chat_id": "42"})
    with pytest.raises(ValueError, match="unknown setting: nope"):
        settings.save(PLUGIN, {"nope": 1})
    with pytest.raises(ValueError) as bad:
        settings.save(PLUGIN, {"token": TOKEN, "chat_id": "@" + TOKEN})
    assert TOKEN not in str(bad.value) and "Chat: not in the expected format" in str(bad.value)
    assert settings.save(PLUGIN, {"token": f"  {TOKEN} ", "chat_id": " -100 ", "verbose": True}) == {
        "token": TOKEN, "chat_id": "-100", "verbose": True}
    path = settings.settings_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    form = {f["key"]: f for f in settings.public(PLUGIN)}
    assert form["token"] == {**form["token"], "set": True} and "value" not in form["token"]
    assert TOKEN not in json.dumps(form) and form["chat_id"]["value"] == "-100" and form["verbose"]["value"] is True
    # An empty secret keeps the saved one; clear removes it.
    settings.save(PLUGIN, {"token": "", "chat_id": "7"})
    assert settings.values("demo")["token"] == TOKEN
    settings.save(PLUGIN, {}, clear=["token"])
    assert "token" not in settings.values("demo") and settings.missing(PLUGIN) == ["Token"]
    path.write_text("not json")
    assert settings.values("demo") == {}


# -- the supervisor --------------------------------------------------------------

SLEEPER = "import sys, time; print('child', sys.argv[1:], flush=True); time.sleep(60)"


@pytest.fixture
def supervisor(monkeypatch):
    sup = services.Supervisor()
    sup.prefix = [sys.executable, "-c"]
    sup.env = {"ALFRD_RUNTIME_DB": "/tmp/x.sqlite"}
    events = []
    sup.notify = events.append
    yield sup, events
    sup.stop_all()


def _until(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_service_start_log_stop(supervisor):
    sup, events = supervisor
    svc = Service("bot", (SLEEPER, "arg"))
    sup.start("demo", svc)
    assert _until(lambda: "child ['arg']" in services.tail("demo", "bot"))
    state = sup.status("demo", svc)
    assert state["status"] == "running" and state["pid"] and state["key"] == "demo:bot" and not state["autostart"]
    assert sup.running("demo") == ["bot"]
    sup.start("demo", svc)  # already running: no second child
    assert sup.status("demo", svc)["pid"] == state["pid"]
    sup.stop("demo", "bot")
    assert sup.status("demo", svc)["status"] == "stopped" and sup.running("demo") == []
    assert [e["status"] for e in events][-1] == "stopped"
    assert "start: alfrd" in services.tail("demo", "bot")


def test_service_restarts_then_gives_up(supervisor, monkeypatch):
    sup, _ = supervisor
    monkeypatch.setattr(services, "BACKOFF", (0.05,))
    monkeypatch.setattr(services, "MAX_FAILURES", 3)
    svc = Service("bot", ("import sys; print('boom', flush=True); sys.exit(3)",))
    sup.start("demo", svc)
    assert _until(lambda: sup.status("demo", svc)["status"] == "failed")
    state = sup.status("demo", svc)
    assert state["exit_code"] == 3 and state["failures"] == 3
    log = services.tail("demo", "bot")
    assert log.splitlines().count("boom") == 3 and "stopped restarting after 3 exits" in log
    sup.start("demo", Service("bot", (SLEEPER,)))  # Start again resets the failures
    assert _until(lambda: sup.status("demo", svc)["status"] == "running")


def test_stop_interrupts_even_when_the_server_ignores_sigint(supervisor):
    import signal

    sup, _ = supervisor
    sup.prefix = [sys.executable, "-u", "-m", "alfrd.extensions.services", "-c"]  # the real bootstrap
    script = ("import time\ntry:\n    print('up', flush=True); time.sleep(60)\n"
              "except KeyboardInterrupt:\n    print('interrupted', flush=True)")
    svc = Service("bot", (script,))
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)  # like `alfrd serve &`
    try:
        sup.start("demo", svc)
        assert _until(lambda: "up" in services.tail("demo", "bot").splitlines())
    finally:
        signal.signal(signal.SIGINT, previous)
    began = time.time()
    sup.stop("demo", "bot")
    assert time.time() - began < services.STOP_GRACE
    assert _until(lambda: "interrupted" in services.tail("demo", "bot"))


def test_child_dies_with_the_server(tmp_path):
    """PR_SET_PDEATHSIG: a SIGKILLed server (no clean stop) still takes its service along."""
    import signal
    import subprocess

    if not sys.platform.startswith("linux"):
        pytest.skip("PR_SET_PDEATHSIG is Linux-only")
    pidfile = tmp_path / "child.pid"
    child = f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    server = ("import sys, time\nfrom alfrd.extensions import Service, services\n"
              "sup = services.Supervisor(); sup.prefix = [sys.executable, '-u', '-m', 'alfrd.extensions.services', '-c']\n"
              f"sup.start('demo', Service('bot', ({child!r},)))\ntime.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", server])
    try:
        assert _until(lambda: pidfile.exists() and pidfile.read_text().isdigit())
        pid = int(pidfile.read_text())
        proc.send_signal(signal.SIGKILL)
        proc.wait()

        def gone():
            try:  # a zombie until init reaps it counts as gone
                return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
            except OSError:  # no such process (FileNotFoundError / ProcessLookupError)
                return True
        assert _until(gone)
    finally:
        proc.kill()


def test_config_error_exit_is_not_restarted(supervisor):
    sup, _ = supervisor
    svc = Service("bot", ("import sys; sys.exit(2)",))
    sup.start("demo", svc)
    assert _until(lambda: sup.status("demo", svc)["status"] == "failed")
    assert sup.status("demo", svc)["failures"] == 1 and "fix the settings" in services.tail("demo", "bot")


def test_autostart_skips_unconfigured_plugins(supervisor):
    sup, _ = supervisor
    svc = Service("bot", (SLEEPER,))
    configured = Plugin(id="demo", version="1", services=[svc])
    unconfigured = Plugin(id="other", version="1", settings=[SettingField("token", required=True)], services=[svc])
    extensions.set_autostart("demo:bot", True)
    extensions.set_autostart("other:bot", True)
    assert extensions.read_state()["autostart"] == ["demo:bot", "other:bot"]
    assert sup.autostart([("demo", configured), ("other", unconfigured)]) == ["demo:bot"]
    extensions.set_autostart("other:bot", False)
    assert extensions.read_state()["autostart"] == ["demo:bot"]


# -- routes ------------------------------------------------------------------------

MANIFEST = f"""
from alfrd.extensions import Plugin, Service, SettingField
def check(values):
    if values["chat_id"] == "0":
        raise ValueError("chat 0 is not allowed")
    if values["chat_id"] == "1":
        raise RuntimeError("leaks {TOKEN}")
    return "fine"
plugin = Plugin(id="confy", version="1", settings=[
    SettingField("token", "Token", kind="secret", required=True),
    SettingField("chat_id", "Chat", required=True, pattern=r"-?[0-9]+"),
], services=[Service("bot", ({SLEEPER!r},))], check=check)
"""


@pytest.fixture
def confy(studio_app, plugin_pkgs, monkeypatch):  # noqa: F811
    app, _ = studio_app
    plugin_pkgs("confy", MANIFEST)
    plugin_pkgs("plain", "from alfrd.extensions import Plugin\nplugin = Plugin(id='plain', version='1')\n")
    extensions.load(force=True)
    monkeypatch.setattr(services.supervisor, "prefix", [sys.executable, "-c"])
    client = app.test_client()
    yield client, _csrf(client)
    services.supervisor.stop_all()


def test_config_routes_save_check_and_never_return_secrets(confy):
    client, csrf = confy
    by_id = {p["id"]: p for p in client.get("/api/studio/plugins").get_json()["plugins"]}
    assert by_id["confy"]["configurable"] and not by_id["plain"]["configurable"]
    assert "settings" in by_id["confy"]["kinds"] and "service" in by_id["confy"]["kinds"]
    assert client.get("/api/studio/plugins/plain/config").get_json()["settings"] == []
    assert client.get("/api/studio/plugins/nope/config").status_code == 404
    config = client.get("/api/studio/plugins/confy/config").get_json()
    assert config["missing"] == ["Token", "Chat"] and config["can_check"]
    assert config["services"][0]["status"] == "stopped"
    assert client.post("/api/studio/plugins/confy/config", json={"values": {"chat_id": "5"}}).status_code in (400, 403)
    bad = client.post("/api/studio/plugins/confy/config", json={"values": {"chat_id": "x"}}, headers=csrf)
    assert bad.status_code == 400 and "Chat" in bad.get_json()["error"]["message"]
    assert client.post("/api/studio/plugins/confy/check", headers=csrf).get_json() == {
        "ok": False, "message": "Fill in: Token, Chat."}
    saved = client.post("/api/studio/plugins/confy/config", json={"values": {"token": TOKEN, "chat_id": "0"}},
                        headers=csrf)
    assert saved.status_code == 200 and TOKEN not in saved.get_data(as_text=True)
    assert saved.get_json()["missing"] == [] and saved.get_json()["restarted"] == []
    assert client.post("/api/studio/plugins/confy/check", headers=csrf).get_json() == {
        "ok": False, "message": "chat 0 is not allowed"}
    client.post("/api/studio/plugins/confy/config", json={"values": {"chat_id": "1"}}, headers=csrf)
    leaked = client.post("/api/studio/plugins/confy/check", headers=csrf).get_data(as_text=True)
    assert TOKEN not in leaked and "RuntimeError" in leaked
    client.post("/api/studio/plugins/confy/config", json={"values": {"chat_id": "2"}}, headers=csrf)
    assert client.post("/api/studio/plugins/confy/check", headers=csrf).get_json() == {"ok": True, "message": "fine"}
    audit = (extensions.plugins_dir() / "audit.jsonl").read_text()
    assert '"action": "settings"' in audit and TOKEN not in audit


def test_service_routes_start_restart_on_save_autostart_and_disable(confy):
    client, csrf = confy
    base = "/api/studio/plugins/confy/services/bot"
    refused = client.post(f"{base}/start", headers=csrf)
    assert refused.status_code == 400 and "Fill in and save" in refused.get_json()["error"]["message"]
    client.post("/api/studio/plugins/confy/config", json={"values": {"token": TOKEN, "chat_id": "2"}}, headers=csrf)
    started = client.post(f"{base}/start", headers=csrf).get_json()["service"]
    assert started["status"] in ("starting", "running") and started["command"][0] == "alfrd"
    assert _until(lambda: client.get("/api/studio/plugins/confy/config").get_json()["services"][0]["status"] == "running")
    assert _until(lambda: "child" in client.get(f"{base}/log").get_data(as_text=True))
    pid = client.get("/api/studio/plugins/confy/config").get_json()["services"][0]["pid"]
    saved = client.post("/api/studio/plugins/confy/config", json={"values": {"chat_id": "3"}}, headers=csrf).get_json()
    assert saved["restarted"] == ["bot"]
    assert _until(lambda: client.get("/api/studio/plugins/confy/config").get_json()["services"][0]["pid"] not in (None, pid))
    assert client.post(f"{base}/autostart-on", headers=csrf).get_json()["service"]["autostart"]
    assert extensions.read_state()["autostart"] == ["confy:bot"]
    assert client.post(f"{base}/explode", headers=csrf).status_code == 404
    assert client.post("/api/studio/plugins/confy/services/nope/start", headers=csrf).status_code == 404
    client.post("/api/studio/plugins/confy/disable", headers=csrf)
    assert services.supervisor.running("confy") == []


def test_project_check_contract_and_gates(studio_app, plugin_pkgs, tmp_path, monkeypatch):  # noqa: F811
    from alfrd.gui import studio as studio_module

    app, _ = studio_app
    plugin_pkgs("projectcheck", '''
from alfrd.extensions import Plugin, SettingField
def check(root, values):
    return "warn", "2 problems: first\\nsecond " + "x" * 200
plugin = Plugin(id="projectcheck", version="1", check_project=check,
                settings=[SettingField("token", required=True)])
''')
    plugin_pkgs("plain", "from alfrd.extensions import Plugin\nplugin = Plugin(id='plain', version='1')\n")
    extensions.load(force=True)
    client = app.test_client()
    csrf = _csrf(client)
    calls = []
    monkeypatch.setattr(studio_module, "_project_root", lambda name: calls.append(name) or tmp_path)
    url = "/api/studio/projects/example/plugins/projectcheck/check"
    assert client.post(url).status_code == 403
    assert client.post(url, headers=csrf, environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 403
    assert calls == []
    assert client.post("/api/studio/projects/example/plugins/plain/check", headers=csrf).status_code == 404
    result = client.post(url, headers=csrf).get_json()
    assert result["level"] == "warn" and result["text"].startswith("2 problems: first second")
    assert len(result["text"]) == 120 and calls == ["example"]
    # Settings missing is left to the callback (off mappings don't need credentials).
    assert "Fill in" not in result["text"]
    extensions.set_enabled("projectcheck", False)
    assert client.post(url, headers=csrf).status_code == 404
    monkeypatch.setenv("ALFRD_NO_PLUGINS", "1")
    extensions.load(force=True)
    assert client.post(url, headers=csrf).status_code == 404


def test_project_check_unexpected_errors_hide_secrets(studio_app, plugin_pkgs, tmp_path, monkeypatch):  # noqa: F811
    from alfrd.gui import studio as studio_module

    app, _ = studio_app
    plugin_pkgs("unsafecheck", '''
from alfrd.extensions import Plugin
def check(root, values):
    raise RuntimeError("SECRET-CREDENTIAL")
plugin = Plugin(id="unsafecheck", version="1", check_project=check)
''')
    extensions.load(force=True)
    monkeypatch.setattr(studio_module, "_project_root", lambda _: tmp_path)
    client = app.test_client()
    result = client.post("/api/studio/projects/example/plugins/unsafecheck/check", headers=_csrf(client)).get_json()
    assert result == {"level": "fail", "text": "The check failed (RuntimeError)."}
    with pytest.raises(TypeError, match="check_project"):
        Plugin(id="bad", version="1", check_project="not a function")
