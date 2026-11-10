"""Graph prompt/reply reads stay scoped to the session, task and archive folder."""
import pytest

from alfrd import chat
from tests.test_chat_task_preconditions import _failed
from tests.test_chat_orchestration import project  # noqa: F401


@pytest.fixture
def archived(project):
    sid = _failed(project)
    units = chat.transcript(project, sid, "r0001.c")["attempts"]
    unit = units[-1]["unit"]
    store = chat.session(project, sid)
    folder = chat._plan_folder(store).path
    return sid, unit, folder


def test_prompt_reply_and_scope(project, archived):
    sid, unit, folder = archived
    for part in ("prompt", "response"):
        page = chat.transcript_part(project, sid, "r0001.c", unit, part)
        assert page["content"] == (folder / "handoffs" / unit / f"{part}.md").read_text()
        assert page["eof"]
    other_sid = chat.create_session(project)["id"]
    for args in ((sid, "r0001.a", unit, "prompt"), (other_sid, "r0001.c", unit, "prompt"),
                 (sid, "r0001.c", "../" + unit, "prompt"),
                 (sid, "r0001.c", str(folder / "handoffs" / unit), "prompt"),
                 (sid, "r0001.c", unit, "../../alfrd.yaml")):
        with pytest.raises(LookupError):
            chat.transcript_part(project, *args)


@pytest.mark.parametrize("component", ["file", "unit", "handoffs", "plan"])
def test_rejects_symlink_escape_in_reads_and_metadata(project, archived, tmp_path, component):
    sid, unit, folder = archived
    source = {"file": folder / "handoffs" / unit / "prompt.md", "unit": folder / "handoffs" / unit,
              "handoffs": folder / "handoffs", "plan": folder}[component]
    destination = tmp_path / "escaped-archive"
    source.rename(destination)
    source.symlink_to(destination, target_is_directory=component != "file")
    with pytest.raises(LookupError, match="symlink"):
        chat.transcript_part(project, sid, "r0001.c", unit, "prompt")
    with pytest.raises(LookupError, match="symlink"):
        chat.transcript(project, sid, "r0001.c")


def test_http_prompt_and_security(project, archived, tmp_path):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    sid, unit, folder = archived
    runtime = RuntimeStore(tmp_path / "runtime.sqlite")
    runtime.initialize()
    service = RuntimeService(runtime)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service), "STUDIO_DEMO": False,
                      "STUDIO_LIVE_INTERVAL": 0, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}"})
    client = app.test_client()
    base = f"/api/studio/projects/{service.list_projects()[0].identifier}/chat/sessions/{sid}/tasks"
    url = f"{base}/r0001.c/transcript/{unit}/prompt"
    response = client.get(url)
    assert response.status_code == 200
    assert response.get_json()["content"] == (folder / "handoffs" / unit / "prompt.md").read_text()
    assert client.post(url).status_code == 405
    assert client.get(f"{base}/r0001.a/transcript/{unit}/prompt").status_code == 404
    assert client.get(f"{base}/r0001.c/transcript/..%2F..%2Fsecret/prompt").status_code == 404
    assert client.get(f"{base}/r0001.c/transcript/{unit}/alfrd.yaml").status_code == 404
    archive = folder / "handoffs" / unit / "prompt.md"
    archive.unlink()
    archive.symlink_to(project / ".alfrd" / "alfrd.yaml")
    assert client.get(url).status_code == 404
    assert client.get(f"{base}/r0001.c/transcript").status_code == 404
