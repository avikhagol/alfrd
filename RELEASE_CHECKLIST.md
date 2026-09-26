# ALFRD 0.2.1.0 Release Checklist

This checklist operationalizes the `0.2.10.0 - Hardening and release
candidate` workstream in `docs/plans/ALFRD_0.2_ROADMAP.md`. It was executed on
`agent/alfrd-release`, an isolated worktree based on integrated commit
`71fc61f` ("Integrate manifest pipeline runtime logframe and web services").
Items marked **blocked** require the four sibling lanes (matrix, execctl,
artifacts, avica-compat) to merge first, or require a human decision this
lane cannot make unilaterally (PyPI publication, tagging).

## 1. Code review and API freeze

- [x] Public API surface reviewed and frozen for `0.2.x` — see the
      "Public API surface (0.2.1.0)" section of `README.md`.
- [x] `__all__` lists audited in `alfrd/__init__.py`, `alfrd/core/__init__.py`,
      `alfrd/core/pipeline.py`, `alfrd/manifest.py`, `alfrd/core/manifest.py`,
      `alfrd/repository.py`, `alfrd/core/logframe.py`, `alfrd/lib.py`,
      `alfrd/runtime/__init__.py`, `alfrd/gui/services.py` — all consistent
      with what is documented and covered by `tests/test_public_api.py`.
- [x] Deprecation warnings added for legacy compatibility classes not
      already covered: `alfrd.plugins.PipelineRun` and
      `alfrd.core.workflow.WorkflowManager`/`Workflow`. Both emit
      `DeprecationWarning` on construction; `alfrd.core.logger` already
      warned on import. Verified: constructing the module-level
      `alfrd.Pipeline` singleton at import time does **not** itself warn
      (`tests/test_deprecations.py::test_importing_alfrd_does_not_warn_for_module_singleton`).

## 2. Hardening: subprocess cleanup and stale-process handling

- [x] `LocalSubprocessWorker` behavior under timeout, non-zero exit, and a
      missing executable is tested
      (`tests/test_worker_cleanup.py`). Confirmed: a `subprocess.run(...,
      timeout=...)` `TimeoutExpired` and an `OSError` from a missing binary
      are both converted into a `FAILED` step-execution transition rather
      than propagating an exception; the run itself is left `RUNNING` for a
      caller-owned finalization decision, matching the documented "worker
      executes one selected step; it does not orchestrate a workflow"
      contract in `alfrd.runtime.protocols.StepWorker`.
- [x] `RuntimeService.recover_stale` marks both the run and any still
      `RUNNING` step execution as `INTERRUPTED` when the run's heartbeat is
      older than the configured threshold (already implemented; added a
      dedicated regression test covering the execution-level side-effect,
      `tests/test_worker_cleanup.py::test_recover_stale_marks_orphaned_running_execution_interrupted`).

## 3. Cancellation, restart, and concurrency edge cases

- [x] `tests/test_runtime_concurrency.py` added, covering:
  - Cancelling a pending vs. a running run, and that cancellation is
    terminal (cannot be restarted directly; must go through `retry_run`).
  - A cancelled/failed/interrupted run can be retried as a new linked run
    (`parent_run_id` set); a succeeded run cannot.
  - A run cannot transition to `SUCCEEDED` while any step execution is
    still pending/running (guards a state-loss defect class explicitly
    called out by the roadmap exit criteria).
  - A step execution cannot start `RUNNING` while its parent run is not
    `RUNNING`.
  - Restart-after-interruption resumes only the unfinished step (new
    attempt), and does not recreate the already-succeeded step.
  - Two runs of the same workflow+dataset remain independent; transitioning
    one does not affect the other.
  - Eight concurrent threads creating runs for distinct datasets against the
    same `RuntimeStore`/SQLite backend all succeed with unique run ids and
    exactly one run per dataset (exercises the store's short
    transaction-scoped session boundary under concurrent writers).
- [ ] **Not covered in this lane:** true multi-process concurrent runs (only
      multi-threaded, single-process concurrency was exercised here, since a
      full multi-process supervisor is part of the not-yet-merged
      matrix/execctl lanes). SQLite's `PRAGMA journal_mode=WAL` (already
      configured in `RuntimeStore._configure_sqlite`) supports concurrent
      readers/writers across processes, but this was not separately
      load-tested.

## 4. Security review

Reviewed: `alfrd/core/project.py` (dynamic project loading, project
directory resolution), `alfrd/core/inspect.py` (`Executor.run_entrypoint`,
`resolve_path_from_schema`), `alfrd/runtime/service.py` and
`alfrd/runtime/worker.py` (command execution), `alfrd/gui/routes.py`
(request-driven lookups).

- [x] `project_directory()` already rejected path separators, `..`, and
      absolute project names (pre-existing; re-verified with
      `tests/test_security.py::test_project_directory_rejects_traversal_names`).
- [x] **Found and fixed:** `RuntimeService.create_run()` joined a
      caller-supplied or generated `run_id` directly into
      `<project_root>/runs/<run_id>` whenever no explicit
      `working_directory` was given, with no validation on `run_id`. A
      `run_id` containing `..`, an absolute path, or path separators could
      write run state, the `.alfrd/run.json` manifest, and recorded
      artifacts outside the project's directory tree. Fixed by
      `alfrd.runtime.service._validate_run_directory_component`, applied in
      `create_run` when `working_directory` is not explicitly supplied (an
      explicit `working_directory` is caller-controlled and out of scope
      for this specific traversal). Regression tests:
      `tests/test_security.py`.
- [x] `LocalSubprocessWorker.execute()` runs `execution.command_json` as an
      argument list via `subprocess.run(..., shell=False)` (the default) —
      no shell interpolation of untrusted strings. Command lists come from
      `StepDefinition.command_json`, itself validated as a non-empty string
      sequence in `RuntimeService.create_workflow`.
- [x] `Executor.run_entrypoint` (`alfrd/core/inspect.py`) similarly invokes
      `subprocess.run(cmd)` with a list built from the manifest's `cmd` plus
      caller-forwarded CLI args — no shell involved. This is unchanged
      from before this lane; flagging it here for the record since the
      roadmap explicitly calls out command execution paths for review.
- [x] `resolve_path_from_schema` (`alfrd/core/inspect.py`) resolves schema
      dotted names to files via `root.rglob(...)`, constrained to matches
      that are relative to `root` (the manifest's directory) — no traversal
      outside the manifest tree was found in this path, since
      `match.relative_to(root)` raises and is caught for anything outside.
- [x] `RepositoryService` reads/writes its index via `Path(...).expanduser()`
      and atomic temp-file replace; no unvalidated user-controlled path
      component is joined onto a privileged root outside of `create_run`
      above.
- [x] Flask routes (`alfrd/gui/routes.py`) never import or execute project
      Python to answer a 404 — the security boundary called out by roadmap
      `0.2.6.0` was already satisfied and remains so; verified by reading
      `_project_or_404`/`_item_or_404`, which only call the `CatalogReader`
      protocol (metadata-only).
- [ ] **Not reviewed in depth in this lane (belongs to avica-compat / matrix
      lanes once merged):** artifact-viewer path containment for the
      not-yet-implemented GUI execution/artifact-serving routes described in
      roadmap `0.2.8.0` ("Artifact viewers reject unsupported paths and do
      not expose files outside registered run/project roots"); those routes
      do not exist yet on this branch.

## 5. Optional dependency groups install/import cleanly

Verified in isolated `uv venv` environments (Python 3.11), each installing
`.` with exactly one extras combination and importing the corresponding
public surface with no other extras present:

- [x] base (`pip install .`): `import alfrd` succeeds; `alfrd.gui` import
      fails cleanly (`ModuleNotFoundError` on `flask`), consistent with GUI
      being optional.
- [x] `alfrd[gui]`: `alfrd.gui.create_app()` succeeds.
- [x] `alfrd[polars]`: `LogFrame(backend="polars")` succeeds without
      requiring pandas-only code paths to break.
- [x] `alfrd[google]`: the four Google/gspread packages install and import;
      no ALFRD module unconditionally imports them (`alfrd.core.logframe`
      only imports gspread-family packages through a caller-supplied
      adapter, never directly).
- [x] `alfrd[all]`: base + gui + polars + google all importable together.

See section 7 below for the exact commands and captured output.

## 6. Source distribution and wheel across supported Python versions

- [x] `python -m build` (sdist + wheel) succeeds from this worktree.
- [x] Wheel installs and smoke-tests cleanly in fresh venvs for Python
      3.10, 3.11, 3.12, and 3.13 (`uv venv --python 3.1x`).
- [x] `python -m compileall src` succeeds with no syntax errors across the
      matrix.
- [x] `git diff --check` reports no whitespace errors.

See section 7 for exact commands and captured output per version.

## 7. Exact commands and captured output

Re-run these from a clean checkout of `agent/alfrd-release` to reproduce.
Results captured during this hardening pass (2026-09-12):

```bash
# Full test suite (dev + gui + polars extras), Python 3.11
uv venv .venv-dev --python 3.11
source .venv-dev/bin/activate
uv pip install -e ".[dev,gui,polars]"
python -m pytest -q
# => 97 passed, 8 warnings (8x DeprecationWarning from tests/test_workflow.py
#    exercising the now-deprecated WorkflowManager; expected and by design)
python -m compileall -q src
# => exit 0, no output
git diff --check
# => exit 0, no output

# Full test suite re-run on every other supported Python version
# (fresh uv venv + `pip install -e ".[dev,gui,polars]"` each time)
#   Python 3.10 -> 97 passed, 8 warnings, 52.54s
#   Python 3.12 -> 97 passed, 8 warnings, 52.81s
#   Python 3.13 -> 97 passed, 8 warnings, 47.20s

# Optional dependency groups, isolated (fresh uv venv per group, Python 3.11)
#   base    (`pip install .`)        -> import alfrd OK; create_app() raises
#                                        ImportError as expected without flask
#   gui     (`pip install .[gui]`)   -> create_app().test_client().get("/health")
#                                        -> 200 {"status": "ok"}
#   polars  (`pip install .[polars]`)-> LogFrame(backend="polars") OK, ensure_row OK
#   google  (`pip install .[google]`)-> google.auth, googleapiclient.discovery,
#                                        gspread, gspread_formatting all import OK
#   all     (`pip install .[all]`)  -> base + gui + polars + google together OK

# sdist + wheel build (Python 3.11)
python -m build
# => Successfully built alfrd-0.2.1.0.tar.gz and alfrd-0.2.1.0-py3-none-any.whl

# Per-Python-version wheel install + smoke test (PipelineCore run + packaged
# JSON schema resource check + `alfrd --help`), fresh uv venv per version
#   Python 3.10 -> WHEEL SMOKE TEST OK, alfrd --help OK
#   Python 3.11 -> WHEEL SMOKE TEST OK, alfrd --help OK
#   Python 3.12 -> WHEEL SMOKE TEST OK, alfrd --help OK
#   Python 3.13 -> WHEEL SMOKE TEST OK, alfrd --help OK

# uv lock --check
uv lock --check
# => error: Unable to find lockfile at `uv.lock`, but `--check` was provided.
#    (expected: this project has no committed uv.lock; see item 9.4 below)
```


## 8. Documentation

- [x] README updated: legacy `GSC`-based examples (referring to a class that
      no longer exists in this codebase) replaced with the current
      `LogFrameAdapter` protocol pattern; a "Public API surface (0.2.1.0)"
      section added; deprecation notice added next to the `Pipeline`
      singleton example.
- [x] `CHANGELOG.md` created, documenting the consolidated `0.2.1.0` feature
      set already present on `agent/alfrd-release`.
- [x] This `RELEASE_CHECKLIST.md` created.

## 9. Remaining blockers only resolvable after sibling lanes merge

These cannot be completed from this isolated worktree without depending on
work that is explicitly out of scope per the task instructions:

1. **matrix / execctl / artifacts / avica-compat lane merges.** The
   `0.2.7.0` sheet-like matrix, `0.2.8.0` browser execution controls
   (start/resume/retry/cancel from the GUI, artifact viewers with path
   containment), and `0.2.9.0` AVICA compatibility fixture are scoped to
   sibling lanes and are not present on `agent/alfrd-release`. Their
   security review (in particular, GUI artifact-viewer path containment)
   and their own test suites must be merged and re-verified together with
   this lane before the consolidated exit criteria in
   `docs/plans/ALFRD_0.2_ROADMAP.md` ("`0.2.10.0`") can be fully satisfied.
2. **PyPI release candidate publication.** Explicitly out of scope per this
   task's instructions ("do not push, do not tag, do not publish"). The
   sdist/wheel are build-verified locally only.
3. **Full multi-process concurrency load test.** Only multi-threaded,
   single-process concurrency against `RuntimeService`/`RuntimeStore` was
   exercised (section 3). A true multi-process supervisor is part of the
   execctl lane.
4. **`uv lock`.** This project has no committed `uv.lock`/workspace lock
   file; it is designed to install via `pip`/`build` against
   `pyproject.toml` version ranges rather than a pinned lock. `uv lock
   --check` was attempted and is reported in the final summary; if the
   other lanes introduce a lock file convention, this lane should adopt it
   consistently rather than inventing its own.
