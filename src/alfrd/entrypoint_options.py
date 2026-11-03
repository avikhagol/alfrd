"""Agent commands (manifest ``entrypoint:`` entries) a new project can start with: the Studio wizard's checkboxes.

``options_for`` lists the catalogue with what is installed here (``GET /studio/entrypoint-options``);
``apply_to_manifest`` writes the ticked ones into template data as complete, folder-independent entries plus
their chat agents; ``validate_custom`` checks a user's own command against the project folder; and
``entrypoint_warnings`` reports commands that will not start here (missing programs, other projects' scripts).
Errors and warnings read ``"<field>: <what is wrong>. <fix>"``.
"""
from __future__ import annotations

import copy
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any, Mapping

# The loop hands each turn its prompt and takes its response through these files (alfrd.agent_loop).
_LOOP_IO = {"stdin_file": "{prompt_file}", "output_file": "{response_file}"}
_CODEX = ["--skip-git-repo-check", "--sandbox", "workspace-write", "--output-last-message", "{response_file}", "-"]
# A relative script is written under this placeholder (rendered to the project folder at run time), so a
# turn that runs in a task worktree still finds it and no absolute path is stored.
PROJECT_DIR = "{project_dir}"
CUSTOM_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
MAX_CUSTOM = 8

# id → wizard row and the entry written. ``needs``: programs on PATH plus "ollama" / "HF_TOKEN";
# ``always``: shown even when missing (users often install after creating); ``chat_only``: no manifest
# entry (a built-in chat entrypoint that cannot edit files, so it is not a loop agent).
OPTIONS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code", "help": "Runs the claude CLI headless; edits files in this project.",
        "needs": ["claude"], "always": True, "group": "common",
        "entry": {"cmd": ["claude", "-p", "--permission-mode", "acceptEdits", "--output-format", "text"],
                  "lean": True, **_LOOP_IO, "output_capture": "stdout"},
        "agent": {"label": "Claude (Opus)", "model": "opus", "roles": ["orchestration"], "lean": True},
    },
    "codex": {
        "label": "Codex", "help": "Runs codex in a workspace-write sandbox and captures its last message.",
        "needs": ["codex"], "always": True, "group": "common",
        "entry": {"cmd": ["codex", "exec", "--ephemeral", *_CODEX], **_LOOP_IO, "output_capture": "file"},
        "agent": {"label": "GPT (Codex)", "roles": ["design", "development"]},
    },
    "agy": {
        "label": "Antigravity (agy)", "help": "Runs the agy CLI through the generic adapter.",
        "needs": ["agy"], "group": "common",
        "entry": {"cmd": ["agy"], "adapter": "generic", **_LOOP_IO, "output_capture": "stdout"},
        "agent": {"label": "Antigravity", "adapter": "generic", "roles": ["research", "image generation"]},
    },
    "codex-oss": {
        "label": "Codex on local Ollama", "help": "Codex using a model pulled in your local Ollama; no cloud.",
        "needs": ["codex", "ollama"], "group": "advanced",
        "entry": {"cmd": ["codex", "exec", "--oss", *_CODEX], **_LOOP_IO, "output_capture": "file"},
        "agent": {"label": "Codex · Ollama (local)", "model": "gpt-oss:20b", "roles": ["development", "design"]},
    },
    "codex-hf": {
        "label": "Codex via Hugging Face",
        "help": "Codex through the Hugging Face router; reads HF_TOKEN from your environment (never asked here).",
        "needs": ["codex", "HF_TOKEN"], "group": "advanced",
        "entry": {"cmd": ["codex", "exec", "-c", 'model_provider="hf"', "-c",
                          ('model_providers.hf={name="Hugging Face",base_url="https://router.huggingface.co/v1",'
                           'env_key="HF_TOKEN",wire_api="chat"}'), *_CODEX], **_LOOP_IO, "output_capture": "file"},
        "agent": {"label": "Codex · Hugging Face", "model": "openai/gpt-oss-120b", "roles": ["development", "research"]},
    },
    "llm-chat": {
        "label": "Text-only LLM chat",
        "help": "Answers only, no tools or file edits; needs an OpenAI-compatible endpoint in your environment.",
        "needs": [], "always": True, "group": "advanced", "chat_only": True,
        "agent": {"label": "Local LLM (text only)", "model": "qwen3:8b", "adapter": "generic",
                  "env": {"ALFRD_LLM_BASE_URL": "http://localhost:11434/v1"},
                  "roles": ["orchestration", "research", "writing"]},
    },
}
CUSTOM = {"id": "custom", "label": "Custom command",
          "help": "Your own program or script; its path is checked in the project folder.", "group": "custom"}
# ticked by default per template (agent-loop: the template's own pair)
DEFAULTS = {"agent-loop": ["claude", "codex"]}


def _needs(needs, env: Mapping[str, str], ollama) -> list[str]:
    missing = []
    for need in needs:
        if need == "HF_TOKEN":
            if not (env.get("HF_TOKEN") or env.get("HUGGING_FACE_HUB_TOKEN")):
                missing.append("HF_TOKEN is not set in the environment ALFRD runs in")
        elif need == "ollama":
            if not ollama().get("reachable"):
                missing.append("Ollama does not answer (start it with: ollama serve)")
        elif not shutil.which(need, path=env.get("PATH")):
            missing.append(f"{need} is not on PATH")
    return missing


def options_for(template: str, env: Mapping[str, str] | None = None, *, ollama=None) -> list[dict[str, Any]]:
    """The wizard's rows for ``template``: ``{id, label, help, group, available, status, default, shown,
    hidden_reason, loop}``. Options with a missing prerequisite are hidden (``shown`` false, the reason kept),
    except the ``always`` ones, which show "not installed". Nothing is run; Ollama gets one local GET,
    only when codex is installed."""
    env = os.environ if env is None else env
    if ollama is None:
        from alfrd import chat

        def ollama():
            return chat._ollama_probe(chat.ollama_url())
    probed: list[dict[str, Any]] = []

    def once():
        if not probed:
            probed.append(ollama())
        return probed[0]

    defaults = DEFAULTS.get(template, [])
    rows = []
    for key, spec in OPTIONS.items():
        program = spec["entry"]["cmd"][0] if "entry" in spec else None
        # Ollama is probed only when codex is here: otherwise codex-oss is hidden for "codex is not on PATH".
        needs = [n for n in spec["needs"] if n != "ollama" or shutil.which("codex", path=env.get("PATH"))]
        missing = _needs(needs, env, once)
        found = shutil.which(program, path=env.get("PATH")) if program else None
        if missing:
            status = "not installed — you can still add it" if spec.get("always") else "; ".join(missing)
        elif spec.get("chat_only"):
            status = "built in (text only; chat agents, not loop turns)"
        else:
            status = f"found at {found}" if found else "ready"
        rows.append({"id": key, "label": spec["label"], "help": spec["help"], "group": spec["group"],
                     "available": not missing, "status": status, "default": key in defaults,
                     "shown": not missing or bool(spec.get("always")),
                     "hidden_reason": None if not missing or spec.get("always") else "; ".join(missing),
                     "loop": not spec.get("chat_only"),
                     "cmd": list(spec["entry"]["cmd"]) if "entry" in spec else None})
    rows.append({**CUSTOM, "available": True, "status": "", "default": False, "shown": True,
                 "hidden_reason": None, "loop": True, "cmd": None})
    return rows


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# Interpreters whose first operand is a script file (``python3 script.py``), with their options that take a
# value (skipped) and those after which no script follows (``-m module``, ``-c code``).
_INTERPRETER = re.compile(r"^(python[0-9.]*|pypy[0-9.]*|sh|bash|dash|zsh|node|perl|ruby|Rscript)$")
_SHELLS = {"sh", "bash", "dash", "zsh"}  # their -e is "exit on error", not inline code
_VALUE_OPTS = {"-W", "-X", "-r", "--require"}
_NO_SCRIPT = {"-m", "-c", "-e", "--eval", "-p", "--print", "-"}


def _script_index(cmd: list[str]) -> int | None:
    """Where the script operand of an interpreter command is (``python3 -u x.py`` → 2), or None."""
    base = os.path.basename(cmd[0])
    if not _INTERPRETER.match(base):
        return None
    i = 1
    while i < len(cmd):
        arg = cmd[i]
        if arg in _NO_SCRIPT and not (arg == "-e" and base in _SHELLS):
            return None
        if not arg.startswith("-"):
            return i
        i += 2 if arg in _VALUE_OPTS else 1
    return None


def _project_path(name: str, raw: str, root: Path, *, program: bool) -> str:
    """A path in a custom command (the program, or an interpreter's script): ``{project_dir}/…`` when it is
    in the project, else the expanded absolute path. Raises ValueError when it would not start."""
    what = "" if program else "script "
    expanded = os.path.expanduser(raw)
    # checked resolved; stored as written (absolute) when outside: a venv's bin/python is a symlink to keep
    lexical = os.path.abspath(expanded if os.path.isabs(expanded) else root / expanded)
    path = Path(lexical).resolve()
    if not os.path.isabs(expanded) and not _inside(path, root):
        raise ValueError(f"custom command {name}: {what}{raw} leaves the project folder. "
                         "Copy the script into this project and use a path inside it, such as .alfrd/bin/agent.py.")
    if not _inside(path, root) and "/.alfrd/bin/" in path.as_posix() + "/":
        raise ValueError(f"custom command {name}: {raw} points into another project's .alfrd/bin. "
                         f"Copy the script into this project and use a relative path such as .alfrd/bin/{path.name}.")
    if not path.is_file():
        where = f" in {root}" if _inside(path, root) else ""
        raise ValueError(f"custom command {name}: {what}{raw} does not exist{where}. "
                         + ("Copy the script there first." if _inside(path, root) else
                            f"Install the program, or copy the script into this project and use a relative path "
                            f"such as .alfrd/bin/{path.name}."))
    if program and not os.access(path, os.X_OK):
        raise ValueError(f"custom command {name}: {raw} is not executable. Run chmod +x on it, "
                         "or put its interpreter first (e.g. python3 script.py).")
    return f"{PROJECT_DIR}/{path.relative_to(root).as_posix()}" if _inside(path, root) else lexical


def validate_custom(root: str | Path, spec: Any, taken=()) -> tuple[dict[str, Any], list[str]]:
    """Check one ``{name, cmd, model_option?}`` custom command for the project at ``root`` (which may not
    exist yet): the manifest entry to write and warnings. Raises ValueError ``"custom command: …"``.

    The program (``cmd[0]``): a bare name should be on PATH (warning); a path must exist and be executable,
    a relative one must stay inside the project, and none may be a script in another ALFRD project's
    ``.alfrd/bin``. A path in the project is written as ``{project_dir}/…`` (turns run in task worktrees);
    one outside is written expanded (``~`` resolved). The script of an interpreter (``python3 x.py``,
    ``bash -e x.sh``) is checked the same way except for the executable bit. ``cmd`` is a list or one line
    split like a POSIX shell does (quotes honoured, nothing run)."""
    root = Path(root).expanduser().resolve()
    if not isinstance(spec, Mapping) or set(spec) - {"name", "cmd", "model_option"}:
        raise ValueError("custom command: give name, cmd and optionally model_option. Fill in the Custom command fields.")
    name = spec.get("name")
    if not isinstance(name, str) or not CUSTOM_NAME.match(name.strip()):
        raise ValueError(f"custom command name: {name!r} is not a valid name. Use 1–40 lowercase letters, digits, "
                         "_ or -, starting with a letter (for example my-agent).")
    name = name.strip()
    if name == "custom" or name in OPTIONS or name in taken:
        raise ValueError(f"custom command name: {name!r} is already used. Pick another name.")
    cmd = spec.get("cmd")
    if isinstance(cmd, str):
        try:
            cmd = shlex.split(cmd)
        except ValueError as exc:  # "No closing quotation"
            raise ValueError(f"custom command {name}: the command cannot be read ({exc}). Close each quote, "
                             'e.g. "./my agent.sh" --flag.') from None
    if not isinstance(cmd, list) or not cmd or len(cmd) > 200 or not all(isinstance(a, str) and a for a in cmd):
        raise ValueError(f"custom command {name}: the command is empty. Enter a program and its arguments, "
                         "e.g. ./scripts/agent.sh --flag.")
    program, warnings = cmd[0], []
    if "{" in program:
        raise ValueError(f"custom command {name}: {program} uses a placeholder as the program. "
                         "Enter the program itself; placeholders such as {prompt_file} go in the arguments.")
    if "/" not in program and os.sep not in program:
        if not shutil.which(program):
            warnings.append(f"custom command {name}: {program} is not on PATH here. Install it before the first run "
                            "or enter a path to it.")
    else:
        program = _project_path(name, program, root, program=True)
    args = list(cmd[1:])
    script = _script_index(cmd)
    if script is not None and "{" not in args[script - 1]:  # python3 agent.py: the turn's cwd is a worktree
        args[script - 1] = _project_path(name, args[script - 1], root, program=False)
    # an argument that is a script of another project (python3 -u /other/.alfrd/bin/x.py) is as unportable
    for arg in args:
        if os.path.isabs(arg) and "/.alfrd/bin/" in arg and not _inside(Path(arg).resolve(), root):
            raise ValueError(f"custom command {name}: {arg} points into another project's .alfrd/bin. "
                             f"Copy the script into this project and use .alfrd/bin/{Path(arg).name}.")
    args = [f"{PROJECT_DIR}/{Path(a).resolve().relative_to(root).as_posix()}"
            if os.path.isabs(a) and _inside(Path(a).resolve(), root) else a for a in args]
    entry: dict[str, Any] = {"name": name, "cmd": [program, *args], "adapter": "generic", **_LOOP_IO,
                             "output_capture": "stdout"}
    option = spec.get("model_option")
    if option not in (None, ""):
        if not isinstance(option, str) or not re.fullmatch(r"--?[A-Za-z0-9][A-Za-z0-9-]*", option):
            raise ValueError(f"custom command {name}: model option {option!r} is not a command-line option. "
                             "Use the flag your program takes, such as --model, or leave it empty.")
        entry["model_option"] = option
    return entry, warnings


def apply_to_manifest(data: dict[str, Any], root: str | Path, selected: list[str],
                      custom: list | None = None) -> tuple[list[str], list[str]]:
    """Make the ticked options the project's agent commands, in place: the loop agents (catalogue order,
    then custom) and warnings. ``entrypoint:`` holds exactly the ticked commands; ``chat.agents`` only
    their agents (the built-in defaults that are not ticked are removed); steps of unticked template
    agents are dropped. Raises ValueError naming the field."""
    if not isinstance(selected, list) or not all(isinstance(s, str) for s in selected):
        raise ValueError("agents: send a list of option ids. Tick the agent commands to use.")
    unknown = [s for s in selected if s not in OPTIONS]
    if unknown:
        raise ValueError(f"agents: unknown option(s) {', '.join(unknown)}. Choose from {', '.join(OPTIONS)}.")
    custom = custom or []
    if not isinstance(custom, list) or len(custom) > MAX_CUSTOM:
        raise ValueError(f"custom command: send a list of at most {MAX_CUSTOM} commands.")
    warnings: list[str] = []
    # the template's own non-agent commands stay (avica's pipeline entrypoints); its agent ones are replaced
    kept = [e for e in data.get("entrypoint") or [] if isinstance(e, Mapping) and e.get("name") not in OPTIONS]
    entries = [{"name": key, **copy.deepcopy(OPTIONS[key]["entry"])} for key in OPTIONS
               if key in selected and "entry" in OPTIONS[key]]
    for spec in custom:
        entry, more = validate_custom(root, spec, taken=[e["name"] for e in kept + entries])
        entries.append(entry)
        warnings += more
    loop = [e["name"] for e in entries]
    data["entrypoint"] = kept + entries
    steps = data.get("steps") if isinstance(data.get("steps"), dict) else {}
    known = {str(e.get("name")) for e in data.get("entrypoint") or []}
    for key in list(steps):  # the template's agent rows (claude, claude-turn, …) of agents not ticked
        base = key.removesuffix("-turn")
        if str((steps[key] or {}).get("category", "")).lower() == "agent" and base not in known:
            steps.pop(key)
    for name in loop:
        steps.setdefault(name, {"label": OPTIONS[name]["label"] if name in OPTIONS else name, "category": "Agent"})
    if "steps" in data or steps:
        data["steps"] = steps
    for form in ((data.get("quickstart") or {}).values() if isinstance(data.get("quickstart"), dict) else []):
        fields = form.get("fields") if isinstance(form, dict) else None
        if isinstance(fields, dict):  # Setup wizard fields of commands that are gone (entrypoint.codex.model, …)
            for key in [k for k in fields if k.startswith("entrypoint.") and k.split(".")[1] not in known]:
                fields.pop(key)
    chat_keys = [k for k in OPTIONS if k in selected] + [e["name"] for e in entries if e["name"] not in OPTIONS]
    if chat_keys:
        from alfrd.chat import DEFAULTS as CHAT_DEFAULTS

        agents: dict[str, Any] = {k: None for k, a in CHAT_DEFAULTS["agents"].items()
                                  if (a.get("entrypoint") or k) not in chat_keys}
        for key in chat_keys:
            agents[key] = {"entrypoint": key, **copy.deepcopy(OPTIONS[key]["agent"])} if key in OPTIONS else \
                {"label": key, "entrypoint": key, "adapter": "generic", "roles": ["development"]}
        block = dict(data.get("chat") or {})
        block["agents"] = agents
        if "claude" not in chat_keys:  # the default orchestrator (with Claude model names) is gone
            block.update(orchestrator=chat_keys[0], orchestrator_model=None, escalation_model=None)
        data["chat"] = block
    return loop, warnings


def _program_of(argv: list[str], root: Path) -> str | None:
    if not argv or not isinstance(argv[0], str):
        return None
    return argv[0].replace(PROJECT_DIR, str(root)).replace("{root}", str(root))


def local_argv(argv: list[str], root: str | Path) -> list[str]:
    """``argv`` with its program as a run in ``root`` sees it (``{project_dir}``/``{root}`` rendered, a
    relative path made absolute): what ``chat._program`` should probe."""
    root = Path(root).expanduser().resolve()
    program = _program_of(argv, root)
    if not program:
        return list(argv)
    if "/" in program and not os.path.isabs(program):
        program = str(root / program)
    return [program, *argv[1:]]


def entrypoint_warnings(data: Mapping[str, Any], root: str | Path) -> list[str]:
    """Commands in ``data`` (manifest mapping) that will not start in ``root``: missing programs, absolute
    paths into another project's ``.alfrd/``, chat agents naming a missing entrypoint or persona. Never raises."""
    import sys

    from alfrd.chat import BUILTIN_ENTRYPOINTS

    root = Path(root).expanduser().resolve()
    out: list[str] = []
    entries = {str(e.get("name")): e for e in data.get("entrypoint") or [] if isinstance(e, Mapping) and e.get("name")}

    def check(field: str, argv) -> None:
        if not isinstance(argv, list):
            return
        program = _program_of(argv, root)
        if not program:
            return
        # before the sys.executable shortcut: [sys.executable, /other/.alfrd/bin/x.py] is the copied-script case
        for arg in argv:
            text = str(arg)
            if os.path.isabs(text) and "/.alfrd/" in text and not _inside(Path(text).resolve(), root):
                out.append(f"{field}: {text} is inside another project's .alfrd folder. Copy the script into this "
                           f"project and use {PROJECT_DIR}/.alfrd/bin/{Path(text).name}.")
                return
        if program == sys.executable or "{" in program:
            return
        if os.path.isabs(program):
            if not Path(program).is_file():
                out.append(f"{field}: {program} does not exist. Install it or correct the path.")
            elif not os.access(program, os.X_OK):
                out.append(f"{field}: {program} is not executable. Run chmod +x on it.")
        elif "/" in program:
            if not (root / program).is_file():
                out.append(f"{field}: {program} does not exist in {root}. Copy the script there or correct the path.")
        elif not shutil.which(program):
            out.append(f"{field}: {program} is not on PATH. Install it before the first run.")

    for name, entry in entries.items():
        check(f"entrypoint {name}", entry.get("cmd"))
    chat = data.get("chat") if isinstance(data.get("chat"), Mapping) else {}
    personas = ((data.get("project_settings") or {}).get("personas") or {}) if isinstance(data.get("project_settings"), Mapping) else {}
    for key, agent in (chat.get("agents") or {}).items() if isinstance(chat.get("agents"), Mapping) else []:
        if not isinstance(agent, Mapping):
            continue
        if agent.get("cmd"):
            check(f"chat agent {key}", agent["cmd"])
            continue
        name = agent.get("entrypoint") or key
        if name not in entries and name not in BUILTIN_ENTRYPOINTS:
            out.append(f"chat agent {key}: entrypoint {name!r} is not defined here. Add it under Entrypoints or "
                       f"pick one of {', '.join(sorted({*entries, *BUILTIN_ENTRYPOINTS}))}.")
        elif name not in entries:
            check(f"chat agent {key} (built-in {name})", [str(a) for a in BUILTIN_ENTRYPOINTS[name]["cmd"]])
        if agent.get("persona") and agent["persona"] not in personas:
            out.append(f"chat agent {key}: persona {agent['persona']!r} is not in project_settings.personas. "
                       "Add the persona or clear the field.")
    return list(dict.fromkeys(out))


def entrypoint_status(data: Mapping[str, Any], root: str | Path) -> list[dict[str, Any]]:
    """One row per manifest entrypoint for the Settings card: ``{name, program, available, detail}``."""
    from alfrd.chat import _program

    root = Path(root).expanduser().resolve()
    rows = []
    for entry in data.get("entrypoint") or []:
        if not isinstance(entry, Mapping) or not entry.get("name"):
            continue
        argv = [str(a) for a in entry.get("cmd") or []]
        probe = _program(local_argv(argv, root))
        # cmd: the saved command, so the card does not show this result beside an edited draft command
        rows.append({"name": str(entry["name"]), "program": argv[0] if argv else None, "cmd": argv,
                     "available": probe["available"], "detail": probe["detail"]})
    return rows


__all__ = ["OPTIONS", "PROJECT_DIR", "apply_to_manifest", "entrypoint_status", "entrypoint_warnings",
           "local_argv", "options_for", "validate_custom"]
