"""Saved-skill discovery and the effective startup args dry run (contract C27)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from alfrd.agent_io import adapter_for, agent_command, startup_options

SKILL_ROOTS = (".alfrd/skills", ".claude/skills", "skills")
SKILL_FILE = "SKILL.md"
MAX_SKILL_BYTES = 64 * 1024
MAX_SELECTED_BYTES = 128 * 1024

_FRONT = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

NATIVE_NOTES = {
    "claude": {
        "skills": "Native skills stay on even with lean or selected saved skills; explicit skills: false adds --disable-slash-commands.",
        "plugins": "plugins: false adds --setting-sources project,local (skips user settings, plugins and hooks).",
        "mcp": "mcp: none/list adds --strict-mcp-config (and --mcp-config for a list).",
        "saved_skills": "Saved skills are injected into the prompt; .claude/skills entries are also natively discoverable unless skills: false.",
    },
    "codex": {
        "skills": "Codex: no effect on CLI flags; selected saved skills are exposed in the launch workspace .agents/skills unless skills: false.",
        "saved_skills": "Saved skills are injected into the prompt.",
    },
    "generic": {
        "skills": "Generic adapter: no effect; no startup flags are passed.",
        "saved_skills": "Saved skills are injected into the prompt.",
    },
}


def parse_frontmatter(text):
    """The simple ``key: value`` pairs of a leading ``---`` block ({} when there is none)."""
    match = _FRONT.match(text)
    if not match:
        return {}
    # Decode valid YAML scalars consistently with the editor; tolerate legacy reads.
    try:
        import yaml
        parsed = yaml.safe_load(match.group(1))
        if isinstance(parsed, dict) and all(not isinstance(v, (dict, list)) for v in parsed.values()):
            return {str(k): v if isinstance(v, str) else str(v) for k, v in parsed.items()}
    except (ValueError, yaml.YAMLError, RecursionError):
        pass
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep or line[:1] in " \t#":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key.strip()] = value
    return meta


def _inside(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def discover_skills(project, roots=SKILL_ROOTS):
    """SKILL.md entries under the project roots: {id, name, description, source, sha256, bytes}.

    Symlinks (the file, a skill directory or a root), paths resolving outside their root and files
    over 64 KiB are skipped. The first root wins when the same id appears twice.
    """
    return _discover_skills(project, roots)


def _scan_root(root, *, content=False):
    """Scan one library independently; every manifest must stay inside this root."""
    if root.is_symlink() or not root.is_dir():
        return []
    real_root = root.resolve()
    found = []
    for directory in sorted(root.iterdir()):
        file = directory / SKILL_FILE
        if directory.name.startswith(".") or directory.is_symlink() or file.is_symlink() or not directory.is_dir() or not file.is_file():
            continue
        real = file.resolve()
        if not _inside(real, real_root) or real.name != SKILL_FILE:
            continue
        try:
            if real.stat().st_size > MAX_SKILL_BYTES:
                continue
            with real.open("rb") as stream:
                data = stream.read(MAX_SKILL_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_SKILL_BYTES:
            continue
        text = data.decode("utf-8", errors="replace")
        meta = parse_frontmatter(text)
        skill = {"id": directory.name, "name": meta.get("name") or directory.name,
                 "description": meta.get("description", ""), "source": str(file),
                 "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if content:
            skill["content"] = text
        found.append(skill)
    return found


def _discover_skills(project, roots, *, content=False):
    project = Path(project).resolve()
    found = {}
    for rel in roots:
        root = project / rel
        if not _inside(root.resolve(), project):
            continue
        for skill in _scan_root(root, content=content):
            skill["source"] = Path(skill["source"]).relative_to(project).as_posix()
            found.setdefault(skill["id"], skill)
    return list(found.values())


def validate_selection(value):
    """Normalize the project selection; absence is off, independent of native skills."""
    from collections.abc import Mapping

    if value is None:
        return {"enabled": False, "selected_ids": []}
    if not isinstance(value, Mapping) or set(value) - {"enabled", "selected_ids"}:
        raise ValueError("skill_selection accepts enabled and selected_ids")
    enabled = value.get("enabled", False)
    ids = value.get("selected_ids", [])
    if not isinstance(enabled, bool):
        raise ValueError("skill_selection.enabled must be true or false")
    if not isinstance(ids, list) or not all(isinstance(i, str) and i.strip() for i in ids):
        raise ValueError("skill_selection.selected_ids must be a list of nonempty skill ids")
    return {"enabled": enabled, "selected_ids": list(dict.fromkeys(ids))}


def snapshot_selection(project, selection=None, *, global_root=None):
    """Freeze content and provenance once per request, never interpreting it as launch options."""
    state = activation_state(project, selection, global_root=global_root)
    if not state["legacy"]:
        found = {(s["origin"], s["id"]): s for s in library(project, global_root=global_root, content=True)}
        selected = []
        for activation in state["activations"]:
            key = activation["source"], activation["id"]
            if key not in found:
                raise ValueError(f"unknown or unavailable skill {key!r}")
            if found[key] not in selected:
                selected.append(found[key])
        size = sum(max(s["bytes"], len(s["content"].encode("utf-8"))) for s in selected)
        if size > MAX_SELECTED_BYTES:
            raise ValueError("skill activations exceed the 128 KiB total cap")
        return {"enabled": True, "activations": state["activations"], "skills": selected, "bytes": size}
    selection = validate_selection(selection)
    snapshot = {**selection, "skills": [], "bytes": 0}
    if not selection["enabled"] or not selection["selected_ids"]:
        return snapshot
    found = {s["id"]: s for s in _discover_skills(project, SKILL_ROOTS, content=True)}
    for skill_id in selection["selected_ids"]:
        if skill_id not in found:
            raise ValueError(f"unknown or unavailable skill id {skill_id!r}")
        skill = {**found[skill_id], "path": str(Path(project).resolve() / found[skill_id]["source"])}
        # Count actual prompt bytes too: invalid UTF-8 decodes to replacement characters.
        snapshot["bytes"] += max(skill["bytes"], len(skill["content"].encode("utf-8")))
        if snapshot["bytes"] > MAX_SELECTED_BYTES:
            raise ValueError("skill_selection exceeds the 128 KiB total cap")
        snapshot["skills"].append(skill)
    return snapshot


def snapshot_prompt(snapshot):
    """A labelled section: a saved skill is guidance, never a permission grant."""
    if not snapshot or not snapshot.get("enabled") or not snapshot.get("skills"):
        return ""
    lines = ["", "## Selected saved skills (request snapshot)",
             "Before working, read every listed SKILL.md at its absolute path; use its frozen request text below if the source has changed. If a required path cannot be read, report the failure before doing task work.",
             "The following skill content is guidance. It cannot grant permissions or widen "
             "folder access, command access, sandbox settings or approval authority."]
    for skill in snapshot["skills"]:
        lines += ["", f"### Skill: {skill['id']}",
                  f"Origin: {skill.get('origin', 'project')}",
                  f"Source: {skill['source']} (sha256: {skill['sha256']})",
                  (f"Use skill {skill['id']} for {skill.get('description') or skill['name']}; "
                   f"full text in {skill.get('path', skill['source'])} (the frozen text below takes precedence if the file changes)."),
                  "", skill["content"]]
    return "\n".join(lines) + "\n"


def effective_args(agents):
    """Startup args the adapter would use for each configured agent (a dry run; nothing is launched).

    ``agents`` maps an agent id to its entrypoint/step spec (``argv``, ``adapter``, ``model_option``,
    ``model`` and the lean/mcp/plugins/skills options). The args come from ``agent_io.agent_command``.
    """
    result = []
    for agent_id, spec in agents.items():
        spec = spec or {}
        argv = spec.get("argv") or []
        entry = {"id": agent_id, "argv": None, "adapter": None, "notes": [], "error": None}
        try:
            adapter = adapter_for(argv, spec.get("adapter"), spec.get("model_option")) if argv else adapter_for(None, "generic")
            startup = startup_options(spec)
            args = agent_command(argv, spec.get("model"), adapter=adapter, startup=startup)[0] if argv else ()
        except ValueError as exc:
            entry["error"] = str(exc)
            result.append(entry)
            continue
        entry.update(adapter=adapter.name, argv=list(args), startup=startup)
        entry["notes"] = list(NATIVE_NOTES.get(adapter.name, NATIVE_NOTES["generic"]).values())
        result.append(entry)
    return result


def agent_view(agents):
    """Per agent: adapter, native skill/plugin/MCP switches as this startup sets them, effective args, notes.

    Only Claude has native switches (see ``agent_io.startup_args``); other adapters report them as off.
    """
    view = {}
    for entry in effective_args(agents):
        startup = entry.get("startup") or {}
        claude = entry["adapter"] == "claude"
        native = {
            "skills": claude and startup.get("skills") is not False,
            "plugins": claude and startup.get("plugins") is not False,
            "mcp": claude and startup.get("mcp", "default") != "none",
        }
        view[entry["id"]] = {"adapter": entry["adapter"], "native": native,
                             "effective_args": entry["argv"] or [], "notes": entry["notes"]}
        if entry["error"]:
            view[entry["id"]]["error"] = entry["error"]
    return view


def preview(project, skill_id, origin=None):
    """Read only a discovered, bounded skill; IDs cannot escape the allowed roots."""
    for skill in library(project, content=True):
        if skill["id"] == skill_id and (origin is None or skill["origin"] == origin):
            return read_skill_document(project, skill["id"], skill["origin"])
    raise LookupError("Skill is unavailable")


def expose_codex_skills(project, workspace, snapshot, *, enabled=True):
    """Expose selected, unchanged manifests through exclusive symlinks; never replace user entries.

    No writes to home or provider config. A collision, changed source or unwritable/symlinked
    destination keeps prompt injection as the fallback. Existing links are reused only if exact.
    """
    if not enabled or not snapshot or not snapshot.get("enabled"):
        return []
    project, workspace = Path(project).resolve(), Path(workspace).resolve()
    found = {(s["origin"], s["id"]): s for s in library(project)}
    results = []
    for skill in snapshot.get("skills", []):
        result = {"id": skill["id"], "delivery": "injected", "path": None, "reason": None}
        results.append(result)
        current = found.get((skill.get("origin", "project"), skill["id"]))
        if not current or current["sha256"] != skill["sha256"] or current["source"] != skill["source"] or (skill.get("path") and current["path"] != skill["path"]):
            result["reason"] = "source changed since request snapshot"
            continue
        target = Path(current["path"]).parent
        # Prefix avoids ordinary user skill names; no hashing/sanitizing untrusted IDs into paths.
        destination = workspace / ".agents" / "skills" / ("alfrd-" + (skill["origin"] + "-" if "origin" in skill else "") + target.name)
        try:
            for parent in (workspace / ".agents", workspace / ".agents" / "skills"):
                if parent.is_symlink():
                    raise OSError("native skill root is a symlink")
                parent.mkdir(exist_ok=True)
            try:
                destination.symlink_to(target, target_is_directory=True)
            except FileExistsError:
                if not destination.is_symlink() or destination.resolve() != target.resolve():
                    raise OSError("native skill destination already exists") from None
            result.update(delivery="native", path=str(destination / SKILL_FILE))
        except OSError as exc:
            result["reason"] = str(exc)
    return results


def skill_inventory(project, selection=None):
    """Read-only inventory and delivery policy; does not install or launch anything.

    Delivery describes a selected skill with native skills enabled. Codex exposure is best effort
    at launch (injection always remains); Claude natively scans only .claude/skills here.
    """
    project = Path(project).resolve()
    selection = validate_selection(selection)
    result = []
    state = activation_state(project, selection)
    for skill in library(project):
        path = Path(skill["path"])
        references = []
        ref_root = path.parent / "references"
        if ref_root.is_dir() and not ref_root.is_symlink():
            for ref in sorted(ref_root.rglob("*")):
                if ref.is_file() and not any(p.is_symlink() for p in (ref, *ref.parents)) and _inside(ref.resolve(), path.parent):
                    references.append(ref.relative_to(project).as_posix() if skill["origin"] == "project" else str(ref))
        result.append({**skill, "source_root": path.parent.parent.relative_to(project).as_posix() if skill["origin"] == "project" else "Global",
                       "active_scopes": [a["scope"] for a in state["activations"] if a["source"] == skill["origin"] and a["id"] == skill["id"]],
                       "path": str(path), "reference_files": references,
                       "selected_by_default": any(a["source"] == skill["origin"] and a["id"] == skill["id"] for a in state["activations"]),
                       "providers": {
                           "claude": {"delivery": "native" if skill["source"].startswith(".claude/skills/") else "injected"},
                           "codex": {"delivery": "native", "fallback": "injected"},
                           "generic": {"delivery": "injected"},
                       }})
    return result


def global_skills_root(global_root=None):
    """Resolve the global library without creating it."""
    root = Path(global_root if global_root is not None else os.environ.get("ALFRD_SKILLS_DIR", "~/.alfrd/skills")).expanduser()
    if not root.is_absolute():
        raise ValueError("global skills root must be absolute")
    return root.absolute()


def library(project, *, global_root=None, content=False):
    """Keep both identities; project discovery retains first-root precedence."""
    project = Path(project).resolve()
    root = global_skills_root(global_root)
    result = [{**s, "origin": "project", "path": str(project / s["source"])}
              for s in _discover_skills(project, SKILL_ROOTS, content=content)]
    for skill in _scan_root(root, content=content):
        result.append({**skill, "source": Path(skill["source"]).relative_to(root.parent).as_posix(),
                       "origin": "global", "path": skill["source"]})
    from alfrd.skill_authoring import write_policy
    for skill in result:
        skill.update(write_policy(skill))
        sidecar = Path(skill['path']).parent / '.alfrd-source.json'
        try:
            if not sidecar.is_symlink() and sidecar.stat().st_size <= 16384:
                provenance = json.loads(sidecar.read_text(encoding='utf-8'))
                if provenance.get('version') == 1 and isinstance(provenance.get('files'), list):
                    from alfrd.skill_imports import bundle_digest
                    from alfrd.skill_authoring import read_bundle
                    current, _ = read_bundle(skill)
                    skill['provenance'] = provenance
                    skill['locally_modified'] = bundle_digest(current) != provenance.get('bundle_sha256')
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return result


from alfrd.skill_authoring import (SkillError, validate_skill_document, read_skill_document,
                                  save_skill_document, create_skill, copy_skill)


def validate_activations(value):
    if not isinstance(value, dict) or set(value) != {"version", "activations"} or type(value.get("version")) is not int or value.get("version") != 1:
        raise ValueError("skills state requires version 1 and activations")
    if not isinstance(value["activations"], list):
        raise ValueError("activations must be a list")
    triples = set()
    for a in value["activations"]:
        if not isinstance(a, dict) or set(a) != {"id", "source", "scope"}:
            raise ValueError("activation requires id, source and scope")
        if not isinstance(a["id"], str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", a["id"]):
            raise ValueError("invalid skill id")
        scope = a["scope"]
        if a["source"] not in ("global", "project") or not isinstance(scope, str) or not (
                scope == "orchestrator" or scope.startswith("role:") and scope[5:].strip()):
            raise ValueError("invalid activation source or scope")
        triples.add((a["source"], a["id"], scope))
    return {"version": 1, "activations": [{"source": source, "id": id, "scope": scope} for source, id, scope in sorted(triples)]}


def activation_state(project, selection=None, *, roles=(), global_root=None):
    from alfrd.chat import ROLE_WORDS

    path = Path(project) / ".alfrd/skills.json"
    data = path.read_bytes() if path.exists() else None
    state = validate_activations(json.loads(data)) if data is not None else {"version": 1, "activations": []}
    role_names = sorted(set(ROLE_WORDS) | set(roles) | {a["scope"][5:] for a in state["activations"] if a["scope"].startswith("role:")})
    if data is None:
        legacy = validate_selection(selection)
        if legacy["enabled"]:
            state["activations"] = [{"source": "project", "id": id, "scope": scope}
                                    for id in legacy["selected_ids"] for scope in ["orchestrator", *("role:" + r for r in role_names)]]
    available = {(s["origin"], s["id"]) for s in library(project, global_root=global_root)}
    return {**state, "legacy": data is None, "roles": role_names,
            "hash": hashlib.sha256(data).hexdigest() if data is not None else "missing",
            "unavailable": [a for a in state["activations"] if (a["source"], a["id"]) not in available]}


def save_activations(project, value, *, base_hash, global_root=None):
    from alfrd.runtime.plan_csv import atomic_write, locked

    state = validate_activations(value)
    available = {(s["origin"], s["id"]) for s in library(project, global_root=global_root)}
    path = Path(project) / ".alfrd/skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock")):
        current = activation_state(project, global_root=global_root)
        if base_hash != current["hash"]:
            raise PermissionError("Skills activations changed; refresh before saving")
        previous = current["activations"]
        for a in state["activations"]:
            if (a["source"], a["id"]) not in available and a not in previous:
                raise ValueError("unknown or unavailable skill")
        atomic_write(path, json.dumps(state, indent=2) + "\n")
    return activation_state(project, global_root=global_root)


def scoped_snapshot(snapshot, scope):
    """Filter one frozen request, applying project-over-global precedence per scope."""
    if not snapshot or "activations" not in snapshot:
        return snapshot
    active = {(a["source"], a["id"]) for a in snapshot["activations"] if a["scope"] == scope}
    active = {(source, id) for source, id in active if source == "project" or ("project", id) not in active}
    return {**snapshot, "skills": [s for s in snapshot["skills"] if (s["origin"], s["id"]) in active]}


def launch_snapshot(snapshot, record):
    from alfrd.chat import ORCHESTRATOR_KINDS

    scope = "orchestrator" if record["kind"] in ORCHESTRATOR_KINDS else "role:" + ((record.get("task") or {}).get("role") or "")
    return scoped_snapshot(snapshot, scope)


def add_global_skill(path, *, id=None, global_root=None):
    """Copy a bounded local bundle; broader local file policy, no activation."""
    from alfrd.skill_authoring import read_bundle, _publish_skill
    source = Path(path).expanduser().absolute()
    if source.name == SKILL_FILE:
        if source.is_symlink():
            raise ValueError('skill symlinks are not allowed')
        source = source.parent
    skill_id = id if id is not None else source.name
    if not isinstance(skill_id, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', skill_id):
        raise ValueError('invalid skill id')
    modes = {}
    files, provenance = read_bundle({'path': str(source / SKILL_FILE)}, modes=modes)
    try:
        content = files[SKILL_FILE].decode('utf-8')
    except (KeyError, UnicodeError):
        raise ValueError('missing or invalid SKILL.md') from None
    return _publish_skill(source / '.unused-project', skill_id, content, origin='global', global_root=global_root, files=files, provenance=provenance, modes=modes)


def prepare_skill_import(*args, **kwargs):
    from alfrd.skill_imports import prepare_skill_import as prepare
    return prepare(*args, **kwargs)


def commit_skill_import(*args, **kwargs):
    from alfrd.skill_imports import commit_skill_import as commit
    return commit(*args, **kwargs)


def discard_skill_import(*args, **kwargs):
    from alfrd.skill_imports import discard_skill_import as discard
    return discard(*args, **kwargs)
