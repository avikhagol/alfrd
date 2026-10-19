"""The ``alfrd.orchestration/1`` protocol: versioned JSON messages between ALFRD and agents.

Schemas live in ``alfrd/schemas/orchestration_{envelope,task,result,blocker,decision}.v1.json``
(see ``docs/orchestration.md``). Everything here is strict: an unknown protocol
version, an unknown field or a wrong type is an :class:`ProtocolError` with a
message that says what to fix.

Artifact references are ``<project-relative path>@<sha256 hex>``. They are resolved
by :func:`read_ref`: confined to the project root (symlinks included), hash-checked,
size-limited and read by byte range, so an agent gets exactly the part it asks for.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

PROTOCOL = "alfrd.orchestration/1"
PROTOCOL_NAME, PROTOCOL_VERSION = PROTOCOL.split("/")
KINDS = ("envelope", "task", "result", "blocker", "decision")
SCHEMA_BASE = "https://github.com/avikhagol/alfrd/schemas/"

MAX_READ_BYTES = 256 * 1024          # one retrieval answers at most this much
MAX_HASH_BYTES = 256 * 1024 * 1024   # files larger than this are not hashed (not referenceable)
_REF = re.compile(r"^(?P<path>[^@\s][^@]*)@(?:sha256:)?(?P<sha>[0-9a-f]{64})$")
_FENCE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\n(.*?)\n[ \t]*```", re.S)


class ProtocolError(ValueError):
    """A message does not follow ``alfrd.orchestration/1``; ``errors`` lists each problem."""

    def __init__(self, errors: Iterable[str]):
        self.errors = [str(e) for e in errors] or ["invalid message"]
        super().__init__("; ".join(self.errors))


class RefError(ValueError):
    """An artifact reference cannot be resolved. ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Schemas


@lru_cache(maxsize=None)
def schema(kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"unknown orchestration schema {kind!r}")
    text = resources.files("alfrd.schemas").joinpath(f"orchestration_{kind}.v1.json").read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=None)
def _validator(kind: str):
    import jsonschema
    from referencing import Registry, Resource

    registry = Registry().with_resources(
        (SCHEMA_BASE + f"orchestration_{name}.v1.json", Resource.from_contents(schema(name))) for name in KINDS)
    return jsonschema.Draft202012Validator(schema(kind), registry=registry)


def _path(error) -> str:
    parts = [str(p) for p in error.absolute_path]
    return "/".join(parts) or "(top level)"


def validate(kind: str, data: Any) -> dict[str, Any]:
    """``data`` checked against the ``kind`` schema; raises :class:`ProtocolError` listing every problem."""
    if kind == "envelope":
        check_protocol(data)
    errors = sorted(_validator(kind).iter_errors(data), key=lambda e: (list(e.absolute_path), e.message))
    if errors:
        raise ProtocolError(f"{_path(e)}: {e.message}" for e in errors[:20])
    return data


def check_protocol(data: Any) -> None:
    """Refuse a missing or different protocol name/version with a message naming the supported one."""
    if not isinstance(data, Mapping):
        raise ProtocolError(["message must be a JSON object"])
    value = data.get("protocol")
    if value == PROTOCOL:
        return
    if value is None:
        raise ProtocolError([f"missing \"protocol\": this ALFRD speaks {PROTOCOL!r}"])
    name, _, version = str(value).partition("/")
    if name == PROTOCOL_NAME:
        raise ProtocolError([f"unsupported protocol version {version!r} ({value!r}); this ALFRD speaks {PROTOCOL!r} only"])
    raise ProtocolError([f"unknown protocol {value!r}; expected {PROTOCOL!r}"])


def envelope(kind: str, **fields: Any) -> dict[str, Any]:
    """A validated envelope (``protocol`` set) for ALFRD's own messages."""
    return validate("envelope", {"protocol": PROTOCOL, "kind": kind, **{k: v for k, v in fields.items() if v is not None}})


def extract(text: str) -> dict[str, Any]:
    """The envelope inside an agent's reply: the last fenced JSON block naming a protocol, else the whole text.

    Raises :class:`ProtocolError` when there is none or it is not valid JSON; the envelope is
    then validated with :func:`validate`.
    """
    candidates = [m.group(1) for m in _FENCE.finditer(text or "")]
    chosen = next((c for c in reversed(candidates) if '"protocol"' in c), None)
    if chosen is None:
        stripped = (text or "").strip()
        if stripped.startswith("{") and '"protocol"' in stripped:
            chosen = stripped
    if chosen is None:
        raise ProtocolError([f"no ```json block with \"protocol\": \"{PROTOCOL}\" found in the reply"])
    try:
        data = json.loads(chosen)
    except ValueError as exc:
        raise ProtocolError([f"the ```json block is not valid JSON: {exc}"]) from exc
    return validate("envelope", data)


def has_envelope(text: str) -> bool:
    return any('"protocol"' in m.group(1) for m in _FENCE.finditer(text or "")) or \
        ((text or "").strip().startswith("{") and '"protocol"' in (text or ""))


# ---------------------------------------------------------------------------
# Plans: graph checks the schema cannot express


def check_graph(tasks: list[Mapping[str, Any]], *, agents: Iterable[str], known: Iterable[str] = ()) -> list[str]:
    """Problems with a list of tasks: duplicate ids, unknown owners, unknown dependencies, cycles.

    ``known`` are ids of earlier tasks a dependency may also name.
    """
    agents, known = set(agents), set(known)
    errors: list[str] = []
    ids = [t["id"] for t in tasks]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"task id {dup!r} is used twice")
    for task in tasks:
        if task["owner"] not in agents:
            errors.append(f"task {task['id']!r}: unknown owner {task['owner']!r} (agents: {', '.join(sorted(agents))})")
        for dep in task.get("depends_on") or []:
            if dep == task["id"]:
                errors.append(f"task {task['id']!r} depends on itself")
            elif dep not in ids and dep not in known:
                errors.append(f"task {task['id']!r}: unknown dependency {dep!r}")
    cycle = find_cycle({t["id"]: [d for d in t.get("depends_on") or [] if d in ids] for t in tasks})
    if cycle:
        errors.append("dependency cycle: " + " → ".join(cycle))
    return errors


def find_cycle(graph: Mapping[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if state.get(nxt) is None:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Artifact references


def parse_ref(ref: str) -> tuple[str, str]:
    """``(path, sha256)`` of ``path@sha256``; raises :class:`RefError`."""
    match = _REF.match(str(ref or "").strip())
    if not match:
        raise RefError("an artifact reference looks like <relative path>@<64 hex sha256>")
    return match.group("path"), match.group("sha")


def format_ref(path: str, sha: str) -> str:
    return f"{path}@{sha}"


def confined(root: str | Path, rel: str) -> Path:
    """``rel`` under ``root``, resolved (symlinks followed); :class:`RefError` when it leaves the root."""
    base = Path(root).resolve()
    text = str(rel or "").strip()
    if not text or text.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", text) or "\x00" in text:
        raise RefError("artifact paths are relative to the project root", 400)
    if ".." in Path(text).parts:
        raise RefError("artifact paths may not contain '..'", 400)
    path = (base / text).resolve()
    if os.path.commonpath([str(base), str(path)]) != str(base):
        raise RefError("artifact path leaves the project root", 403)
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_ref(root: str | Path, rel: str) -> str:
    """The ``path@sha256`` of an existing project file (``rel`` relative to ``root``)."""
    path = confined(root, rel)
    if not path.is_file():
        raise RefError(f"{rel}: no such file", 404)
    if path.stat().st_size > MAX_HASH_BYTES:
        raise RefError(f"{rel}: larger than {MAX_HASH_BYTES} bytes; not referenceable", 413)
    return format_ref(str(path.relative_to(Path(root).resolve())), file_sha256(path))


def read_ref(root: str | Path, ref: str, *, offset: int = 0, length: int | None = None,
             max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
    """Selective retrieval of one artifact version.

    The file must be inside ``root`` (after resolving symlinks), regular, and still
    have the referenced hash (409 otherwise: the artifact changed since it was
    referenced). At most ``max_bytes`` from ``offset`` are returned as text.
    """
    rel, sha = parse_ref(ref)
    path = confined(root, rel)
    if not path.is_file():
        raise RefError(f"{rel}: no such file", 404)
    size = path.stat().st_size
    if size > MAX_HASH_BYTES:
        raise RefError(f"{rel}: larger than {MAX_HASH_BYTES} bytes; not referenceable", 413)
    actual = file_sha256(path)
    if actual != sha:
        raise RefError(f"{rel}: content changed since it was referenced (sha256 {actual[:12]}…, ref {sha[:12]}…)", 409)
    try:
        offset = max(0, int(offset or 0))
        want = max_bytes if length in (None, "") else max(0, min(int(length), max_bytes))
    except (TypeError, ValueError) as exc:
        raise RefError("offset and length must be integers") from exc
    with open(path, "rb") as stream:
        stream.seek(offset)
        data = stream.read(want)
    return {"ref": format_ref(rel, sha), "path": rel, "sha256": sha, "size": size, "offset": offset,
            "length": len(data), "eof": offset + len(data) >= size,
            "truncated": offset + len(data) < size, "content": data.decode("utf-8", errors="replace")}


__all__ = [
    "MAX_READ_BYTES", "PROTOCOL", "ProtocolError", "RefError", "check_graph", "check_protocol", "confined",
    "envelope", "extract", "find_cycle", "format_ref", "has_envelope", "make_ref", "parse_ref", "read_ref",
    "schema", "validate",
]


def validate_mailbox_blocker(blocker, store, run_id: str, unit_id: str) -> None:
    """Validate optional durable-mail linkage against the originating execution."""
    mid = blocker.get("mailbox_message_id")
    if not mid:
        return
    from alfrd.mailbox import Mailbox, MailboxError
    try:
        box = Mailbox(store.root, store.id, run_id)
        with box.locked():
            message = box._find(mid)
            if message["sender"].get("unit_id") != unit_id or message["kind"] not in ("question", "permission_request"):
                raise MailboxError("blocker mailbox message must originate from this attempt")
    except MailboxError as exc:
        raise ProtocolError([str(exc)]) from exc
