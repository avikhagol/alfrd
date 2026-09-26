
from dataclasses import dataclass, field
from __future__ import annotations
import time
import traceback
from pathlib import Path
from typing import Optional, List, Any
import polars as pl

class LogFramework:
    """
    A DataFrame-backed logging utility for tracking pipeline step results
    against rows identified by a primary key column.

    Supports pandas and polars backends, CSV persistence,
    and optional Google Sheets sync (gspread) without hard dependency.

    Parameters
    ----------
    primary_colname : str
        Column name used as the unique row identifier.
    primary_value : str
        The value in primary_colname that identifies the current row.
    working_col : str
        Default column to read/write when no colname is explicitly given.
    csv_file : str
        Path to the CSV file used as the data source and persistence target.
    polars : bool
        If True, use polars as the internal backend. Falls back to pandas if unavailable.
    gsc : optional
        A gspread-backed connection object (e.g. your existing GSC wrapper).
        Pass None to work in CSV-only mode. No gspread import is required
        unless you actually pass a gsc instance.
    """

    def __init__(
        self,
        primary_colname : str,
        primary_value   : str,
        csv_file        : str,
        working_col     : str,
        polars          : bool = False,
        gsc             : Optional[Any] = None,
    ):
        self.working_col        = working_col
        self.primary_colname    = primary_colname
        self.primary_value      = primary_value
        
        self.csv_file           = Path(csv_file)
        self.gsc                = gsc
        self._use_polars        = polars and HAS_POLARS

        self.working_cols       : List[str] = []
        self.registered         : tuple     = (0, 0)    # (count_success, count_failed)
        self.update_cooldown_count          = 0
        self._t0                            = time.time()

        self._df    = self._load()
        self._df0   = self._snapshot()                  # immutable baseline for diff on sheet update

    # ~~~ loading    ~~~~~~~~~~~~~~~~~~~~~~~~

    def _load(self):
        if self.gsc is not None:
            raw = self.gsc.df
            return pl.from_pandas(raw) if self._use_polars else raw
        if not self.csv_file.exists():
            raise FileNotFoundError(f"CSV not found: {self.csv_file}")
        if self._use_polars:
            return pl.read_csv(self.csv_file)
        return pd.read_csv(self.csv_file)

    def _snapshot(self):
        if self._use_polars:
            return self._df.clone()
        return self._df.copy(deep=True)

    # ~~~~~~ public df properties ~~~~~~

    @property
    def df_sheet(self):
        """mutable sheet obj."""
        return self._df

    @df_sheet.setter
    def df_sheet(self, value):
        self._df = value

    @property
    def df_sheet0(self):
        """shet df for diffs"""
        return self._df0

    # ~~~~~~~~~ polars/ pandas ~~~~~~~~~~~~~~~~~~

    def get_pandas(self) -> "pd.DataFrame":
        if not HAS_PANDAS:
            raise ImportError("pandas is not installed.")
        if self._use_polars:
            return self._df.to_pandas()
        return self._df

    def get_polars(self) -> "pl.DataFrame":
        if not HAS_POLARS:
            raise ImportError("polars is not installed.")
        if not self._use_polars:
            return pl.from_pandas(self._df)
        return self._df

    # ~~~~~~~ row data ~~~~~~~~~~~~~

    def _row_mask(self, expressions: List[str] | None = None):
        """
        Returns a boolean mask (pandas) or filter expression (polars)
        for the current primary_value, optionally AND-ed with extra expressions.
        """
        if self._use_polars:
            mask = pl.col(self.primary_colname).cast(pl.Utf8).str.strip_chars() == self.primary_value
            if expressions:
                for expr in expressions:
                    mask = mask & expr
            return mask

        primary_vals = self._df[self.primary_colname].astype(str).str.strip()
        mask = primary_vals == self.primary_value
        if expressions:
            for expr in expressions:
                extra = self._df.eval(expr)
                mask  = mask & extra
        return mask

    # ~~~~~~~ reading values ~~~~~~~~~~~~~~~~~~

    def get_value(self, colname: str = "", where: List[str] | None = None) -> str:
        """
        Returns the cell value for colname (or working_col) at the current
        primary_value row. Returns empty string when not found.
        """
        colname = colname or self.working_col
        mask    = self._row_mask(where)

        if self._use_polars:
            result = self._df.filter(mask)[colname]
            return str(result[0]).strip() if len(result) else ""

        located = self._df.loc[mask, colname]
        return str(located.values[0]).strip() if located.count() else ""

    def isvalue(self, value: Any, colname: str = "") -> bool:
        """Returns True if the cell value matches `value`."""
        return str(value) == self.get_value(colname)

    def get_working_cols(self) -> List[str]:
        return self.working_cols

    def get_previous_working_col(self) -> str | None:
        if len(self.working_cols) >= 2 and self.working_col in self.working_cols:
            idx = self.working_cols.index(self.working_col) - 1
            return self.working_cols[idx] if idx != -1 else None
        return None

    # ~~~~~~~~~ update data  ~~~~~~~~~~~

    def put_value(
        self,
        value       : Any,
        colname     : str           = "",
        count       : int           = 0,
        where       : List[str] | None = None,
        force       : bool          = False,
    ) -> int:
        """
        Writes `value` into colname (or working_col) for the current primary_value row.
        Skips the write if the cell is already populated and force=False.
        Returns count + 1 on a successful write.
        """
        colname = colname or self.working_col
        mask    = self._row_mask(where)

        if self._use_polars:
            existing = self._df.filter(mask)[colname]
            cell_empty = len(existing) == 0 or str(existing[0]).strip() == ""
            if force or cell_empty:
                self._df = self._df.with_columns(
                    pl.when(mask)
                    .then(pl.lit(str(value)))
                    .otherwise(pl.col(colname))
                    .alias(colname)
                )
                count += 1
            else:
                print(f"skipping put_value — cell not empty: {existing[0]!r}")
            return count

        cell_empty = not self._df.loc[mask, colname].count()
        if force or cell_empty:
            self._df.loc[mask, colname] = value
            count += 1
        else:
            print(f"skipping put_value — cell not empty: {self._df.loc[mask, colname].values!r}")
        return count

    # ~~ persistence ~~~~~~~~

    def update_csv(self, count: int, failed: int, csvfile: str | None = None) -> None:
        """Persists the current frame to CSV if anything changed since last save."""
        if count - self.registered[0] or failed - self.registered[1]:
            out = Path(csvfile) if csvfile else self.csv_file
            if self._use_polars:
                self._df.write_csv(out)
            else:
                self._df.fillna("", inplace=True)
                self._df.to_csv(out, index=False)
            self.registered = (count, failed)
        else:
            print("update_csv: no changes detected, skipping.")

    def update_sheet(
        self,
        count       : int,
        failed      : int,
        by_cell     : bool  = True,
        comment_col : str   = "Comment",
        csvfile     : str | None = None,
    ) -> None:
        """
        Syncs changes to Google Sheets if a gsc instance was provided.
        Falls back to CSV on failure. Rate-limited to stay under 60 req/min.
        No-ops cleanly when gsc is None.
        """
        if self.gsc is None:
            self.update_csv(count, failed, csvfile)
            return

        tf = time.time()
        td = tf - self._t0
        has_changes = count - self.registered[0] or failed - self.registered[1]

        if td <= 1 and self.update_cooldown_count >= 1 and has_changes:
            time.sleep(1)
            self.update_cooldown_count = 0

        try:
            if has_changes:
                df_pd = self.get_pandas()
                df_pd.fillna("", inplace=True)

                if not by_cell:
                    self.gsc.update(df_pd)
                else:
                    import numpy as np
                    df0_pd = self._df0 if not self._use_polars else self._df0.to_pandas()
                    I, J = np.where(df_pd.astype(str).ne(df0_pd.astype(str)))
                    self.gsc.update_cell(df_pd, I, J)

                self.update_cooldown_count += 1
                self.registered = (count, failed)
            else:
                print("update_sheet: no changes, skipping.")
        except Exception as e:
            traceback.print_exc()
            print(f"update_sheet: failed — {e}")
            self.put_value(f"failed:{e}", colname=comment_col, force=True)
            self.update_csv(count, failed, csvfile)

        self._t0 = time.time()