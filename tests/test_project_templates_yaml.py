"""Every project template yields an alfrd.yaml that Python and the Studio parse alike."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from alfrd.manifest import parse_manifest
from alfrd.project_creation import create_project
from alfrd.runtime import RuntimeService, RuntimeStore
from alfrd.studio_defs import template_path

WEB = Path(__file__).parents[1] / "src/alfrd/web/js/utils/yaml_parser.js"
TEMPLATES = sorted(p.stem for p in template_path("basic").parent.glob("*.yaml"))


def js_parse(text: str):
    script = (f"import {{ parseYaml }} from {json.dumps(str(WEB))}; let s = ''; for await (const c of process.stdin) s += c; "
              "process.stdout.write(JSON.stringify(parseYaml(s)));")
    return json.loads(subprocess.run(["node", "--input-type=module", "-e", script], input=text, text=True,
                                     capture_output=True, check=True).stdout)


@pytest.fixture
def service(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("template", TEMPLATES)
def test_created_project_parses_the_same_in_python_and_studio(service, tmp_path, template):
    root = tmp_path / template
    create_project(service, root, template=template, task="Do the task")
    text = (root / "alfrd.yaml").read_text()
    data = yaml.safe_load(text)
    parse_manifest(data)
    assert js_parse(text) == json.loads(json.dumps(data))


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("template", TEMPLATES)
def test_template_files_parse_the_same(template):
    text = template_path(template).read_text()
    assert js_parse(text) == json.loads(json.dumps(yaml.safe_load(text)))


def test_written_yaml_keeps_long_and_multiline_text_readable():
    from alfrd.yaml_text import dump
    data = {"description": "word " * 60, "instructions": "- Focus: a\n- Tone: b\n\nend", "odd": "trailing \nspace"}
    text = dump(data)
    assert yaml.safe_load(text) == data
    assert len([line for line in text.splitlines() if line.startswith("description:")]) == 1
    assert "instructions: |-\n  - Focus: a\n  - Tone: b\n\n  end" in text
