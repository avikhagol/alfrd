"""Short-lived, single-use loopback sessions, isolated from the Studio origin."""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import oauth
from .client import SyncError

MIME = "application/vnd.google-apps.spreadsheet"
ID = re.compile(r"[A-Za-z0-9_-]{20,128}")
def _matches(value, expected):
    return isinstance(value, str) and secrets.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


_registry = OrderedDict()
_registry_lock = threading.RLock()


class Session:
    def __init__(self, kind, *, cfg=None, token=None, clock=time.monotonic, exchange=None):
        self.kind, self.cfg, self.token = kind, cfg or {}, token
        self.clock, self.exchange = clock, exchange or oauth.exchange
        self.id, self.nonce = (secrets.token_urlsafe(32) for _ in range(2))
        self.verifier = secrets.token_urlsafe(48)
        self.lock = threading.RLock()
        self.state, self.message, self.email, self.file = "waiting", "", "", None
        self.claimed, self.page_served = False, False
        self.expires_in = 300 if kind == "auth" else 600
        self.expires = clock() + self.expires_in
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # never log state, code, nonce or token

            def reply(self, status, body="", *, html=False):
                raw = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8" if html else "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                if html:
                    self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self' 'unsafe-inline' https://apis.google.com https://*.gstatic.com; frame-src https://docs.google.com https://drive.google.com; connect-src 'self' https://*.googleapis.com; style-src 'unsafe-inline'; img-src https://*.googleusercontent.com https://*.gstatic.com data:; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(raw)

            def valid(self):
                if self.headers.get("Host") != owner.host:
                    self.reply(403)
                    return False
                if owner.clock() >= owner.expires:
                    owner.finish("expired")
                if owner.state != "waiting":
                    self.reply(410)
                    return False
                return True

            def do_GET(self):
                if not self.valid():
                    return
                url = urlsplit(self.path)
                args = parse_qs(url.query)
                if url.path == "/" and owner.kind == "auth":
                    owner.callback(args)
                    self.reply(200, (Path(__file__).with_name("pages") / "signed_in.html").read_text(), html=True)
                elif url.path == "/picker" and owner.kind == "picker":
                    with owner.lock:
                        if (owner.page_served or not _matches(args.get("n", [""])[0], owner.nonce)):
                            self.reply(403)
                            return
                        owner.page_served = True
                        cfg = json.dumps({"token": owner.token, "key": owner.cfg["picker_api_key"],
                                          "appId": owner.cfg["project_number"], "nonce": owner.nonce}).replace("<", "\\u003c")
                        owner.token = None
                    page = (Path(__file__).with_name("pages") / "picker.html").read_text()
                    self.reply(200, page.replace("__CONFIG__", cfg), html=True)
                else:
                    self.reply(404)

            def do_POST(self):
                if not self.valid():
                    return
                if self.path != "/picked" or owner.kind != "picker":
                    self.reply(404)
                    return
                # Same-origin JSON only, with an unguessable nonce (no CORS).
                if self.headers.get("Origin") != owner.origin:
                    self.reply(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        self.reply(413)
                        return
                    data = json.loads(self.rfile.read(size))
                    owner.picked(data)
                except (ValueError, TypeError, UnicodeError):
                    self.reply(400)
                    return
                self.reply(200, "Return to ALFRD.")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.origin = f"http://{self.host}"
        self.redirect_uri = self.origin + "/"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True).start()
        self.timer = threading.Timer(self.expires_in, lambda: self.finish("expired"))
        self.timer.daemon = True
        self.timer.start()

    def finish(self, state, message=""):
        with self.lock:
            if self.state != "waiting":
                return
            if not message and state == "expired":
                message = "Google sign-in timed out. Choose Sign in with Google to try again; on a remote server use a service account."
            if not message and state == "cancelled":
                message = "Browser session cancelled. Choose Sign in with Google to try again."
            self.state, self.message, self.token = state, message, None
            self.verifier = ""
            self.timer.cancel()
        # shutdown must run outside the serving thread; also close its socket.
        def close():
            self.server.shutdown()
            self.server.server_close()
        threading.Thread(target=close, daemon=True).start()

    def callback(self, args):
        with self.lock:
            if self.state != "waiting" or self.claimed:
                return
            self.claimed = True
            states = args.get("state", [])
            if len(states) != 1 or not _matches(states[0], self.nonce):
                self.finish("mismatch", "Sign-in state did not match. Start sign-in again.")
                return
            if args.get("error"):
                self.finish("denied", str(oauth.authorization_error(args["error"][0])))
                return
        try:
            codes = args.get("code", [])
            if len(codes) != 1:
                raise SyncError("Google did not return a sign-in code.")
            data = self.exchange(codes[0], self.verifier, self.redirect_uri, self.cfg)
            with self.lock:
                if self.state != "waiting" or self.clock() >= self.expires:
                    self.finish("expired")
                    return
                oauth.save_token(data)
                self.email = data.get("email", "")
                self.finish("signed_in")
        except oauth.ScopeMissing as exc:
            self.finish("scope_missing", str(exc))
        except Exception:
            self.finish("error", "Google sign-in failed. Try again or check the OAuth configuration.")

    def picked(self, data):
        with self.lock:
            if (not isinstance(data, dict) or self.state != "waiting" or self.claimed or
                    not isinstance(data.get("nonce"), str) or not _matches(data["nonce"], self.nonce)):
                raise ValueError("Invalid Picker callback.")
            if data.get("action") == "cancel":
                self.claimed = True
                self.finish("cancelled")
                return
            sid = data.get("id")
            if (data.get("action") != "picked" or not isinstance(sid, str) or not ID.fullmatch(sid)
                    or data.get("mimeType") != MIME):
                raise ValueError("Choose a Google spreadsheet.")
            self.claimed = True
            self.file = {"id": sid}
            self.finish("picked")

    def status(self):
        if self.clock() >= self.expires:
            self.finish("expired", "This browser session expired. Start again; remote servers should use a service account.")
        return {"state": self.state, **({"email": self.email} if self.email else {}),
                **({"message": self.message} if self.message else {})}


def start(kind, **kwargs):
    with _registry_lock:
        for old in _registry.values():
            if old.kind == kind:
                old.finish("cancelled")
        session = Session(kind, **kwargs)
        _registry[session.id] = session
        while len(_registry) > 16:
            _, old = _registry.popitem(last=False)
            old.finish("cancelled")
        return session


def get(session_id, kind):
    if not isinstance(session_id, str):
        raise SyncError("Unknown browser session. Start again.")
    with _registry_lock:
        session = _registry.get(session_id)
    if session is None or session.kind != kind:
        raise SyncError("Unknown browser session. Start again.")
    session.status()
    return session


def cancel_all():
    with _registry_lock:
        for session in _registry.values():
            session.finish("cancelled")
