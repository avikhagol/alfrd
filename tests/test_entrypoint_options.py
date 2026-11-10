"""Studio New-project wizard: agent-command options, portable entries, custom-command checks and warnings."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from alfrd import entrypoint_options as eo
from alfrd.manifest import parse_manifest
from alfrd.project_creation import create_project
from alfrd.runtime import RuntimeService, RuntimeStore


def tool(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def service(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    return RuntimeService(store)


def rows(template, env, reachable=False):
    calls = []

    def ollama():
        calls.append(1)
        return {"reachable": reachable}

    out = {r["id"]: r for r in eo.options_for(template, env, ollama=ollama)}
    return out, calls


def test_options_follow_what_is_installed(tmp_path):
    tool(tmp_path / "bin", "claude")
    out, calls = rows("agent-loop", {"PATH": str(tmp_path / "bin")})
    assert out["claude"]["available"] and out["claude"]["status"].startswith("found at ")
    assert out["claude"]["default"] and out["codex"]["default"]
    assert out["codex"]["shown"] and not out["codex"]["available"] and "not installed" in out["codex"]["status"]
    for hidden in ("agy", "codex-oss", "codex-hf"):
        assert not out[hidden]["shown"] and out[hidden]["hidden_reason"]
    assert "agy is not on PATH" in out["agy"]["hidden_reason"]
    assert out["custom"]["shown"] and out["llm-chat"]["shown"] and not out["llm-chat"]["loop"]
    assert not calls  # no codex: Ollama is not probed
    basic, _ = rows("basic", {"PATH": str(tmp_path / "bin")})
    assert not any(r["default"] for r in basic.values())


def test_hugging_face_and_ollama_options_need_codex_and_their_service(tmp_path):
    tool(tmp_path / "bin", "codex")
    env = {"PATH": str(tmp_path / "bin")}
    out, calls = rows("basic", env)
    assert not out["codex-hf"]["shown"] and "HF_TOKEN" in out["codex-hf"]["hidden_reason"]
    assert not out["codex-oss"]["shown"] and "Ollama" in out["codex-oss"]["hidden_reason"] and len(calls) == 1
    out, _ = rows("basic", {**env, "HF_TOKEN": "x"}, reachable=True)
    assert out["codex-hf"]["shown"] and out["codex-oss"]["shown"]


def test_apply_writes_only_ticked_complete_entries(tmp_path):
    data = {"entrypoint": [{"name": "agy", "cmd": ["/mnt/x/.alfrd/bin/agy_prompt.py"]}],
            "steps": {"claude": {"label": "Claude", "category": "Agent"}, "codex-turn": {"label": "Codex", "category": "Agent"},
                      "build": {"label": "Build"}}}
    loop, warnings = eo.apply_to_manifest(data, tmp_path / "new", ["claude"])
    assert loop == ["claude"] and warnings == []
    assert [e["name"] for e in data["entrypoint"]] == ["claude"]
    assert data["entrypoint"][0]["stdin_file"] == "{prompt_file}" and data["entrypoint"][0]["cmd"][0] == "claude"
    assert set(data["steps"]) == {"claude", "build"}
    agents = data["chat"]["agents"]
    assert agents["claude"]["entrypoint"] == "claude" and agents["codex"] is None and agents["agy"] is None
    assert "sonnet" not in agents  # still a default, on the ticked claude entrypoint
    assert "/mnt" not in yaml.safe_dump(data) and str(tmp_path) not in yaml.safe_dump(data)


def test_apply_keeps_template_commands_that_are_not_agents(tmp_path):
    data = {"entrypoint": [{"name": "avica-step", "cmd": ["avica", "pipe", "run"]}, {"name": "codex", "cmd": ["codex"]}]}
    eo.apply_to_manifest(data, tmp_path, ["claude"])
    assert [e["name"] for e in data["entrypoint"]] == ["avica-step", "claude"]
    with pytest.raises(ValueError, match="already used"):
        eo.apply_to_manifest(data, tmp_path, [], [{"name": "avica-step", "cmd": "x"}])


def test_apply_without_claude_moves_the_orchestrator(tmp_path):
    data = {}
    eo.apply_to_manifest(data, tmp_path, ["codex"])
    assert data["chat"]["orchestrator"] == "codex" and data["chat"]["orchestrator_model"] is None
    assert data["chat"]["agents"]["claude"] is None and data["chat"]["agents"]["sonnet"] is None
    from alfrd.chat import merge_settings

    assert set(merge_settings(data["chat"])["agents"]) == {"codex"}
    with pytest.raises(ValueError, match="^agents: unknown option"):
        eo.apply_to_manifest({}, tmp_path, ["nope"])


def test_validate_custom_paths(tmp_path):
    root = tmp_path / "proj"
    script = tool(root / ".alfrd" / "bin", "agent.sh")
    entry, warnings = eo.validate_custom(root, {"name": "mine", "cmd": "./.alfrd/bin/agent.sh --fast"})
    assert entry["cmd"] == ["{project_dir}/.alfrd/bin/agent.sh", "--fast"] and warnings == []
    assert entry["stdin_file"] == "{prompt_file}" and entry["adapter"] == "generic"
    entry, _ = eo.validate_custom(root, {"name": "mine", "cmd": [str(script)]})  # absolute inside: made relative
    assert entry["cmd"] == ["{project_dir}/.alfrd/bin/agent.sh"]
    _, warnings = eo.validate_custom(root, {"name": "mine", "cmd": "surely-not-installed-xyz"})
    assert warnings and "not on PATH" in warnings[0]
    other = tool(tmp_path / "other" / ".alfrd" / "bin", "agy_prompt.py")
    cases = [({"name": "a", "cmd": "/does/not/exist.py"}, "does not exist. Install"),
             ({"name": "a", "cmd": "../escape.sh"}, "leaves the project folder"),
             ({"name": "a", "cmd": "bin/missing.sh"}, "does not exist in"),
             ({"name": "a", "cmd": [str(other)]}, "another project's .alfrd/bin"),
             ({"name": "a", "cmd": ["python3", str(other)]}, "another project's .alfrd/bin"),
             ({"name": "Bad Name", "cmd": "x"}, "custom command name"),
             ({"name": "custom", "cmd": "x"}, "already used"),
             ({"name": "claude", "cmd": "x"}, "already used"),
             ({"name": "a", "cmd": ""}, "command is empty"),
             ({"name": "a", "cmd": "x", "model_option": "model"}, "model option")]
    for spec, message in cases:
        with pytest.raises(ValueError, match="^custom command") as caught:
            eo.validate_custom(root, spec)
        assert message in str(caught.value), (spec, str(caught.value))


def run_in_worktree(entry, root: Path, cwd: Path, prompt: Path):
    """Start ``entry`` as a loop turn would: placeholders rendered, cwd a task worktree, not the project."""
    cwd.mkdir(parents=True, exist_ok=True)
    argv = [a.replace("{project_dir}", str(root)).replace("{prompt_file}", str(prompt)) for a in entry["cmd"]]
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=30, check=False)


def test_interpreter_scripts_are_checked_and_portable(tmp_path):
    root = tmp_path / "proj"
    script = root / ".alfrd" / "bin" / "agent.py"
    script.parent.mkdir(parents=True)
    script.write_text("import sys\nprint('ran', open(sys.argv[1]).read(), sys.argv[2:])\n")  # 0644: no x bit needed
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hi")
    entry, warnings = eo.validate_custom(root, {"name": "mine", "cmd": f"{sys.executable} -u .alfrd/bin/agent.py {{prompt_file}} --out result.txt"})
    assert entry["cmd"] == [sys.executable, "-u", "{project_dir}/.alfrd/bin/agent.py", "{prompt_file}", "--out", "result.txt"]
    assert warnings == []
    done = run_in_worktree(entry, root, tmp_path / "worktrees" / "task-1", prompt)
    assert done.returncode == 0 and done.stdout.strip() == "ran hi ['--out', 'result.txt']", done.stderr
    entry, _ = eo.validate_custom(root, {"name": "mine", "cmd": ["python3", str(script)]})  # absolute inside: made relative
    assert entry["cmd"] == ["python3", "{project_dir}/.alfrd/bin/agent.py"]
    elsewhere = tmp_path / "tools" / "x.py"
    elsewhere.parent.mkdir()
    elsewhere.write_text("")
    assert eo.validate_custom(root, {"name": "a", "cmd": ["python3", str(elsewhere)]})[0]["cmd"] == ["python3", str(elsewhere)]
    sh = root / "scripts" / "run.sh"
    sh.parent.mkdir()
    sh.write_text("echo ok\n")
    assert eo.validate_custom(root, {"name": "a", "cmd": "bash -e scripts/run.sh"})[0]["cmd"] == ["bash", "-e", "{project_dir}/scripts/run.sh"]
    # no script operand: a module, inline code, a placeholder
    assert eo.validate_custom(root, {"name": "a", "cmd": "python3 -m pkg.agent run.py"})[0]["cmd"] == ["python3", "-m", "pkg.agent", "run.py"]
    assert eo.validate_custom(root, {"name": "a", "cmd": "node -e 1"})[0]["cmd"] == ["node", "-e", "1"]
    assert eo.validate_custom(root, {"name": "a", "cmd": "python3 {project_dir}/gen.py"})[0]["cmd"] == ["python3", "{project_dir}/gen.py"]
    (tmp_path / "outside.py").write_text("")
    for cmd, message in [("python3 .alfrd/bin/nope.py", "script .alfrd/bin/nope.py does not exist in"),
                         ("python3 ../outside.py", "script ../outside.py leaves the project folder"),
                         ("python3 /no/such/agent.py", "script /no/such/agent.py does not exist. Install")]:
        with pytest.raises(ValueError, match="^custom command a:") as caught:
            eo.validate_custom(root, {"name": "a", "cmd": cmd})
        assert message in str(caught.value), (cmd, str(caught.value))


def test_program_paths_must_be_executable(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    plain = root / ".alfrd" / "bin" / "agent.py"
    plain.parent.mkdir(parents=True)
    plain.write_text("#!/bin/sh\nexit 0\n")
    plain.chmod(0o644)
    for cmd in ("./.alfrd/bin/agent.py", str(plain)):  # relative, and absolute inside the project
        with pytest.raises(ValueError, match="is not executable. Run chmod"):
            eo.validate_custom(root, {"name": "a", "cmd": cmd})
    outside = tool(tmp_path / "opt", "plain")
    outside.chmod(0o644)
    with pytest.raises(ValueError, match="is not executable"):
        eo.validate_custom(root, {"name": "a", "cmd": str(outside)})
    plain.chmod(0o755)
    assert eo.validate_custom(root, {"name": "a", "cmd": "./.alfrd/bin/agent.py"})[0]["cmd"] == ["{project_dir}/.alfrd/bin/agent.py"]
    home = tmp_path / "home"
    mine = tool(home / ".local" / "bin", "mytool")
    monkeypatch.setenv("HOME", str(home))
    entry, _ = eo.validate_custom(root, {"name": "a", "cmd": "~/.local/bin/mytool --x"})
    assert entry["cmd"] == [str(mine), "--x"], "~ is expanded: argv is not run through a shell"
    assert run_in_worktree(entry, root, tmp_path / "wt", tmp_path / "p").returncode == 0


def test_quoted_commands(tmp_path):
    root = tmp_path / "proj"
    tool(root, "agent tool")
    (root / "my script.py").write_text("import sys\nprint(sys.argv[1:])\n")
    entry, _ = eo.validate_custom(root, {"name": "a", "cmd": '"./agent tool" --flag'})
    assert entry["cmd"] == ["{project_dir}/agent tool", "--flag"]
    assert run_in_worktree(entry, root, tmp_path / "wt", tmp_path / "p").returncode == 0
    entry, _ = eo.validate_custom(root, {"name": "a", "cmd": f"{sys.executable} './my script.py' \"two words\""})
    assert entry["cmd"] == [sys.executable, "{project_dir}/my script.py", "two words"]
    assert run_in_worktree(entry, root, tmp_path / "wt", tmp_path / "p").stdout.strip() == "['two words']"
    with pytest.raises(ValueError, match="^custom command a: the command cannot be read .*Close each quote"):
        eo.validate_custom(root, {"name": "a", "cmd": '"./agent tool --flag'})


def test_create_project_with_chosen_agents(service, tmp_path):
    root = tmp_path / "p"
    with pytest.raises(ValueError, match="Valid names: claude"):
        create_project(service, root, template="agent-loop", task="t", agents=["claude"], sequence=["claude", "codex"])
    assert not root.exists()
    warnings: list[str] = []
    create_project(service, root, template="agent-loop", task="t", iterations=3, agents=["claude", "codex"],
                   warnings=warnings)
    text = (root / ".alfrd" / "alfrd.yaml").read_text()
    data = yaml.safe_load(text)
    parse_manifest(data)
    assert str(root) not in text and str(tmp_path) not in text
    assert data["workflows"][0]["repeat"]["sequence"] == ["claude", "codex"]
    assert {e["name"] for e in data["entrypoint"]} == {"claude", "codex"}
    assert data["chat"]["agents"]["agy"] is None
    from alfrd.execution import load_execution

    assert len(load_execution(root).step_ids) == 3
    only = tmp_path / "only"
    create_project(service, only, template="agent-loop", task="t", agents=["claude"])
    fields = yaml.safe_load((only / ".alfrd" / "alfrd.yaml").read_text())["quickstart"]["setup"]["fields"]
    assert "entrypoint.claude.cmd.0" in fields and not any(k.startswith("entrypoint.codex.") for k in fields)


def test_create_basic_project_with_custom_command(service, tmp_path):
    root = tmp_path / "b"
    tool(root / "scripts", "run.sh")
    create_project(service, root, template="basic", agents=[], custom=[{"name": "runner", "cmd": "scripts/run.sh {prompt_file}"}])
    data = yaml.safe_load((root / ".alfrd" / "alfrd.yaml").read_text())
    assert data["entrypoint"] == [{"name": "runner", "cmd": ["{project_dir}/scripts/run.sh", "{prompt_file}"], "adapter": "generic",
                                   "stdin_file": "{prompt_file}", "output_file": "{response_file}", "output_capture": "stdout"}]
    assert data["chat"]["agents"]["runner"]["entrypoint"] == "runner" and data["chat"]["orchestrator"] == "runner"
    from alfrd import chat

    assert set(chat.settings(root)["agents"]) == {"runner"}


def test_agent_loop_needs_a_loop_agent(service, tmp_path):
    with pytest.raises(ValueError, match="^agents: an agent-loop project needs"):
        create_project(service, tmp_path / "p", template="agent-loop", task="t", agents=["llm-chat"])
    assert not (tmp_path / "p").exists()


def test_warnings_for_copied_configuration(tmp_path):
    root = tmp_path / "copy"
    root.mkdir()
    data = {"entrypoint": [{"name": "agy", "cmd": ["/usr/bin/python3", "/mnt/elsewhere/.alfrd/bin/agy_prompt.py"]},
                           {"name": "gone", "cmd": ["/no/such/program"]}],
            "chat": {"agents": {"luna": {"entrypoint": "luna"}, "agy": {"entrypoint": "agy", "persona": "designer"}}}}
    warnings = eo.entrypoint_warnings(data, root)
    assert any(w.startswith("entrypoint agy:") and "another project's .alfrd" in w for w in warnings)
    assert any(w.startswith("entrypoint gone:") and "does not exist" in w for w in warnings)
    assert any(w.startswith("chat agent luna:") and "not defined here" in w for w in warnings)
    assert any("persona 'designer'" in w for w in warnings)
    # the same copied script run by this very Python is still another project's script
    warnings = eo.entrypoint_warnings({"entrypoint": [{"name": "mine", "cmd": [sys.executable, "/tmp/other/.alfrd/bin/agent.py"]}]}, root)
    assert len(warnings) == 1 and warnings[0].startswith("entrypoint mine:") and "another project's .alfrd" in warnings[0]
    assert eo.entrypoint_warnings({"entrypoint": [{"name": "mine", "cmd": [sys.executable, "-m", "alfrd"]}]}, root) == []


def test_save_settings_warns_but_saves(tmp_path):
    from alfrd import chat

    root = tmp_path / "proj"
    (root / ".alfrd").mkdir(parents=True)
    (root / ".alfrd" / "alfrd.yaml").write_text(yaml.safe_dump({
        "version": 1, "name": "proj", "entrypoint": [{"name": "far", "cmd": ["/no/such/agent"]}]}))
    saved = chat.save_settings(root, {"agents": {"far": {"label": "Far", "entrypoint": "far", "roles": []}}})
    assert any(w.startswith(("chat agent far", "entrypoint far")) for w in saved["warnings"])
    assert "far" in chat.settings(root)["agents"]
    assert "far" in chat.settings_view(root)["entrypoints"]


pytest.importorskip("flask")


def make_client(tmp_path, experimental=True):
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'c.sqlite'}",
                      "RUNTIME_SERVICE": service, "CATALOG_READER": RuntimeCatalogReader(service),
                      "STUDIO_DEMO": False, "STUDIO_LIVE_INTERVAL": 0, "STUDIO_EXPERIMENTAL": experimental})
    c = app.test_client()
    c.h = {"X-CSRF-Token": c.get("/api/studio/session").get_json()["csrf_token"]}
    return c


def test_api_options_and_create(tmp_path, monkeypatch):
    monkeypatch.setattr("alfrd.chat._ollama_probe", lambda *a, **k: {"reachable": False})
    client = make_client(tmp_path)
    body = client.get("/api/studio/entrypoint-options?template=agent-loop").get_json()
    assert body["experimental"] is False
    ids = [o["id"] for o in body["options"]]
    assert ids[:2] == ["claude", "codex"] and ids[-1] == "custom"
    assert {"id", "label", "help", "available", "status", "default", "group", "hidden_reason", "shown"} <= set(body["options"][0])
    root = tmp_path / "made"
    made = client.post("/api/studio/projects/create", json={"path": str(root), "template": "agent-loop", "task": "t",
                                                           "iterations": 2, "agents": ["codex"]}, headers=client.h)
    assert made.status_code == 201, made.get_json()
    assert isinstance(made.get_json()["warnings"], list)
    data = yaml.safe_load((root / ".alfrd" / "alfrd.yaml").read_text())
    assert data["workflows"][0]["repeat"]["sequence"] == ["codex"]
    bad = client.post("/api/studio/projects/create", json={"path": str(tmp_path / "bad"), "template": "basic",
                                                          "custom": [{"name": "x", "cmd": "/mnt/z/.alfrd/bin/a.py"}]}, headers=client.h)
    assert bad.status_code == 400 and bad.get_json()["error"]["message"].startswith("custom command")
    assert not (tmp_path / "bad").exists()
    wrong = client.post("/api/studio/projects/create", json={"path": str(tmp_path / "w"), "agents": "claude"}, headers=client.h)
    assert wrong.status_code == 400
    check = client.post("/api/studio/entrypoint-check", json={"path": str(tmp_path / "w"),
                                                             "custom": [{"name": "x", "cmd": "../up.sh"}]}, headers=client.h)
    assert check.status_code == 400 and "leaves the project folder" in check.get_json()["error"]["message"]


def test_api_without_experimental_features(tmp_path, monkeypatch):
    monkeypatch.setattr("alfrd.chat._ollama_probe", lambda *a, **k: {"reachable": False})
    client = make_client(tmp_path, experimental=False)
    assert client.get("/api/studio/entrypoint-options").get_json()["experimental_templates"] == ["agent-loop"]
    refused = client.post("/api/studio/projects/create", json={"path": str(tmp_path / "p"), "template": "agent-loop",
                                                              "task": "t", "agents": ["claude"]}, headers=client.h)
    assert refused.status_code == 400 and "--experimental-features" in refused.get_json()["error"]["message"]


def test_probe_lists_manifest_entrypoints(tmp_path, monkeypatch):
    from alfrd import chat

    monkeypatch.setattr(chat, "_ollama_probe", lambda *a, **k: {"reachable": False})
    monkeypatch.setattr(chat, "codex_models", list)
    root = tmp_path / "proj"
    tool(root / "bin", "agent.sh")
    (root / ".alfrd").mkdir()
    (root / ".alfrd" / "alfrd.yaml").write_text(yaml.safe_dump({
        "version": 1, "name": "proj", "entrypoint": [{"name": "mine", "cmd": ["{project_dir}/bin/agent.sh"]},
                                                     {"name": "lost", "cmd": ["no-such-cli-xyz"]}],
        "chat": {"agents": {"mine": {"label": "Mine", "entrypoint": "mine", "roles": []},
                            "own": {"label": "Own", "cmd": ["{project_dir}/bin/agent.sh", "-x"], "roles": []}}}}))
    out = chat.probe(root)
    found = {e["name"]: e for e in out["entrypoints"]}
    assert found["mine"]["available"] and not found["lost"]["available"]
    assert os.path.basename(found["mine"]["program"]) == "agent.sh" and found["mine"]["cmd"] == ["{project_dir}/bin/agent.sh"]
    # a chat agent on that entrypoint (or with its own such cmd) is probed in the project, as the card is
    assert out["agents"]["mine"]["available"] and out["agents"]["mine"]["path"] == str(root / "bin" / "agent.sh")
    assert out["agents"]["mine"]["entrypoint"] == "mine" and out["agents"]["mine"]["cmd"] == ["{project_dir}/bin/agent.sh"]
    assert out["agents"]["own"]["available"] and out["agents"]["own"]["cmd"] == ["{project_dir}/bin/agent.sh", "-x"]
