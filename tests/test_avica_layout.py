"""AVICA reduction-tree reader (import-free) used by the Studio and CLI."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd.avica_layout import (
    parse_config_summary,
    read_log,
    read_workdir,
    resolve_config,
    scan_layout,
)
from alfrd.cli import alfrd_cli

TREE = Path(__file__).parent / "fixtures" / "avica_tree"


def test_parse_rich_summary_with_wrapped_cells():
    rows = parse_config_summary((TREE / "avica.summary.txt").read_text(encoding="utf-8"))
    by_key = {(r["step"], r["parameter"]): r for r in rows}
    assert by_key[("fits_to_ms", "mpi_cores")] == {"step": "fits_to_ms", "parameter": "mpi_cores", "source": "default/step", "value": 5}
    assert by_key[("rpicard", "picard_input_template_update")]["value"] == "input_temp_update"
    assert by_key[("other", "size_limit")]["value"] == 2000.0


def test_parse_plain_pipe_table():
    text = "| Step | Parameter | Source | Value |\n| rpicard | mpi_cores | cli | 7 |\n|  | rm_pre | default/step | False |\n"
    assert parse_config_summary(text) == [
        {"step": "rpicard", "parameter": "mpi_cores", "source": "cli", "value": 7},
        {"step": "rpicard", "parameter": "rm_pre", "source": "default/step", "value": False},
    ]


def test_resolve_config_layers_inp_and_cached_summary():
    config = resolve_config(TREE)
    assert config.get("target_dir") == "reductions/"
    assert config.get("picard_input_template_update") == "input_temp_update"
    assert config.sources["snr_threshold_phref"] == "avica.inp"
    assert config.summary and config.summary["file"] == "avica.summary.txt"
    assert config.step_params()["rpicard"]["mpi_cores"] == 3


def test_scan_layout_finds_project_codes_targets_and_logs():
    layout = scan_layout(TREE)
    assert layout["target_dir"] == "reductions"
    assert layout["result_csvs"] == [{"file": "reductions/0742+103_result.csv", "target": "0742+103"}]
    codes = {c["id"]: c for c in layout["project_codes"]}
    assert set(codes) == {"BV019/wd", "BV019/wd_1", "RDV41"}  # wd_{n} from alfrd.yaml avica.workdir
    assert codes["BV019/wd_1"]["targets"] == ["1309+555"]
    codes["BV019"] = codes["BV019/wd"]
    assert codes["RDV41"]["targets"] == ["0742+103"]
    assert codes["RDV41"]["bands"] == ["S", "X"]
    assert codes["BV019"]["targets"] == ["1309+555"]
    assert layout["patterns"]["result_csv"] == ["{target_dir}/{target}_result.csv"]
    assert layout["picard_input_template_update"] == "input_temp_update"
    crash = [log for log in layout["logs"] if log["kind"] == "crash"]
    assert crash and crash[0]["step"] == "rpicard"


def test_read_workdir_filters_target_and_marks_template_updates():
    wd = read_workdir(TREE, "RDV41", "0742+103")
    names = {m["name"] for m in wd["meta"]}
    assert "refants_X_0742+103.avica" in names and "fitsfiles_used.avica" in names
    refants = next(m for m in wd["meta"] if m["name"] == "refants_X_0742+103.avica")
    assert refants["data"]["refant"][0] == "NL" and refants["band"] == "X"
    snrating = next(m for m in wd["meta"] if m["name"].startswith("snrating_X"))
    assert snrating["format"] == "text"
    array = [t for t in wd["templates"] if t["file"] == "array.inp"]
    assert {t["folder"] for t in array} == {
        "reductions/RDV41/wd/input_template",
        "reductions/RDV41/wd/wd_X_0742+103/input_template_X_0742+103",
    }
    assert array[0]["updated"] == {"fringe_solint_optimize_search_cal": "estimate", "fringe_solint_optimize_search_sci": "estimate"}
    finetune = next(t for t in wd["templates"] if t["file"] == "array_finetune.inp")
    assert finetune["values"]["accor_solint"] == "int" and finetune["updated"]["accor_solint"] == 10
    assert wd["template_update"]["folder"] == "input_temp_update"


def test_read_workdir_rejects_traversal():
    with pytest.raises(ValueError):
        read_workdir(TREE, "../..", None)
    with pytest.raises(ValueError):
        read_log(TREE, "../alfrd.yaml")
    assert "preprocess_fitsidi" in read_log(TREE, "avica__log-20260922_122612.log")


def test_cli_avica_scan_and_cached_summary(tmp_path):
    root = tmp_path / "tree"
    shutil.copytree(TREE, root)
    runner = CliRunner()
    result = runner.invoke(alfrd_cli, ["avica", "scan", str(root)])
    assert result.exit_code == 0, result.output
    assert '"RDV41"' in result.output
    result = runner.invoke(alfrd_cli, ["avica", "summary", str(root), "--no-run", "--json"])
    assert result.exit_code == 0, result.output
    assert '"mpi_cores"' in result.output


def test_cli_avica_summary_runs_avica_when_available(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    shutil.copytree(TREE, root)
    fake = tmp_path / "bin" / "avica"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\ncat " + str(root / "avica.summary.txt") + "\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{__import__('os').environ['PATH']}")
    result = CliRunner().invoke(alfrd_cli, ["avica", "summary", str(root)])
    assert result.exit_code == 0, result.output
    assert (root / "avica.summary.json").is_file()


def test_studio_avica_endpoints(tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    root = tmp_path / "tree"
    shutil.copytree(TREE, root)
    store = RuntimeStore(tmp_path / "rt.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.create_project("vasco", root, "test")
    app = create_app({
        "TESTING": True, "SECRET_KEY": "x",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
        "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
    })
    client = app.test_client()
    layout = client.get("/api/studio/avica/vasco/layout").get_json()
    assert {c["code"] for c in layout["project_codes"]} == {"BV019", "RDV41"}
    wd = client.get("/api/studio/avica/vasco/workdir?code=RDV41&target=0742%2B103").get_json()
    assert wd["code"] == "RDV41" and wd["templates"]
    assert client.get("/api/studio/avica/vasco/workdir?code=..").status_code == 400
    log = client.get("/api/studio/avica/vasco/logs/avica__log-20260922_122612.log")
    assert log.status_code == 200 and b"fits_to_ms" in log.data
    assert client.post("/api/studio/avica/vasco/summary").status_code == 403  # CSRF-gated


def test_layout_patterns_come_from_alfrd_yaml(tmp_path):
    root = tmp_path / "tree"
    shutil.copytree(TREE, root)
    text = (root / "alfrd.yaml").read_text(encoding="utf-8")
    text = text.replace('    - "{target_dir}/{project_code}/wd_{n}"\n', "")
    (root / "alfrd.yaml").write_text(text, encoding="utf-8")
    ids = {c["id"] for c in scan_layout(root)["project_codes"]}
    assert ids == {"BV019", "RDV41"}
    assert read_workdir(root, "BV019")["wd"] == "reductions/BV019/wd"


def test_rpicard_words_stay_text_for_json(tmp_path):
    inp = tmp_path / "array_finetune.inp"
    inp.write_text("fringe_solint_mb_long = inf\naccor_solint = int\nminsnr = 3.5\nn = 4\n", encoding="utf-8")
    from alfrd.avica_layout import read_key_values
    assert read_key_values(inp) == {"fringe_solint_mb_long": "inf", "accor_solint": "int", "minsnr": 3.5, "n": 4}


def test_collect_studio_files_bundle(tmp_path):
    import shutil

    from alfrd.avica_layout import collect_studio_files

    base = tmp_path / "vasco_0.3"
    shutil.copytree(Path(__file__).parent / "fixtures" / "avica_tree", base)
    (base / "reductions" / "RDV41" / "wd" / "VLBI.ms" / "ANTENNA").mkdir(parents=True)
    (base / "reductions" / "RDV41" / "wd" / "VLBI.ms" / "ANTENNA" / "table.dat").write_text("x")
    (base / "reductions" / "RDV41" / "wd" / "wd_S").mkdir(exist_ok=True)
    data = collect_studio_files(base, log_tail=10)
    rels = {f["rel"] for f in data["files"]}
    assert data["alfrd_avica_scan"] == 1 and data["root_name"] == "vasco_0.3"
    assert {"alfrd.yaml", "avica.inp", "reductions/0742+103_result.csv", "input_temp_update/array.inp"} <= rels
    assert "reductions/RDV41/wd/avica.meta/refants_X_0742+103.avica" in rels
    assert "reductions/RDV41/wd/wd_X_0742+103/input_template_X_0742+103/array.inp" in rels
    assert "reductions/RDV41/wd/wd_S/.dir" in rels
    assert not any(".ms" in r for r in rels)
    log = next(f for f in data["files"] if f["rel"].endswith(".log"))
    assert len(log["text"]) <= 10
    assert next(f for f in data["files"] if f["rel"] == "avica.summary.txt")["hint"] == "summary"
    # Step logs declared in alfrd.yaml carry their tail too (a bundle has no server to read them from)...
    step_logs = [f for f in data["files"] if f.get("log") and not f["rel"].startswith("avica.logs/")]
    assert step_logs and all(len(f["text"]) <= 10 for f in step_logs)
    # ...while the server scan (log_tail=0) only lists them.
    listed = collect_studio_files(base, log_tail=0)
    assert all("text" not in f for f in listed["files"] if f.get("log") and not f["rel"].startswith("avica.logs/"))


def test_cli_avica_scan_bundle(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from alfrd.cli import alfrd_cli

    out = tmp_path / "scan.json"
    result = CliRunner().invoke(alfrd_cli, ["avica", "scan", str(Path(__file__).parent / "fixtures" / "avica_tree"), "--bundle", str(out), "--log-tail", "0"])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert any(f["rel"].startswith("avica.logs/") and "text" not in f for f in data["files"])
    # No alfrd.yaml: the default one is used ...
    default = CliRunner().invoke(alfrd_cli, ["avica", "scan", str(tmp_path), "--bundle", str(tmp_path / "x.json")])
    assert default.exit_code == 0, default.output
    assert json.loads((tmp_path / "x.json").read_text())["default_manifest"] is True
    # ... unless there is none either.
    monkeypatch.setenv("ALFRD_DEFAULT_MANIFEST", str(tmp_path / "none.yaml"))
    missing = CliRunner().invoke(alfrd_cli, ["avica", "scan", str(tmp_path), "--bundle", str(tmp_path / "y.json")])
    assert missing.exit_code == 1


def _old_root_layout(base: Path, target_value: str | None = None) -> Path:
    """Old AVICA projects: ``<CODE>/wd*/`` directly next to ``avica.inp`` (target_dir = .)."""
    (base / "alfrd.yaml").write_text(
        "version: 1\n"
        "name: 1ktest\n"
        "template: avica\n"
        "avica:\n"
        '  target_dir: "."\n'
        "  meta_dir: vasco.meta\n"
        '  band_dir: ["wd_{band}", "wd_{band}/wd_{band}_{target}"]\n',
        encoding="utf-8",
    )
    (base / "avica.inp").write_text(f"target_dir = {target_value or '.'}\n", encoding="utf-8")
    for rel in ("BV019/wd/wd_X/wd_X_0742+103", "BV019/wd_1/wd_X/wd_X_1309+555", "BV019/wd/vasco.meta", "RDV41/wd/wd_S/wd_S_3C274"):
        (base / rel).mkdir(parents=True)
    (base / "BV019/wd/vasco.meta/listobs.json").write_text("{}", encoding="utf-8")
    return base


@pytest.mark.parametrize("target_value", [".", "./", "ABSOLUTE"])
def test_target_dir_can_be_the_project_root(tmp_path, target_value):
    from alfrd.studio_defs import fill, studio_context

    root = tmp_path / "1ktest"
    root.mkdir()
    value = str(root) + "/" if target_value == "ABSOLUTE" else target_value
    _old_root_layout(root, value)
    layout = scan_layout(root)
    codes = {c["id"]: c for c in layout["project_codes"]}
    assert set(codes) == {"BV019/wd", "BV019/wd_1", "RDV41"}
    assert codes["BV019/wd"]["targets"] == ["0742+103"]
    assert codes["BV019/wd"]["bands"] == ["X"]
    assert codes["BV019/wd_1"]["targets"] == ["1309+555"]
    assert codes["RDV41"]["bands"] == ["S"]
    assert codes["RDV41"]["targets"] == ["3C274"]
    assert codes["BV019/wd"]["meta_files"] == ["listobs.json"]
    assert layout["result_csvs"] == []
    ctx = studio_context(root)
    assert ctx["target_dir"] == "."
    assert {wd["rel"] for wd in ctx["workdirs"]} == {"BV019/wd", "BV019/wd_1", "RDV41/wd"}
    assert fill("{target_dir}/{target}_result.csv", ctx) == "{target}_result.csv"
