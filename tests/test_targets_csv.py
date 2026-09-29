"""alfrd.targets.csv: the project's target list (alfrd.targets_csv, its endpoints and CLI)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd import targets_csv as tc
from alfrd.cli import alfrd_cli

runner = CliRunner()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "alfrd.yaml").write_text(
        "version: 1\nname: proj\ntemplate: avica\n"
        "workflows:\n  - name: avica\n    steps: [preprocess_fitsidi, fits_to_ms]\n"
    )
    (root / "avica.inp").write_text("target_dir = reductions\n")
    return root


def test_spec_defaults_follow_the_plan_csv_columns(project: Path):
    spec = tc.load_spec(project)
    assert spec.csv == project / "alfrd.targets.csv"
    assert (spec.key_column, spec.files_column, spec.code_column) == ("TARGET_NAME", "FILENAMES", "PROJECT_CODE")
    assert "fitsfilenames" in spec.aliases["files"] and spec.aliases["files"][0] == "FILENAMES"


def test_spec_from_alfrd_yaml(project: Path):
    (project / "alfrd.yaml").write_text(
        (project / "alfrd.yaml").read_text()
        + "targets:\n  csv: lists/sources.csv\n  columns:\n    key: [src]\n    files: [idi]\n"
        + "execution:\n  files_column: FITS\n"
    )
    spec = tc.load_spec(project)
    assert spec.csv == project / "lists" / "sources.csv"
    assert spec.files_column == "FITS"
    assert spec.aliases["key"] == ["TARGET_NAME", "src"] and spec.aliases["files"] == ["FITS", "idi"]


def test_parse_aliases_separators_and_problems(project: Path):
    spec = tc.load_spec(project)
    text = (
        "\ufeffSource Name,fitsfilenames,Project_Code,notes\n"
        "3C274,\"a.idifits; b.idifits\",BW106,x\n"
        "\n"
        "J0742+103,c.idifits d.idifits,,\n"
        "3C274,e.idifits,BW106,dup\n"
        "#skip,f,,\n"
        "A@B,g,,\n"
        ",h,,\n"
    )
    out = tc.parse(text, spec, {"key": "Source Name"})
    assert out["columns"] == {"key": "Source Name", "files": "fitsfilenames", "code": "Project_Code"}
    assert [(r["target"], r["files"], r["code"]) for r in out["rows"]] == [
        ("3C274", "a.idifits,b.idifits", "BW106"), ("J0742+103", "c.idifits,d.idifits", "")]
    assert out["rows"][0]["extra"] == {"notes": "x"}
    assert [p["line"] for p in out["problems"]] == [5, 6, 7, 8]  # file lines; the header is line 1
    assert "repeats line 2" in out["problems"][0]["message"]


def test_parse_tsv_and_missing_key(project: Path):
    spec = tc.load_spec(project)
    tsv = tc.parse("target\tfits\nT1\ta.fits\n", spec)
    assert tsv["rows"][0]["files"] == "a.fits"
    bad = tc.parse("x,y\n1,2\n", spec)
    assert bad["rows"] == [] and "no target column" in bad["problems"][0]["message"]


def test_save_merge_replace_and_atomic(project: Path):
    spec = tc.load_spec(project)
    first = tc.save(spec, "source,fits,code\nA,a1,BV019\nB,b1,\n")
    assert first["added"] == ["A@BV019", "B"]
    assert spec.csv.read_text() == "TARGET_NAME,FILENAMES,PROJECT_CODE\nA,a1,BV019\nB,b1,\n"
    merged = tc.save(spec, "name,files\nB,b2\nC,c1\n")
    assert (merged["added"], merged["updated"]) == (["C"], ["B"])
    assert [r["target"] for r in tc.read(spec)["rows"]] == ["A", "B", "C"]
    # An empty files cell never erases known file names.
    tc.save(spec, "name,files\nB,\n")
    assert tc.read(spec)["rows"][1]["files"] == "b2"
    replaced = tc.save(spec, "name,files\nC,c2\n", mode="replace")
    assert replaced["removed"] == ["A@BV019", "B"] and replaced["updated"] == ["C"]
    assert [r["target"] for r in tc.read(spec)["rows"]] == ["C"]
    with pytest.raises(ValueError, match="line 3"):
        tc.save(spec, "name,files\nD,d\nD,d\n")
    assert [r["target"] for r in tc.read(spec)["rows"]] == ["C"]  # nothing written
    assert not list(spec.csv.parent.glob(".alfrd.targets.csv.*.tmp"))


def test_preview_does_not_write(project: Path):
    spec = tc.load_spec(project)
    result = tc.preview(spec, "name,files\nA,a\n")
    assert result["added"] == ["A"] and result["total"] == 1
    assert not spec.csv.exists()


def test_add_one_row(project: Path):
    spec = tc.load_spec(project)
    tc.add(spec, "A", "a b", "BV019")
    tc.add(spec, "A", "c", "BV019")
    assert tc.read(spec)["rows"] == [{"target": "A", "files": "c", "code": "BV019", "extra": {}}]
    with pytest.raises(ValueError):
        tc.add(spec, "#x")


def test_cli_import_show_and_plan_from_targets(project: Path, tmp_path: Path):
    source = tmp_path / "list.csv"
    source.write_text("source,fitsfilenames,project_code\nT1,a.idifits;b.idifits,BV019\nT2,c.idifits,\n")
    dry = runner.invoke(alfrd_cli, ["targets", "import", str(source), "--root", str(project), "--dry-run"])
    assert dry.exit_code == 0 and "2 added" in dry.output and "(dry run)" in dry.output
    assert not (project / "alfrd.targets.csv").exists()
    done = runner.invoke(alfrd_cli, ["targets", "import", str(source), "--root", str(project)])
    assert done.exit_code == 0, done.output
    shown = runner.invoke(alfrd_cli, ["targets", "show", "--root", str(project)])
    assert "T1@BV019  a.idifits,b.idifits" in shown.output
    plan = runner.invoke(alfrd_cli, ["plan", "new", "--root", str(project), "--from-targets", "-o", str(project / "p.csv")])
    assert plan.exit_code == 0, plan.output
    text = (project / "p.csv").read_text().splitlines()
    assert text[0].startswith("TARGET_NAME,FILENAMES,PROJECT_CODE")
    assert text[1].startswith('T1,"a.idifits,b.idifits",BV019') and text[2].startswith("T2,c.idifits,")


def test_cli_plan_from_targets_without_file(project: Path):
    result = runner.invoke(alfrd_cli, ["plan", "new", "--root", str(project), "--from-targets"])
    assert result.exit_code == 1 and "alfrd targets import" in result.output


# ---------------------------------------------------------------------------
# Studio endpoints


@pytest.fixture()
def studio(project: Path, tmp_path: Path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, project)
    identifier = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({
        "TESTING": True, "SECRET_KEY": "k",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}",
        "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
        "STUDIO_DEFAULT_PROJECT": identifier, "STUDIO_DEMO": False,
    })
    client = app.test_client()
    token = client.get("/api/studio/session").get_json()["csrf_token"]
    return client, {"X-CSRF-Token": token}, identifier


def test_targets_endpoints(studio, project: Path):
    client, h, pid = studio
    empty = client.get(f"/api/studio/projects/{pid}/targets").get_json()
    assert empty["rows"] == [] and empty["spec"]["exists"] is False and empty["spec"]["csv"] == "alfrd.targets.csv"
    body = {"text": "source,fits\nA,a.idifits\n"}
    assert client.post(f"/api/studio/projects/{pid}/targets/preview", json=body).status_code == 403
    assert client.post(f"/api/studio/projects/{pid}/targets", json=body).status_code == 403
    far = client.post(f"/api/studio/projects/{pid}/targets", json=body, headers=h, environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert far.status_code == 403
    preview = client.post(f"/api/studio/projects/{pid}/targets/preview", json=body, headers=h).get_json()
    assert preview["added"] == ["A"] and not (project / "alfrd.targets.csv").exists()
    saved = client.post(f"/api/studio/projects/{pid}/targets", json=body, headers=h)
    assert saved.status_code == 200 and saved.get_json()["added"] == ["A"]
    rows = client.get(f"/api/studio/projects/{pid}/targets").get_json()["rows"]
    assert rows == [{"target": "A", "files": "a.idifits", "code": "", "extra": {}}]
    bad = client.post(f"/api/studio/projects/{pid}/targets", json={"text": "x,y\n1,2\n"}, headers=h)
    assert bad.status_code == 400 and "no target column" in bad.get_json()["error"]["message"]
    assert client.post(f"/api/studio/projects/{pid}/targets", json={"text": 5}, headers=h).status_code == 400
    mode = client.post(f"/api/studio/projects/{pid}/targets", json={**body, "mode": "wipe"}, headers=h)
    assert mode.status_code == 400
    # The targets file is a root CSV, so the Studio's scan lists it like any dataset table.
    scan = client.get(f"/api/studio/projects/{pid}/scan").get_json()
    assert any(f["rel"] == "alfrd.targets.csv" for f in scan["files"])


def test_remove_rows_by_target_or_target_at_code(project: Path):
    spec = tc.load_spec(project)
    tc.save(spec, "source,fits,code\nA,a.fits,C1\nA,b.fits,C2\nB,b.fits,C1\nC,c.fits,C1\n")
    one = tc.remove(spec, ["A@C2", "nope"])
    assert one["removed"] == ["A@C2"] and one["missing"] == ["nope"]
    both = tc.remove(spec, ["A", "C"])
    assert sorted(both["removed"]) == ["A@C1", "C@C1"]
    assert [r["target"] for r in tc.read(spec)["rows"]] == ["B"]
    before = spec.csv.read_text()
    assert tc.remove(spec, ["zzz"])["removed"] == [] and spec.csv.read_text() == before
    with pytest.raises(ValueError):
        tc.remove(spec, [])


def test_cli_remove(project: Path):
    spec = tc.load_spec(project)
    tc.save(spec, "source,fits\nA,a.fits\nB,b.fits\n")
    ok = runner.invoke(alfrd_cli, ["targets", "remove", "A", "--root", str(project)])
    assert ok.exit_code == 0, ok.output
    assert "1 row(s) removed" in ok.output
    none = runner.invoke(alfrd_cli, ["targets", "remove", "A", "--root", str(project)])
    assert none.exit_code == 1 and "not in" in none.output


def test_remove_endpoint(studio, project: Path):
    client, h, pid = studio
    client.post(f"/api/studio/projects/{pid}/targets", json={"text": "source,fits\nA,a.fits\nB,b.fits\n"}, headers=h)
    url = f"/api/studio/projects/{pid}/targets/remove"
    assert client.post(url, json={"targets": ["A"]}).status_code == 403
    assert client.post(url, json={"targets": "A"}, headers=h).status_code == 400
    assert client.post(url, json={"targets": []}, headers=h).status_code == 400
    done = client.post(url, json={"targets": ["A", "Q"]}, headers=h).get_json()
    assert done["removed"] == ["A"] and done["missing"] == ["Q"] and [r["target"] for r in done["rows"]] == ["B"]
