"""Collections (kind: collection artifacts): rPicard diagnostics runs, their files, safe serving."""

from __future__ import annotations

from pathlib import Path

import pytest

from alfrd import artifact_collections as ac

NAME = "rpicard_diagnostics"


def _write(path: Path, data: bytes | str = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "alfrd.yaml").write_text("version: 1\nname: proj\ntemplate: avica\nworkflows:\n  - name: avica\n    steps: [rpicard]\n")
    (root / "avica.inp").write_text("target_dir = reductions\n")
    band = root / "reductions" / "BW106" / "wd" / "wd_Q_3C274"
    (root / "reductions" / "BW106" / "wd" / "avica.meta").mkdir(parents=True)
    for stamp in ("2025-09-13_22-56-46", "2025-09-10_10-00-00"):
        run = band / f"diagnostics_{stamp}"
        _write(run / "ACCOR" / "BR_amp_vs_time_accor.png", b"\x89PNG\r\n")
        _write(run / "ACCOR" / "SUMMARY_amp_vs_time_accor.pdf", b"%PDF-1.4")
        _write(run / "PLOTS_VISIBILITIES" / "vis.ps", b"%!PS")
        _write(run / "PLOTS_OPTIMAL_FRINGE_SOLINT_SEARCH" / "scan41" / "d.png", b"\x89PNG")
        _write(run / "FLAGTABLES" / "t.flag")
        _write(run / "fringes_overview.csv.cal", "scan,snr\n1,7.5\n2,12\n")
        _write(run / "flags.list", "flagdata\n")
        _write(run / "evil.svg", "<svg onload=alert(1)>")
        _write(run / "ACCOR" / "x.html", "<script>")
    # A second band / target and a folder that only looks like a run.
    _write(band.parent / "wd_X_J0804" / "diagnostics_2025-01-01_00-00-00" / "GAIN" / "a.png", b"\x89PNG")
    _write(root / "reductions" / "diagnostics_2025-02-02_00-00-00" / "ACCOR" / "a.png", b"\x89PNG")
    return root


def test_declared_by_the_avica_template(project: Path):
    names = [c["name"] for c in ac.collections(project)]
    assert NAME in names


def test_runs_newest_first_with_band_target_and_filters(project: Path):
    runs = ac.list_runs(project, NAME)["runs"]
    assert [(r["band"], r["target"], r["stamp"]) for r in runs] == [
        ("Q", "3C274", "2025-09-13 22:56:46"), ("Q", "3C274", "2025-09-10 10:00:00"), ("X", "J0804", "2025-01-01 00:00:00")]
    assert all(r["code"] == "BW106" and r["workdir"] == "BW106" for r in runs)
    assert [r["target"] for r in ac.list_runs(project, NAME, target="J0804")["runs"]] == ["J0804"]
    assert len(ac.list_runs(project, NAME, band="Q")["runs"]) == 2
    assert ac.list_runs(project, NAME, code="RDV41")["runs"] == []
    with pytest.raises(ac.CollectionError):
        ac.list_runs(project, "nope")


def test_run_order_oldest_from_alfrd_yaml(project: Path):
    (project / "alfrd.yaml").write_text((project / "alfrd.yaml").read_text() + (
        "artifacts:\n  - name: rpicard_diagnostics\n    kind: collection\n"
        "    path_pattern: \"{workdir}/wd_{band}_{target}/diagnostics_*\"\n    run_order: oldest\n    include: [\"*/*.png\"]\n"))
    runs = ac.list_runs(project, NAME)["runs"]
    assert runs[0]["stamp"] == "2025-01-01 00:00:00"
    files = ac.list_files(project, NAME, runs[-1]["rel"])["files"]
    assert {f["path"] for f in files} == {"ACCOR/BR_amp_vs_time_accor.png"}


def test_files_groups_pinned_kinds_and_exclude(project: Path):
    run = ac.list_runs(project, NAME)["runs"][0]["rel"]
    out = ac.list_files(project, NAME, run)
    groups = {g["folder"]: g for g in out["groups"]}
    assert set(groups) == {"", "ACCOR", "PLOTS_VISIBILITIES", "PLOTS_OPTIMAL_FRINGE_SOLINT_SEARCH"}  # FLAGTABLES excluded
    assert groups["ACCOR"]["kinds"] == {"image": 1, "pdf": 1}
    kinds = {f["path"]: f["kind"] for f in out["files"]}
    assert kinds["PLOTS_VISIBILITIES/vis.ps"] == "postscript"
    assert kinds["fringes_overview.csv.cal"] == "table"
    assert "PLOTS_OPTIMAL_FRINGE_SOLINT_SEARCH/scan41/d.png" in kinds  # depth 3
    assert "evil.svg" not in kinds and "ACCOR/x.html" not in kinds  # not included
    pinned = {f["path"] for f in out["files"] if f["pinned"]}
    assert pinned == {"ACCOR/SUMMARY_amp_vs_time_accor.pdf", "fringes_overview.csv.cal", "flags.list"}
    # One folder (plus the pinned files).
    only = {f["path"] for f in ac.list_files(project, NAME, run, folder="PLOTS_VISIBILITIES")["files"]}
    assert only == {"PLOTS_VISIBILITIES/vis.ps"} | pinned


def test_runs_must_match_the_pattern_below_a_workdir(project: Path):
    for bad in ["reductions/BW106/wd", "reductions/diagnostics_2025-02-02_00-00-00", "../x", "/etc",
                "reductions/BW106/wd/wd_Q_3C274/diagnostics_2025-09-13_22-56-46/ACCOR"]:
        with pytest.raises(ac.CollectionError):
            ac.list_files(project, NAME, bad)


def test_resolve_file_types_and_confinement(project: Path):
    run = ac.list_runs(project, NAME)["runs"][0]["rel"]
    path, media, inline = ac.resolve_file(project, NAME, run, "ACCOR/BR_amp_vs_time_accor.png")
    assert (media, inline) == ("image/png", True) and path.is_file()
    assert ac.resolve_file(project, NAME, run, "ACCOR/SUMMARY_amp_vs_time_accor.pdf")[1:] == ("application/pdf", True)
    assert ac.resolve_file(project, NAME, run, "PLOTS_VISIBILITIES/vis.ps")[1:] == ("application/octet-stream", False)
    assert ac.resolve_file(project, NAME, run, "fringes_overview.csv.cal")[1:] == ("text/plain", True)
    for bad in ["../../../../alfrd.yaml", "/etc/passwd", "FLAGTABLES/t.flag", "evil.svg", "ACCOR/x.html"]:
        with pytest.raises(ac.CollectionError):
            ac.resolve_file(project, NAME, run, bad)
    with pytest.raises(FileNotFoundError):
        ac.resolve_file(project, NAME, run, "ACCOR/missing.png")


def test_symlinked_run_folders_are_not_followed(project: Path, tmp_path: Path):
    outside = tmp_path / "outside" / "diagnostics_2030-01-01_00-00-00"
    _write(outside / "ACCOR" / "a.png", b"\x89PNG")
    link = project / "reductions" / "BW106" / "wd" / "wd_Q_3C274" / outside.name
    link.symlink_to(outside, target_is_directory=True)
    assert all(r["name"] != outside.name for r in ac.list_runs(project, NAME)["runs"])
    with pytest.raises(ac.CollectionError):
        ac.list_files(project, NAME, str(link.relative_to(project)))


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
    return app.test_client(), identifier


def test_collection_endpoints(studio):
    client, pid = studio
    base = f"/api/studio/projects/{pid}/collections"
    listed = client.get(base).get_json()
    assert NAME in [c["name"] for c in listed["collections"]]
    runs = client.get(base, query_string={"name": NAME, "target": "3C274"}).get_json()["runs"]
    assert len(runs) == 2
    run = runs[0]["rel"]
    files = client.get(f"{base}/{NAME}/files", query_string={"run": run}).get_json()
    assert any(f["path"] == "ACCOR/BR_amp_vs_time_accor.png" for f in files["files"])
    img = client.get(f"{base}/{NAME}/file", query_string={"run": run, "path": "ACCOR/BR_amp_vs_time_accor.png"})
    assert img.status_code == 200 and img.mimetype == "image/png"
    assert img.headers["X-Content-Type-Options"] == "nosniff" and "sandbox" in img.headers["Content-Security-Policy"]
    assert "attachment" not in img.headers.get("Content-Disposition", "")
    ps = client.get(f"{base}/{NAME}/file", query_string={"run": run, "path": "PLOTS_VISIBILITIES/vis.ps"})
    assert ps.status_code == 200 and "attachment" in ps.headers["Content-Disposition"]
    dl = client.get(f"{base}/{NAME}/file", query_string={"run": run, "path": "ACCOR/BR_amp_vs_time_accor.png", "download": "1"})
    assert "attachment" in dl.headers["Content-Disposition"]
    for query in ({"run": run, "path": "../../../../alfrd.yaml"}, {"run": run, "path": "evil.svg"}, {"run": "reductions", "path": "x"},
                  {"run": run, "path": "ACCOR/missing.png"}):
        assert client.get(f"{base}/{NAME}/file", query_string=query).status_code == 404
    assert client.get(f"{base}/nope/files", query_string={"run": run}).status_code == 404
