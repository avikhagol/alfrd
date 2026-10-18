"""Chat setup: agent env / fallback settings, presets, saving the chat block, probe, live activity,
the orchestrator rows in the task graph, limit fallback, and the built-in OpenAI-compatible client."""
from __future__ import annotations

import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from alfrd import chat, llm_chat
from alfrd.runtime import scheduler
from test_chat_orchestration import (calls, configure, events, fake, orchestrator_calls, plan, project, result,  # noqa: F401
                                     run, say, task)

LIMIT = {"exit": 1, "say": "Error: 429 Too Many Requests: rate limit reached for this model"}


# ---------------------------------------------------------------------------
# Settings


def test_agent_env_fallback_and_model_option_are_validated(project):
    conf = chat.merge_settings({"agents": {"codex": {"fallback": "agy", "env": {"OPENAI_BASE_URL": "http://x", "PORT": 8080}}}})
    assert conf["agents"]["codex"]["env"] == {"OPENAI_BASE_URL": "http://x", "PORT": "8080"}
    for bad, match in (({"codex": {"fallback": "ghost"}}, "fallback 'ghost' is not a chat agent"),
                       ({"codex": {"fallback": "codex"}}, "another agent"),
                       ({"codex": {"env": ["A=1"]}}, "env must map"),
                       ({"codex": {"env": {"1BAD": "x"}}}, "not a variable name"),
                       ({"codex": {"env": {"A": True}}}, "must be text"),
                       ({"codex": {"model_option": "model"}}, "model_option"),
                       ({"agy": None, "codex": {"fallback": "agy"}}, "is not a chat agent")):
        with pytest.raises(Exception, match=match):
            chat.merge_settings({"agents": bad})
    configure(project, agents={"sonnet": {"persona": "tester"}, "codex": {"env": {"CODEX_HOME": "/tmp/ch"}}})
    sid = chat.create_session(project)["id"]
    cfg = chat.execution_config(project, sid)
    assert cfg.step("codex").env["CODEX_HOME"] == "/tmp/ch"


def test_presets_are_offered_not_active_and_valid_when_added(project):
    view = chat.settings_view(project)
    assert set(view["presets"]) == {"codex", "claude", "agy", "ollama", "ollama-claude", "hf", "local-llm"}
    # base CLIs are templates for new agents (a second Codex on another model); the free-model ones are new
    assert set(view["presets"]) & set(view["agents"]) == {"codex", "claude", "agy"} & set(view["agents"])
    assert "orchestration" in view["roles"] and "writing" in view["roles"]
    assert view["block"] == {"agents": {"sonnet": {"persona": "tester"}}} and len(view["hash"]) == 64
    assert view["presets"]["ollama-claude"]["agent"]["env"]["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    agents = {k: p["agent"] for k, p in view["presets"].items()}
    conf = chat.merge_settings({"agents": agents})
    chat._check_entrypoints(project, conf)
    configure(project, agents={**agents, "sonnet": {"persona": "tester"}})
    sid = chat.create_session(project)["id"]
    cfg = chat.execution_config(project, sid)
    assert "--oss" in cfg.step("ollama").argv and "--model" in cfg.step("ollama").argv
    assert cfg.step("ollama-claude").env["ANTHROPIC_AUTH_TOKEN"] == "ollama"
    assert any(a.startswith("model_providers.hf=") for a in cfg.step("hf").argv)
    local = cfg.step("local-llm").argv
    assert local[:3] == (sys.executable, "-m", "alfrd.llm_chat") and local[local.index("--model") + 1] == "qwen3:8b"


def test_save_settings_keeps_the_rest_of_alfrd_yaml(project):
    from alfrd import history

    manifest = project / ".alfrd" / "alfrd.yaml"
    manifest.write_text("# top comment\n" + manifest.read_text())
    base = chat.settings_view(project)["hash"]
    block = {"orchestrator": "sonnet", "agents": {"sonnet": {"persona": "tester", "fallback": "codex"}}}
    out = chat.save_settings(project, block, base_hash=base)
    text = manifest.read_text()
    assert text.startswith("# top comment\n") and out["hash"] == history.text_hash(text)
    assert yaml.safe_load(text)["chat"] == block and chat.settings(project)["orchestrator"] == "sonnet"
    with pytest.raises(history.Conflict):
        chat.save_settings(project, {}, base_hash=base)
    with pytest.raises(Exception, match="no entrypoint"):
        chat.save_settings(project, {"agents": {"x": {"entrypoint": "nope"}}}, base_hash=out["hash"])
    chat.save_settings(project, {}, base_hash=out["hash"])
    assert "chat" not in yaml.safe_load(manifest.read_text())


def _client(project, tmp_path):
    pytest.importorskip("flask")
    from alfrd.gui import create_app
    from alfrd.gui.services import RuntimeCatalogReader
    from alfrd.runtime import RuntimeService, RuntimeStore

    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    service.register_manifest(project / ".alfrd" / "alfrd.yaml")
    app = create_app({"TESTING": True, "SECRET_KEY": "k", "RUNTIME_SERVICE": service,
                      "CATALOG_READER": RuntimeCatalogReader(service),
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}"})
    client = app.test_client()
    h = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    return client, h, f"/api/studio/projects/{service.list_projects()[0].identifier}/chat"


def test_http_settings_round_trip_probe_and_conflict(project, tmp_path, monkeypatch):
    client, h, base = _client(project, tmp_path)
    got = client.get(base + "/settings").get_json()
    assert {"presets", "roles", "hash", "block"} <= set(got)
    block = {**got["block"], "agents": {**got["block"]["agents"], "local": got["presets"]["local-llm"]["agent"]}}
    assert client.post(base + "/settings", json={"chat": block, "base_hash": got["hash"]}).status_code == 403   # CSRF
    saved = client.post(base + "/settings", json={"chat": block, "base_hash": got["hash"]}, headers=h)
    assert saved.status_code == 200
    body = saved.get_json()
    assert "local" in body["settings"]["agents"] and body["hash"] == body["settings"]["hash"] != got["hash"]
    stale = client.post(base + "/settings", json={"chat": {}, "base_hash": got["hash"]}, headers=h)
    assert stale.status_code == 409 and stale.get_json()["current_hash"] == body["hash"]
    bad = client.post(base + "/settings", json={"chat": {"agents": {"codex": {"fallback": "codex"}}}, "base_hash": body["hash"]}, headers=h)
    assert bad.status_code == 400
    assert client.post(base + "/settings", json={}, headers=h).status_code == 400
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    probe = client.get(base + "/probe").get_json()
    assert probe["agents"]["local"]["available"] and probe["ollama"]["reachable"] is False
    assert probe["huggingface"] == {"token": False}


def test_probe_finds_programs_on_path_and_no_ollama(project, tmp_path, monkeypatch):
    configure(project, agents={"sonnet": {"persona": "tester"}, "ghost": {"cmd": ["no-such-agent-program"]},
                               "pathy": {"entrypoint": "codex-oss"}})
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    monkeypatch.setenv("HF_TOKEN", "secret")
    out = chat.probe(project)
    assert out["agents"]["claude"]["available"] and out["agents"]["claude"]["path"].endswith("/bin/claude")
    assert out["agents"]["ghost"] == {"available": False, "program": "no-such-agent-program", "path": None,
                                      "detail": "no-such-agent-program is not on PATH"}
    assert out["agents"]["pathy"]["available"] and out["agents"]["pathy"]["path"] == str(tmp_path / "bin" / "codex")
    assert out["presets"]["local-llm"]["available"] and out["presets"]["local-llm"]["path"] == sys.executable
    assert out["ollama"]["url"] == "http://127.0.0.1:9" and not out["ollama"]["reachable"] and out["ollama"]["error"]
    assert out["huggingface"] == {"token": True} and "secret" not in json.dumps(out)
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert chat.ollama_url() == "http://127.0.0.1:11434"


# ---------------------------------------------------------------------------
# Activity, graph


TALKER = r'''
import json, pathlib, sys, time
sys.stdin.read()
err = sys.stderr  # stdout is the reply (output_capture stdout); progress goes to the log
print("\x1b[1;32mstarting work\x1b[0m \x1b]0;title\x07", file=err, flush=True)
for word in ("Hello ", "streaming ", "world"):
    print(json.dumps({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": word}}}), file=err, flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}), file=err, flush=True)
while pathlib.Path("hold-talker").exists(): time.sleep(.05)
print("finished")
'''


def test_activity_tails_the_running_unit(project, tmp_path):
    script = tmp_path / "talker.py"
    script.write_text(TALKER)
    configure(project, agents={"sonnet": {"persona": "tester"}, "talker": {"cmd": [sys.executable, str(script)]}})
    (project / "hold-talker").write_text("")
    sid = chat.create_session(project)["id"]
    say(project, sid, "@talker go")
    folder = scheduler.PlanDir(project, f"chat-{sid}")
    scheduler.spawn_runner(folder)
    deadline = time.time() + 20
    out = {"running": []}
    try:
        while time.time() < deadline:
            out = chat.activity(project, sid, lines=20)
            if out["running"] and any("→ Bash" in line for line in out["running"][0]["tail"]):
                break
            time.sleep(.05)
        item = out["running"][0]
        assert item["row"] == "r0001.t1" and item["owner"] == "talker" and item["kind"] == "task"
        assert item["title"] == "go" and item["attempt"] == 1 and item["bytes"] > 0 and item["seconds"] >= 0
        assert item["log"].startswith(".alfrd/plans/") and (project / item["log"]).is_file()
        tail = item["tail"]
        assert "starting work" in tail and "Hello streaming world" in tail
        assert any(line.startswith('→ Bash {"command": "ls"}') for line in tail)
        assert not any("\x1b" in line or line.startswith("{") for line in tail)
        assert len(chat.activity(project, sid, lines=1)["running"][0]["tail"]) == 1
    finally:
        (project / "hold-talker").unlink()
    while time.time() < deadline and (folder.runner_alive() or folder.load()["status"] == "running"):
        time.sleep(.05)
    assert chat.activity(project, sid)["running"] == []
    assert chat.session_state(project, sid)["requests"][0]["status"] == "done"


def test_graph_planned_edges_fall_back_for_older_records(project):
    fake(project, plan=plan(task("a"), task("b", owner="agy", deps=["a"])), a=result(), b=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "two steps")
    run(project, sid)
    store = chat.session(project, sid)
    rec = store.task("r0001.b")
    assert rec["planned_by"] == "r0001.plan"
    rec.pop("planned_by")                    # a record written before planned_by existed
    store.save_task(rec)
    g = chat.graph(store)
    assert ("r0001.plan", "r0001.b", "planned") in {(e["from"], e["to"], e["kind"]) for e in g["edges"]}
    assert {n["kind"] for n in g["nodes"]} == {"plan", "task"}


# ---------------------------------------------------------------------------
# Fallback on a limit


def test_a_rate_limited_task_moves_to_the_fallback_agent_without_spending_a_retry(project):
    configure(project, agents={"sonnet": {"persona": "tester"}, "codex": {"fallback": "agy"}})
    fake(project, plan=plan(task("a"), task("b", owner="sonnet", deps=["a"])),
         a=[LIMIT, {"exit": 3}, result(summary="agy did it")], b=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "do a then b")
    state = run(project, sid)
    assert [(c["agent"], c["task"]) for c in calls(project) if c["kind"] == "task"] == \
        [("codex", "a"), ("agy", "a"), ("agy", "a"), ("claude", "b")]   # agy still had its own retry
    a = {t["row"]: t for t in state["tasks"]}["r0001.a"]
    assert a["owner"] == "agy" and a["fallback_from"] == ["codex"] and a["status"] == "done"
    assert a["task"]["owner"] == "agy" and "You are `agy`" in [c for c in calls(project) if c["agent"] == "agy"][0]["prompt"]
    assert a["cell"] == "done" and a["fallbacks"][0]["reason"].startswith("Error: 429")
    assert state["requests"][0]["status"] == "done"
    moved = [e for e in events(project, sid) if e["kind"] == "task.fallback"]
    assert len(moved) == 1 and moved[0]["data"]["from"] == "codex" and moved[0]["data"]["to"] == "agy"
    assert any(m.get("kind") == "fallback" for m in state["messages"])


def test_orchestrator_rows_fall_back_once_and_use_the_fallback_agents_model(project):
    configure(project, agents={"sonnet": {"persona": "tester"}, "claude": {"fallback": "agy"}, "agy": {"fallback": "claude"}})
    fake(project, plan=[{"exit": 1, "say": "Claude AI usage limit reached|1760000000"}, plan(task("x", owner="codex"))],
         x=result())
    sid = chat.create_session(project)["id"]
    say(project, sid, "plan it")
    state = run(project, sid)
    used = [(c["agent"], c["kind"], c["model"]) for c in orchestrator_calls(project)]
    assert used == [("claude", "plan", "sonnet"), ("agy", "plan", "agy")]   # no orchestrator_model on agy
    rows = {t["row"]: t for t in state["tasks"]}
    assert rows["r0001.plan"]["owner"] == "agy" and rows["r0001.plan"].get("model") is None
    assert state["requests"][0]["status"] == "done"
    # exit 0 with a limit text instead of a plan; then both agents limited: one hop each, no loop
    (project / "calls.jsonl").unlink()
    fake(project, plan=[{"text": "You've hit your limit · resets 5pm"}, {"exit": 1, "say": "rate_limit_error"}])
    say(project, sid, "again")
    state = run(project, sid)
    assert [c["agent"] for c in orchestrator_calls(project)] == ["claude", "agy"]
    rows = {t["row"]: t for t in state["tasks"]}
    assert rows["r0002.plan"]["fallback_from"] == ["claude"] and rows["r0002.plan"]["status"] == "failed"
    assert state["requests"][1]["status"] == "failed"


def test_a_failure_without_a_limit_does_not_fall_back(project):
    configure(project, agents={"sonnet": {"persona": "tester"}, "codex": {"fallback": "agy"}})
    fake(project, t1={"exit": 2})
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex fail plainly")
    state = run(project, sid)
    assert [c["agent"] for c in calls(project)] == ["codex", "codex"]
    assert state["tasks"][0]["owner"] == "codex" and "fallback_from" not in state["tasks"][0]
    assert chat._limit_reason("model 'x' not found, try pulling it first") and not chat._limit_reason("all good")


# ---------------------------------------------------------------------------
# alfrd.llm_chat against a fake OpenAI-compatible server


class _Fake(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        model = body["model"]
        if model == "limited":
            self._json(429, {"error": {"message": "Rate limit reached", "type": "rate_limit"}})
        elif model == "missing":
            self._json(404, {"error": {"message": f"model \"{model}\" not found, try pulling it first"}})
        elif not body.get("stream"):
            self._json(200, {"choices": [{"message": {"content": "<think>hmm</think>plain answer"}}],
                             "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ({"choices": [{"delta": {"reasoning": "thinking..."}}]},
                          {"choices": [{"delta": {"content": "Hello "}}]},
                          {"choices": [{"delta": {"content": "from the fake"}}]},
                          {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}}):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

    def _json(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def llm_server():
    _Fake.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def _main(monkeypatch, capsys, *args, prompt="hi"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(prompt))
    code = llm_chat.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_llm_chat_streams_and_reports_usage(llm_server, monkeypatch, capsys):
    monkeypatch.setenv("MY_KEY", "sk-test")
    code, out, err = _main(monkeypatch, capsys, "--base-url", llm_server, "--model", "m1", "--api-key-env", "MY_KEY",
                           "--max-tokens", "50", prompt="Say hello")
    assert code == 0 and out == "Hello from the fake\n"
    assert "[thinking] thinking..." in err and "Hello from the fake" in err
    assert json.loads(err.strip().splitlines()[-1]) == {"type": "usage", "usage": {"input_tokens": 7, "output_tokens": 4, "total_tokens": 11}}
    seen = _Fake.seen[-1]
    assert seen["path"] == "/v1/chat/completions" and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["messages"] == [{"role": "user", "content": "Say hello"}] and seen["body"]["max_tokens"] == 50
    code, out, _ = _main(monkeypatch, capsys, "--base-url", llm_server, "--model", "m1", "--no-stream")
    assert code == 0 and out == "plain answer\n"


def test_llm_chat_exit_codes(llm_server, monkeypatch, capsys):
    code, _, err = _main(monkeypatch, capsys, "--base-url", llm_server, "--model", "limited")
    assert code == 2 and "rate limit" in err and chat._limit_reason(err)
    code, _, err = _main(monkeypatch, capsys, "--base-url", llm_server, "--model", "missing")
    assert code == 1 and "model missing not found" in err and chat._limit_reason(err)
    monkeypatch.delenv("NOPE_KEY", raising=False)
    code, _, err = _main(monkeypatch, capsys, "--base-url", llm_server, "--api-key-env", "NOPE_KEY")
    assert code == 1 and "NOPE_KEY is not set" in err
    code, _, err = _main(monkeypatch, capsys, "--base-url", "http://127.0.0.1:9/v1")
    assert code == 1 and "cannot reach" in err
    code, _, _ = _main(monkeypatch, capsys, "--base-url", llm_server, prompt="  ")
    assert code == 1


def test_local_llm_preset_answers_a_chat_task(project, llm_server):
    preset = chat.PRESETS["local-llm"]["agent"]
    configure(project, agents={"sonnet": {"persona": "tester"},
                               "local": {**preset, "model": "m1", "env": {"ALFRD_LLM_BASE_URL": llm_server}}})
    sid = chat.create_session(project)["id"]
    say(project, sid, "@local summarize")
    state = run(project, sid)
    assert state["requests"][0]["status"] == "done"
    assert state["messages"][-1]["text"] == "Hello from the fake" and state["messages"][-1]["agent"] == "local"
    assert _Fake.seen[-1]["body"]["model"] == "m1" and "summarize" in _Fake.seen[-1]["body"]["messages"][0]["content"]


def test_codex_models_come_from_codex_own_files(tmp_path):
    from alfrd import chat

    home = tmp_path / "codex"
    home.mkdir()
    assert chat.codex_models(home) == {"models": [], "default": None}
    (home / "config.toml").write_text('model = "gpt-5.6-luna"\n[profiles.fast]\nmodel = "gpt-5-codex"\n', encoding="utf-8")
    (home / "models_cache.json").write_text('{"models": [{"slug": "gpt-5.6-sol"}, {"slug": "gpt-5.6-luna"}, "bad name "]}', encoding="utf-8")
    assert chat.codex_models(home) == {"models": ["gpt-5.6-luna", "gpt-5-codex", "gpt-5.6-sol"], "default": "gpt-5.6-luna"}
    (home / "config.toml").write_text("not = [toml", encoding="utf-8")
    assert chat.codex_models(home)["models"] == ["gpt-5.6-sol", "gpt-5.6-luna"]


def test_a_new_codex_agent_with_its_own_model_validates():
    from alfrd import chat

    agent = {**chat.PRESETS["codex"]["agent"], "model": "gpt-5.6-luna", "roles": ["research"]}
    out = chat.merge_settings({"agents": {"luna": agent}})
    assert out["agents"]["luna"]["entrypoint"] == "codex" and out["agents"]["luna"]["model"] == "gpt-5.6-luna"
    assert {"codex", "claude", "agy"} <= set(chat.PRESETS)
