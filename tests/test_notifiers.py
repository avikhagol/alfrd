"""Built-in notifiers (Ticket N4) and the dispatcher fixes from the N1–N3 review (R1, R2, R3, R5)."""
import hmac
import json
import subprocess
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_agent_loop import wait
from test_notify import Sink, record, write
from alfrd import notifiers, notify
from alfrd.api.status import project_info
from alfrd.events import EVENTS_FILE, EventLog

MESSAGE = notify.build_message([record("review.pending", status="pending")])


@pytest.fixture
def server():
    """A webhook receiver on 127.0.0.1; ``server.status`` sets the reply code."""
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            hits.append({"path": self.path, "headers": dict(self.headers), "body": body})
            self.send_response(httpd.status)
            if httpd.status in (301, 302, 307):
                self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.status, httpd.hits = 204, hits
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}/hook"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_builtins_are_registered():
    assert {"desktop", "webhook", "command"} <= set(notify.SENDERS)
    assert notify.SENDERS["webhook"] is notifiers.webhook


def test_route_options_are_checked():
    good = [{"via": "webhook", "on": ["*"], "url": "https://x", "secret": "s", "headers": {"X-A": "1"}},
            {"via": "command", "on": ["*"], "argv": ["true"]}]
    assert len(notify.validate_routes(good, user=True)) == 2
    for bad, msg in (({"via": "webhook"}, "url"), ({"via": "webhook", "url": "https://x", "secret": 1}, "secret"),
                     ({"via": "webhook", "url": "https://x", "headers": {"X": 1}}, "headers"),
                     ({"via": "command"}, "argv"), ({"via": "command", "argv": []}, "argv"),
                     ({"via": "command", "argv": "curl x"}, "argv")):
        with pytest.raises(notify.RouteError, match=msg):
            notify.validate_routes([{"on": ["*"], **bad}], user=True)


# -- webhook ------------------------------------------------------------------

def test_webhook_posts_signed_json(server):
    route = {"via": "webhook", "url": server.url, "secret": "s3cret", "headers": {"X-Team": "ops"}}
    notifiers.webhook(route, MESSAGE, 5)
    hit = server.hits[0]
    assert hit["path"] == "/hook" and json.loads(hit["body"]) == MESSAGE
    assert hit["headers"]["Content-Type"] == "application/json"
    assert hit["headers"]["User-Agent"].startswith("alfrd/") and hit["headers"]["X-Team"] == "ops"
    expected = "sha256=" + hmac.new(b"s3cret", hit["body"], "sha256").hexdigest()
    assert hmac.compare_digest(hit["headers"]["X-Alfrd-Signature"], expected)
    notifiers.webhook({"via": "webhook", "url": server.url}, MESSAGE, 5)
    assert "X-Alfrd-Signature" not in server.hits[1]["headers"]


def test_webhook_errors_and_redirects_raise(server):
    server.status = 500
    with pytest.raises(urllib.error.HTTPError):
        notifiers.webhook({"url": server.url}, MESSAGE, 5)
    server.status = 302
    with pytest.raises(urllib.error.HTTPError):
        notifiers.webhook({"url": server.url}, MESSAGE, 5)
    assert len(server.hits) == 2  # the redirect was not followed


def test_webhook_needs_https_for_remote_hosts(monkeypatch):
    monkeypatch.setattr(notifiers.urllib.request, "build_opener", lambda *a: pytest.fail("no request expected"))
    for url in ("http://example.com/hook", "http://10.0.0.1/hook", "ftp://localhost/x", "nonsense"):
        with pytest.raises(notify.Skip):
            notifiers.webhook({"url": url}, MESSAGE, 5)
    assert all(notifiers.is_loopback(h) for h in ("localhost", "127.0.0.1", "127.8.0.1", "::1", "[::1]"))
    assert not any(notifiers.is_loopback(h) for h in ("example.com", "10.0.0.1", "localhost.example.com", "", None))


# -- command ------------------------------------------------------------------

def test_command_gets_the_json_on_stdin(tmp_path):
    out = tmp_path / "got.json"
    argv = [sys.executable, "-c", "import sys; open(sys.argv[1], 'wb').write(sys.stdin.buffer.read())", str(out)]
    notifiers.command({"argv": argv}, MESSAGE, 5)
    assert json.loads(out.read_text()) == MESSAGE


def test_command_failures(tmp_path):
    with pytest.raises(RuntimeError, match="exit 3: boom"):
        notifiers.command({"argv": [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]},
                          MESSAGE, 5)
    with pytest.raises(notify.Skip, match="not found"):
        notifiers.command({"argv": [str(tmp_path / "missing")]}, MESSAGE, 5)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        notifiers.command({"argv": ["sleep", "5"]}, MESSAGE, 0.5)
    assert time.monotonic() - started < 2


def test_a_slow_command_is_retried_then_given_up_without_blocking(tmp_path):
    logs = []
    d = notify.Dispatcher(tmp_path, [{"via": "command", "on": ["*"], "argv": ["sleep", "5"]}],
                          senders={"command": notifiers.command}, log=logs.append, batch_window=0, timeout=0.3,
                          retries=2, backoff=(0,), project={"identifier": "p", "name": "p"}).start()
    started = time.monotonic()
    d.submit(write(tmp_path, record("plan.failed"))[0])
    assert time.monotonic() - started < 0.2
    assert wait(lambda: any("gave up after 3 attempts" in line for line in logs), 10)
    assert time.monotonic() - started < 5
    assert sum("TimeoutExpired" in line for line in logs) == 2
    d.stop(2)


# -- desktop ------------------------------------------------------------------

@pytest.fixture
def desktop(monkeypatch, tmp_path):
    """Linux, a fake ``notify-send``, no session; returns the list of argv/env it was called with."""
    calls = []
    monkeypatch.setattr(notifiers.sys, "platform", "linux")
    monkeypatch.setattr(notifiers.os, "name", "posix")
    monkeypatch.setattr(notifiers, "_user_bus", lambda: tmp_path / "no-bus")
    monkeypatch.setattr(notifiers.shutil, "which", lambda name: f"/usr/bin/{name}")
    for var in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(var, raising=False)

    def run(argv, **kw):
        calls.append({"argv": argv, **kw})
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(notifiers.subprocess, "run", run)
    return calls


def test_desktop_without_a_session_is_skipped_once(desktop, tmp_path):
    with pytest.raises(notify.Skip, match="no desktop session"):
        notifiers.desktop({}, MESSAGE, 5)
    logs = []
    d = notify.Dispatcher(tmp_path, [{"via": "desktop", "on": ["*"]}], log=logs.append, batch_window=0,
                          backoff=(0,), project={"identifier": "p", "name": "p"}).start()
    for r in write(tmp_path, record("review.pending")):
        d.submit(r)
    assert wait(lambda: notify.read_cursor(tmp_path / notify.CURSOR_FILE) == 1, 5)
    for r in write(tmp_path, record("plan.finished")):
        d.submit(r)
    d.stop(5)
    assert desktop == [] and notify.read_cursor(tmp_path / notify.CURSOR_FILE) == 2
    assert [line for line in logs if "desktop" in line] == [
        f"warning: notify desktop: no desktop session (DBUS_SESSION_BUS_ADDRESS, DISPLAY and WAYLAND_DISPLAY "
        f"are unset, no {tmp_path / 'no-bus'}); skipped"]


def test_desktop_uses_the_user_bus_and_notify_send(desktop, monkeypatch, tmp_path):
    bus = tmp_path / "bus"
    bus.touch()
    monkeypatch.setattr(notifiers, "_user_bus", lambda: bus)
    notifiers.desktop({}, {**MESSAGE, "title": "-x title"}, 5)
    call = desktop[0]
    assert call["env"]["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={bus}" and call["timeout"] == 5
    assert call["argv"] == ["/usr/bin/notify-send", "--app-name=ALFRD", "--urgency=critical", "--", "-x title",
                            MESSAGE["body"]]
    finished = notify.build_message([record("plan.finished")])
    notifiers.desktop({}, finished, 5)
    assert "--urgency=critical" not in desktop[1]["argv"]


def test_desktop_on_macos_passes_text_as_arguments(desktop, monkeypatch):
    monkeypatch.setattr(notifiers.sys, "platform", "darwin")
    tricky = {**MESSAGE, "title": 'a" & do shell script "x', "body": "b"}
    notifiers.desktop({}, tricky, 5)
    argv = desktop[0]["argv"]
    assert argv[0] == "/usr/bin/osascript" and argv[-2:] == [tricky["title"], "b"]
    assert all(tricky["title"] not in a for a in argv[:-2])


def test_desktop_failure_raises(desktop, monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/x")
    monkeypatch.setattr(notifiers.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"no server"))
    with pytest.raises(RuntimeError, match="exit 1: no server"):
        notifiers.desktop({}, MESSAGE, 5)


# -- review fixes ---------------------------------------------------------------

def test_links_use_the_studio_project_key(tmp_path):
    root = tmp_path / "proj"
    plan_dir = root / ".alfrd" / "plans" / "p1"
    plan_dir.mkdir(parents=True)
    key = project_info(root)["identifier"]
    assert key != str(root.resolve())
    sink = Sink()
    d = notify.Dispatcher(plan_dir, [{"via": "fake", "on": ["*"]}], senders={"fake": sink}, log=[].append,
                          batch_window=0).start()
    for r in write(plan_dir, {**record("review.pending"), "project": str(root)}):
        d.submit(r)
    d.stop(5)
    message = sink.messages[0]
    assert message["project"] == str(root) and message["project_name"] == "proj"
    assert message["link"] == f"/studio/#/workflow?project={key}&plan=p1&unit=0001-a"
    assert message["events"][0]["link"] == message["link"]


def test_project_ref_falls_back_to_the_root(monkeypatch):
    import alfrd.api.status as status

    monkeypatch.setattr(status, "project_info", lambda root: 1 / 0)
    assert notify.project_ref("/x/proj") == {"root": "/x/proj", "name": "proj", "identifier": "/x/proj"}


def test_a_hanging_route_does_not_delay_the_others_or_the_stop(tmp_path):
    logs = []
    hang, fast = Sink(hang=30), Sink()
    d = notify.Dispatcher(tmp_path, [{"via": "hang", "on": ["*"]}, {"via": "fast", "on": ["*"]}],
                          senders={"hang": hang, "fast": fast}, log=logs.append, batch_window=0, timeout=10,
                          retries=3, backoff=(4,), project={"identifier": "p", "name": "p"}).start()
    started = time.monotonic()
    d.submit(write(tmp_path, record("plan.finished"))[0])
    assert wait(lambda: fast.messages, 2) and time.monotonic() - started < 1.5
    started = time.monotonic()
    d.stop(2)
    assert time.monotonic() - started < 2.5 and not d.thread.is_alive()
    assert notify.read_cursor(tmp_path / notify.CURSOR_FILE) == 1
    assert any("hang gave up after 1 attempt:" in line for line in logs)


def test_rotation_counts_bytes(tmp_path):
    log = EventLog(tmp_path / EVENTS_FILE, max_bytes=1200)
    for _ in range(8):
        log.append({"kind": "turn.started", "text": "é" * 200})  # 400 bytes, 200 characters
        assert log.path.stat().st_size <= 1200
    assert log.rotated.exists() and log.last_seq() == 8
