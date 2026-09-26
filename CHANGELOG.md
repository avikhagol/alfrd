# Changelog

## [0.2.1.0]

### Changed

- The web UI is now called **ALFRD Studio** (was "AVICA & ALFRD Workflow
  Studio"); the header logo uses `web/assets/favicon.svg`.

### Fixed

- CI / PyPI: the `dev` extra now installs Flask (GUI tests failed to import it
  in a clean `pip install ".[dev]"`), pytest only collects `tests/`, and the
  workflows run `twine check` on the built distributions.
- GitHub Pages workflow moved to `.github/workflows/pages.yml`.
- `alfrd serve` started in a project folder shows only that project; every
  project ever connected stays in `~/.alfrd/runtime.sqlite` and used to appear
  too. `--all-projects` shows them all; `alfrd projects list` /
  `alfrd projects forget NAME` manage them (database rows only). The Studio
  settings list them with a *Forget* button, and a *Quit* button stops
  `alfrd serve` (loopback + CSRF; `POST /api/studio/projects/<p>/forget`,
  `POST /api/studio/quit`). Quit closes the Studio tab when the browser
  allows it; view switches no longer add browser-history entries, so the tab
  `alfrd serve` opened stays closable.

### Added

- **Studio driven by alfrd.yaml** (`alfrd.studio_defs`, `web/js/data/defs.js`):
  `template: avica` (defaults in `web/assets/templates/avica.yaml`), per-step
  `label`/`category`/`stage`/`description`/`icon`, `metadata:` (Metadata
  health replaces the HDU checks), `logs:` (collapsible, scrollable step logs
  with a full-screen view; new **Logs** workspace grouping step logs and
  `kind: log` artifacts), `overview.ms_path` (MS Storage Path column) and
  `project_settings.field_aliases` (replaces the built-in VASCO → AVICA rules).
  **Project settings** edits and saves alfrd.yaml; the Workflow inspector shows
  step parameters from `alfrd avica summary` and writes changes to avica.inp.
  Results default to the most recent attempt per step with a *Full history*
  switch; the Workflow opens in list view. Demo data only with
  `alfrd serve --demo` / `alfrd studio --demo`; `alfrd serve` connects the
  alfrd.yaml in the current folder (or `--project DIR`). New endpoints:
  `GET /api/studio/projects/<p>/scan`, `GET …/file?path=` (declared logs
  only), `POST …/manifest`, `POST /api/studio/avica/<p>/config`.
- **AVICA & ALFRD Workflow Studio** (`alfrd.web`): a client-side, light-mode
  web UI (vanilla ES modules, no runtime dependencies) with Overview grid and
  target drawer, Workflow canvas (node graph, mini-map, inspector, browser
  simulation), Metadata HDU health checks, Results analytics and ALFRD
  project/validator configuration. Served by `alfrd serve` at `/studio/`
  (now the default page; `/dashboard/` is kept), by the new dependency-free
  `alfrd studio` command, or from GitHub Pages (`.github/workflows/pages.yml`,
  `alfrd studio --export DIR`). Legacy VASCO step names are resolved to AVICA
  with alias warnings. New JSON endpoints: `GET /api/studio/session`,
  `POST /api/projects/connect`.
- **AVICA tree reader** (`alfrd.avica_layout`, import-free): resolves
  `target_dir` from `avica pipe config --summary` / `avica.inp`, finds AVICA
  project-code work dirs, `avica.meta/`, rPicard templates and
  `picard_input_template_update` overrides, and `avica.logs/`. Layout patterns
  (`workdir`, `band_dir`, `meta_dir`, `input_templates`, `result_csv`) are read
  from alfrd.yaml's `avica:` block. New CLI `alfrd avica summary|scan` and
  Studio endpoints `/api/studio/avica/<project>/{layout,workdir,summary,logs/<name>}`.
  The Studio separates ALFRD projects from AVICA project codes, lets you attach
  work folders to targets, and remembers view state.
  Opening a project folder is a targeted scan (File System Access API): the
  Studio reads alfrd.yaml first and opens only the artifacts it points to,
  never listing measurement sets; the folder is remembered for **Re-scan** and
  for reading logs after a reload. `alfrd avica scan ROOT --bundle scan.json`
  packs the same files for importing a tree from another machine.

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
