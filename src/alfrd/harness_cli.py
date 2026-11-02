"""Harness launch/attach helpers; no scheduler or agent activity."""
import ipaddress
import json
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def route(project=None, chat=None):
    fragment = "/harness" + ("/" + quote(chat, safe="") if chat else "")
    return "#" + fragment + ("?" + urlencode({"p": project}) if project else "")


def base_url(value):
    parts = urlsplit(value)
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or parts.path not in ("", "/", "/studio", "/studio/")):
        raise ValueError("attach requires an http(s) server URL without credentials, query or fragment")
    # Force validation of malformed ports before any network request.
    _ = parts.port
    return f"{parts.scheme}://{parts.netloc}"


def is_local(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def listening(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except ConnectionRefusedError:
        return False
    except OSError as error:
        # A timeout/unknown host is not proof that a new server can bind here.
        raise ValueError(f"Cannot probe server: {error}") from error


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_json(base, path, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    # Do not forward local credentials to a redirect target.
    try:
        with build_opener(_NoRedirect).open(Request(base + path, headers=headers), timeout=3) as response:
            data = json.load(response)
            if not isinstance(data, dict):
                raise TypeError("server did not return a JSON object")
            return data
    except (URLError, ValueError, TypeError, OSError) as error:
        raise ValueError("Unable to attach to an authenticated harness-capable Alfrd server; check access or restart/upgrade it") from error


def attach_url(base, project=None, chat=None, *, local_credentials=False):
    from alfrd.gui.auth import read_server_file
    parts = urlsplit(base)
    info = read_server_file(parts.port or (443 if parts.scheme == "https" else 80)) if local_credentials and is_local(parts.hostname) else None
    token = info.get("token") if info else None
    session = get_json(base, "/api/studio/session", token)
    if session.get("app") != "alfrd" or session.get("harness_state_version") != 1:
        raise ValueError("Running server lacks harness state v1; upgrade/restart it")
    selected = session.get("default_project")
    if project:
        projects = get_json(base, "/api/projects", token).get("projects", [])
        folder = str(Path(project).expanduser().resolve())
        match = next((p for p in projects if project in (p.get("id"), p.get("identifier"), p.get("name"))
                      or folder == p.get("root_path")), None)
        if not match:
            raise ValueError("Project not served; connect it in this server's Studio first")
        selected = match.get("identifier") or match.get("id")
    allowed = session.get("projects")
    if allowed is not None and selected and selected not in allowed:
        raise ValueError("Project not served; connect it in this server's Studio first")
    if selected and not chat:
        sessions = get_json(base, f"/api/studio/projects/{quote(selected, safe='')}/chat/sessions", token).get("sessions", [])
        chat = sessions[0]["id"] if sessions else None
    query = "?" + urlencode({"token": token}) if token else ""
    return base + "/studio/" + query + route(selected, chat)


def startup_project(service, selector, connect):
    """Accept a registered id/name as well as the ordinary folder selector."""
    if selector and not Path(selector).expanduser().exists():
        from alfrd.runtime import RuntimeNotFound
        try:
            return service.get_project_by_selector(selector).identifier
        except RuntimeNotFound:
            pass
    return connect(service, selector)


def latest_chat(service, project):
    if not project:
        return None
    root = Path(service.get_project_by_selector(project).root_path)
    from alfrd import harness
    sessions = harness.records(root / ".alfrd" / "chat", "*/session.json")
    return max(sessions, key=lambda s: s.get("created", "")).get("id") if sessions else None
