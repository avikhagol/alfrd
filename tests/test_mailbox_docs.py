"""Execute the actual mailbox documentation shell blocks against temporary stores."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from alfrd.mailbox import USER, action_hash, execution_env
from alfrd.mailbox_codes import CODES
from test_agent_mailbox import AGENT, box, question  # noqa: F401
from test_chat_orchestration import project, say  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "agent-mailbox.md"
BLOCKS = re.findall(r"```sh\n(.*?)\n```", DOC.read_text(), re.S)


@pytest.mark.parametrize("index", range(len(BLOCKS)))
def test_documented_shell_example(index, box, tmp_path):
    # Every example gets a fresh request. #1 is the documented incoming fixture.
    incoming = box.send(USER, question("incoming", recipient=AGENT))["message"]
    context = execution_env(box.session, box.session.task("r0001.a"), {"id": AGENT["unit_id"]})
    env = {**os.environ, **context,
           "PYTHONPATH": str(ROOT / "src"),
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    work = tmp_path / "examples"
    work.mkdir()
    # Use the installed CLI entry-point function from the checkout under test.
    # This avoids accidentally exercising another alfrd executable on PATH.
    entry = shlex.quote("from alfrd.cli import main; main()")
    wrapper = f'alfrd() {{ {shlex.quote(sys.executable)} -c {entry} "$@"; }}\n'
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", wrapper + BLOCKS[index]],
                            cwd=work, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    messages = box.list()["messages"]
    if index == 0:
        for code in CODES:
            assert f"| {code} |" in result.stdout
        outputs = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        row = outputs[0]["messages"][0]
        assert not {"body", "description", "payload"} & row.keys()
        assert outputs[1]["message"]["id"] == incoming["id"]
        assert box.status(incoming["id"], AGENT)["version"] == 1
    elif index == 1:
        sent = json.loads((work / "sent.json").read_text())["message"]
        assert sent == box.status(sent["id"], AGENT)["message"]
        assert (sent["code"], sent["category"], sent["priority"], sent["step_id"]) == (
            "PRG", "progress", "low", "docs-check")
        assert sent["summary"] == "Checking examples"
        assert sent["description"] == "Examples run in an isolated mailbox.\n"
        assert sent["payload"] == {"step": 1, "of": 2, "label": "examples"}
    elif index == 2:
        sent = json.loads((work / "question-sent.json").read_text())["message"]
        assert sent["kind"] == "question" and sent["code"] == "ASK"
        assert sent["payload"] == {"options": [{"id": "a", "label": "Text"}], "allow_text": True}
        assert box.status(sent["id"], AGENT)["state"] == "pending"
    elif index == 3:
        sent = json.loads((work / "permission-sent.json").read_text())["message"]
        assert sent["kind"] == "permission_request" and sent["code"] == "PRM"
        assert sent["payload"]["action_hash"] == action_hash(sent["payload"])
        assert box.status(sent["id"], AGENT)["state"] == "pending"
    elif index == 4:
        saved = box.status(incoming["id"], AGENT)
        assert saved["state"] == "resolved"
        assert saved["reply"]["payload"]["choice_id"] == "a"
        assert saved["reply"]["payload"]["answer"] == "Prepare text.\n"
        assert saved["reply"]["code"] == "REP"
    else:
        pytest.fail("New documentation block needs outcome assertions")
    assert len(messages) == (1 if index == 0 else 2)


def test_documented_code_table_matches_cli_source():
    # Catch drift in code rows as well as additions to the executable examples.
    assert len(BLOCKS) == 5
    doc = DOC.read_text()
    for code, row in CODES.items():
        category = row["category"] or "(inherited)"
        priority = row["priority"] or "(inherited)"
        assert f"| {code} | {row['kind']} | {category} | {priority} |" in doc
