"""Setup forms (``quickstart:``): AVICA writes avica.inp, the agent loop writes alfrd.yaml."""

import pytest
import yaml

from alfrd import quickstart
from alfrd.execution import load_execution
from alfrd.project_creation import create_project
from alfrd.runtime import RuntimeService, RuntimeStore


@pytest.fixture
def service(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


@pytest.fixture
def avica(service, tmp_path):
    root = tmp_path / "avica"
    create_project(service, root, template="avica")
    (root / "avica.inp").write_text("# my settings\ntarget_dir = reductions/   # keep\nsnr_threshold_phref = 7\n")
    return root


@pytest.fixture
def loop(service, tmp_path):
    root = tmp_path / "loop"
    create_project(service, root, template="agent-loop", task="Do it")
    return root


def form(root, form_id="setup"):
    return next(f for f in quickstart.forms(root) if f["id"] == form_id)


def test_avica_form_reads_and_writes_avica_inp(avica, tmp_path):
    setup = form(avica)
    fields = {f["key"]: f for f in setup["fields"]}
    assert setup["target"] == {"file": "avica.inp", "format": "key_value"}
    assert fields["target_dir"]["value"] == "reductions/" and fields["folder_for_fits"]["type"] == "path"
    assert fields["use_local_antab"]["type"] == "toggle" and fields["rpicard.mpi_cores"]["type"] == "number"
    fits = tmp_path / "fits"
    fits.mkdir()
    result = quickstart.apply(avica, "setup", {"folder_for_fits": str(fits), "use_local_antab": "false",
                                               "rpicard.mpi_cores": "8", "snr_threshold_phref": 7, "casadir": "/no/casa"})
    assert result["written"] == {"folder_for_fits": str(fits), "use_local_antab": False, "rpicard.mpi_cores": 8, "casadir": "/no/casa"}
    assert "casadir" in result["warnings"]
    text = (avica / "avica.inp").read_text()
    assert text.startswith("# my settings\ntarget_dir = reductions/   # keep\nsnr_threshold_phref = 7\n")  # unchanged lines kept as they were
    assert f"folder_for_fits = {fits}" in text and "use_local_antab = False" in text and "rpicard.mpi_cores = 8" in text
    assert form(avica)["fields"][0]["value"] == str(fits)


@pytest.mark.parametrize("values, field, message", [
    ({}, "folder_for_fits", "required"),
    ({"folder_for_fits": "x", "rpicard.mpi_cores": 0}, "rpicard.mpi_cores", "at least 1"),
    ({"folder_for_fits": "x", "rpicard.mpi_cores": "many"}, "rpicard.mpi_cores", "number"),
    ({"folder_for_fits": "x", "use_local_antab": "maybe"}, "use_local_antab", "true or false"),
])
def test_invalid_values_are_reported_per_field_and_nothing_is_written(avica, values, field, message):
    before = (avica / "avica.inp").read_text()
    with pytest.raises(quickstart.QuickstartError) as caught:
        quickstart.apply(avica, "setup", values)
    assert message in caught.value.errors[field]
    assert (avica / "avica.inp").read_text() == before


def test_agent_loop_form_configures_entrypoints_and_loop(loop):
    fields = {f["key"]: f for f in form(loop)["fields"]}
    assert fields["workflows.0.repeat.sequence"]["value"] == ["claude", "codex"]
    assert fields["entrypoint.claude.cmd.0"]["value"] == "claude"
    result = quickstart.apply(loop, "setup", {
        "workflows.0.repeat.sequence": "claude, claude, codex", "workflows.0.repeat.iterations": 6,
        "entrypoint.claude.model": "opus", "entrypoint.claude.fallback_models": "sonnet",
        "entrypoint.codex.model": "", "project_settings.human_review": True, "loop.workspace": "shared",
        "entrypoint.claude.cmd.0": "claude", "entrypoint.codex.cmd.0": "codex"})
    assert set(result["written"]) == {"workflows.0.repeat.sequence", "workflows.0.repeat.iterations", "entrypoint.claude.model",
                                      "entrypoint.claude.fallback_models", "project_settings.human_review", "loop.workspace"}
    cfg = load_execution(loop)
    assert [s.entrypoint for s in cfg.steps] == ["claude", "claude", "codex", "claude", "claude", "codex"]
    assert cfg.steps[0].model == "opus" and cfg.steps[0].fallback_models == ("sonnet",)
    assert all(s.human_review for s in cfg.steps) and cfg.loop_workspace == "shared"
    data = yaml.safe_load((loop / "alfrd.yaml").read_text())
    claude = next(e for e in data["entrypoint"] if e["name"] == "claude")
    assert claude["model"] == "opus" and "model" not in next(e for e in data["entrypoint"] if e["name"] == "codex")
    quickstart.apply(loop, "setup", {"entrypoint.claude.model": ""})
    assert "model" not in next(e for e in yaml.safe_load((loop / "alfrd.yaml").read_text())["entrypoint"] if e["name"] == "claude")


def test_manifest_changes_that_do_not_load_are_reverted(loop):
    before = (loop / "alfrd.yaml").read_text()
    with pytest.raises(quickstart.QuickstartError, match="would not load"):
        quickstart.apply(loop, "setup", {"workflows.0.repeat.sequence": "claude, gemini"})
    assert (loop / "alfrd.yaml").read_text() == before
    assert load_execution(loop).steps[0].entrypoint == "claude"


def test_manifest_write_keeps_untouched_sections_byte_for_byte(loop):
    path = loop / "alfrd.yaml"
    path.write_text("# my comment\n" + path.read_text().replace("execution:\n", "execution:  # keep me\n", 1))
    quickstart.apply(loop, "setup", {"entrypoint.claude.model": "opus"})
    text = path.read_text()
    assert text.startswith("# my comment\n") and "execution:  # keep me\n" in text


def test_custom_form_from_alfrd_yaml_and_bad_declarations(loop):
    path = loop / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    data["quickstart"]["notes"] = {"type": "form", "target": "settings.inp",
                                   "fields": {"OWNER": "textbox", "MODE": {"type": "select", "options": ["a", "b"]}, "DIR": "path"}}
    data["quickstart"]["broken"] = {"type": "form", "target": "../x", "fields": {"A": "textbox"}}
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    forms = {f["id"]: f for f in quickstart.forms(loop)}
    assert "error" in forms["broken"] and "setup" in forms
    quickstart.apply(loop, "notes", {"OWNER": "me", "MODE": "b"})
    assert (loop / "settings.inp").read_text() == "OWNER = me\nMODE = b\n"
    with pytest.raises(quickstart.QuickstartError, match="one of"):
        quickstart.apply(loop, "notes", {"MODE": "c"})
    with pytest.raises(quickstart.QuickstartError, match="unknown fields"):
        quickstart.apply(loop, "notes", {"NOPE": 1})


def test_set_path_by_name_index_and_removal():
    data = {"entrypoint": [{"name": "a", "cmd": ["x", "-p"]}]}
    quickstart.set_path(data, "entrypoint.a.cmd.0", "y")
    quickstart.set_path(data, "entrypoint.a.model", "m")
    quickstart.set_path(data, "loop.workspace", "shared")
    assert data == {"entrypoint": [{"name": "a", "cmd": ["y", "-p"], "model": "m"}], "loop": {"workspace": "shared"}}
    quickstart.set_path(data, "entrypoint.a.model", None)
    assert "model" not in data["entrypoint"][0]
    with pytest.raises(quickstart.QuickstartError):
        quickstart.set_path(data, "entrypoint.missing.model", "m")


def test_http_api(service, loop, tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}"})
    client = app.test_client()
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    prefix = f"/api/studio/projects/{service.list_projects()[0].identifier}/quickstart"
    assert client.get(prefix).get_json()["forms"][0]["id"] == "setup"
    assert client.post(prefix + "/setup", json={"values": {"entrypoint.claude.model": "opus"}}).status_code == 403
    ok = client.post(prefix + "/setup", json={"values": {"entrypoint.claude.model": "opus"}}, headers=headers)
    assert ok.status_code == 200 and ok.get_json()["written"] == {"entrypoint.claude.model": "opus"}
    bad = client.post(prefix + "/setup", json={"values": {"workflows.0.repeat.iterations": 0}}, headers=headers)
    assert bad.status_code == 400 and "workflows.0.repeat.iterations" in bad.get_json()["field_errors"]
