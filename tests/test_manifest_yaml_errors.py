"""A broken alfrd.yaml is reported (file, line, problem) wherever commands run, not read as "no steps"."""

import pytest

from alfrd.execution import ExecutionError, load_execution
from alfrd.manifest import ManifestError
from alfrd.manifest_default import manifest_data
from alfrd.studio_defs import studio_manifest

BROKEN = """name: p
template: avica
entrypoint:
  - name: claude
    cmd: [claude, -p]
- name: avica-step
  cmd: [avica]
"""


def test_strict_reading_names_the_line(tmp_path):
    (tmp_path / "alfrd.yaml").write_text(BROKEN)
    with pytest.raises(ManifestError, match=r"alfrd\.yaml line 6, column 1: invalid YAML"):
        manifest_data(tmp_path, strict=True)
    with pytest.raises(ExecutionError, match="invalid YAML"):
        load_execution(tmp_path)


def test_lenient_reading_still_shows_an_empty_project(tmp_path):
    """Studio views keep working while the file is being fixed (Settings shows the error)."""
    (tmp_path / "alfrd.yaml").write_text(BROKEN)
    assert manifest_data(tmp_path)[0] == {}
    assert studio_manifest(tmp_path)["step_order"] == []


def test_a_list_is_not_a_manifest(tmp_path):
    (tmp_path / "alfrd.yaml").write_text("- a\n- b\n")
    with pytest.raises(ExecutionError, match="must be a YAML mapping"):
        load_execution(tmp_path)
