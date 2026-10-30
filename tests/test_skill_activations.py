"""Global library and per-project, per-scope activation contracts."""
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from alfrd import chat, skills
from alfrd.cli import alfrd_cli
from alfrd.runtime import scheduler
from test_chat_orchestration import calls, configure, fake, plan, project, result, run, say, task  # noqa: F401
from test_chat_schedule import add, queued, sweep  # noqa: F401
from test_skills import skills_web, write_skill  # noqa: F401


def activation(id, source="project", scope="orchestrator"):
    return {"id": id, "source": source, "scope": scope}


def save(root, *items):
    return skills.save_activations(root, {"version": 1, "activations": list(items)},
                                   base_hash=skills.activation_state(root)["hash"])


def global_skill(id="shared", text="GLOBAL-GUIDANCE"):
    root = Path(os.environ["ALFRD_SKILLS_DIR"])
    return write_skill(root.parent, root.name, id, f"---\nname: {id}\ndescription: guidance\n---\n{text}")


def test_root_resolution_and_read_only_discovery(tmp_path, monkeypatch):
    root = Path(os.environ["ALFRD_SKILLS_DIR"])
    assert skills.global_skills_root() == root
    assert skills.library(tmp_path) == [] and not root.exists()
    assert skills.global_skills_root(tmp_path / "explicit") == tmp_path / "explicit"
    with pytest.raises(ValueError, match="absolute"):
        skills.global_skills_root("relative")
    monkeypatch.delenv("ALFRD_SKILLS_DIR")
    # Resolve the default without reading or writing it.
    assert skills.global_skills_root() == Path.home() / ".alfrd/skills"


def test_both_sources_clash_and_scope_precedence(tmp_path):
    global_skill()
    write_skill(tmp_path, ".alfrd/skills", "shared", "PROJECT-GUIDANCE")
    write_skill(tmp_path, "skills", "shared", "SHADOWED-PROJECT")
    found = skills.library(tmp_path)
    assert [(s["origin"], s["id"]) for s in found] == [("project", "shared"), ("global", "shared")]
    save(tmp_path, activation("shared"), activation("shared", "global"),
         activation("shared", "global", "role:custom role"))
    snapshot = skills.snapshot_selection(tmp_path)
    assert skills.scoped_snapshot(snapshot, "orchestrator")["skills"][0]["content"] == "PROJECT-GUIDANCE"
    assert len(skills.scoped_snapshot(snapshot, "orchestrator")["skills"]) == 1
    role = skills.launch_snapshot(snapshot, {"kind": "task", "task": {"role": "custom role"}})
    assert [s["origin"] for s in role["skills"]] == ["global"]
    assert skills.launch_snapshot(snapshot, {"kind": "task", "task": {}})["skills"] == []
    assert skills.scoped_snapshot(snapshot, "role:verification")["skills"] == []
    assert skills.preview(tmp_path, "shared")["origin"] == "project"
    assert skills.preview(tmp_path, "shared", "global")["origin"] == "global"
    inventory = skills.skill_inventory(tmp_path)
    assert inventory[0]["active_scopes"] == ["orchestrator"]
    legacy_inventory = skills.skill_inventory(tmp_path, {"enabled": True, "selected_ids": ["shared"]})
    assert all(s["selected_by_default"] for s in legacy_inventory)
    assert all(Path(s["path"]).is_absolute() for s in inventory)


def test_activation_persistence_legacy_conflict_and_missing(tmp_path):
    manifest = write_skill(tmp_path, "skills", "one", "ONE")
    legacy = {"enabled": True, "selected_ids": ["one"]}
    initial = skills.activation_state(tmp_path, legacy, roles=["custom role"])
    assert initial["legacy"] and initial["hash"] == "missing"
    assert activation("one", scope="role:custom role") in initial["activations"]
    assert skills.launch_snapshot(skills.snapshot_selection(tmp_path, legacy), {"kind": "task"})["skills"]
    saved = save(tmp_path, activation("one"), activation("one"))
    assert not saved["legacy"] and saved["activations"] == [activation("one")]
    with pytest.raises(PermissionError, match="changed"):
        skills.save_activations(tmp_path, {"version": 1, "activations": []}, base_hash="missing")
    manifest.unlink()
    assert skills.activation_state(tmp_path)["unavailable"] == [activation("one")]
    with pytest.raises(ValueError, match="unavailable"):
        skills.snapshot_selection(tmp_path)
    save(tmp_path)
    assert skills.snapshot_selection(tmp_path, legacy)["skills"] == []
    with pytest.raises(ValueError, match="unavailable"):
        save(tmp_path, activation("missing"))
    (tmp_path / ".alfrd/skills.json").write_text('{"version": 2, "activations": []}')
    with pytest.raises(ValueError, match="version"):
        skills.snapshot_selection(tmp_path, legacy)


@pytest.mark.parametrize("item", [activation("../bad"), activation("x", "other"), activation("x", scope="role:"), activation("x", scope="role: "), activation("x", scope="development")])
def test_reject_malformed_activation(item):
    with pytest.raises(ValueError):
        skills.validate_activations({"version": 1, "activations": [item]})


def test_global_install_cli_promotion_and_collision(tmp_path):
    manifest = write_skill(tmp_path, "skills", "review", "---\nname: Review\ndescription: check diffs\n---\nbody")
    ref = manifest.parent / "references/guide.md"
    ref.parent.mkdir()
    ref.write_text("reference")
    script = manifest.parent / "run.sh"
    script.write_text("echo review\n")
    script.chmod(0o755)
    result = CliRunner().invoke(alfrd_cli, ["skills", "add", "--global", str(manifest)])
    assert result.exit_code == 0, result.output
    item = json.loads(result.output)
    target = Path(item["path"])
    assert item["origin"] == "global" and target.read_bytes() == manifest.read_bytes()
    assert (target.parent / "references/guide.md").read_text() == "reference"
    assert (target.parent / "run.sh").stat().st_mode & 0o111
    assert manifest.exists() and not (tmp_path / ".alfrd/skills.json").exists()
    legacy_inventory = skills.skill_inventory(tmp_path, {"enabled": True, "selected_ids": ["review"]})
    assert [s["origin"] for s in legacy_inventory if s["selected_by_default"]] == ["project"]
    with pytest.raises(ValueError, match="already exists"):
        skills.add_global_skill(manifest)
    assert skills.add_global_skill(manifest.parent, id="review-two")["id"] == "review-two"


def test_global_install_validation_and_safe_discovery(tmp_path):
    root = Path(os.environ["ALFRD_SKILLS_DIR"])
    manifest = write_skill(tmp_path, "staged", "one", "missing metadata")
    with pytest.raises(ValueError, match="name and description"):
        skills.add_global_skill(manifest)
    manifest.write_text("---\nname: One\ndescription: safe\n---\nbody")
    (manifest.parent / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlinks"):
        skills.add_global_skill(manifest)
    (manifest.parent / "escape").unlink()
    with pytest.raises(ValueError, match="invalid skill id"):
        skills.add_global_skill(manifest, id="../escape")
    (manifest.parent / "large").write_bytes(b"x" * (10 * 1024 * 1024))
    with pytest.raises(ValueError, match="exceeds"):
        skills.add_global_skill(manifest)
    assert not root.exists()
    global_skill()
    (root / "link").symlink_to(manifest.parent, target_is_directory=True)
    assert [s["id"] for s in skills.library(tmp_path) if s["origin"] == "global"] == ["shared"]


def test_activation_api_inventory_preview_and_conflict(skills_web):
    client, base, root = skills_web
    global_skill()
    write_skill(root, "skills", "shared", "PROJECT-GUIDANCE")
    configure(root, agents={"codex": {"roles": ["custom role"]}})
    endpoint = base + "/skills/activations"
    headers = {"X-CSRF-Token": client.get("/api/studio/session").get_json()["csrf_token"]}
    initial = client.get(endpoint).get_json()
    assert initial["legacy"] and "custom role" in initial["roles"]
    inventory = client.get(base + "/skills/inventory").get_json()["skills"]
    assert {s["origin"] for s in inventory} == {"global", "project"}
    assert client.get(base + "/skills/preview?id=shared&origin=global").get_json()["content"].endswith("GLOBAL-GUIDANCE")
    payload = {"version": 1, "activations": [activation("shared", "global", "role:custom role")], "base_hash": initial["hash"]}
    response = client.put(endpoint, json=payload, headers=headers)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["activations"] == payload["activations"]
    assert client.get(endpoint).get_json()["hash"] == response.get_json()["hash"]
    assert client.put(endpoint, json=payload, headers=headers).status_code == 409
    assert client.put(endpoint, json={"version": 1, "activations": []}, headers=headers).status_code == 400
    payload.update(base_hash=response.get_json()["hash"], activations=[activation("missing")])
    assert client.put(endpoint, json=payload, headers=headers).status_code == 400


@pytest.mark.parametrize("kind", chat.ORCHESTRATOR_KINDS)
def test_all_orchestrator_kinds_use_only_orchestrator_scope(tmp_path, kind):
    global_skill()
    write_skill(tmp_path, "skills", "role", "ROLE-ONLY")
    save(tmp_path, activation("shared", "global"), activation("role", scope="role:development"))
    snapshot = skills.snapshot_selection(tmp_path)
    section = skills.snapshot_prompt(skills.launch_snapshot(snapshot, {"kind": kind}))
    assert "Before working, read every listed SKILL.md at its absolute path" in section
    assert str(Path(os.environ["ALFRD_SKILLS_DIR"]) / "shared/SKILL.md") in section
    assert "GLOBAL-GUIDANCE" in section and "ROLE-ONLY" not in section


def test_scoped_prompts_frozen_on_dispatch_and_native_delivery(project):
    global_skill()
    write_skill(project, "skills", "role", "ROLE-ONLY")
    write_skill(project, "skills", "inactive", "INACTIVE-GUIDANCE")
    save(project, activation("shared", "global"), activation("role", scope="role:development"))
    configure(project, final_summary=True)
    fake(project, plan={"text": "invalid"}, repair=plan(task("a", role="development"), task("b", role="verification"), task("c")),
         a=result(), b=result(), c=result(), summary={"text": "done"})
    sid = chat.create_session(project)["id"]
    posted = say(project, sid, "work")
    frozen = posted["request"]["skill_snapshot"]
    save(project)  # current request keeps its snapshot
    global_skill(text="EDITED-GUIDANCE")
    run(project, sid)
    launched = calls(project)
    assert [c["kind"] for c in launched] == ["plan", "repair", "task", "task", "task", "summary"]
    for c in launched:
        section = c["prompt"].split("## Selected saved skills (request snapshot)")[-1]
        assert "INACTIVE-GUIDANCE" not in c["prompt"] and "EDITED-GUIDANCE" not in c["prompt"]
        if c["kind"] in chat.ORCHESTRATOR_KINDS:
            assert "GLOBAL-GUIDANCE" in section and "ROLE-ONLY" not in section
        elif '"id": "a"' in c["prompt"]:
            assert "ROLE-ONLY" in section and "GLOBAL-GUIDANCE" not in section
        else:
            assert "## Selected saved skills" not in c["prompt"]
    assert chat.session(project, sid).request("r0001")["skill_snapshot"] == frozen
    units = scheduler.Runner(project, f"chat-{sid}").folder.units()
    a = next(u for u in units if u.get("row") == "r0001.a")
    assert [s["id"] for s in a["skill_delivery"]] == ["role"]
    assert (project / ".agents/skills/alfrd-project-role").is_symlink()
    assert not (project / ".agents/skills/alfrd-project-inactive").exists()


def test_scheduled_dispatch_freezes_current_activation_state(queued):
    root, sid, _ = queued
    global_skill(text="AT-QUEUE")
    add(queued, "build something")
    save(root, activation("shared", "global"))
    global_skill(text="AT-DISPATCH")
    sweep(queued)
    store = chat.session(root, sid)
    [request] = store.requests()
    snapshot = request["skill_snapshot"]
    assert snapshot["activations"] == [activation("shared", "global")]
    assert snapshot["skills"][0]["content"].endswith("AT-DISPATCH")
    save(root)
    global_skill(text="AFTER-DISPATCH")
    sweep(queued)
    assert store.request(request["id"])["skill_snapshot"] == snapshot


def test_explicit_role_snapshot_survives_retries(project):
    manifest = write_skill(project, "skills", "role", "ORIGINAL-ROLE-GUIDANCE")
    configure(project, agents={"codex": {"roles": ["custom role"]}})
    save(project, activation("role", scope="role:custom role"))
    fake(project, t1={"exit": 2})
    sid = chat.create_session(project)["id"]
    say(project, sid, "@codex work")
    manifest.write_text("CHANGED-GUIDANCE")
    save(project)
    run(project, sid)
    assert len(calls(project)) == 2
    fake(project, t1=result())
    chat.retry_task(project, sid, "r0001.t1", spawn=False)
    run(project, sid)
    assert len(calls(project)) == 3
    for call in calls(project):
        assert call["prompt"].count("ORIGINAL-ROLE-GUIDANCE") == 1
        assert call["prompt"].count("## Selected saved skills") == 1
        assert "CHANGED-GUIDANCE" not in call["prompt"]


def test_native_verifies_global_path_and_filters_scope(tmp_path, monkeypatch):
    global_skill()
    write_skill(tmp_path, "skills", "inactive", "INACTIVE")
    save(tmp_path, activation("shared", "global"))
    snapshot = skills.scoped_snapshot(skills.snapshot_selection(tmp_path), "orchestrator")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert skills.expose_codex_skills(tmp_path, workspace, snapshot)[0]["delivery"] == "native"
    # Identical content at a different library root must not substitute for the frozen path.
    monkeypatch.setenv("ALFRD_SKILLS_DIR", str(tmp_path / "other-global"))
    global_skill()
    delivery = skills.expose_codex_skills(tmp_path, workspace, snapshot)
    assert delivery[0]["delivery"] == "injected" and "source changed" in delivery[0]["reason"]
    assert skills.expose_codex_skills(tmp_path, workspace, skills.scoped_snapshot(snapshot, "role:development")) == []
