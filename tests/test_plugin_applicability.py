"""Plugin availability by project type (alfrd.extensions.applicability).

Covers the declared default, the global override (plugins.json), the per-project override in the
*active* alfrd.yaml, unknown/custom project types and switching between projects of different
types inside one server process (panels, project actions, project checks, step hooks, the list).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_plugin_routes import _csrf, plugin_pkgs  # noqa: F401  (fixture)
from test_studio import studio_app  # noqa: F401  (fixture)

from alfrd import extensions
from alfrd import layout_generic as lg
from alfrd.extensions import Plugin, StepContext, StepHooks
from alfrd.extensions import applicability as ap
from alfrd.extensions import step_hooks as sh

TYPED = '''
from alfrd.extensions import PanelSpec, Plugin, ProjectAction
CALLS = []

def echo(root, values, payload):
    CALLS.append(("echo", root.name))
    return {"root": root.name}

def check(root, values):
    CALLS.append(("check", root.name))
    return "ok", "fine"

plugin = Plugin(id="typed", version="1", project_types=["avica"], check_project=check,
                project_actions=[ProjectAction("echo", echo)],
                panels=[PanelSpec("typed_panel", client=True, auto=lambda root: {"title": "Typed", "scope": "project"})])
'''
ANY = '''
from alfrd.extensions import PanelSpec, Plugin, ProjectAction
plugin = Plugin(id="anyplug", version="1", project_actions=[ProjectAction("echo", lambda r, v, p: {"ok": 1})],
                panels=[PanelSpec("any_panel", client=True, auto=lambda root: {"title": "Any", "scope": "project"})])
'''


def _project(base: Path, name: str, body: str, *, nested: bool = False) -> Path:
    root = base / name
    path = root / ".alfrd" / "alfrd.yaml" if nested else root / "alfrd.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(f"# {name} settings\nname: {name}\n{body}")
    return root


@pytest.fixture
def world(studio_app, plugin_pkgs, tmp_path, monkeypatch):  # noqa: F811
    from alfrd.gui import studio as studio_module

    app, _ = studio_app
    plugin_pkgs("typed", TYPED)
    plugin_pkgs("anyplug", ANY)
    extensions.load(force=True)
    projects = {
        "avi": _project(tmp_path, "avi", "template: avica\n", nested=True),
        "plain": _project(tmp_path, "plain", "steps: {}\n"),
        "lab": _project(tmp_path, "lab", "template: my-lab\n"),
    }
    # A shadowed root alfrd.yaml next to the active .alfrd/alfrd.yaml: it must never be written.
    (projects["avi"] / "alfrd.yaml").write_text("name: shadowed\n")
    monkeypatch.setattr(studio_module, "_project_root", lambda name: projects[name])
    client = app.test_client()
    yield client, _csrf(client), projects, sys.modules["alfrd_rt_typed"].CALLS


def _listing(client, project):
    data = client.get(f"/api/studio/plugins?project={project}").get_json()
    return data, {p["id"]: p for p in data["plugins"]}


def _auto_kinds(root: Path) -> set[str]:
    return {p.get("panel") for p in lg._applicable(root, lg._auto_panels(root, []))}


# -- the rule ---------------------------------------------------------------------------


def test_parse_types_and_plugin_declaration():
    assert ap.parse_types("all") is None and ap.parse_types(["avica", "all"]) is None
    assert ap.parse_types(["avica", "avica", "x"]) == ("avica", "x")
    assert ap.parse_types("avica") == ("avica",)
    assert ap.parse_types([]) is False and ap.parse_types([1]) is False and ap.parse_types(3) is False
    assert Plugin(id="p", version="1").project_types is None  # backward compatible: all types
    assert Plugin(id="p", version="1", project_types="avica").project_types == ("avica",)
    with pytest.raises(ValueError):
        Plugin(id="p", version="1", project_types=[])
    with pytest.raises(ValueError):
        Plugin(id="p", version="1", project_types=["", "x"])


def test_resolve_precedence_project_over_global_over_default():
    default = ("avica",)
    rule = ap.resolve("p", default, "basic", project=None, global_types={})
    assert rule["available"] is False and rule["source"] == "default"
    assert rule["reason"] == "Not for basic projects: the plugin's default limits it to avica."
    assert ap.resolve("p", default, "avica", global_types={})["available"] is True
    assert ap.resolve("p", None, "anything", global_types={})["available"] is True
    glob = ap.resolve("p", default, "basic", global_types={"p": ("basic",)})
    assert glob["available"] is True and glob["source"] == "global"
    assert ap.resolve("p", default, "avica", global_types={"p": ("basic",)})["reason"].startswith("Not for avica projects: the global")
    assert ap.resolve("p", default, "basic", global_types={"p": None})["available"] is True  # global "all"
    proj = ap.resolve("p", default, "avica", project={"project_types": ("x",)}, global_types={"p": None})
    assert proj["available"] is False and proj["source"] == "project" and "this project's alfrd.yaml" in proj["reason"]
    off = ap.resolve("p", None, "avica", project={"enabled": False}, global_types={})
    assert off["available"] is False and "enabled: false" in off["reason"]
    on = ap.resolve("p", default, "basic", project={"enabled": True}, global_types={})
    assert on["available"] is True and on["source"] == "project"


def test_project_type_comes_from_configuration(tmp_path):
    assert ap.project_type(_project(tmp_path, "a", "template: avica\n")) == "avica"
    assert ap.project_type(_project(tmp_path, "b", "avica: {}\n")) == "avica"  # an avica: block implies it
    assert ap.project_type(_project(tmp_path, "c", "template: my-lab\n")) == "my-lab"  # custom: free text
    assert ap.project_type(_project(tmp_path, "d", "steps: {}\n")) == "basic"
    assert {"avica", "basic", "agent-loop"} <= set(ap.known_types())


# -- enforcement and the API ------------------------------------------------------------


def test_declared_default_and_switching_projects_in_one_process(world):
    client, csrf, projects, calls = world
    data, plugins = _listing(client, "avi")
    assert data["project_type"] == "avica" and "my-lab" not in data["project_types_known"]
    assert plugins["typed"]["available"] is True and plugins["typed"]["project_types"] == ["avica"]
    data, plugins = _listing(client, "plain")
    assert data["project_type"] == "basic"
    assert plugins["typed"]["available"] is False
    assert plugins["typed"]["unavailable_reason"] == "Not for basic projects: the plugin's default limits it to avica."
    assert plugins["typed"]["applicability"]["source"] == "default"
    assert plugins["anyplug"]["available"] is True  # declares nothing: every project type
    # Without ?project= nothing changes for older clients.
    assert all(p["available"] for p in client.get("/api/studio/plugins").get_json()["plugins"])

    url = "/api/studio/projects/{}/plugins/typed/actions/echo"
    assert client.post(url.format("avi"), json={}, headers=csrf).get_json() == {"ok": True, "data": {"root": "avi"}}
    refused = client.post(url.format("plain"), json={}, headers=csrf)
    assert refused.status_code == 403 and refused.get_json()["error"]["reason"] == "not_applicable"
    assert client.post(url.format("avi"), json={}, headers=csrf).status_code == 200  # and back again
    assert client.post("/api/studio/projects/plain/plugins/anyplug/actions/echo", json={}, headers=csrf).status_code == 200
    assert client.post("/api/studio/projects/plain/plugins/typed/check", headers=csrf).status_code == 403
    assert client.post("/api/studio/projects/avi/plugins/typed/check", headers=csrf).get_json() == {"level": "ok", "text": "fine"}
    assert calls == [("echo", "avi"), ("echo", "avi"), ("check", "avi")]  # never ran for the basic project

    assert _auto_kinds(projects["avi"]) == {"typed_panel", "any_panel"}
    assert _auto_kinds(projects["plain"]) == {"any_panel"}
    # A manifest that lists the panel does not bring it back for an inapplicable project.
    assert lg._applicable(projects["plain"], [{"panel": "typed_panel"}, {"panel": "text"}]) == [{"panel": "text"}]


def test_step_hooks_follow_the_unit_project(world):
    _, _, projects, _ = world
    seen = []
    active = [("typed", StepHooks(before=lambda ctx: seen.append(Path(ctx.project_root).name)))]
    for name in ("avi", "plain", "lab", "avi"):
        ctx = StepContext(project_root=str(projects[name]), plan_id="p", unit_id="u", mode="step", steps=("s",), rows=())
        sh.call_before(ctx, active)
    assert seen == ["avi", "avi"]


def test_unknown_custom_type_and_global_override(world):
    client, csrf, projects, _ = world
    data, plugins = _listing(client, "lab")
    assert data["project_type"] == "my-lab" and "my-lab" in data["project_types_known"]
    assert plugins["typed"]["unavailable_reason"].startswith("Not for my-lab projects")
    assert client.post("/api/studio/plugins/typed/project-types", json={"project_types": ["my-lab"]}).status_code == 403  # CSRF
    reply = client.post("/api/studio/plugins/typed/project-types", json={"project_types": ["my-lab", "basic"]}, headers=csrf)
    assert reply.get_json() == {"id": "typed", "project_types": ["my-lab", "basic"], "overridden": True}
    assert extensions.read_state()["project_types"] == {"typed": ("my-lab", "basic")}
    assert _listing(client, "lab")[1]["typed"]["available"] is True
    assert _listing(client, "plain")[1]["typed"]["applicability"]["source"] == "global"
    avi = _listing(client, "avi")[1]["typed"]
    assert avi["available"] is False and "global plugin setting" in avi["unavailable_reason"]
    assert _auto_kinds(projects["avi"]) == {"any_panel"} and "typed_panel" in _auto_kinds(projects["lab"])
    assert client.post("/api/studio/plugins/typed/project-types", json={"project_types": "all"}, headers=csrf).get_json()["project_types"] == "all"
    assert all(_listing(client, p)[1]["typed"]["available"] for p in projects)
    assert client.post("/api/studio/plugins/typed/project-types", json={"project_types": []}, headers=csrf).status_code == 400
    reply = client.post("/api/studio/plugins/typed/project-types", json={"project_types": None}, headers=csrf).get_json()
    assert reply["overridden"] is False and _listing(client, "plain")[1]["typed"]["available"] is False  # back to the default
    assert client.post("/api/studio/plugins/ghost/project-types", json={"project_types": "all"}, headers=csrf).status_code == 404


def test_project_override_writes_the_active_manifest_with_the_guard(world):
    client, csrf, projects, _ = world
    from alfrd import history

    avi = projects["avi"]
    active = avi / ".alfrd" / "alfrd.yaml"
    url = "/api/studio/projects/avi/plugins/typed/applicability"
    assert client.post(url, json={"enabled": False}).status_code == 403  # CSRF
    reply = client.post(url, json={"enabled": False}, headers=csrf)
    assert reply.status_code == 200, reply.get_json()
    body = reply.get_json()
    assert body["available"] is False and "enabled: false" in body["unavailable_reason"]
    text = active.read_text()
    assert text.startswith("# avi settings\n") and "plugins:\n  typed:\n    enabled: false" in text  # comments kept
    assert (avi / "alfrd.yaml").read_text() == "name: shadowed\n"  # the shadowed root file is untouched
    assert history.versions(avi, "alfrd.yaml")[0]["source"] == "studio"
    assert _listing(client, "avi")[1]["typed"]["applicability"]["source"] == "project"
    assert client.post("/api/studio/projects/avi/plugins/typed/actions/echo", json={}, headers=csrf).status_code == 403

    # Opt a basic project in, whatever the default says; the other projects keep their rules.
    reply = client.post("/api/studio/projects/plain/plugins/typed/applicability", json={"project_types": ["basic"]}, headers=csrf)
    assert reply.get_json()["available"] is True
    assert "project_types:\n    - basic" in (projects["plain"] / "alfrd.yaml").read_text()
    assert _listing(client, "lab")[1]["typed"]["available"] is False

    # Conflict guard: a stale base_hash is refused and nothing is written.
    _, plugins = _listing(client, "avi")
    stale = _listing(client, "avi")[0]["manifest_hash"]
    active.write_text(active.read_text() + "# edited elsewhere\n")
    before = active.read_text()
    reply = client.post(url, json={"enabled": None, "base_hash": stale}, headers=csrf)
    assert reply.status_code == 409 and active.read_text() == before
    fresh = _listing(client, "avi")[0]["manifest_hash"]
    reply = client.post(url, json={"enabled": None, "base_hash": fresh}, headers=csrf)
    assert reply.status_code == 200 and reply.get_json()["available"] is True
    assert "plugins:" not in active.read_text() and "# edited elsewhere" in active.read_text()  # empty block removed

    assert client.post(url, json={"enabled": "no"}, headers=csrf).status_code == 400
    assert client.post(url, json={"project_types": []}, headers=csrf).status_code == 400
    assert client.post("/api/studio/projects/avi/plugins/ghost/applicability", json={}, headers=csrf).status_code == 404


def test_project_override_beats_global(world):
    client, csrf, _, _ = world
    client.post("/api/studio/plugins/typed/project-types", json={"project_types": ["basic"]}, headers=csrf)
    client.post("/api/studio/projects/lab/plugins/typed/applicability", json={"project_types": "all"}, headers=csrf)
    lab = _listing(client, "lab")[1]["typed"]
    assert lab["available"] is True and lab["applicability"]["source"] == "project"
    assert lab["applicability"]["global"] == ["basic"] and lab["applicability"]["project"] == {"project_types": "all"}
    assert _listing(client, "avi")[1]["typed"]["available"] is False  # global still applies elsewhere


def test_record_reports_declared_types(plugin_pkgs):  # noqa: F811
    plugin_pkgs("typed", TYPED)
    records = {r.id: r for r in extensions.load(force=True)}
    assert records["typed"].to_dict()["project_types"] == ["avica"]
