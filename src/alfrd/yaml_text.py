"""YAML that ALFRD writes for people and for the Studio's own parser.

Long strings stay on one line (no folded plain or quoted continuation lines)
and text with line breaks becomes a ``|`` block, so files read naturally and
round-trip through ``web/js/utils/yaml_parser.js`` exactly as through PyYAML.
"""
from __future__ import annotations

from typing import Any

import yaml


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    # A block keeps the text exactly unless lines end in spaces or it has tabs / control characters.
    if "\n" in value and all(line == line.rstrip() for line in value.split("\n")) \
            and all(c == "\n" or c.isprintable() for c in value):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _str)


def dump(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=float("inf"))


def replace_sections(text: str, old: dict, new: dict) -> str:
    """Rewrite only the top-level keys whose value changed; other lines (and comments) stay as they are.

    Mirrors ``replaceSection`` in web/js/data/agent_settings.js.
    """
    import re

    lines = text.split("\n")
    for key in list(dict.fromkeys([*old, *new])):
        if key in old and key in new and old[key] == new[key]:
            continue
        start = next((i for i, line in enumerate(lines) if re.match(rf"^{re.escape(str(key))}\s*:", line)), None)
        block = dump({key: new[key]}).rstrip("\n").split("\n") if key in new else []
        if start is None:
            if block:
                while lines and lines[-1] == "":
                    lines.pop()
                lines += ["", *block, ""]
            continue
        end = start + 1
        while end < len(lines) and not re.match(r"^[^\s#-][^:]*:", lines[end]):
            end += 1
        while end > start + 1 and (lines[end - 1].strip() == "" or lines[end - 1].startswith("#")):
            end -= 1
        lines[start:end] = block
    out = "\n".join(lines)
    return out if out.endswith("\n") else out + "\n"
