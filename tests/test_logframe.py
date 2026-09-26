from __future__ import annotations

from io import StringIO
from pathlib import Path

import pandas as pd
import pytest


class RecordingAdapter:
    def __init__(self, df):
        self.df = df
        self.full_updates = []
        self.cell_updates = []

    def update(self, dataframe):
        self.full_updates.append(dataframe.copy())

    def update_cell(self, dataframe, rows, columns):
        self.cell_updates.append((dataframe.copy(), list(rows), list(columns)))


class FailsOnceAdapter(RecordingAdapter):
    def update_cell(self, dataframe, rows, columns):
        if not self.cell_updates:
            self.cell_updates.append("failed")
            raise RuntimeError("temporary outage")
        super().update_cell(dataframe, rows, columns)


def test_logframe_has_canonical_and_legacy_imports():
    from alfrd import LogFrame as TopLevelLogFrame
    from alfrd.core import LogFrame as CoreLogFrame
    from alfrd.core.logframe import LogFrame
    from alfrd.lib import LogFrame as LegacyLogFrame

    assert TopLevelLogFrame is LogFrame
    assert CoreLogFrame is LogFrame
    assert LegacyLogFrame is LogFrame


def test_pandas_is_default_for_empty_path_stream_and_memory(tmp_path: Path):
    from alfrd.core.logframe import LogFrame

    empty = LogFrame()
    assert isinstance(empty.df, pd.DataFrame)
    assert empty.df.empty

    csv_path = tmp_path / "log.csv"
    csv_path.write_text("FILE_NAME,status\na.fits,pending\n")
    from_path = LogFrame(csv_path)
    from_stream = LogFrame(StringIO("FILE_NAME,status\nb.fits,done\n"))
    memory = pd.DataFrame({"FILE_NAME": ["c.fits"], "status": ["new"]})
    from_memory = LogFrame(memory)

    assert from_path.get_value("status", primary_value="a.fits") == "pending"
    assert from_stream.get_value("status", primary_value="b.fits") == "done"
    assert from_memory.df is memory


def test_adapter_mode_needs_no_csv_and_exposes_df_alias():
    from alfrd.core.logframe import LogFrame

    adapter = RecordingAdapter(pd.DataFrame({"FILE_NAME": ["a.fits"], "status": [""]}))
    frame = LogFrame(adapter, primary_value="a.fits")

    assert frame.adapter is adapter
    assert frame.gsc is adapter
    assert frame.df is adapter.df
    assert frame.df_sheet is adapter.df


def test_primary_lookup_and_ensure_row():
    from alfrd.core.logframe import LogFrame

    frame = LogFrame(
        pd.DataFrame({"FILE_NAME": [" a.fits "], "status": ["done"]}),
        primary_value="a.fits",
    )

    assert frame.primary_row_index() == 0
    assert frame.get_primary_row()["status"] == "done"
    assert frame.ensure_row() == 0
    assert frame.ensure_row("b.fits", status="pending") == 1
    assert frame.primary_row_index("b.fits") == 1
    assert frame.get_value("status", primary_value="b.fits") == "pending"

    with pytest.raises(ValueError, match="not unique"):
        LogFrame(pd.DataFrame({"FILE_NAME": ["x", "x"]})).primary_row_index("x")


def test_working_column_assignment_tracks_ordered_history():
    from alfrd.core.logframe import LogFrame

    frame = LogFrame()
    frame.working_col = "download"
    frame.working_col = "calibrate"
    frame.working_col = "download"

    assert frame.working_cols == ["download", "calibrate", "download"]
    assert frame.get_previous_working_col() == "calibrate"


def test_put_value_obeys_fill_only_and_force_modes():
    from alfrd.core.logframe import LogFrame

    frame = LogFrame(
        pd.DataFrame({"FILE_NAME": ["a.fits"], "status": ["existing"]}),
        primary_value="a.fits",
    )

    assert frame.put_value("ignored", "status", count=2) == 2
    assert frame.get_value("status") == "existing"
    assert frame.col_data("status", "forced", count=2, force=True) == 3
    assert frame.get_value("status") == "forced"


def test_changed_cell_sync_refreshes_baseline():
    from alfrd.core.logframe import LogFrame

    adapter = RecordingAdapter(
        pd.DataFrame(
            {"FILE_NAME": ["a.fits", "b.fits"], "status": ["pending", "pending"]}
        )
    )
    frame = LogFrame(adapter)
    frame.df.loc[1, "status"] = "done"

    assert frame.sync() is True
    assert len(adapter.cell_updates) == 1
    _, rows, columns = adapter.cell_updates[0]
    assert rows == [1]
    assert columns == [1]

    assert frame.sync() is False
    assert len(adapter.cell_updates) == 1

    frame.df.loc[0, "status"] = "done"
    assert frame.sync() is True
    assert adapter.cell_updates[-1][1:] == ([0], [1])


def test_full_sync_and_legacy_update_sheet_refresh_baseline():
    from alfrd.core.logframe import LogFrame

    adapter = RecordingAdapter(pd.DataFrame({"FILE_NAME": ["a.fits"], "status": [""]}))
    frame = LogFrame(adapter)
    frame.df.loc[0, "status"] = "done"

    assert frame.update_sheet(1, 0, by_cell=False) is True
    assert len(adapter.full_updates) == 1
    assert frame.update_sheet(2, 0, by_cell=False) is False
    assert len(adapter.full_updates) == 1


def test_failed_legacy_sync_keeps_changes_retryable_and_writes_backup(tmp_path: Path):
    from alfrd.core.logframe import LogFrame

    adapter = FailsOnceAdapter(
        pd.DataFrame({"FILE_NAME": ["a.fits"], "status": ["pending"]})
    )
    frame = LogFrame(adapter)
    frame.df.loc[0, "status"] = "done"
    backup = tmp_path / "backup.csv"

    assert frame.update_sheet(1, 0, csvfile=backup) is False
    assert backup.is_file()
    assert frame.sync() is True
    assert adapter.cell_updates[-1][1:] == ([0], [1])


def test_optional_polars_backend(tmp_path: Path):
    pl = pytest.importorskip("polars")
    from alfrd.core.logframe import LogFrame

    csv_path = tmp_path / "log.csv"
    csv_path.write_text("FILE_NAME,status\na.fits,pending\n")
    frame = LogFrame(csv_path, backend="polars", primary_value="a.fits")

    assert isinstance(frame.df, pl.DataFrame)
    assert frame.get_value("status") == "pending"
    frame.put_value("done", "status", force=True)
    assert frame.get_value("status") == "done"
    assert frame.ensure_row({"FILE_NAME": "b.fits", "status": "new"}) == 1
