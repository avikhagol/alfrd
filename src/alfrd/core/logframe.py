"""Tabular execution log with local and adapter-backed storage."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from io import IOBase
from typing import Any, Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class LogFrameAdapter(Protocol):
    """Storage adapter consumed by :class:`LogFrame`."""

    df: Any

    def update(self, dataframe: Any) -> Any:
        """Replace the remote table with ``dataframe``."""

    def update_cell(
        self, dataframe: Any, rows: Sequence[int], columns: Sequence[int]
    ) -> Any:
        """Update the specified zero-based cells from ``dataframe``."""


class LogFrame:
    """Manage a pandas or Polars execution log.

    ``source`` may be an in-memory dataframe, a path, an open file object, or an
    adapter implementing ``df``, ``update`` and ``update_cell``. Pandas is used
    unless ``backend="polars"`` is requested or a Polars dataframe is supplied.
    The ``gsc`` and ``csv`` keywords remain available for older callers.
    """

    def __init__(
        self,
        source: Any = None,
        primary_value: Any = "",
        primary_colname: str = "FILE_NAME",
        csv: Any = "",
        *,
        gsc: LogFrameAdapter | None = None,
        backend: str = "pandas",
    ) -> None:
        if gsc is not None:
            if source is not None:
                raise TypeError("pass either source or gsc, not both")
            source = gsc
        if csv not in ("", None):
            if source is not None:
                raise TypeError("pass either source/gsc or csv, not both")
            source = csv

        backend = backend.lower()
        if backend not in {"pandas", "polars"}:
            raise ValueError("backend must be 'pandas' or 'polars'")

        self.adapter = source if self._is_adapter(source) else None
        self.gsc = self.adapter  # historical name
        table_source = self.adapter.df if self.adapter is not None else source
        self._backend = self._infer_backend(table_source, backend)
        self._df = self._load(table_source)
        self._baseline = self._copy_frame(self._df)
        self.df_sheet0 = self._baseline  # historical inspection attribute
        self.csvmode = self.adapter is None and isinstance(
            source, (str, os.PathLike, IOBase)
        )
        self.primary_value = primary_value
        self.primary_colname = primary_colname
        self._working_col = ""
        self.working_cols: list[str] = []
        self.t0 = time.time()
        self.registered = (0, 0)
        self.update_cooldown_count = 0

    @staticmethod
    def _is_adapter(value: Any) -> bool:
        return (
            value is not None
            and hasattr(value, "df")
            and callable(getattr(value, "update", None))
            and callable(getattr(value, "update_cell", None))
        )

    @staticmethod
    def _polars() -> Any:
        try:
            import polars as pl
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise ImportError(
                "Polars support requires the optional 'polars' package"
            ) from exc
        return pl

    @classmethod
    def _infer_backend(cls, source: Any, requested: str) -> str:
        if source is not None:
            try:
                pl = cls._polars()
            except ImportError:
                pl = None
            if pl is not None and isinstance(source, pl.DataFrame):
                return "polars"
        return requested

    def _load(self, source: Any) -> Any:
        if self._backend == "polars":
            pl = self._polars()
            if source is None:
                return pl.DataFrame()
            if isinstance(source, pl.DataFrame):
                return source
            if isinstance(source, pd.DataFrame):
                return pl.from_pandas(source)
            if isinstance(source, (str, os.PathLike, IOBase)):
                return pl.read_csv(source)
            raise TypeError(f"unsupported LogFrame source: {type(source).__name__}")

        if source is None:
            return pd.DataFrame()
        if isinstance(source, pd.DataFrame):
            return source
        if isinstance(source, (str, os.PathLike, IOBase)):
            return pd.read_csv(source)
        raise TypeError(f"unsupported LogFrame source: {type(source).__name__}")

    @staticmethod
    def _copy_frame(dataframe: Any) -> Any:
        if isinstance(dataframe, pd.DataFrame):
            return dataframe.copy(deep=True)
        return dataframe.clone()

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def df(self) -> Any:
        return self._df

    @df.setter
    def df(self, dataframe: Any) -> None:
        self._df = dataframe
        self._backend = self._infer_backend(dataframe, "pandas")
        if self.adapter is not None:
            self.adapter.df = dataframe

    @property
    def df_sheet(self) -> Any:
        """Legacy alias for :attr:`df`."""
        return self.df

    @df_sheet.setter
    def df_sheet(self, dataframe: Any) -> None:
        self.df = dataframe

    @property
    def working_col(self) -> str:
        return self._working_col

    @working_col.setter
    def working_col(self, colname: str) -> None:
        self._working_col = colname
        if colname and (not self.working_cols or self.working_cols[-1] != colname):
            self.working_cols.append(colname)

    @property
    def is_googlesheet(self) -> bool:
        """Whether this frame is adapter-backed (historical property name)."""
        return self.adapter is not None

    @staticmethod
    def _normalise_key(value: Any) -> str:
        return str(value).strip()

    def _column_values(self, colname: str) -> list[Any]:
        if colname not in self.df.columns:
            raise KeyError(colname)
        if self.backend == "pandas":
            return self.df[colname].tolist()
        return self.df.get_column(colname).to_list()

    def _matching_positions(self, primary_value: Any = None) -> list[int]:
        value = self.primary_value if primary_value is None else primary_value
        if value is None or self._normalise_key(value) == "":
            raise ValueError("a primary value is required")
        wanted = self._normalise_key(value)
        return [
            index
            for index, candidate in enumerate(self._column_values(self.primary_colname))
            if self._normalise_key(candidate) == wanted
        ]

    def primary_row_index(self, primary_value: Any = None) -> int | None:
        """Return the unique zero-based row position for a primary value."""
        positions = self._matching_positions(primary_value)
        if not positions:
            return None
        if len(positions) > 1:
            value = self.primary_value if primary_value is None else primary_value
            raise ValueError(f"primary value {value!r} is not unique")
        return positions[0]

    find_primary_row = primary_row_index

    def get_primary_row(self, primary_value: Any = None) -> Any:
        """Return the unique primary row, or ``None`` when it is absent."""
        position = self.primary_row_index(primary_value)
        if position is None:
            return None
        if self.backend == "pandas":
            return self.df.iloc[position]
        return self.df.row(position, named=True)

    def ensure_row(
        self,
        primary_value: Any = None,
        values: Mapping[str, Any] | None = None,
        **fields: Any,
    ) -> int:
        """Return an existing primary row or append it when missing."""
        if isinstance(primary_value, Mapping):
            if values is not None:
                raise TypeError("values cannot accompany a row mapping")
            row = dict(primary_value)
            row.update(fields)
            primary_value = row.get(self.primary_colname, self.primary_value)
        else:
            row = dict(values or {})
            row.update(fields)
            primary_value = (
                self.primary_value if primary_value is None else primary_value
            )
            row.setdefault(self.primary_colname, primary_value)

        if primary_value is None or self._normalise_key(primary_value) == "":
            raise ValueError("a primary value is required")
        row[self.primary_colname] = primary_value
        existing = self.primary_row_index(primary_value)
        if existing is not None:
            return existing

        if self.backend == "pandas":
            self.df = pd.concat([self.df, pd.DataFrame([row])], ignore_index=True)
            return len(self.df) - 1

        pl = self._polars()
        addition = pl.DataFrame([row])
        self.df = pl.concat([self.df, addition], how="diagonal_relaxed")
        return self.df.height - 1

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None or value == "":
            return True
        try:
            return bool(pd.isna(value))
        except (TypeError, ValueError):
            return False

    def _filtered_position(
        self, primary_value: Any, expressions: Sequence[Any]
    ) -> int | None:
        position = self.primary_row_index(primary_value)
        if position is None or not expressions:
            return position
        if self.backend != "pandas":
            raise NotImplementedError("where expressions currently require pandas")
        candidate = self.df.iloc[[position]]
        for expression in expressions:
            if callable(expression):
                candidate = candidate.loc[expression(candidate)]
            else:
                candidate = candidate.query(str(expression))
        return position if not candidate.empty else None

    def _set_cell(self, position: int, colname: str, value: Any) -> None:
        if self.backend == "pandas":
            if colname not in self.df.columns:
                self.df[colname] = pd.NA
            self.df.iat[position, self.df.columns.get_loc(colname)] = value
            return

        pl = self._polars()
        if colname not in self.df.columns:
            self.df = self.df.with_columns(pl.lit(None).alias(colname))
        self.df = self.df.with_columns(
            pl.when(pl.int_range(0, self.df.height, eager=False) == position)
            .then(pl.lit(value))
            .otherwise(pl.col(colname))
            .alias(colname)
        )

    def col_data(
        self,
        colname: str = "",
        data: Any = "",
        count: int = 0,
        force: bool = False,
        chk_colname: str = "",
        expressions: Sequence[Any] | None = None,
        *,
        primary_value: Any = None,
    ) -> Any:
        """Read or update the selected primary row.

        Writes fill an empty cell by default; ``force=True`` replaces a value.
        The historical counter-oriented return values are preserved.
        """
        target_col = colname or self.working_col
        value = self.primary_value if primary_value is None else primary_value
        position = self._filtered_position(value, expressions or ())
        if chk_colname:
            if position is None:
                return count, ""
            located = self._value_at(position, chk_colname)
            if self._is_empty(located):
                return count, ""
            return count + 1, str(located).strip()
        if not target_col:
            raise ValueError("a column name or working_col is required")
        if position is None:
            return count
        current = (
            self._value_at(position, target_col)
            if target_col in self.df.columns
            else None
        )
        if force or self._is_empty(current):
            self._set_cell(position, target_col, data)
            return count + 1
        return count

    def _value_at(self, position: int, colname: str) -> Any:
        if self.backend == "pandas":
            return self.df.iloc[position][colname]
        return self.df.get_column(colname)[position]

    def isval_unique(self, colname: str = "") -> bool:
        target_col = colname or self.working_col
        value = self.get_value(target_col)
        return (
            sum(
                self._normalise_key(candidate) == self._normalise_key(value)
                for candidate in self._column_values(target_col)
            )
            == 1
        )

    def get_value(
        self,
        colname: str = "",
        where: Sequence[Any] | None = None,
        *,
        primary_value: Any = None,
    ) -> str:
        target_col = colname or self.working_col
        _, value = self.col_data(
            chk_colname=target_col,
            expressions=where,
            primary_value=primary_value,
        )
        return str(value).strip()

    def get_previous_working_col(self) -> str | None:
        if len(self.working_cols) < 2:
            return None
        return self.working_cols[-2]

    def isvalue(self, value: Any, colname: str = "") -> bool:
        return str(value) == self.get_value(colname)

    def put_value(
        self,
        value: Any,
        colname: str = "",
        count: int = 0,
        where: Sequence[Any] | None = None,
        *,
        force: bool = False,
        primary_value: Any = None,
    ) -> int:
        return self.col_data(
            colname=colname,
            data=value,
            count=count,
            force=force,
            expressions=where,
            primary_value=primary_value,
        )

    @staticmethod
    def _values_equal(left: Any, right: Any) -> bool:
        try:
            if bool(pd.isna(left)) and bool(pd.isna(right)):
                return True
        except (TypeError, ValueError):
            pass
        try:
            result = left == right
            return bool(result)
        except (TypeError, ValueError):
            return False

    def _shape(self, dataframe: Any) -> tuple[int, int]:
        if self.backend == "pandas":
            return dataframe.shape
        return dataframe.height, dataframe.width

    def _changed_cells(self) -> tuple[list[int], list[int]] | None:
        old_rows, _ = self._shape(self._baseline)
        new_rows, new_columns = self._shape(self.df)
        if list(self._baseline.columns) != list(self.df.columns) or new_rows < old_rows:
            return None

        rows: list[int] = []
        columns: list[int] = []
        for row in range(new_rows):
            for column in range(new_columns):
                if row >= old_rows or not self._values_equal(
                    self._cell(self._baseline, row, column),
                    self._cell(self.df, row, column),
                ):
                    rows.append(row)
                    columns.append(column)
        return rows, columns

    def _cell(self, dataframe: Any, row: int, column: int) -> Any:
        if self.backend == "pandas":
            return dataframe.iat[row, column]
        return dataframe.item(row, column)

    def _refresh_baseline(self) -> None:
        self._baseline = self._copy_frame(self.df)
        self.df_sheet0 = self._baseline

    def sync(self, *, by_cell: bool = True) -> bool:
        """Send changed data to the adapter and refresh the comparison baseline."""
        if self.adapter is None:
            return False
        changes = self._changed_cells()
        if changes is not None and not changes[0]:
            return False
        if by_cell and changes is not None:
            rows, columns = changes
            self.adapter.update_cell(self.df, rows, columns)
        else:
            self.adapter.update(self.df)
        self._refresh_baseline()
        self.update_cooldown_count += 1
        self.t0 = time.time()
        return True

    def update_sheet(
        self,
        count: int = 0,
        failed: int = 0,
        by_cell: bool = True,
        comment_col: str = "Comment4",
        csvfile: str | os.PathLike[str] = "df_sheet.csv",
    ) -> bool:
        """Compatibility wrapper around :meth:`sync`.

        On adapter failure, retain the legacy local CSV backup behavior while
        leaving the baseline untouched so a later call can retry the changes.
        """
        try:
            changed = self.sync(by_cell=by_cell)
        except Exception as exc:
            if self.primary_value not in (None, ""):
                try:
                    self.col_data(comment_col, f"failed:{exc}", count=failed)
                except (KeyError, ValueError):
                    pass
            if self.backend == "pandas":
                self.df.to_csv(csvfile, index=False)
            else:
                self.df.write_csv(csvfile)
            return False
        if changed:
            self.registered = (count, failed)
        return changed


__all__ = ["LogFrame", "LogFrameAdapter"]
