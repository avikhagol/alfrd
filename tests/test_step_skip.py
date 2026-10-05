"""``skip: true`` in alfrd.yaml leaves a template step out of the workflow."""
import pytest
import yaml

from alfrd.execution import load_execution
from alfrd.project_creation import create_project
from alfrd.runtime import RuntimeService, RuntimeStore
from alfrd.studio_defs import studio_manifest, template_path

_TEMPLATE = yaml.safe_load(template_path("avica").read_text())["steps"]
AVICA_STEPS = [s for s, spec in _TEMPLATE.items() if not spec.get("skip")]  # phaseshift is skipped by default


@pytest.fixture
def avica(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    root = tmp_path / "vlbi"
    create_project(RuntimeService(store), root, template="avica")
    return root


def edit(root, change):
    path = root / "alfrd.yaml"
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_top_level_steps_skip(avica):
    merged = studio_manifest(avica)  # the template skips phaseshift by default
    assert "phaseshift" not in merged["step_order"] and merged["skipped_steps"] == ["phaseshift"]
    assert load_execution(avica).step_ids == AVICA_STEPS
    edit(avica, lambda d: d["steps"]["avica_snr"].update(skip=True))
    assert studio_manifest(avica)["skipped_steps"] == ["phaseshift", "avica_snr"]
    assert load_execution(avica).step_ids == [s for s in AVICA_STEPS if s != "avica_snr"]


def test_a_default_skip_can_be_turned_off(avica):
    edit(avica, lambda d: d["steps"]["phaseshift"].update(skip=False))
    ids = load_execution(avica).step_ids
    assert ids.index("phaseshift") == ids.index("fits_to_ms") + 1


def test_workflow_list_skip_and_dependencies(avica):
    def change(data):
        data["workflows"] = [{"name": "avica", "steps": [
            *[s for s in AVICA_STEPS if s not in ("avica_snr", "avica_fill_input")],
            {"id": "avica_snr", "skip": True}, {"id": "avica_fill_input", "depends_on": ["avica_snr", "avica_avg"]}]}]
    edit(avica, change)
    merged = studio_manifest(avica)
    assert "avica_snr" not in merged["steps"] and merged["steps"]["avica_fill_input"]["depends_on"] == ["avica_avg"]
    assert "avica_snr" not in load_execution(avica).step_ids


def test_plan_csv_has_no_column_for_a_skipped_step(avica):
    from alfrd.runtime import plan_csv
    edit(avica, lambda d: d["steps"]["rpicard"].update(skip=True))
    cfg = load_execution(avica)
    plan_csv.create(avica / "p.csv", [{"target": "J1"}], cfg.step_ids, cfg.step_ids, key_column=cfg.key_column,
                    files_column=cfg.files_column, code_column=cfg.code_column, workdir_column=cfg.workdir_column)
    assert "rpicard" not in (avica / "p.csv").read_text().splitlines()[0]


def test_own_step_overrides_now_apply(avica):
    edit(avica, lambda d: d["steps"]["fits_to_ms"].update(label="Load to MS"))
    assert studio_manifest(avica)["steps"]["fits_to_ms"]["label"] == "Load to MS"


def test_after_as_a_step_name_is_not_a_delay(avica):
    edit(avica, lambda d: d["steps"]["phaseshift"].update(after="fits_to_ms", skip=False))
    step = next(s for s in load_execution(avica).steps if s.id == "phaseshift")
    assert step.after is None
