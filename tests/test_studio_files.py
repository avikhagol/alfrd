"""Measurement Set discovery from alfrd.yaml ``artifacts:`` and the confined project explorer (alfrd.project_files)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from test_studio import studio_app  # noqa: F401  (fixture)

from alfrd import project_files as pf


def _ms(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "table.dat").touch()
    (path / "table.info").write_text("Type = Measurement Set\n")
    return path


def _project(tmp_path: Path, artifacts: str = "") -> Path:
    root = tmp_path / "proj"
    (root / ".alfrd").mkdir(parents=True)
    (root / ".alfrd" / "alfrd.yaml").write_text(f"name: proj\n{artifacts}")
    return root


CONFIGURED = """artifacts:
  - name: measurement_sets
    kind: directory
    path_pattern: "data/**/*.ms"
    fallback_patterns: ["legacy/*.ms"]
  - name: not_ms
    kind: directory
    path_pattern: "data/*.tables"
  - name: ms_files_are_not_dirs
    kind: file
    path_pattern: "*.ms"
"""


@pytest.fixture
def client(studio_app, tmp_path, monkeypatch):  # noqa: F811
    from alfrd.gui import studio as studio_module

    app, _ = studio_app
    holder: dict[str, Path] = {}
    monkeypatch.setattr(studio_module, "_project_root", lambda name: holder["root"])
    return app.test_client(), holder


def test_configured_patterns_and_fallback_patterns(tmp_path, client):
    http, holder = client
    root = _project(tmp_path, CONFIGURED)
    _ms(root / "data" / "a.ms")
    _ms(root / "data" / "night1" / "deep" / "b.ms")
    _ms(root / "data" / "c.ms" / "SUBMSS" / "inner.ms")  # never entered: inner.ms is not listed
    _ms(root / "legacy" / "old.ms")
    (root / "data" / "notes.ms").write_text("a file, not a Measurement Set folder")
    holder["root"] = root
    data = http.get("/api/studio/projects/proj/measurement-sets").get_json()
    assert data["configured"] is True
    assert [a["name"] for a in data["artifacts"]] == ["measurement_sets"]
    assert data["artifacts"][0]["patterns"] == ["data/**/*.ms", "legacy/*.ms"]
    assert [e["path"] for e in data["entries"]] == ["data/a.ms", "data/c.ms", "data/night1/deep/b.ms", "legacy/old.ms"]
    assert data["entries"][0] == {"path": "data/a.ms", "name": "a.ms", "artifact": "measurement_sets"}
    assert data["root"] == os.path.realpath(root) and data["truncated"] is False


def test_not_configured_and_empty_results(tmp_path, client):
    http, holder = client
    holder["root"] = root = _project(tmp_path)
    _ms(root / "x.ms")
    data = http.get("/api/studio/projects/proj/measurement-sets").get_json()
    assert data["configured"] is False and data["entries"] == []  # the Studio offers the explorer
    (root / ".alfrd" / "alfrd.yaml").write_text("name: proj\n" + CONFIGURED)
    data = http.get("/api/studio/projects/proj/measurement-sets").get_json()
    assert data["configured"] is True and data["entries"] == []  # configured, nothing there yet


def test_workdir_pattern_without_work_folders_is_explained(tmp_path):
    root = _project(tmp_path, "artifacts:\n  - {name: ms, kind: directory, path_pattern: '{workdir}/**/*.ms'}\n")
    data = pf.measurement_sets(root)
    assert data["configured"] is True and data["entries"] == []
    assert data["notes"] == ["{workdir}/**/*.ms: no work folders found for {workdir}"]


def test_explorer_lists_one_folder_with_ms_as_leaves(tmp_path, client):
    http, holder = client
    holder["root"] = root = _project(tmp_path)
    _ms(root / "obs" / "b.ms")
    (root / "obs" / "flags.flagversions").mkdir()
    (root / "obs" / "sub").mkdir()
    (root / "obs" / "readme.txt").write_text("hello")
    (root / "obs" / ".hidden").write_text("x")
    data = http.get("/api/studio/projects/proj/files").get_json()
    assert data["path"] == "" and data["parent"] is None
    assert [e["name"] for e in data["entries"]] == ["obs"]  # .alfrd and dot files are hidden
    data = http.get("/api/studio/projects/proj/files?path=obs").get_json()
    by = {e["name"]: e for e in data["entries"]}
    assert set(by) == {"b.ms", "flags.flagversions", "sub", "readme.txt"}
    assert by["b.ms"] == {"name": "b.ms", "path": "obs/b.ms", "kind": "ms", "browsable": False}
    assert by["flags.flagversions"]["browsable"] is False and by["sub"]["browsable"] is True
    assert by["readme.txt"]["kind"] == "file" and by["readme.txt"]["size"] == 5
    assert data["parent"] == "" and data["path"] == "obs"
    reply = http.get("/api/studio/projects/proj/files?path=obs/b.ms")
    assert reply.status_code == 400 and "opened, not browsed" in reply.get_json()["error"]["message"]


def test_missing_and_not_a_folder(tmp_path, client):
    http, holder = client
    holder["root"] = root = _project(tmp_path)
    (root / "f.txt").write_text("x")
    reply = http.get("/api/studio/projects/proj/files?path=nope")
    assert reply.status_code == 404 and reply.get_json()["error"]["message"] == "nope does not exist"
    assert http.get("/api/studio/projects/proj/files?path=f.txt").status_code == 400


@pytest.mark.parametrize("path", ["..", "../proj", "obs/../..", "a/../b", "/etc", "C:/x", "a\x00b"])
def test_dotdot_and_absolute_paths_are_refused(tmp_path, client, path):
    http, holder = client
    holder["root"] = _project(tmp_path)
    reply = http.get("/api/studio/projects/proj/files", query_string={"path": path})
    assert reply.status_code == 400


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_symlink_escape_is_shown_but_never_followed(tmp_path, client):
    http, holder = client
    holder["root"] = root = _project(tmp_path, CONFIGURED)
    outside = tmp_path / "outside"
    _ms(outside / "secret.ms")
    (outside / "private.txt").write_text("secret")
    (root / "data").mkdir()
    (root / "data" / "escape").symlink_to(outside)
    (root / "data" / "escape.ms").symlink_to(outside / "secret.ms")
    (root / "data" / "inner").mkdir()
    (root / "data" / "inside-link").symlink_to(root / "data" / "inner")
    (root / "data" / "dangling").symlink_to(tmp_path / "missing")
    data = http.get("/api/studio/projects/proj/files?path=data").get_json()
    by = {e["name"]: e for e in data["entries"]}
    assert by["escape"] == {"name": "escape", "path": "data/escape", "kind": "link", "link": True, "outside": True, "browsable": False}
    assert by["escape.ms"]["kind"] == "link" and by["escape.ms"]["outside"] is True
    assert by["inside-link"]["kind"] == "dir" and by["inside-link"]["browsable"] is True and by["inside-link"]["outside"] is False
    assert by["dangling"]["kind"] in ("broken", "link") and by["dangling"]["browsable"] is False
    reply = http.get("/api/studio/projects/proj/files?path=data/escape")
    assert reply.status_code == 403 and "leaves the project" in reply.get_json()["error"]["message"]
    assert http.get("/api/studio/projects/proj/files?path=data/inside-link").status_code == 200
    # Discovery never offers an MS reached through a link out of the project.
    assert http.get("/api/studio/projects/proj/measurement-sets").get_json()["entries"] == []
    # A project root that is itself reached through a symlink still works (realpath on both sides).
    linked = tmp_path / "linked-root"
    linked.symlink_to(root)
    holder["root"] = linked
    assert http.get("/api/studio/projects/proj/files?path=data").status_code == 200
    assert http.get("/api/studio/projects/proj/files?path=data/escape").status_code == 403


def test_listing_is_bounded(tmp_path, monkeypatch):
    root = _project(tmp_path)
    for i in range(5):
        (root / f"f{i}.txt").write_text("x")
    monkeypatch.setattr(pf, "MAX_ENTRIES", 3)
    data = pf.list_dir(root, "")
    assert len(data["entries"]) == 3 and data["truncated"] is True
