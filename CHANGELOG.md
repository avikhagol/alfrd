# Changelog

All notable changes to ALFRD are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and version numbers follow the four-component scheme adopted for the `0.2.x`
series (see `docs/plans/ALFRD_0.2_ROADMAP.md`).

## [0.2.1.0] - Unreleased (release candidate)

`0.2.1.0` consolidates the work previously distributed across the proposed
`0.2.2.0`-`0.2.10.0` milestones into a single release. It is the first
release candidate suitable for real ALFRD workflows and subsequent AVICA
integration.

### Added

- **Typed pipeline engine** (`alfrd.core.pipeline`): `PipelineCore`,
  `PipelineContext`, `PipelineStepBase`, `PipelineStepValidatorBase`,
  `PipelineStepValidatorResult`, `StepResult`, `DatasetResult`, `BatchResult`,
  `ArtifactRef`, `ColName`, execution lifecycle events (`RunStarted`,
  `DatasetStarted`, `StepStarted`, `StepSucceeded`, `StepFailed`,
  `StepSkipped`, `DatasetFinished`, `RunFinished`), and adapters
  (`CrashSnapshotAdapter`, `ResultCSVAdapter`).
- **Project Manifest v1** (`alfrd.manifest`): typed, import-free loading and
  JSON Schema validation of `alfrd.yaml` (`ProjectManifest`, `Entrypoint`,
  `SchemaDefinition`/`ProjectSchema`, `ArtifactDefinition`,
  `discover_manifest`, `load_manifest`, `parse_manifest`, `validate_manifest`).
- **Consumer repository registration** (`alfrd.repository`): `RepositoryService`
  and function-style `add_repository`/`sync_repository`/`inspect_repository`,
  maintaining an ALFRD-owned index without writing into consumer repos.
- **Canonical LogFrame** (`alfrd.core.logframe`): one maintained tabular
  execution log with pandas (default) and optional Polars backends,
  filesystem/`IOBase`/in-memory construction, an explicit `LogFrameAdapter`
  protocol, `ensure_row`, and `LogFrameEventSink` for projecting pipeline
  events into a status column per step.
- **Durable runtime persistence** (`alfrd.runtime`): SQLite-backed
  `RuntimeStore`/`RuntimeService` covering projects, versioned workflow and
  step definitions, datasets, runs, step executions, artifacts (checksummed),
  and an audit trail; atomic `<run directory>/.alfrd/run.json` manifest
  writes and recovery; stale-running-process detection
  (`recover_stale`); `resume_run`/`retry_run`; dataset CSV import/export;
  `LocalSubprocessWorker` for local, shell-free step execution;
  `RuntimePipelineRunner`/`RuntimeEventSink`/`CompositeEventSink` adapters
  bridging typed pipeline execution into durable runtime state.
- **Web application foundation** (`alfrd.gui`): installable, testable Flask
  application factory (`create_app`) with health/version endpoints, read-only
  project/workflow/step/validator/parameter/dataset-column/artifact-definition
  catalog routes and dashboard templates, a narrow `CatalogReader` protocol
  with both a standalone Flask-SQLAlchemy catalog
  (`SqlAlchemyCatalogReader`) and a `RuntimeCatalogReader` projecting the
  durable runtime state through the same contract, and `alfrd serve`/`alfrd
  gui` CLI commands.
- Packaged JSON Schema for the Project Manifest
  (`alfrd/schemas/project-manifest-v1.schema.json`).
- Deprecation warnings on the legacy `PipelineRun` (`alfrd.plugins`) and
  `WorkflowManager`/`Workflow` (`alfrd.core.workflow`) compatibility
  facades, pointing callers to `PipelineCore`.
- Security hardening: project-directory and run-working-directory
  identifiers are validated against path traversal
  (`alfrd.core.project.project_directory`,
  `alfrd.runtime.service._validate_run_directory_component`).
- `RELEASE_CHECKLIST.md` documenting the release-candidate verification
  procedure.

### Changed

- `requires-python` and PyPI classifiers now reflect the actually supported
  range (3.10-3.13).
- README examples now match the currently importable public API; historical
  examples for unavailable APIs were removed or updated.
- GitHub Actions now install and test across the supported Python matrix.

### Fixed

- The `logger.py` -> `logging.py` transition is complete; `alfrd.core.logging`
  is the one documented logging entry point, and `alfrd.core.logger` is a
  deprecated compatibility shim that warns on import.
- Tests no longer touch the real `~/.alfrd/projects` directory; `ALFRD_HOME`
  is redirected to an isolated temporary directory for every test via
  `tests/conftest.py`.
- The shadowed `src/alfrd/core.py` experiment was removed; `alfrd.core` is
  reliably a package, not a module (see `tests/test_public_api.py`).
- Flask templates, static assets, and the SQL schema file are packaged with
  the wheel (`tests/test_package_data.py`, `tests/test_wheel.py`).

### Security

- `alfrd.core.project.project_directory` rejects project names containing
  path separators, `..`, or absolute paths, confining all project state to
  `ALFRD_HOME/projects`.
- `alfrd.runtime.service.RuntimeService.create_run` rejects a caller-supplied
  `run_id` that would escape `<project_root>/runs/` when no explicit
  `working_directory` is given.
- Flask routes never import project Python to answer a 404; catalog lookups
  are metadata-only (`alfrd.gui.routes`).

### Known limitations / deferred

- No PyPI package has been published for `0.2.1.0`; this is a release
  candidate pending sibling-lane merges (matrix, execctl, artifacts,
  avica-compat) and human sign-off. See `RELEASE_CHECKLIST.md`.
- The browser control plane (start/resume/retry/cancel from the GUI) is
  scoped to sibling workstreams not yet merged into this branch.
- No `uv.lock` is committed; the project intentionally supports installation
  via plain `pip`/`build` rather than a locked workspace.
