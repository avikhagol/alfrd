"""CLI arguments and Claude's activity stream, separate from the final handoff."""
from __future__ import annotations

import json
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


SUPPORTED_AGENTS = ("claude", "codex")


def agent_command(argv, model=None, stream=False, access=None):
    args = list(argv or [])
    agent = Path(args[0]).name if args else ""
    if model and agent in SUPPORTED_AGENTS:
        args = set_option(args, "--model", model, "-m")
    if stream and agent == "claude":
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
    return tuple(args), option(args, "--model", "-m"), stream and agent == "claude"


class ClaudeStream:
    """Keep raw events for inspection, log activity, capture only a successful result."""

    def __init__(self, response: Path, exit_file: Path):
        self.response = response
        self.events = exit_file.with_suffix(".events.jsonl").open("w", encoding="utf-8")
        self.metadata_path = exit_file.with_suffix(".agent.json")
        self.metadata = {"models": [], "result_seen": False, "error": None}
        self.saw_deltas = False

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
            if event.get("is_error") or event.get("subtype") not in (None, "success"):
                self.metadata["error"] = event.get("result") or str(event.get("errors") or event.get("subtype"))
                print(f"\n[claude] Error: {self.metadata['error']}", flush=True)
            elif isinstance(event.get("result"), str) and event["result"].strip():
                from alfrd.agent_loop import atomic_text

                atomic_text(self.response, event["result"])
            else:
                self.metadata["error"] = "Claude returned no final text"
            for name in (event.get("modelUsage") or {}):
                if name not in self.metadata["models"]:
                    self.metadata["models"].append(name)
        from alfrd.agent_loop import atomic_text

        atomic_text(self.metadata_path, json.dumps(self.metadata))

    def close(self):
        self.events.close()
        if not self.metadata["result_seen"]:
            self.metadata["error"] = "Claude stream ended without a final result"
        return self.metadata["error"]
