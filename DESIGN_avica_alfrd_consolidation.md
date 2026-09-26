# Design: avica ↔ alfrd Consolidation + Multi-Row Batch Execution

## Context and Motivation

avica has two components that duplicate logic already in alfrd:

| Component | avica (current) | alfrd (current) | Winner |
|-----------|----------------|-----------------|--------|
| Sheet/CSV frame | `LogFramework` (config.py) | `LogFrame` (lib.py) | **avica's** — more complete (polars, rate-limit, `io.IOBase`) |
| Step sequencer | `AvicaPipelineCore` (core.py) | `PipelineRun` / `WorkflowManager` (plugins.py / core/workflow.py) | **avica's** — richer (crash snapshots, structured validators, timing) |

Goal: promote avica's implementations into alfrd so that alfrd is the canonical engine and avica registers its domain-specific steps on top.

Secondary goal: make multi-row batch execution possible — currently one `avica pipe run` call = one `TARGET_NAME`. The sheet already has all rows; we need a way to iterate over them.

---

## Current data-flow (single row)

```
avica pipe run --t TARGET_NAME --f FILENAME --configfile avica.inp
        |
        v
run_pipeline() [cli_new.py]
  builds pipe_params{primary_value=TARGET_NAME, fitsfilenames=[...]}
        |
        v
AvicaPipeline(pipe_params)  [main.py -> core.py]
  PipelineContext.params ← pipe_params   (thread-local/global state)
  LogFramework (lf) ← default StringIO   ← THE PROBLEM for multi-row
        |
        v
AvicaPipelineCore.execute()
  for step in self._steps:
      validate_before → step.run() → validate_after
      UpdateResults.run()   ← writes timestamp, appends to per-target result.csv
      UpdateSheet.run()     ← pushes lf row to Google Sheet (if gsc != None)
```

Two CSVs involved:
- **Sheet / tracking frame** (`lf`): rows=datasets, cols=pipeline steps. Mirrored to Google Sheets.
- **Per-target run log** (`{target}_result.csv`): rows=steps, cols=desc/success/start/end.

The `lf` is re-created from a blank `StringIO` every run. That's why sharing the sheet across rows breaks.

---

## Phase 1 — Multi-row batch (avica-only, no alfrd changes)

This is purely an avica-side addition. No alfrd changes needed yet.

### New: `AvicaBatch` in `avica/pipe/main.py`

```python
class AvicaBatch:
    def __init__(self, pipe_params: dict, steps=None):
        self.base_params = pipe_params
        self.steps = steps or AvicaPipeline.DEFAULT_STEPS
        self.lf = self._build_lf(pipe_params)   # built ONCE, shared across rows

    def _build_lf(self, params) -> LogFramework:
        # if sheet_url in params → build gsc, return LogFramework(gsc=gsc, ...)
        # else → LogFramework(csv_file=params['csv_file'], ...)
        ...

    def targets_from_file(self, path: str) -> list[str]:
        # one TARGET_NAME value per line, strips blanks/comments
        return [line.strip() for line in Path(path).read_text().splitlines()
                if line.strip() and not line.startswith('#')]

    def targets_from_sheet(self) -> list[str]:
        # reads primary_colname column from lf, returns non-empty values
        primary_col = self.base_params.get('primary_colname', 'TARGET_NAME')
        return list(self.lf.df_sheet[primary_col].dropna().unique())

    def resolve_filename(self, target: str) -> list[str]:
        filename_col = self.base_params.get('filename_col', 'FILENAMES')
        self.lf.primary_value = target
        val = self.lf.get_value(filename_col)
        if val:
            return [f.strip() for f in val.split(',')]
        return self.base_params.get('fitsfilenames', [])

    def run(self, targets: list[str], resume: bool = False) -> dict:
        results = {}
        for target in targets:
            self.lf.primary_value = target
            params = {
                **self.base_params,
                "primary_value"  : target,
                "target"         : target,
                "fitsfilenames"  : self.resolve_filename(target),
                "lf"             : self.lf,        # SHARED — one sheet, many rows
                "result_csv_file": str(_result_csv_path({**self.base_params, "target": target})),
            }
            pipe = AvicaPipeline(pipe_params=params, steps=self.steps)
            if resume:
                step = _infer_resume_step(Path(params["result_csv_file"]),
                                          pipe.step_names())
                if step is None:
                    continue   # already complete
                pipe.filter_steps(*pipe.steps_from(step))
            results[target] = pipe.execute()
        return results
```

### CLI additions in `avica/pipe/cli_new.py`

```python
@pipeline_app.command("run")
def run_pipeline(
    ...existing args...,
    targets_file : Optional[str] = typer.Option(None,  "--targets-file",
        help="Textfile with one TARGET_NAME per line."),
    all_rows     : bool          = typer.Option(False, "--all-rows",
        help="Run for every row found in the connected sheet/CSV."),
):
    ...

    if targets_file or all_rows:
        batch = AvicaBatch(pipe_params=pipe_params, steps=stps or None)
        targets = (batch.targets_from_file(targets_file) if targets_file
                   else batch.targets_from_sheet())
        batch.run(targets, resume=resume)
    else:
        ...existing single-row path...
```

Key invariant: **single-row path is untouched** — no risk of regression.

### `PipelineContext` and the shared `lf`

`PipelineContext` is module-level global state (a class with class vars). Currently `lf` in it gets reset on each `execute()` call. That's fine — `AvicaBatch` passes a fresh per-row `lf` reference into `pipe_params`, and `execute()` reads it from there into `PipelineContext`. The sheet object (`gsc`) underneath is the same Python object across all rows, so mutations accumulate correctly.

No changes needed to `PipelineContext` itself.

---

## Phase 2 — Promote `LogFramework` into alfrd

### What moves

`avica/pipe/config.py:LogFramework` → `alfrd/lib.py` replacing the existing `LogFrame`.

Differences to reconcile:

| Feature | alfrd `LogFrame` | avica `LogFramework` | Action |
|---------|-----------------|---------------------|--------|
| Backend | pandas only | pandas + polars | Keep polars opt-in |
| CSV source | path only | path / `io.IOBase` / None | Keep IOBase support |
| Update rate-limit | none | `update_cooldown_count` + sleep | Keep |
| `get_previous_working_col` | yes | yes | Keep |
| `working_cols` list | no | yes | Keep |
| GSC wrapper | fully commented-out | fully commented-out | Leave as gsc protocol (duck-typed) |

Rename to `LogFrame` for consistency. avica then does:

```python
# avica/pipe/config.py
from alfrd.lib import LogFrame as LogFramework   # alias keeps avica internals unchanged
```

### alfrd package exports

Add to `alfrd/__init__.py`:

```python
from alfrd.lib import LogFrame
```

---

## Phase 3 — Promote `AvicaPipelineCore` into alfrd

### What moves

`avica/pipe/core.py:AvicaPipelineCore` → `alfrd/core/pipeline.py` (new file)

The generic parts (step sequencing, crash snapshots, pre/post validators, timing, result CSV) all belong in alfrd. The avica-specific parts stay in avica.

### Split

**Goes to alfrd (`alfrd/core/pipeline.py`):**
- `StepResult`, `AvicaResult` → rename `StepResult`, `BatchResult`
- `ColName`, `PipelineContext`
- `PipelineStepBase`, `PipelineStepValidatorBase`
- `AvicaPipelineCore` → rename `PipelineCore`
- `append_step_result_csv`
- `step_stage` context manager

**Stays in avica:**
- `UpdateResults`, `UpdateSheet` — depend on `LogFramework`/`lf`
- All domain steps (`ImportFITSIdi`, `FringeFit`, etc.)
- `WorkDirMeta`, `InitVariables`, `CasaSetup`, `ColValidation`, `RowValidation`
- `GenerateAndAppendAntab`, `PicardCMD`, `CasaTask*`, subprocess runners

**avica/pipe/main.py after:**
```python
from alfrd.core.pipeline import PipelineCore
from .steps import ...

class AvicaPipeline(PipelineCore):
    DEFAULT_STEPS = [...]
    ...
```

### Relationship to existing alfrd `WorkflowManager` / `PipelineRun`

`PipelineRun` (plugins.py) and `WorkflowManager` (core/workflow.py) are an older, simpler version of the same loop. After Phase 3:

- `PipelineRun` → **deprecate**, replace with `PipelineCore` wherever alfrd projects use it
- `WorkflowManager` → **deprecate** in favour of `PipelineCore` + `Project`
- Both can be kept as thin re-exports or thin wrappers during transition so existing alfrd users aren't broken

---

## Migration order

```
Phase 1  (avica only)
  ├─ AvicaBatch in main.py
  ├─ --targets-file / --all-rows in cli_new.py
  └─ shared-lf wiring (pass lf via pipe_params, don't reset)

Phase 2  (alfrd + avica)
  ├─ Upgrade alfrd LogFrame with avica's LogFramework body
  ├─ avica imports LogFrame from alfrd
  └─ delete LogFramework from config.py

Phase 3  (alfrd + avica)
  ├─ Move PipelineCore + support types to alfrd/core/pipeline.py
  ├─ avica inherits from alfrd.core.pipeline.PipelineCore
  ├─ Deprecate PipelineRun / WorkflowManager
  └─ Update alfrd tests + any existing alfrd step definitions
```

Each phase ships independently. Phase 1 unblocks multi-row execution now; Phases 2–3 are refactors that can happen after.

---

## Open questions

1. **Sheet pre-population**: is the sheet already populated with all `TARGET_NAME` rows before a batch run, or does `AvicaBatch` need to call `ensure_row(target)` to create missing rows? This affects `targets_from_sheet` — if it reads only existing rows it's fine; if new targets arrive externally it needs to append rows.

2. **Failure policy for batch**: when one target fails mid-pipeline (e.g. exception), does the batch continue to the next target or abort? Current single-row behaviour is to set `validation_success=False` and skip remaining steps *for that target*, then write `alfrd.failed`. Batch should continue to next target and collect all failures at the end.

3. **alfrd step registry conflict**: `PipelineCore` uses instance-level `_steps` dict. alfrd's existing `REGISTERED_STEPS` is a global dict decorated at import time. Phase 3 needs to decide whether `PipelineCore` pulls from `REGISTERED_STEPS` or keeps its own class-list pattern — the class-list is cleaner for typed steps.
