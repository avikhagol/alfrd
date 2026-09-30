"""Entity paths: one way to name "this target in this code and work dir" or "line 812 of this log".

An entity path is a small dict::

    {"project": "avica-t-0.3", "target": "J0742+103", "project_code": "BV019",
     "workdir": "wd_1", "step": "rpicard", "file": "reductions/BV019/wd_1/casa.log", "line": 812}

Every key after ``project`` is optional. The level names between ``project``
and ``step`` come from the template's ``hierarchy`` (``[{level: …}, …]``);
without one, the AVICA levels are used (``target``, ``project_code``,
``workdir``, ``band``). ``project`` is the project identifier internally; UIs
show the project name.

The URL form (a query string, or ``#e?…`` as a fragment) gives deep links. Its
key order is stable: project, the levels, step, file, line. The same rules are
implemented in ``web/js/data/entities.js``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote, unquote

AVICA_LEVELS: tuple[str, ...] = ("target", "project_code", "workdir", "band")
TAIL: tuple[str, ...] = ("step", "file", "line")
FRAGMENT = "#e?"


def levels_from(manifest: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Level names for a (merged) manifest: ``target`` then its ``hierarchy`` levels."""
    items = (manifest or {}).get("hierarchy") if isinstance(manifest, Mapping) else None
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)) or not items:
        return AVICA_LEVELS
    names = [str(i.get("level")) for i in items if isinstance(i, Mapping) and i.get("level")]
    out = ["target", *[n for n in names if n != "target"]]
    return tuple(dict.fromkeys(out))


def keys(levels: Sequence[str] | None = None) -> tuple[str, ...]:
    return ("project", *(levels or AVICA_LEVELS), *TAIL)


def make(entity: Mapping[str, Any] | None = None, *, levels: Sequence[str] | None = None, **values: Any) -> dict[str, Any]:
    """A validated entity path in the stable key order (empty values dropped).

    Raises ``ValueError`` for an unknown key, a missing ``project`` or a bad ``line``.
    """
    raw = {**(entity or {}), **values}
    allowed = keys(levels)
    unknown = [k for k in raw if k not in allowed]
    if unknown:
        raise ValueError(f"unknown entity level(s): {', '.join(sorted(map(str, unknown)))}; allowed: {', '.join(allowed)}")
    out: dict[str, Any] = {}
    for key in allowed:
        value = raw.get(key)
        if value is None or value == "":
            continue
        if key == "line":
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"entity line must be an integer, got {value!r}") from exc
            if value < 1:
                raise ValueError("entity line starts at 1")
        else:
            value = str(value)
        out[key] = value
    if "project" not in out:
        raise ValueError("an entity path needs a project")
    if "line" in out and "file" not in out:
        raise ValueError("an entity line needs a file")
    return out


def to_query(entity: Mapping[str, Any], *, levels: Sequence[str] | None = None) -> str:
    """``project=…&target=…`` in the stable key order (RFC 3986 escaping, ``+`` kept as ``%2B``)."""
    clean = make(entity, levels=levels)
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in clean.items())


def from_query(text: str, *, levels: Sequence[str] | None = None) -> dict[str, Any]:
    """Inverse of :func:`to_query`; accepts a leading ``?`` or the ``#e?`` fragment."""
    text = str(text or "")
    for prefix in (FRAGMENT, "#", "?"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    # Not parse_qsl: it reads "+" as a space (form encoding), and target names contain "+".
    raw: dict[str, str] = {}
    for part in text.split("&"):
        if part:
            key, _, value = part.partition("=")
            raw[unquote(key)] = unquote(value)
    return make(raw, levels=levels)


def to_fragment(entity: Mapping[str, Any], *, levels: Sequence[str] | None = None) -> str:
    return FRAGMENT + to_query(entity, levels=levels)


def contains(outer: Mapping[str, Any], inner: Mapping[str, Any]) -> bool:
    """True when ``inner`` is ``outer`` or below it (every key of ``outer`` matches)."""
    return all(inner.get(k) == v for k, v in outer.items())


__all__ = ["AVICA_LEVELS", "FRAGMENT", "contains", "from_query", "keys", "levels_from", "make", "to_fragment", "to_query"]
