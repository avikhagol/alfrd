# AVICA -> ALFRD migration notes (workstream `0.2.9.0`)

This document is produced by the `agent/alfrd-avica-compat` lane. It only
changes ALFRD (`tests/avica_compat/`); `/home/avi/intelligence/gh/avica` was
read for reference only and is never modified by this lane.

## What this fixture proves

`tests/avica_compat/` is a domain-neutral pipeline (`pipeline.py`), an
AVICA-shaped manifest (`alfrd.yaml`), a documented alias module
(`aliases.py`), and a contract test suite (`test_avica_compat.py`) that
exercises, using only public ALFRD imports:

- no-argument step and validator construction (`StepCls()`),
- class-based validators wired through `validate_by`,
- AVICA-compatible parameter precedence: step-qualified explicit > global
  explicit > context > merged config > callable default,
- structured `StepResult`/`BatchResult` returns,
- crash-snapshot (`CrashSnapshotAdapter`) and result-CSV (`ResultCSVAdapter`)
  hooks,
- multi-dataset execution against one shared `LogFrame` via
  `LogFrameEventSink`,
- manifest `path`/`glob` artifact-pattern discovery for result CSVs, logs,
  and diagnostic plots,
- an optional domain inspector that returns generic metadata without ALFRD
  importing CASA or AVICA.

No CASA, FITS, MS, rPICARD, or AVICA import appears anywhere in
`tests/avica_compat/`.

## Documented downstream aliases

Once ALFRD's public contracts are adopted, AVICA can add a small compatibility
module (e.g. `avica/pipe/alfrd_compat.py`) with:

```python
from alfrd.core.pipeline import PipelineCore as AvicaPipelineCore
from alfrd.core.pipeline import BatchResult as AvicaResult
from alfrd.core.logframe import LogFrame as LogFramework
from alfrd.core.config import BaseConfig, Config, CONFIG_MAPPING
```

This fixture's `tests/avica_compat/aliases.py` demonstrates the exact same
import statements and asserts (`test_documented_avica_aliases_resolve_to_alfrd_public_names`)
that they resolve to the live ALFRD classes.

## Manifest compatibility

AVICA's real `alfrd.yaml` (read-only reference at
`/home/avi/intelligence/gh/avica/alfrd.yaml`) uses:

```yaml
name: avica pipeline
entrypoint:
  - name: runner
    cmd: [avica, pipe, run]
  - name: config
    cmd: [avica, pipe, config]
schema:
  - name: steps
    definitions:
      - avica.pipe.steps
```

ALFRD's current `src/alfrd/manifest.py` / `project-manifest-v1.schema.json`
already validate exactly this `name`/`entrypoint`/`schema` shape (with
`entrypoint[].name`/`cmd` and `schema[].name`/`definitions`), plus an
additional optional `artifacts` array with `name`/`path_pattern`/
`description`/`media_type`. `tests/avica_compat/alfrd.yaml` is derived from
AVICA's real manifest (same field names and structure, fixture-specific
values) with an `artifacts` block added to prove path/glob discovery.
AVICA's manifest continues to validate as-is; adding `artifacts` is optional
and purely additive.

Note: the roadmap document (`docs/plans/ALFRD_0.2_ROADMAP.md`) sketches a
richer aspirational manifest shape for `0.2.9.0` (`project:`, `datasets:`,
`workflows:`, `providers:`) that is not what the *implemented*
`alfrd.core.manifest` / schema v1 accepts today. This fixture intentionally
targets the manifest shape that ALFRD actually validates right now
(`name`/`entrypoint`/`schema`/`artifacts`), which is also what AVICA's real
`alfrd.yaml` already uses. The richer manifest shape remains a documented gap
(see below) for a future manifest-schema iteration, not something this
lane invents or hard-codes.

## Parameter precedence: contract match and one implementation nuance

`alfrd.core.pipeline.PipelineCore.resolve_parameters` implements the same
5-level precedence AVICA's `avica.pipe.core.AvicaPipelineCore.get_kwargs`
implements informally:

1. step-qualified explicit (`"stepname.param"` in `provided_pipe_params`,
   here surfaced through `run(..., params=...)` / constructor
   `provided_pipe_params`),
2. global explicit (`"param"` in the same explicit mapping),
3. pipeline context (`PipelineContext`/`self.context`),
4. merged configuration (`self.config`, built from one mapping or several
   layered mappings),
5. callable default (the step/validator function's own default value).

AVICA's real implementation reads `PipelineContext.params` (a *class-level*
mutable dict shared by all `AvicaPipelineCore` instances) merged with
`self.pipe_params`/`self.provided_pipe_params` (instance-level). ALFRD's
`PipelineContext` is instance-scoped (a `MutableMapping` you pass to the
constructor), matching roadmap architectural decision 5 ("pipeline context
and run state are instance-scoped so simultaneous runs cannot overwrite
global state"). This is an intentional, documented behavior change, not a
bug: AVICA-side migration should stop relying on `PipelineContext` as a
process-wide singleton and instead thread one `PipelineContext` instance
per `AvicaPipelineCore`/`PipelineCore` instance.

## AVICA-side gaps this fixture surfaces (informational; not fixed here)

These remain in AVICA and are **not** changed by this lane (scope boundary:
"this milestone changes ALFRD only"):

1. **Class-level `PipelineContext`.** AVICA's `PipelineContext` uses
   `@classmethod`s and class attributes (`params: Dict = {}` shared across
   all steps/instances). Migrating to `alfrd.core.pipeline.PipelineContext`
   means AVICA must switch every `PipelineContext.params.update(...)` call
   to an instance obtained from (or passed into) `PipelineCore`, and any
   code that reads `PipelineContext.params` directly from module scope needs
   a reference to the active pipeline/context instance instead.
2. **`StepResult` shape differs.** AVICA's `avica.pipe.core.StepResult` is a
   flat dataclass with `success: List[bool]`, `success_count`,
   `failed_count`, `start_stamp`, `end_stamp`, `detail: Dict`,
   `desc: List[str]`. ALFRD's `alfrd.core.pipeline.StepResult` uses
   `status: str` (`"succeeded"/"failed"/"skipped"`), `error:
   PipelineError | None`, `artifacts: list[ArtifactRef]`, and exposes
   `success_count`/`failed_count`/`start_stamp`/`end_stamp`/`detail`/`desc`
   as *computed properties* for source compatibility (see
   `alfrd/core/pipeline.py:313-347`). AVICA code that constructs
   `StepResult(name=..., success_count=..., success=[True], ...)` directly
   must be adapted to either return artifacts/values from `step.run()` (and
   let `PipelineCore` build the `StepResult`) or construct
   `alfrd.core.pipeline.StepResult` with `status=`/`dataset_id=`/`step_name=`
   instead of AVICA's `name=`/`success_count=`/`success=[...]`.
3. **`AvicaResult` is a `UserList[StepResult]` with `to_polars()`/
   `summary()`.** ALFRD's `BatchResult` groups results per dataset
   (`BatchResult.datasets[i].steps`) rather than as a flat list, and has no
   built-in Polars conversion. AVICA call sites that iterate
   `self.allresults` as a flat list of `StepResult` need to iterate
   `batch_result.step_results` (a flattened property ALFRD already
   provides) instead, and `to_polars()`/`summary()` become an AVICA-side
   adapter function written against `step_results`, not part of ALFRD.
4. **`LogFramework` vs `LogFrame` constructor keyword differences.** AVICA's
   `LogFramework(primary_colname=..., primary_value=..., csv_file=...,
   working_col=..., polars=bool, gsc=...)` differs slightly from
   `alfrd.core.logframe.LogFrame(source=None, primary_value="",
   primary_colname="FILE_NAME", csv="", *, gsc=None, backend="pandas")`:
   the boolean `polars=True/False` becomes `backend="polars"`, and
   `csv_file=` becomes the positional `source=`/keyword `csv=`. This is a
   thin, mechanical adapter (already validated compatible in
   `alfrd/core/logframe.py`, which documents "the `gsc` and `csv` keywords
   remain available for older callers"), not a functional gap.
5. **Validator ABC vs plain base class.** AVICA's
   `PipelineStepValidatorBase`/`PipelineStepBase` are `abc.ABC` subclasses
   with `@abstractmethod def run(...)`. ALFRD's equivalents are plain
   classes with a `NotImplementedError`-raising `run`/`execute`/`validate`.
   Subclassing works identically for AVICA's existing step/validator
   classes; the only observable difference is that ALFRD does not raise
   `TypeError` at class-definition time for an incomplete subclass (it
   raises at call time instead).
6. **Manifest richness.** AVICA's real `alfrd.yaml` today only declares
   `name`/`entrypoint`/`schema`. The roadmap's aspirational `0.2.9.0` example
   manifest (`project:`, `datasets:`, `workflows:`, `providers:`,
   `artifacts:` with `id`/`produced_by`/`glob`) is **not implemented** by
   `alfrd.core.manifest`/the packaged JSON Schema today; only a simpler
   `artifacts[].name/path_pattern/description/media_type` array exists. If
   AVICA wants declarative dataset/workflow definitions, ALFRD's manifest
   schema needs a v2 (or additive v1 extension) before AVICA can adopt it;
   until then AVICA-side dataset/workflow declarations stay in AVICA code
   (e.g. `avica.pipe.config.DEFAULT_PARAMS`), not in the shared manifest.
7. **`get_kwargs`/`check_config_requirements` reporting.** AVICA's
   `AvicaPipelineCore.check_config_requirements` produces a diagnostic
   report (`ParamStatus` named tuples showing whether a parameter has a
   default, is in input config, or in context) that has no ALFRD
   equivalent. `PipelineCore.resolve_parameters`/`get_kwargs` resolve values
   but do not currently expose this same diagnostic/reporting surface; an
   AVICA-side wrapper (or a small ALFRD enhancement) would be needed to keep
   the same "missing parameter" warnings AVICA prints today.

## Explicit ALFRD version floor for AVICA integration

AVICA's real manifest (`/home/avi/intelligence/gh/avica/alfrd.yaml`) has no
`minimum_alfrd` field today (it predates the versioned manifest project
section). Per the roadmap's own consolidated-versioning policy, the ALFRD
version floor for eventual AVICA integration is **`alfrd>=0.2.1.0`** (the
consolidated release that ships this compatibility contract, the typed
pipeline engine, `LogFrame`, and the manifest v1 schema all documented
above). AVICA should not attempt the migration against any pre-`0.2.1.0`
ALFRD snapshot, since `PipelineCore`, `LogFrame`, and `CONFIG_MAPPING`/
`Config` did not exist in their final public-API form before this
consolidated release.

## Scope confirmation

This lane's changes are confined to:

- `tests/avica_compat/` (this fixture package: `pipeline.py`, `aliases.py`,
  `alfrd.yaml`, `test_avica_compat.py`, `__init__.py`),
- `docs/plans/AVICA_COMPAT_MIGRATION_NOTES.md` (this file).

`/home/avi/intelligence/gh/avica` was opened read-only for reference and is
verified unmodified (see the final report's `git status`/`git diff`
confirmation for that repository).
