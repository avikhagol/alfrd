"""Saved-skill discovery and the effective startup args dry run (contract C27)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from alfrd.agent_io import adapter_for, agent_command, startup_options

SKILL_ROOTS = (".alfrd/skills", ".claude/skills", "skills")
SKILL_FILE = "SKILL.md"
MAX_SKILL_BYTES = 64 * 1024

_FRONT = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)

NATIVE_NOTES = {
    "claude": {
        "skills": "skills: false adds --disable-slash-commands (disables all native skills and slash commands).",
        "plugins": "plugins: false adds --setting-sources project,local (skips user settings, plugins and hooks).",
        "mcp": "mcp: none/list adds --strict-mcp-config (and --mcp-config for a list).",
        "saved_skills": "Saved skills are injected into the prompt; native flags do not select them.",
    },
    "codex": {
        "skills": "Codex: no effect; ALFRD passes no skill, plugin or MCP flags to Codex.",
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
    project = Path(project).resolve()
    found = {}
    for rel in roots:
        root = project / rel
        if root.is_symlink() or not root.is_dir():
            continue
        real_root = root.resolve()
        if not _inside(real_root, project):
            continue
        for directory in sorted(root.iterdir()):
            file = directory / SKILL_FILE
            if directory.is_symlink() or file.is_symlink() or not directory.is_dir() or not file.is_file():
                continue
            real = file.resolve()
            if not _inside(real, real_root) or real.name != SKILL_FILE:
                continue
            try:
                if real.stat().st_size > MAX_SKILL_BYTES:
                    continue
                data = real.read_bytes()
            except OSError:
                continue
            if len(data) > MAX_SKILL_BYTES:
                continue
            meta = parse_frontmatter(data.decode("utf-8", errors="replace"))
            skill_id = directory.name
            if skill_id in found:
                continue
            found[skill_id] = {
                "id": skill_id,
                "name": meta.get("name") or skill_id,
                "description": meta.get("description", ""),
                "source": file.relative_to(project).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
    return list(found.values())


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
