"""Keep served filenames safe for default content blockers, without network access."""

from pathlib import Path

import pytest

from alfrd.web import web_root

# EasyPrivacy snapshot, 2026-11-10, easyprivacy_general.txt; these are plain
# substring network filters, with no anchors, wildcards, or resource options.
# Source: https://ublockorigin.github.io/uAssets/thirdparties/easyprivacy.txt
BLOCKING_PATTERNS = (
    "/logview.js",      # line 1553: the Studio startup failure
    "/logview_new.js",  # line 1555
    "/log-view.js",     # line 1455
    "/logstat.js",      # line 1551
    "/logcollect.js",   # line 1521
)
RISKY_SUBSTRINGS = (
    "log", "track", "analytics", "stats", "beacon", "pixel", "ad",
    "metrics", "telemetry", "collect", "counter",
)


def asset_name_issues(path: str) -> list[str]:
    url_path = "/" + path.lstrip("/").lower()
    basename = Path(url_path).name
    return [f"filter {rule}" for rule in BLOCKING_PATTERNS if rule in url_path] + [
        f"basename contains {word}" for word in RISKY_SUBSTRINGS if word in basename
    ]


def test_served_asset_names_are_blocker_safe():
    root = web_root()
    issues = []
    for file in root.rglob("*"):
        rel = file.relative_to(root)
        # Local Python, linter and default-project state are not shipped assets.
        if (not file.is_file() or file.suffix in {".py", ".pyc"}
                or any(part in {"__pycache__", ".ruff_cache", ".alfrd"} for part in rel.parts)):
            continue
        issues.extend(f"{rel.as_posix()}: {issue}" for issue in asset_name_issues(rel.as_posix()))
    assert not issues, "\n".join(issues)


@pytest.mark.parametrize("rule", BLOCKING_PATTERNS)
def test_guard_catches_filter_even_in_directory_path(rule):
    # A harmless basename must not conceal a blocking substring in a URL path.
    assert f"filter {rule}" in asset_name_issues(f"js{rule}/panel.js")


@pytest.mark.parametrize("word", RISKY_SUBSTRINGS)
def test_guard_catches_risky_basename(word):
    assert f"basename contains {word}" in asset_name_issues(f"js/{word.upper()}_panel.js")
