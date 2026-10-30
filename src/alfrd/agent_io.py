"""CLI arguments and Claude's activity stream, separate from the final handoff."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path


def option(argv, name, short=None):
    for index, arg in enumerate(argv):
        if arg in (name, short) and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def set_option(argv, name, value, short=None):
    result = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in (name, short):
            skip = True
        elif not arg.startswith(name + "="):
            result.append(arg)
    # Put options before Codex's stdin positional argument.
    at = len(result) - 1 if result and result[-1] == "-" else len(result)
    result[at:at] = [name, value]
    return result


USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "total_tokens")
# input_uncached_tokens: input not served from cache, comparable across providers.
NORMALIZED_KEYS = ("input_uncached_tokens", *USAGE_KEYS)


@dataclass(frozen=True)
class AgentAdapter:
    """What ALFRD knows about one agent CLI; ``generic`` assumes nothing."""
    name: str
    model_option: tuple[str, str | None] | None = None
    stream: bool = False                 # Claude's stream-json activity log
    cached_in_input: bool = False        # the reported input count already includes cached input
    native_fallback: str | None = None   # CLI option naming a fallback model
    log_usage: bool = False              # usage is read from the command log after exit


ADAPTERS = {
    "claude": AgentAdapter("claude", ("--model", "-m"), stream=True, native_fallback="--fallback-model"),
    "codex": AgentAdapter("codex", ("--model", "-m"), cached_in_input=True, log_usage=True),
    "generic": AgentAdapter("generic"),
}
SUPPORTED_AGENTS = tuple(name for name in ADAPTERS if name != "generic")


def adapter_for(argv=None, name=None, model_option=None):
    """The entrypoint's ``adapter`` when given, else the program name, else ``generic``."""
    if name is not None:
        if name not in ADAPTERS:
            raise ValueError(f"unknown agent adapter {name!r}; choose one of {', '.join(ADAPTERS)}")
        adapter = ADAPTERS[name]
    else:
        adapter = ADAPTERS.get(Path(argv[0]).name if argv else "", ADAPTERS["generic"])
    if model_option and adapter.model_option is None:
        adapter = replace(adapter, model_option=(model_option, None))
    return adapter


def token_usage(value, cached_in_input=False):
    """Normalize reported counts, preserving missing fields as unknown."""
    if not isinstance(value, dict):
        return None
    result = {key: value.get(key) if isinstance(value.get(key), int) and not isinstance(value.get(key), bool)
              and value[key] >= 0 else None for key in USAGE_KEYS}
    if result["total_tokens"] is None and result["input_tokens"] is not None and result["output_tokens"] is not None:
        result["total_tokens"] = sum(result[key] or 0 for key in USAGE_KEYS[:-1])
    if not any(v is not None for v in result.values()):
        return None
    result["input_uncached_tokens"] = uncached_input(result, cached_in_input)
    return result


def sum_usage(usages, cached_in_input=False):
    """Field-wise sum of normalized usages; a field stays unknown when no usage reported it."""
    usages = [u for u in usages if isinstance(u, dict)]
    if not usages:
        return None
    out = {}
    for key in USAGE_KEYS:
        known = [u[key] for u in usages if isinstance(u.get(key), int) and not isinstance(u.get(key), bool)]
        out[key] = sum(known) if known else None
    out["input_uncached_tokens"] = uncached_input(out, cached_in_input)
    return out


def uncached_input(usage, cached_in_input):
    """Unknown unless every count it depends on was reported."""
    total, cached = usage.get("input_tokens"), usage.get("cache_read_input_tokens")
    if not cached_in_input:
        return total
    if not isinstance(total, int) or not isinstance(cached, int) or cached > total:
        return None
    return total - cached


def codex_report(text):
    """(usage, usage_source) from a Codex log; never inferred from response length."""
    for line in reversed(text.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            reported = event.get("usage")
            if isinstance(reported, dict):
                reported = dict(reported)
                reported["cache_read_input_tokens"] = reported.get("cached_input_tokens")
                # Codex's input count already includes the cached input.
                if all(isinstance(reported.get(key), int) for key in ("input_tokens", "output_tokens")):
                    reported["total_tokens"] = reported["input_tokens"] + reported["output_tokens"]
            usage = token_usage(reported, cached_in_input=True)
            if usage:
                return usage, "codex_event"
    matches = re.findall(r"(?mi)^tokens used\s*\n\s*([\d,]+)\s*$", text)
    if matches:
        return token_usage({"total_tokens": int(matches[-1].replace(",", ""))}, cached_in_input=True), "codex_total_only"
    return None, "unavailable"


def codex_usage(text):
    return codex_report(text)[0]


def unit_adapter(unit):
    if unit.get("adapter") in ADAPTERS:
        return ADAPTERS[unit["adapter"]]
    return adapter_for(unit.get("argv") or [unit.get("agent") or ""])


def normalized_usage(unit):
    """A unit's usage with ``input_uncached_tokens``; derived for records written before it existed."""
    usage = unit.get("agent_usage")
    if not isinstance(usage, dict):
        return None
    if "input_uncached_tokens" in usage:
        return usage
    return {**usage, "input_uncached_tokens": uncached_input(usage, unit_adapter(unit).cached_in_input)}


def agent_totals(units):
    """Aggregate all recorded turns, independently of status pagination."""
    def known_sum(values):
        values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
        return sum(values) if values else None

    usages = [normalized_usage(unit) or {} for unit in units]
    result = {key: known_sum(usage.get(key) for usage in usages) for key in NORMALIZED_KEYS}
    result["total_cost_usd"] = known_sum(unit.get("total_cost_usd") for unit in units)
    return result


STARTUP_KEYS = ("lean", "mcp", "plugins", "skills")


def startup_options(spec):
    """Validated lean-startup options of an entrypoint or step (``lean``, ``mcp``, ``plugins``, ``skills``).

    ``lean: true`` means ``mcp: none``, ``plugins: false`` unless overridden. Native skills stay on unless ``skills: false`` is explicit.
    Returns {} when nothing is asked for (existing projects start agents unchanged). Raises ValueError.
    """
    spec = spec or {}
    if not any(key in spec for key in STARTUP_KEYS):
        return {}
    lean = spec.get("lean", False)
    if not isinstance(lean, bool):
        raise ValueError("lean must be true or false")
    out = {"mcp": "none", "plugins": False} if lean else {}
    mcp = spec.get("mcp")
    if mcp is not None:
        if isinstance(mcp, str) and mcp in ("none", "default"):
            out["mcp"] = mcp
        elif isinstance(mcp, list) and mcp and all(isinstance(m, str) and m.strip() for m in mcp):
            out["mcp"] = [m.strip() for m in mcp]
        else:
            raise ValueError("mcp must be none, default or a list of MCP config files (or JSON strings)")
    for key in ("plugins", "skills"):
        if key in spec and spec[key] is not None:
            if not isinstance(spec[key], bool):
                raise ValueError(f"{key} must be true or false")
            out[key] = spec[key]
    return {k: v for k, v in out.items() if not (k == "mcp" and v == "default") and v is not True}


# Claude Code CLI flags for lean startup (checked against `claude --help`, 2.1.x):
#   --strict-mcp-config            use only MCP servers from --mcp-config (none when it is not given)
#   --mcp-config <configs...>      load MCP servers from these JSON files or strings
#   --setting-sources project,local  skip user settings (~/.claude/settings.json: user plugins, hooks, MCP servers)
#   --disable-slash-commands       disable all skills (and slash commands)
LEAN_FLAGS = ("--strict-mcp-config", "--mcp-config", "--setting-sources", "--disable-slash-commands")


def startup_args(adapter, startup):
    """CLI arguments for ``startup`` (see :func:`startup_options`); only Claude has such flags."""
    if not startup or adapter.name != "claude":
        return []
    args = []
    mcp = startup.get("mcp")
    if isinstance(mcp, list):
        args += ["--mcp-config", *mcp]  # variadic: always followed by another option below
    if mcp is not None:
        args.append("--strict-mcp-config")
    if startup.get("plugins") is False:
        args += ["--setting-sources", "project,local"]
    if startup.get("skills") is False:
        args.append("--disable-slash-commands")
    return args


# Read-only shell commands a Claude agent may always run once agent_access.bash_commands is set
# (agent_access.default_readonly: false turns this off). Claude Code checks every part of
# `a && b` / `a | b` separately, so `cd X && ls | head` needs cd and head too.
READONLY_COMMANDS = ("cd *", "pwd", "ls *", "cat *", "head *", "tail *", "wc *", "sha256sum *")
# The mailbox protocol every chat task prompt describes; without it `alfrd mailbox read` is denied.
MAILBOX_COMMAND = "alfrd mailbox *"


def claude_bash_rules(commands, readonly=False):
    """``Bash(...)`` rules for --allowedTools. A pattern without a wildcard (``npm``) matches only that
    exact command line in Claude Code, so ``npm install`` would still be denied: such a pattern also
    gets ``<pattern> *``."""
    rules = []
    for command in commands or []:
        command = str(command).strip()
        if not command:
            continue
        rules.append(command)
        if "*" not in command and ":" not in command:
            rules.append(command + " *")
    if readonly:
        rules.extend(READONLY_COMMANDS)
    return [f"Bash({rule})" for rule in dict.fromkeys(rules)]


def access_folders(base, folders):
    """agent_access folders as absolute paths: ``~`` expanded, relative to ``base``, globs expanded (a
    pattern that matches nothing is dropped), each kept both as written and resolved (symlinks), so an
    agent that types /home/... and one that types the real /mnt/... path are both allowed."""
    import glob
    import os

    out = []
    for raw in folders or []:
        path = Path(str(raw)).expanduser()
        path = path if path.is_absolute() else Path(base) / path
        text = str(path)
        if any(ch in text for ch in "*?["):
            paths = [Path(m) for m in sorted(glob.glob(text)) if Path(m).is_dir()]
        else:
            paths = [path]
        for found in paths:
            for form in (os.path.normpath(str(found)), str(found.resolve())):
                if form not in out:
                    out.append(form)
    return out


def merge_access(*parts):
    """Several agent_access mappings as one (folders and commands added up; the first sandbox wins)."""
    out = {"folders": [], "bash_commands": []}
    for part in parts:
        if not part:
            continue
        out["folders"] = list(dict.fromkeys([*out["folders"], *(part.get("folders") or [])]))
        out["bash_commands"] = list(dict.fromkeys([*out["bash_commands"], *(part.get("bash_commands") or [])]))
        if part.get("sandbox") and not out.get("sandbox"):
            out["sandbox"] = part["sandbox"]
        if "default_readonly" in part and "default_readonly" not in out:
            out["default_readonly"] = part["default_readonly"]
    return out


def agent_command(argv, model=None, stream=False, access=None, adapter=None, fallback=None, startup=None):
    args = list(argv or [])
    adapter = adapter or adapter_for(args)
    agent = adapter.name
    lean = startup_args(adapter, startup)
    if lean and not any(a.split("=")[0] in LEAN_FLAGS for a in args):  # a command that sets them itself wins
        at = len(args) - 1 if args and args[-1] == "-" else len(args)
        args[at:at] = lean
    if model and adapter.model_option:
        args = set_option(args, adapter.model_option[0], model, adapter.model_option[1])
    if fallback and adapter.native_fallback:
        args = set_option(args, adapter.native_fallback, fallback)
    if stream and adapter.stream:
        args = set_option(args, "--output-format", "stream-json")
        for flag in ("--verbose", "--include-partial-messages"):
            if flag not in args:
                args.append(flag)
    if agent in SUPPORTED_AGENTS and access:
        folders = access.get("folders", [])
        if folders:
            at = len(args) - 1 if args and args[-1] == "-" else len(args)
            additions = ["--add-dir", *folders] if agent == "claude" else [part for directory in folders for part in ("--add-dir", directory)]
            args[at:at] = additions
        if agent == "claude" and access.get("bash_commands"):
            existing = option(args, "--allowedTools", "--allowed-tools")
            tools = [existing] if existing else []
            known = set(",".join(tools).split(",")) if tools else set()
            tools.extend(rule for rule in claude_bash_rules(access["bash_commands"], access.get("default_readonly", True))
                         if rule not in known)
            args = set_option(args, "--allowedTools", ",".join(tools), "--allowed-tools")
        if agent == "codex" and access.get("sandbox"):
            args = set_option(args, "--sandbox", access["sandbox"], "-s")
    chosen = option(args, *adapter.model_option) if adapter.model_option else model
    return tuple(args), chosen, stream and adapter.stream


NO_FINAL_TEXT = "Claude returned no final text"


class ClaudeStream:
    """Keep raw events for inspection, log activity, capture only a successful result."""

    def __init__(self, response: Path, exit_file: Path, headings=None):
        self.response = response
        self.headings = headings  # a handoff's required headings: a result with them is not replaced by one without
        self.kept_valid = False
        self.wrote_text = False
        self.events = exit_file.with_suffix(".events.jsonl").open("w", encoding="utf-8")
        self.metadata_path = exit_file.with_suffix(".agent.json")
        self.metadata = {"models": [], "result_seen": False, "error": None, "usage": None, "usage_source": "unavailable", "total_cost_usd": None}
        self.saw_deltas = False
        self.pending_tasks: set[str] = set()  # background agents/shells Claude still waits on
        self.settled_at: float | None = None  # monotonic time of a final result with nothing pending
        self.result_usage: dict[str, dict] = {}  # usage per result event (uuid, else its position)

    def feed(self, line: str):
        self.events.write(line)
        self.events.flush()
        try:
            event = json.loads(line)
        except ValueError:
            print("[claude] " + line.rstrip(), flush=True)
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        message = event.get("message") or {}
        model = event.get("model") or (message.get("model") if isinstance(message, dict) else None)
        if model and model not in self.metadata["models"]:
            self.metadata["models"].append(model)
            self.metadata.setdefault("model", model)
            print(f"\n[claude] Model: {model}", flush=True)
        if kind == "system" and event.get("subtype") == "background_tasks_changed":
            self.pending_tasks = {str(t.get("task_id")) for t in event.get("tasks") or [] if isinstance(t, dict)}
        elif kind in ("assistant", "stream_event") or event.get("subtype") == "task_started":
            self.settled_at = None  # Claude is working again
        if kind == "stream_event":
            delta = (event.get("event") or {}).get("delta") or {}
            if delta.get("type") == "text_delta":
                self.saw_deltas = True
                print(delta.get("text", ""), end="", flush=True)
        elif kind in ("assistant", "user"):
            for block in message.get("content", []) if isinstance(message, dict) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    print(f"\n[claude] Tool: {block.get('name')} {json.dumps(block.get('input', {}), ensure_ascii=False)}", flush=True)
                elif block.get("type") == "tool_result":
                    print(f"\n[claude] Tool result: {str(block.get('content', ''))[:16000]}", flush=True)
                elif block.get("type") == "text" and not self.saw_deltas:
                    print(block.get("text", ""), flush=True)
        elif kind == "result":
            self.metadata["result_seen"] = True
            # A run emits one result per wake-up (background-task follow-ups too). Each result's
            # usage covers that wake-up only, so tokens are summed (a repeated uuid counts once);
            # total_cost_usd is already cumulative for the session, so the latest one is kept.
            usage = token_usage(event.get("usage"))
            key = str(event.get("uuid") or f"#{len(self.result_usage)}")
            if usage:
                self.result_usage[key] = usage
            self.metadata["usage"] = sum_usage(self.result_usage.values())
            self.metadata["results"] = len(self.result_usage)
            self.metadata["usage_source"] = "claude_result" if self.metadata["usage"] else "unavailable"
            cost = event.get("total_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
                self.metadata["total_cost_usd"] = cost
            if event.get("is_error") or event.get("subtype") not in (None, "success"):
                self.metadata["error"] = event.get("result") or str(event.get("errors") or event.get("subtype"))
                print(f"\n[claude] Error: {self.metadata['error']}", flush=True)
            elif isinstance(event.get("result"), str) and event["result"].strip():
                from alfrd.agent_loop import atomic_text, validate_response

                # Claude answers each finished background task with another result; a short
                # status note after the report ("the report above stands") must not replace it.
                valid = self.headings is not None and not validate_response(event["result"], self.headings)
                if valid or not self.kept_valid:
                    atomic_text(self.response, event["result"])
                    self.kept_valid = self.kept_valid or valid
                    self.wrote_text = True
                else:
                    print("\n[alfrd] Kept the earlier handoff: a later Claude result has no handoff headings.", flush=True)
                if self.metadata["error"] == NO_FINAL_TEXT:
                    self.metadata["error"] = None  # a later result supplied the text
            elif not self.wrote_text:
                # A background task can wake Claude for an empty follow-up result; that
                # only fails the turn when no result with text arrives at all.
                self.metadata["error"] = NO_FINAL_TEXT
            if not self.pending_tasks and not self.metadata["error"]:
                self.settled_at = time.monotonic()
            for name in (event.get("modelUsage") or {}):
                if name not in self.metadata["models"]:
                    self.metadata["models"].append(name)
        from alfrd.agent_loop import atomic_text

        atomic_text(self.metadata_path, json.dumps(self.metadata))

    def close(self):
        self.events.close()
        if not self.metadata["result_seen"]:
            self.metadata["error"] = "Claude stream ended without a final result"
        from alfrd.agent_loop import atomic_text

        atomic_text(self.metadata_path, json.dumps(self.metadata))
        return self.metadata["error"]
