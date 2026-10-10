"""Shared sheet-value → plan-target resolver for Studio and sync.

File lists use ALFRD's comma/whitespace splitting and lexical path normalization.
Matching is case-sensitive, independent of filesystem contents, and requires all
listed files to belong to one target. Basenames are never guessed.
"""
from __future__ import annotations

import os
from collections.abc import Sequence

from alfrd.runtime import plan_csv

from .mapping import MappingError


def files(value: str) -> frozenset[str]:
    return frozenset(os.path.normpath(name) for name in plan_csv.join_files(value).split(",") if name)


class Resolver:
    def __init__(self, rows: Sequence[plan_csv.PlanRow], mode: str = "target", *, with_code: bool = False):
        if mode not in {"target", "files"}:
            raise MappingError("rows.match_against must be target or files")
        self.rows, self.mode, self.with_code = rows, mode, with_code
        self.names = {row.key: files(row.files) for row in rows} if mode == "files" else {}

    def sample(self, value: str, code: str = "") -> dict:
        names = files(value) if self.mode == "files" else frozenset()
        candidates = [row for row in self.rows
                      if (not self.with_code or row.code == code.strip())
                      and (bool(names) and names <= self.names[row.key] if self.mode == "files"
                           else row.target == value.strip())]
        identities = {plan_csv.row_key(row.target, row.code if self.with_code else "") for row in candidates}
        return {"value": value, "status": "ambiguous" if len(candidates) > 1 else "matched" if candidates else "unmatched",
                "target": candidates[0].target if len(candidates) == 1 else "",
                "key": next(iter(identities)) if len(candidates) == 1 else ""}

    def key(self, value: str, code: str = "") -> str | None:
        result = self.sample(value, code)
        if result["status"] == "ambiguous":
            raise MappingError("sheet row matches multiple targets; correct filenames or configure rows.code_column")
        return result["key"] or None
