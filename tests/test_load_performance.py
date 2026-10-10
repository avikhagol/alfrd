"""Project loading stays bounded: folder listings counted, not timed."""

from __future__ import annotations

import os
import shutil
from collections import Counter
from pathlib import Path

import pytest

from alfrd import avica_layout
from alfrd.avica_layout import collect_studio_files, result_csvs, scan_layout
from alfrd.studio_defs import (
    ScanCache,
    collect_log_files,
    collect_ms_paths,
    studio_context,
)

TREE = Path(__file__).parent / "fixtures" / "avica_tree"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(TREE, root)
    red = root / "reductions"
    # Deep folders no pattern names: measurement sets, scratch, hidden, archives.
    for deep in (
        red / "RDV41" / "wd" / "wd_X_0742+103" / "VLBI_X.ms" / "ANTENNA" / "sub",
        red / "RDV41" / "wd" / "tmp_files" / "a" / "b",
        red / "RDV41.ms" / "x",
        red / ".snapshot" / "RDV41" / "wd",
        red / "archive" / "old" / "deeper" / "still",
    ):
        deep.mkdir(parents=True)
        (deep / "stray.csv").write_text("x\n")
    (red / "RDV41" / "wd" / "result__0742+103__RDV41__wd.csv").write_text("a\n")
    (red / "result__1309+555__BV019__wd_1.csv").write_text("a\n")
    return root


@pytest.fixture
def scans(monkeypatch):
    """Paths passed to os.scandir (pathlib's iterdir/glob use it too)."""
    seen: Counter[str] = Counter()
    real = os.scandir

    def counting(path="."):
        seen[os.fspath(path)] += 1
        return real(path)

    monkeypatch.setattr(os, "scandir", counting)
    return seen


def _rglob_result_csvs(root: Path, target_dir: Path, patterns, known):
    """The pre-optimisation discovery: every CSV up to three parts deep."""
    base = root.resolve()
    pats = patterns
    groups = {"workdirname": avica_layout.workdirname_regex(pats["workdir"])}
    rel_target = os.path.relpath(target_dir, base)
    regexes = [avica_layout.pattern_regex(p, {"target_dir": rel_target}, groups) for p in pats["result_csv"]]
    out = []
    for path in sorted(target_dir.rglob("*.csv")):
        if len(path.relative_to(target_dir).parts) > 3:
            continue
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        if avica_layout._most_specific_match(regexes, rel):
            out.append(rel)
    return out


def test_result_csvs_match_full_walk_without_entering_irrelevant_folders(tree, scans):
    base = tree.resolve()
    target_dir = base / "reductions"
    patterns = avica_layout.layout_patterns(base)
    codes = [c.code for c in avica_layout.scan_project_codes(base, target_dir, patterns)]
    scans.clear()
    found = result_csvs(base, target_dir, patterns, codes)
    counts = dict(scans)
    listed = {os.path.relpath(p, target_dir).replace(os.sep, "/") for p in counts}
    assert [i["file"] for i in found] == _rglob_result_csvs(base, target_dir, patterns, codes)
    assert {i["file"] for i in found} == {
        "reductions/0742+103_result.csv",
        "reductions/RDV41/wd/result__0742+103__RDV41__wd.csv",
        "reductions/result__1309+555__BV019__wd_1.csv",
    }
    # Only the target dir, project-code folders and their work dirs are listed.
    assert listed <= {".", "BV019", "RDV41", "archive", "BV019/wd", "BV019/wd_1", "RDV41/wd", "archive/old"}
    assert not any(".ms" in p or ".snapshot" in p or "tmp_files" in p or "wd_X" in p for p in listed)
    assert max(counts.values()) == 1


def test_result_csvs_literal_folder_and_unrooted_patterns(tmp_path):
    root = tmp_path
    red = root / "reductions"
    (red / "results").mkdir(parents=True)
    (red / "results" / "3C84.csv").write_text("a\n")
    (red / ".hidden").mkdir()
    (red / ".hidden" / "3C84.csv").write_text("a\n")
    pats = {k: avica_layout._as_list(v) for k, v in avica_layout.DEFAULT_PATTERNS.items()}
    literal = {**pats, "result_csv": ["{target_dir}/results/{target}.csv"]}
    assert [i["file"] for i in result_csvs(root, red, literal)] == ["reductions/results/3C84.csv"]
    # A pattern naming a hidden folder literally still finds it.
    hidden = {**pats, "result_csv": ["{target_dir}/.hidden/{target}.csv"]}
    assert [i["file"] for i in result_csvs(root, red, hidden)] == ["reductions/.hidden/3C84.csv"]
    # Not rooted at {target_dir}/: no pruning, same three-part bound as before.
    unrooted = {**pats, "result_csv": ["reductions/results/{target}.csv"]}
    assert [i["file"] for i in result_csvs(root, red, unrooted)] == ["reductions/results/3C84.csv"]


def test_collect_studio_files_walks_work_dirs_once(tree, monkeypatch):
    calls = []
    real = avica_layout.scan_project_codes

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(avica_layout, "scan_project_codes", counting)
    data = collect_studio_files(tree, log_tail=0, read=False)
    assert len(calls) == 1
    markers = sorted(f["rel"] for f in data["files"] if f.get("marker"))
    assert markers == [
        "reductions/RDV41/wd/wd_S/.dir",
        "reductions/RDV41/wd/wd_X_0742+103/.dir",
    ]
    # studio_context without a precomputed layout still discovers the work dirs itself.
    ctx = studio_context(tree.resolve())
    assert [w["id"] for w in ctx["workdirs"]] == [c["id"] for c in scan_layout(tree)["project_codes"]]


def test_log_and_ms_patterns_list_each_folder_once(tree, scans):
    base = tree.resolve()
    ctx = studio_context(base)
    plain_logs, plain_ms = collect_log_files(base, ctx), collect_ms_paths(base, ctx)
    scans.clear()
    cache = ScanCache()
    assert collect_log_files(base, ctx, cache) == plain_logs
    assert collect_ms_paths(base, ctx, cache) == plain_ms
    assert scans and max(scans.values()) == 1


def test_full_scan_listing_count_is_bounded(tree, scans):
    collect_studio_files(tree, log_tail=0, read=False)
    # A folder is listed at most once by each of the work-dir walk, the result
    # CSV walk and the (shared) log / ms_path expansions, however many patterns.
    per_dir = Counter(os.path.normpath(p) for p in scans.elements())
    assert max(per_dir.values()) <= 3, per_dir.most_common(5)
    assert not any(".ms" in p or ".snapshot" in p for p in scans)
    # Scratch folders are never descended into (the work-dir walk may list
    # their first level, which is bounded; nothing below it).
    assert not any("tmp_files/a" in p or "archive/old/deeper" in p for p in scans)
