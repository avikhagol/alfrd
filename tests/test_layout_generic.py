"""Template-driven layout and Metadata panels (alfrd.layout_generic): schema, walk, panels, AVICA parity."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from alfrd import layout_generic as lg
from alfrd.avica_layout import layout_patterns, read_workdir, resolve_config, resolve_dir, scan_project_codes
from alfrd.studio_defs import studio_manifest

TREE = Path(__file__).parent / "fixtures" / "avica_tree"
IMAGING = Path(__file__).parent / "fixtures" / "imaging_tree"
GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "avica_tree_view.json"


def test_avica_template_blocks_are_valid():
    spec = lg.spec_for(TREE)
    assert spec["errors"] == []
    assert [lv["level"] for lv in spec["hierarchy"]] == ["project_code", "workdir", "band"]
    assert [p["panel"] for p in spec["views"]["metadata"]] == ["file_status", "files", "csv_table", "image", "avica_config", "avica_inputs"]
    assert set(lg.CLIENT_PANELS) == {"avica_config", "avica_inputs"}
    bad = lg.validate({"hierarchy": [{"level": "step", "dir": "x"}], "views": {"metadata": [{"panel": "chart"}, {"panel": "text"}]}})
    assert len(bad) >= 3, bad


def test_golden_parity_with_the_avica_reader():
    """The generic walk finds the same work dirs, bands, targets and metadata files as avica_layout."""
    root = TREE.resolve()
    target_dir = resolve_dir(root, resolve_config(root).get("target_dir"))
    avica = {(c.code, c.wd.name): c for c in scan_project_codes(root, target_dir, layout_patterns(root))}
    nodes = [n for n in lg.flatten(lg.walk(root)) if n["level"] == "workdir"]
    generic = {(n["values"]["project_code"], n["value"]): n for n in nodes}
    assert set(generic) == set(avica)
    for key, node in generic.items():
        code = avica[key]
        bands = sorted(c["value"] for c in node["children"] if c["level"] == "band")
        assert bands == code.bands, key
        assert node["targets"] == code.targets, key
        assert node["path"] == os.path.relpath(code.wd, root)
    # Metadata files: per work dir and target, file_status finds exactly the declared files read_workdir lists.
    merged = studio_manifest(root)
    declared = [m["file"] for sid in merged["step_order"] for m in merged["steps"][sid].get("metadata") or []]
    for (code_name, wd_name), code in avica.items():
        for target in code.targets:
            regexes = [re.compile("^" + re.escape(f).replace(r"\{target\}", re.escape(target)).replace(r"\{band\}", "[A-Z][A-Z0-9]*?") + "$") for f in declared]
            wd_id = code.id
            expected = {m["name"] for m in read_workdir(root, wd_id, target)["meta"] if any(r.match(m["name"]) for r in regexes)}
            view = lg.view(root, {"target": target, "project_code": code_name, "workdir": wd_name})
            [status] = [p for p in view["panels"] if p["panel"] == "file_status"]
            [inst] = status["instances"]
            found = {f["name"] for e in inst["entries"] for f in e["files"]}
            assert found == expected, (code_name, wd_name, target)
            # The files panel lists what the legacy reader shows for this target in avica.meta.
            [files] = [p for p in view["panels"] if p["panel"] == "files"]
            listed = {f["name"] for i in files["instances"] for f in i["files"]}
            legacy = {m["name"] for m in read_workdir(root, wd_id, target)["meta"]}
            assert listed == legacy, (code_name, wd_name, target, listed ^ legacy)


def test_golden_view_snapshot():
    """The whole Metadata view for 0742+103; regenerate with ALFRD_UPDATE_GOLDEN=1 after an intended change."""
    view = lg.view(TREE, {"target": "0742+103"})
    text = json.dumps(view, indent=1, sort_keys=True, default=str)
    if os.environ.get("ALFRD_UPDATE_GOLDEN") or not GOLDEN.exists():
        GOLDEN.write_text(text + "\n")
    assert json.loads(text) == json.loads(GOLDEN.read_text())
    status = next(p for p in view["panels"] if p["panel"] == "file_status")["instances"][0]
    by_label = {e["label"]: e["status"] for e in status["entries"]}
    assert by_label["MS metadata per band"] == "ok" and by_label["Work dirs and input templates"] == "missing"


def test_panels_scope_json_paths_and_invalid_files(tmp_path):
    root = tmp_path / "p"
    (root / "nights" / "n1" / "chip1").mkdir(parents=True)
    (root / "nights" / "n1" / "chip2").mkdir(parents=True)
    (root / "nights" / "n2" / "chip1").mkdir(parents=True)
    (root / "nights" / "n1" / "chip1" / "M31.json").write_text(json.dumps({"exp": [{"t": 30}, {"t": 60}], "ok": True}))
    (root / "nights" / "n1" / "chip2" / "M31.json").write_text("{broken")
    (root / "nights" / "n2" / "chip1" / "M31.json").write_text(json.dumps({"exp": [], "ok": True}))
    (root / "alfrd.yaml").write_text("""version: 1
name: p
hierarchy:
  - {level: night, dir: "nights/{night}"}
  - {level: chip, dir: "{chip}", targets_from: "{target}.json"}
views:
  metadata:
    - panel: file_status
      scope: chip
      files: [{step: reduce, path: "{chip_dir}/{target}.json", label: frame info, expect: {require: [exp]}}]
    - {panel: json_fields, scope: target, source: "{chip_dir}/{target}.json", fields: ["exp[*].t", ok]}
  vars: {chip_dir: "nights/{night}/{chip}"}
""")
    spec = lg.spec_for(root)
    assert spec["errors"] == []
    nodes = lg.flatten(lg.walk(root))
    assert [(n["level"], n["value"], n["targets"]) for n in nodes] == [
        ("night", "n1", ["M31"]), ("chip", "chip1", ["M31"]), ("chip", "chip2", ["M31"]), ("night", "n2", ["M31"]), ("chip", "chip1", ["M31"])]
    view = lg.view(root, {"target": "M31", "night": "n1"})
    status = next(p for p in view["panels"] if p["panel"] == "file_status")
    assert [(i["where"], i["entries"][0]["status"]) for i in status["instances"]] == [
        ({"night": "n1", "chip": "chip1"}, "ok"), ({"night": "n1", "chip": "chip2"}, "invalid")]
    fields = next(p for p in view["panels"] if p["panel"] == "json_fields")["instances"][0]
    assert fields["fields"] == [{"field": "exp[*].t", "value": [30, 60]}, {"field": "ok", "value": True}]
    assert lg.view(root, {"target": "M31", "night": "n2"})["panels"][0]["instances"][0]["entries"][0]["status"] == "invalid", "an empty required key"


def test_studio_view_endpoint(tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.manifest_default import register_project_folder
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    registered = register_project_folder(service, TREE)
    pid = getattr(registered, "identifier", None) or registered[0].identifier
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service), "STUDIO_DEMO": False})
    client = app.test_client()
    data = client.get(f"/api/studio/projects/{pid}/view", query_string={"entity": "project=x&target=0742%2B103&project_code=RDV41"}).get_json()
    assert data["entity"] == {"project": pid, "target": "0742+103", "project_code": "RDV41"}
    assert data["panels"][0]["instances"][0]["where"] == {"project_code": "RDV41", "workdir": "wd"}
    assert client.get(f"/api/studio/projects/{pid}/view", query_string={"entity": "project=x&galaxy=M87"}).status_code == 400
    short = client.get(f"/api/studio/projects/{pid}/view", query_string={"entity": "target=0742+103"}).get_json()
    assert short["entity"] == {"project": pid, "target": "0742+103"}, "the project comes from the URL"
    assert client.get(f"/api/studio/projects/{pid}/view/file", query_string={"entity": "project=x&target=0742%2B103", "panel": 4, "path": "alfrd.yaml"}).status_code == 403


def test_a_non_avica_project_described_only_by_alfrd_yaml():
    """Phase (e): an imaging tree (nights/chips, no template) gets its hierarchy and Metadata panels from alfrd.yaml."""
    spec = lg.spec_for(IMAGING)
    assert spec["errors"] == []
    assert [(n["path"], n["targets"]) for n in lg.flatten(lg.walk(IMAGING))] == [
        ("nights/n1", ["M31"]), ("nights/n1/chip1", ["M31"]), ("nights/n1/chip2", ["M31"]),
        ("nights/n2", ["NGC253"]), ("nights/n2/chip1", ["NGC253"])], "M31_bias.json names M31, not M31_bias"
    view = lg.view(IMAGING, {"target": "M31"})
    panels = {p["title"]: p for p in view["panels"]}
    status = panels["Step outputs"]["instances"]
    assert [i["where"] for i in status] == [{"night": "n1", "chip": "chip1"}, {"night": "n1", "chip": "chip2"}]
    assert {e["step"]: e["status"] for e in status[0]["entries"]} == {"bias": "ok", "flat": "ok", "stack": "ok"}
    assert {e["step"]: e["status"] for e in status[1]["entries"]} == {"bias": "missing", "flat": "missing", "stack": "invalid"}
    chip1 = {f["name"]: f for f in panels["Chip files"]["instances"][0]["files"]}
    assert chip1["M31_bias.json"]["step"] == "bias" and chip1["M31_bias.json"]["data"] == {"nframes": 11}
    assert chip1["readme.txt"]["step"] is None and chip1["readme.txt"]["format"] == "text"
    [exposures] = panels["Exposures"]["instances"]
    assert exposures["source"] == "nights/n1/chip1/M31.json", "the readable file, not the broken chip2 one"
    assert exposures["fields"][0] == {"field": "exp[*].t", "value": [30, 60]}
    [photometry] = panels["Photometry"]["instances"]
    assert [t["rel"] for t in photometry["tables"]] == ["photometry_M31.csv"], "one table, not one per chip"
    assert [i["where"] for i in panels["Night log"]["instances"]] == [{"night": "n1"}]
    other = lg.view(IMAGING, {"target": "NGC253"})
    assert [i["where"] for i in other["panels"][0]["instances"]] == [{"night": "n2", "chip": "chip1"}]


def test_from_steps_base_defaults_to_meta_dir():
    spec = {"step_order": ["a"], "steps": {"a": {"metadata": [{"file": "{target}.json"}]}}}
    assert [f["path"] for f in lg._files_from_steps(spec)] == ["{meta_dir}/{target}.json"]
    assert [f["path"] for f in lg._files_from_steps(spec, "{chip_dir}/")] == ["{chip_dir}/{target}.json"]
    assert [f["path"] for f in lg._files_from_steps(spec, ".")] == ["./{target}.json"]


def test_project_scope_evaluates_once_without_merging_folder_rows(monkeypatch):
    """A project summary must stay one table even when the selected target spans two chips."""
    spec = lg.spec_for(IMAGING)
    spec["views"]["metadata"] = [{"panel": "project_summary", "scope": "project"}]
    monkeypatch.setattr(lg, "spec_for", lambda _: spec)
    calls = []

    def evaluate(root, panel, values, _spec):
        calls.append(dict(values))
        return {"rows": [{"step": "reduce"}], "total": 1}

    lg.register_panel("project_summary", evaluate, client=True)
    try:
        result = lg.view(IMAGING, {"target": "M31"})
        assert len(result["panels"][0]["instances"]) == 1
        assert result["panels"][0]["instances"][0] == {"where": {}, "rows": [{"step": "reduce"}], "total": 1}
        assert calls == [{}]
    finally:
        lg.unregister_panel("project_summary")


def test_auto_panels_append_once_and_ignore_failures(monkeypatch):
    """``auto(root)`` adds a plugin panel without a views.metadata entry; listed kinds aren't added twice."""
    spec = lg.spec_for(IMAGING)
    listed = list(spec["views"].get("metadata") or [])
    monkeypatch.setattr(lg, "spec_for", lambda _: spec)
    roots = []

    def auto(root):
        roots.append(root)
        return {"title": "Sheet", "scope": "project"} if (root / "alfrd.yaml").exists() else None

    lg.register_panel("sheet_status", lambda *a: {"rows": []}, auto=auto)
    lg.register_panel("broken_auto", lambda *a: {}, auto=lambda root: 1 / 0)
    try:
        result = lg.view(IMAGING, {"target": "M31"})
        auto_panels = [p for p in result["panels"] if p.get("auto")]
        assert [(p["panel"], p["title"], p["scope"]) for p in auto_panels] == [("sheet_status", "Sheet", "project")]
        assert auto_panels[0]["index"] == len(listed) and auto_panels[0]["instances"] == [{"where": {}, "rows": []}]
        assert roots == [IMAGING.resolve()]
        assert all(not p.get("auto") for p in lg.view(IMAGING, {"target": "M31"}, name="other")["panels"])
        spec["views"]["metadata"] = [*listed, {"panel": "sheet_status", "scope": "project", "title": "Mine"}]
        panels = lg.view(IMAGING, {"target": "M31"})["panels"]
        assert [p["title"] for p in panels if p["panel"] == "sheet_status"] == ["Mine"]
    finally:
        lg.unregister_panel("sheet_status")
        lg.unregister_panel("broken_auto")
    assert "sheet_status" not in lg.AUTO_PANELS and "broken_auto" not in lg.AUTO_PANELS
    with pytest.raises(ValueError, match="auto is not callable"):
        lg.register_panel("bad_auto", lambda *a: {}, auto="nope")
