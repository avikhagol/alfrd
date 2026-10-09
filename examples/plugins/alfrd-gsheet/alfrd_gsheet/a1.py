"""One-based A1 addresses and unambiguous, trimmed header matching.

Always quote worksheet titles, including titles that happen to resemble cells.
Range origins keep partial reads (e.g. B3:AZ) aligned with absolute sheet cells.
"""

from __future__ import annotations

import re
from collections.abc import Sequence


def letters(index: int) -> str:
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise ValueError("column index must be a positive integer")
    out = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        out = chr(65 + remainder) + out
    return out


def index(column: str) -> int:
    if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z]+", column):
        raise ValueError("column letters must contain only A-Z")
    out = 0
    for char in column.upper():
        out = out * 26 + ord(char) - 64
    return out


def quote(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


def cell(sheet: str, row: int, col: int) -> str:
    if isinstance(row, bool) or not isinstance(row, int) or row < 1:
        raise ValueError("row index must be a positive integer")
    return f"{quote(sheet)}!{letters(col)}{row}"


def column(headers: Sequence[str], name: str, *, first_col: int = 1) -> int:
    """Prefer a header over letters; repeated normalized headers are unsafe."""
    matches = [i + first_col for i, h in enumerate(headers) if h.strip().casefold() == name.strip().casefold()]
    if len(matches) > 1:
        raise ValueError(f"ambiguous sheet column {name!r}")
    if matches:
        return matches[0]
    if re.fullmatch(r"[A-Z]+", name):
        return index(name)
    raise ValueError(f"unknown sheet column {name!r}")


def origin(value: str | None) -> tuple[int, int]:
    if value is None:
        return 1, 1
    match = re.fullmatch(r"([A-Za-z]+)([1-9][0-9]*)?(?::([A-Za-z]+)([1-9][0-9]*)?)?", value)
    if not match:
        raise ValueError("read_range must be an A1 range without a worksheet title")
    col, row = index(match[1]), int(match[2] or 1)
    if match[3] and (index(match[3]) < col or (match[4] and int(match[4]) < row)):
        raise ValueError("read_range ends before it starts")
    return row, col
