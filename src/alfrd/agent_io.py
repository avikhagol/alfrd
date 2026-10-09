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


def agent_command(argv, model=None, stream=False, access=None, adapter=None, fallback=None):
    args = list(argv or [])
    adapter = adapter or adapter_for(args)
    agent = adapter.name
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
            tools.extend(f"Bash({command})" for command in access["bash_commands"])
            args = set_option(args, "--allowedTools", ",".join(tools), "--allowed-tools")
        if agent == "codex" and access.get("sandbox"):
            args = set_option(args, "--sandbox", access["sandbox"], "-s")
    chosen = option(args, *adapter.model_option) if adapter.model_option else model
    return tuple(args), chosen, stream and adapter.stream


class ClaudeStream:
    """Keep raw events for inspection, log activity, capture only a successful result."""

    def __init__(self, response: Path, exit_file: Path, headings=None):
        self.response = response
        self.headings = headings  # a handoff's required headings: a result with them is not replaced by one without
        self.kept_valid = False
        self.events = exit_file.with_suffix(".events.jsonl").open("w", encoding="utf-8")
        self.metadata_path = exit_file.with_suffix(".agent.json")
        self.metadata = {"models": [], "result_seen": False, "error": None, "usage": None, "usage_source": "unavailable", "total_cost_usd": None}
        self.saw_deltas = False
        self.pending_tasks: set[str] = set()  # background agents/shells Claude still waits on
        self.settled_at: float | None = None  # monotonic time of a final result with nothing pending

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
            self.metadata["usage"] = token_usage(event.get("usage"))
            self.metadata["usage_source"] = "claude_result" if self.metadata["usage"] else "unavailable"
            cost = event.get("total_cost_usd")
            self.metadata["total_cost_usd"] = cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None
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
                else:
                    print("\n[alfrd] Kept the earlier handoff: a later Claude result has no handoff headings.", flush=True)
            else:
                self.metadata["error"] = "Claude returned no final text"
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
