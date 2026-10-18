"""A text-only chat agent for any OpenAI-compatible endpoint (standard library only).

    python -m alfrd.llm_chat [--base-url URL] [--model M] [--api-key-env NAME] [--max-tokens N] < prompt.md

Reads the prompt from stdin, POSTs ``{base-url}/chat/completions`` (streaming; the text is echoed to
stderr as it arrives, so the unit log shows progress), and writes the final answer to stdout. A last
stderr line ``{"type": "usage", "usage": {...}}`` carries the token counts when the server reports them.

Defaults come from the environment (a chat agent's ``env``): ``ALFRD_LLM_BASE_URL`` (default
``http://localhost:11434/v1``, a local Ollama), ``ALFRD_LLM_MODEL`` (default ``qwen3:8b``),
``ALFRD_LLM_API_KEY_ENV`` (the *name* of the variable holding the API key, e.g. ``HF_TOKEN``).

Exit codes: 0 done; 2 a rate/usage/quota limit (HTTP 429, "quota" …) — the chat's fallback detector
moves the task to another agent; 1 any other error (unreachable server, unknown model, bad reply).
No tools, no file edits: use it for planning, research summaries and writing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen3:8b"
LIMIT = re.compile(r"rate[ _-]?limit|quota|usage limit|too many requests|insufficient_quota|credit", re.I)
LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class LimitError(RuntimeError):
    """The provider refused for a rate, usage or quota limit."""


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in LOOPBACK or host.startswith("127."):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a local server: never via a proxy
    return urllib.request.build_opener()


def _say(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


def _strip_think(text: str) -> str:
    """Reasoning models (qwen3, deepseek-r1) may put their thinking in <think>…</think>; the answer is the rest."""
    return re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.S).strip()


def _http_error(exc: urllib.error.HTTPError, model: str, url: str) -> Exception:
    try:
        body = exc.read(4096).decode("utf-8", errors="replace")
    except OSError:
        body = ""
    detail = f"HTTP {exc.code} {exc.reason} from {url}: {body.strip()[:1000]}"
    if exc.code == 429 or (exc.code in (402, 403) and LIMIT.search(body)) or LIMIT.search(body):
        return LimitError(f"rate limit / quota: {detail}")
    if exc.code == 404 or re.search(r"model", body, re.I) and re.search(r"not found|does not exist|unknown", body, re.I):
        return RuntimeError(f"model {model} not found or unavailable: {detail}")
    return RuntimeError(detail)


def complete(prompt: str, *, base_url: str, model: str, api_key: str | None = None, max_tokens: int | None = None,
             stream: bool = True, timeout: float = 600.0, echo=_say) -> tuple[str, dict[str, Any] | None]:
    """The answer text and the usage the server reported (or None). Raises LimitError / RuntimeError."""
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": stream}
    if stream:
        body["stream_options"] = {"include_usage": True}
    if max_tokens:
        body["max_tokens"] = int(max_tokens)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        response = _opener(url).open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc, model, url) from None
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"cannot reach {url}: {getattr(exc, 'reason', exc)}") from None
    usage = None
    with response:
        kind = response.headers.get("Content-Type", "")
        if "text/event-stream" not in kind:  # the server did not stream (or stream was off)
            try:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            except ValueError as exc:
                raise RuntimeError(f"not a JSON reply from {url}: {exc}") from None
            if isinstance(data, dict) and data.get("error"):
                raise _error_payload(data["error"], model)
            try:
                text = data["choices"][0]["message"].get("content") or ""
            except (KeyError, IndexError, TypeError, AttributeError):
                raise RuntimeError(f"unexpected reply from {url}: {str(data)[:500]}") from None
            echo(text + "\n")
            return _strip_think(text), data.get("usage")
        parts: list[str] = []
        reasoning = False
        for raw in response:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("error"):
                raise _error_payload(event["error"], model)
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                think = delta.get("reasoning") or delta.get("reasoning_content")
                if think:
                    if not reasoning:
                        echo("[thinking] ")
                        reasoning = True
                    echo(str(think))
                text = delta.get("content")
                if text:
                    if reasoning:
                        echo("\n")
                        reasoning = False
                    parts.append(str(text))
                    echo(str(text))
        echo("\n")
    return _strip_think("".join(parts)), usage


def _error_payload(error: Any, model: str) -> Exception:
    text = json.dumps(error) if not isinstance(error, str) else error
    if LIMIT.search(text) or "429" in text:
        return LimitError(f"rate limit / quota: {text[:1000]}")
    if re.search(r"model", text, re.I) and re.search(r"not found|does not exist", text, re.I):
        return RuntimeError(f"model {model} not found or unavailable: {text[:1000]}")
    return RuntimeError(text[:1000])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alfrd.llm_chat", description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default=os.environ.get("ALFRD_LLM_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--model", default=os.environ.get("ALFRD_LLM_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--api-key-env", default=os.environ.get("ALFRD_LLM_API_KEY_ENV") or None,
                        help="name of the environment variable that holds the API key (e.g. HF_TOKEN)")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("ALFRD_LLM_TIMEOUT") or 600))
    parser.add_argument("--no-stream", action="store_true")
    args = parser.parse_args(argv)
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("llm_chat: empty prompt on stdin", file=sys.stderr)
        return 1
    key = None
    if args.api_key_env:
        key = os.environ.get(args.api_key_env)
        if not key:
            print(f"llm_chat: {args.api_key_env} is not set (the API key for {args.base_url})", file=sys.stderr)
            return 1
    _say(f"[llm_chat] {args.model} at {args.base_url}\n")
    try:
        text, usage = complete(prompt, base_url=args.base_url, model=args.model, api_key=key,
                               max_tokens=args.max_tokens, stream=not args.no_stream, timeout=args.timeout)
    except LimitError as exc:
        print(f"llm_chat: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"llm_chat: {exc}", file=sys.stderr)
        return 1
    if not text:
        print("llm_chat: the model returned no text", file=sys.stderr)
        return 1
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()
    if usage:
        out = {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
               "total_tokens": usage.get("total_tokens")}
        print(json.dumps({"type": "usage", "usage": {k: v for k, v in out.items() if v is not None}}), file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
