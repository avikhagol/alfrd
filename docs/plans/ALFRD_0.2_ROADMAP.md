# ALFRD 0.2 Release Roadmap

> **For Hermes:** Use the writing-plans and software-development-lifecycle skills to turn each milestone into a task-level implementation plan before changing code.

**Goal:** Deliver ALFRD `0.2.1.0` as the canonical workflow engine and a lightweight browser-based control plane suitable for AVICA. The work previously distributed across proposed releases `0.2.2.0`–`0.2.10.0` is consolidated into this version for design and implementation.

**Architecture:** ALFRD owns generic typed pipeline execution, validation, configuration, run persistence, tabular status, and the web interface. AVICA remains the owner of CASA, FITS-IDI/MS, calibration, and other radio-astronomy behavior, and will later consume ALFRD through compatibility imports and adapters.

**Tech Stack:** Python 3.10+, Typer, pandas with optional Polars, SQLite, SQLAlchemy, Flask, Jinja, Bootstrap, HTMX or simple browser polling, pytest.

---

## Status and scheduling policy

- `0.2.1.0` is now one consolidated feature release rather than ten sequential releases.
- The former release milestones remain below as independently reviewable workstreams and quality gates, but they do not change the package version.
- Usage examples are specified before implementation so command names, manifest fields, APIs, and GUI behavior can be revised through discussion.
- Scope may be reordered when implementation findings justify it, but the architectural decisions below remain the default.
- Until the ALFRD contracts are stable, work is restricted to the ALFRD repository. AVICA is read-only and serves as a compatibility reference.

| Release | Consolidated scope | Estimated effort |
|---|---|---:|
| `0.2.1.0` | Baseline stabilization, typed pipeline engine, failure semantics, LogFrame, Project Manifest, persistence/resume, web application, sheet-like matrix, browser controls, artifacts, AVICA compatibility contract, and release hardening | 42–59 person-days |

The estimate assumes implementation follows the usage-design review. Near-full-time work is approximately 9–12 weeks; a part-time schedule should expect approximately 12–18 weeks. Intermediate workstream checkpoints may be tagged internally, but the public package version remains `0.2.1.0` until the consolidated acceptance criteria pass.

---

## Fixed architectural decisions

1. ALFRD is the canonical generic workflow engine.
2. AVICA retains all radio-astronomy and calibration-specific behavior.
3. Typed step and validator classes are the primary execution API.
4. Decorator/global registries may remain as compatibility adapters, but are not the engine's internal state model.
5. Pipeline context and run state are instance-scoped so simultaneous runs cannot overwrite global state.
6. The GUI is a workflow run-control matrix, not a general spreadsheet application.
7. SQLite is the initial source of truth for projects, runs, datasets, steps, and execution state.
8. CSV and Google Sheets are import/export or synchronization adapters, not the primary run-state model.
9. The first GUI remains server-rendered Flask/Jinja with Bootstrap and polling or HTMX; no separate JavaScript application is required.
10. Workflow execution occurs outside the Flask request process, initially using local subprocesses.
11. Distributed scheduling, cluster resource management, formulas, collaborative spreadsheet editing, and spreadsheet scripting are outside the `0.2.x` scope.
12. Each consumer repository may own a versioned `alfrd.yaml` Project Manifest describing its workflows, datasets, entrypoints, and expected artifacts.
13. ALFRD validates project manifests without importing or executing project Python code.
14. Static project manifests describe what a project may produce; ALFRD-generated run manifests record what a particular run actually produced.
15. ALFRD never silently patches consumer source repositories. `project add` and `project sync` update ALFRD's registration and database state; repository changes require an explicit command and user intent.

---

## Project Manifest and artifact discovery

### File ownership and terminology

- **Project Manifest:** user-owned YAML stored in the consumer repository, preferably at `alfrd.yaml`. ALFRD may also discover `.alfrd.yaml` and `.alfrd/project.yaml` for compatibility.
- **Project Manifest Schema:** ALFRD-owned, versioned JSON Schema used to validate the YAML manifest and provide editor completion.
- **Run Manifest:** ALFRD-generated JSON stored with a run, recording resolved datasets, steps, statuses, outputs, and artifacts.
- **ArtifactRef:** ALFRD's normalized reference to one output produced for a dataset and step.

The existing AVICA file `/home/avi/intelligence/gh/avica/alfrd.yaml` is the compatibility starting point. Its current `name`, `entrypoint`, and Python `schema.definitions` fields must remain readable while the v1 manifest is introduced.

### Static project manifest

The repository-owned manifest declares:

- project identity, package, description, and minimum ALFRD version,
- named command entrypoints,
- dataset primary key and typed columns,
- workflows and ordered steps,
- command argument templates,
- expected artifacts and the steps that produce them,
- generic viewers or optional project-provided inspectors.

Example target structure:

```yaml
alfrd_manifest: "1.0"

project:
  id: avica
  name: AVICA
  package: avica
  minimum_alfrd: "0.2.9.0"

entrypoints:
  run:
    command: [avica, pipe, run]

datasets:
  primary_key: TARGET_NAME
  columns:
    TARGET_NAME:
      type: string
      required: true
    FILENAMES:
      type: path_list
      required: true
    WORKDIR:
      type: path
      required: true

workflows:
  calibration:
    command:
      entrypoint: run
      arguments: ["--target", "{dataset.TARGET_NAME}", "--workdir", "{dataset.WORKDIR}"]
    steps:
      - id: preprocess_fitsidi
        label: Preprocess FITS-IDI
      - id: fits_to_ms
        label: FITS to MS
      - id: rpicard
        label: Calibration

artifacts:
  - id: result_table
    produced_by: "*"
    path: "{dataset.WORKDIR}/{dataset.TARGET_NAME}_result.csv"
    kind: table
    viewer: table
  - id: pipeline_logs
    produced_by: "*"
    glob: "{dataset.WORKDIR}/avica.logs/*.log"
    kind: log
    viewer: text
  - id: diagnostic_plots
    produced_by: avica_snr
    glob: "{dataset.WORKDIR}/avica.diag/*.png"
    kind: image_collection
    viewer: gallery
```

Exact AVICA output patterns must be confirmed during `0.2.9.0`; ALFRD must not hard-code them.

### Dynamic run manifest

A static manifest cannot know every runtime-generated filename. ALFRD therefore writes a dynamic record such as:

```text
<run-directory>/.alfrd/run.json
```

It records:

- project and manifest versions,
- run and dataset identifiers,
- resolved workflow and parameters,
- each step's status, timing, result, and error,
- resolved artifact paths and metadata,
- whether each artifact exists,
- the event/audit sequence required for restart and resume.

### Artifact contract

The generic execution result may expose normalized artifacts:

```python
@dataclass
class ArtifactRef:
    id: str
    dataset_id: str
    step_id: str
    kind: str
    path: str
    label: str | None = None
    media_type: str | None = None
    exists: bool = False
    size: int | None = None
    metadata: dict = field(default_factory=dict)
```

Initial generic kinds are `file`, `directory`, `table`, `json`, `yaml`, `text`, `log`, `image`, `image_collection`, `html`, and `archive`. A Measurement Set remains a generic directory unless AVICA supplies an optional inspector returning generic metadata. ALFRD must not import CASA to inspect it.

Artifact discovery has three levels:

1. **Declarative:** resolve manifest `path` and `glob` patterns without importing project code.
2. **Structured results:** steps return explicit `ArtifactRef` objects; this is preferred when ALFRD owns the execution.
3. **Optional project inspectors:** trusted project entrypoints inspect domain-specific outputs and return generic JSON metadata. This is an extension point, not an MVP requirement.

### Repository connection flow

Given `alfrd project add /path/to/repository`, ALFRD:

1. Locates the project manifest.
2. Parses and validates it without importing the package.
3. Registers project, workflow, dataset-column, and artifact definitions.
4. Displays the discovered structure in CLI and GUI inspection views.
5. Imports or executes project code only after an explicit run or trusted inspection action.

`alfrd project sync <project>` refreshes ALFRD's stored representation after the repository manifest changes. It does not rewrite the repository.

---

# `0.2.1.0` consolidated workstreams

The version labels in the headings below preserve the original roadmap provenance only. They are no longer intended as published package versions; every workstream is part of `0.2.1.0`.

## `0.2.1.0` — Stable development baseline

**Objective:** Convert the current mixed committed/uncommitted state into a reproducible, testable ALFRD baseline without introducing new architecture.

### Scope

- Inventory current tracked and untracked work and preserve it in reviewable commits.
- Reconcile the `logger.py` → `logging.py` transition and provide one documented logging API.
- Make project and workflow tests pass.
- Isolate tests from the real `~/.alfrd/projects` directory using temporary paths.
- Remove or relocate the shadowed `src/alfrd/core.py` experiment.
- Correct `requires-python` and classifiers to the real supported Python range.
- Correct GitHub Actions Python setup.
- Package Flask templates, static assets, and SQL schema files.
- Normalize version naming to the four-component scheme used by this roadmap.
- Repair README imports and remove examples for unavailable APIs.
- Add wheel install/import and CLI smoke tests.

### Non-goals

- No new pipeline engine.
- No functional GUI execution controls.
- No AVICA changes.

### Exit criteria

- Clean, intentionally committed working tree.
- Unit tests pass from a fresh environment.
- Wheel builds and installs successfully.
- CLI help and all public imports smoke-test successfully.
- Installed wheel contains all GUI resources.
- CI passes on every supported Python version selected for this release.

---

## `0.2.2.0` — Canonical typed pipeline contracts

**Objective:** Establish the public, generic contracts that AVICA-shaped workflows can use without importing AVICA.

### Scope

Create or finalize:

```text
src/alfrd/core/pipeline.py
src/alfrd/core/config.py
src/alfrd/core/logframe.py
```

Define and test:

- `PipelineCore`
- `PipelineContext`
- `PipelineStepBase`
- `PipelineStepValidatorBase`
- `PipelineStepValidatorResult`
- `StepResult`
- `BatchResult`
- `ColName`
- `ArtifactRef`
- `BaseConfig`
- `Config`
- shared `CONFIG_MAPPING`
- typed Project Manifest models for projects, entrypoints, datasets, workflows, steps, and artifact rules
- versioned JSON Schema validation for YAML manifests

Specify parameter resolution precedence:

1. step-qualified explicit parameter,
2. global explicit parameter,
3. pipeline context parameter,
4. merged configuration parameter,
5. callable default.

Use instance-owned step registries and context. Adapt decorated functions into typed steps at the boundary rather than making globals the core engine.

### Non-goals

- No database-backed run recovery yet.
- No AVICA domain classes.
- No browser execution.

### Exit criteria

- Public API and import paths are documented.
- Two independent pipeline instances can run without state leakage.
- Typed steps and validators pass contract tests.
- Configuration mappings preserve shared mutable-object semantics.
- A repository manifest can be located, parsed, and validated without importing repository code.
- Existing AVICA-style `name`, `entrypoint`, and `schema.definitions` fields have a documented compatibility path.
- Existing decorator-style example projects continue through a compatibility adapter.

---

## `0.2.3.0` — Complete execution and failure semantics

**Objective:** Make `PipelineCore` the sole canonical execution loop and define predictable behavior for success, failure, validation, and interruption.

### Scope

- Implement ordered step execution.
- Implement pre-step and post-step validators.
- Define validator Boolean/list normalization.
- Record start time, end time, duration, descriptions, and structured details.
- Allow `StepResult` to return normalized `ArtifactRef` objects.
- Define skipped-step behavior after validation or execution failure.
- Implement crash snapshots using a storage-neutral interface.
- Implement result CSV output as an adapter.
- Add execution events:
  - `RunStarted`
  - `DatasetStarted`
  - `StepStarted`
  - `StepSucceeded`
  - `StepFailed`
  - `StepSkipped`
  - `DatasetFinished`
  - `RunFinished`
- Resolve declarative artifact patterns after the producing step completes.
- Deprecate `PipelineRun` and `WorkflowManager`; retain tested thin wrappers during `0.2.x`.
- Resolve the current single-step versus remaining-sequence FIXME.

### Default failure policy

- A failed step stops remaining steps for that dataset unless explicitly configured otherwise.
- A failed dataset does not stop other datasets in a batch.
- The batch result aggregates every dataset failure and returns a non-successful final state.

### Exit criteria

- One canonical execution loop exists.
- Success, validator failure, exception, skip, and interruption paths are tested.
- Compatibility wrappers delegate to `PipelineCore` rather than duplicate its loop.
- Event order and result serialization have stable contract tests.
- Declarative and step-returned artifacts produce the same normalized artifact records.

---

## `0.2.4.0` — Canonical LogFrame and table adapters

**Objective:** Replace competing frame implementations with one tabular API suitable for CSV, the future GUI, and optional external-sheet adapters.

### Scope

- Promote the useful AVICA/new-ALFRD frame behavior into `alfrd.core.logframe.LogFrame`.
- Re-export `LogFrame` from a stable public path.
- Support filesystem paths, `io.IOBase`, and empty/in-memory construction.
- Retain pandas as the default backend.
- Keep Polars optional and ensure no unconditional import.
- Define a small table adapter protocol:

```python
adapter.df
adapter.update(dataframe)
adapter.update_cell(dataframe, row_indexes, column_indexes)
```

- Support primary-key row lookup, working columns, previous working-column lookup, and changed-cell synchronization.
- Refresh the synchronization baseline after updates.
- Support row creation through an explicit `ensure_row()` operation.
- Remove disabled Google-specific implementation from the core module; keep Google support as an optional adapter boundary.

### Non-goals

- No formula engine.
- No arbitrary spreadsheet formatting.
- No live Google Sheets implementation required for this release.

### Exit criteria

- Only one maintained LogFrame implementation remains.
- pandas, optional Polars, CSV, stream, and in-memory paths are tested.
- Pure adapter mode works without requiring an existing CSV file.
- Repeated synchronization sends only new changes.

---

## `0.2.5.0` — Persistent runs and resumability

**Objective:** Persist enough execution state to inspect and resume a workflow after process termination.

### Scope

Introduce a storage layer containing at minimum:

- `Project`
- `WorkflowDefinition`
- `Run`
- `Dataset`
- `StepDefinition`
- `StepExecution`
- `Artifact`
- project-manifest version and source-revision metadata
- ordered execution events or equivalent audit records

Implement:

- SQLite schema and migrations/versioning.
- Repository/service boundary so `PipelineCore` does not import Flask or SQLAlchemy.
- Event persistence.
- Dynamic `.alfrd/run.json` generation and recovery.
- Artifact existence, size, media type, and metadata persistence.
- Per-dataset and per-step status.
- Detection and marking of stale `running` executions after restart.
- Resume from the first failed, interrupted, or incomplete step.
- Explicit retry behavior.
- CSV import/export for dataset tables.
- Local subprocess worker abstraction.

### Exit criteria

- A test run can be interrupted, the process restarted, and the run resumed.
- Completed steps are not repeated unless retry is explicitly requested.
- Multiple datasets can succeed or fail independently.
- Database state can produce a complete dataset × step status matrix.
- Restarted runs retain their resolved artifact index and source manifest version.

---

## `0.2.6.0` — Web application foundation

**Objective:** Turn the existing Flask scaffold into an installable, testable ALFRD application with read-only project and workflow inspection.

### Scope

- Add `alfrd serve` or `alfrd gui` CLI command.
- Use an application factory with explicit configuration.
- Correct project-not-found and project-load error ordering.
- Remove unused POST declarations from read-only routes.
- Replace the stale `user`/`post` SQL schema.
- Display registered projects.
- Display project configuration and descriptions.
- Display workflows, steps, validators, and parameter schemas.
- Display dataset-column and artifact definitions read from the Project Manifest.
- Add manifest validation and synchronization views without importing project code.
- Add health and version endpoints.
- Add Flask route, template, and installed-package tests.

### Security boundary

Project Python code must not be loaded merely to return a 404. Dynamic project imports must use an explicit service boundary and return controlled errors.

### Exit criteria

- A wheel-installed ALFRD instance can start the web server from the CLI.
- Project/workflow metadata is viewable without inert or misleading controls.
- Missing and broken projects return stable HTTP errors.
- All rendered templates are tested from the installed package.

---

## `0.2.7.0` — Sheet-like monitoring matrix

**Objective:** Provide the simple Google-Sheets-like interface needed to monitor ALFRD and future AVICA runs.

### Scope

- Render one row per dataset and one generated column per pipeline step.
- Display statuses:
  - pending,
  - queued,
  - running,
  - succeeded,
  - failed,
  - skipped,
  - interrupted.
- Show run summary counts and elapsed time.
- Filter by status and search dataset identifiers.
- Open a cell detail panel showing timestamps, duration, short result description, and error summary.
- Show artifact indicators in their producing step cells.
- Update status through simple periodic polling or HTMX.
- Import datasets from CSV.
- Export the current matrix and detailed results to CSV.
- Keep dataset identity based on an explicit primary column.

### Non-goals

- No arbitrary editable cells.
- No formulas or rich spreadsheet formatting.
- No collaborative editing.

### Exit criteria

- A running example workflow updates the browser matrix without a page restart.
- Failed cells expose actionable details.
- Each cell can list the artifacts associated with that dataset and step.
- Filters and CSV round trips are tested.
- The matrix remains usable for realistically sized AVICA batches selected during implementation planning.

---

## `0.2.8.0` — Browser execution, logs, and resume controls

**Objective:** Make the GUI a functional control plane rather than a metadata dashboard.

### Scope

- Start a workflow for selected datasets.
- Start all eligible datasets.
- Resume failed/interrupted datasets.
- Retry an individual step where dependency rules allow it.
- View live and completed logs.
- Add generic table, text/log, JSON/YAML, image/gallery, file, and directory artifact viewers.
- Display subprocess PID and run ownership metadata.
- Add safe cancellation with explicit confirmation.
- Prevent duplicate starts of the same dataset/run.
- Validate parameters before enqueue/start.
- Record every control action in the run audit trail.

### Execution constraint

Flask routes request actions through the runtime service. They must not execute arbitrary workflow code synchronously in the request process.

### Exit criteria

- An example workflow can be started, monitored, failed, inspected, resumed, and completed from the browser.
- Page refresh and ALFRD server restart do not lose run state.
- Duplicate-run protection and cancellation behavior are tested.
- Artifact viewers reject unsupported paths and do not expose files outside registered run/project roots.

---

## `0.2.9.0` — AVICA compatibility contract

**Objective:** Prove, within ALFRD, that the public engine and table APIs can host AVICA-shaped pipelines with minimal downstream changes.

### Scope

- Add a domain-neutral compatibility fixture modeled on AVICA's current contracts.
- Add an AVICA-shaped `alfrd.yaml` fixture derived from AVICA's existing manifest without modifying AVICA.
- Verify no-argument step construction.
- Verify class-based validator registration.
- Verify AVICA-compatible parameter precedence.
- Verify structured `StepResult` and `BatchResult` behavior.
- Verify result CSV and crash-state hooks.
- Verify multi-dataset execution with one shared LogFrame/table adapter.
- Verify path/glob artifact discovery for AVICA-shaped result CSVs, logs, crash snapshots, plots, and output directories.
- Verify an optional domain inspector can return generic metadata without introducing CASA or AVICA dependencies into ALFRD.
- Document intended downstream aliases:

```python
from alfrd.core.pipeline import PipelineCore as AvicaPipelineCore
from alfrd.core.pipeline import BatchResult as AvicaResult
from alfrd.core.logframe import LogFrame as LogFramework
from alfrd.core.config import BaseConfig, Config, CONFIG_MAPPING
```

- Publish a migration guide describing what remains in AVICA.
- Set an explicit ALFRD version floor for eventual AVICA integration.

### Scope boundary

This milestone changes ALFRD only. Actual edits and runtime verification in the AVICA repository require a separately approved AVICA migration phase.

### Exit criteria

- The compatibility fixture executes single-dataset and multi-dataset workflows.
- The fixture uses only public ALFRD APIs.
- The fixture drives the sheet matrix and artifact views from its manifest rather than hard-coded AVICA knowledge.
- Required AVICA-side changes are documented as a small, path-specific migration.
- No CASA, FITS, MS, rPICARD, or AVICA defaults enter ALFRD.

---

## `0.2.10.0` — Hardening and release candidate

**Objective:** Produce a documented, installable release candidate suitable for real ALFRD workflows and subsequent AVICA integration.

### Scope

- Review public APIs and freeze the `0.2.x` compatibility surface.
- Freeze Project Manifest Schema v1 and document compatible evolution rules.
- Complete deprecation warnings and migration documentation.
- Add subprocess cleanup and stale-process handling.
- Test cancellation, restart, concurrent runs, and database failure behavior.
- Add database backup/export guidance.
- Review dynamic project loading and command execution for security risks.
- Test source distribution and wheel installation in clean environments.
- Test supported Python versions.
- Verify optional dependency groups independently:
  - base,
  - GUI,
  - Google adapter if present,
  - all.
- Update README, changelog, examples, and architecture documentation.
- Produce a release checklist and PyPI release candidate.

### Exit criteria

- All tests and packaging checks pass from clean environments.
- No known state-loss defect exists in start, failure, cancellation, restart, or resume paths.
- Public documentation matches installed APIs.
- Manifest validation, synchronization, run-manifest recovery, and artifact path-security tests pass.
- Browser MVP acceptance workflow passes end to end.
- ALFRD is ready for a separately controlled AVICA integration effort.

---

# Cross-release quality gates

Every milestone must include:

1. A task-level implementation plan with exact file paths.
2. Tests that fail before the behavior change and pass afterward where practical.
3. Focused commits that do not combine unrelated refactors and features.
4. A clean `git diff --check`.
5. Unit and integration test results.
6. Wheel build and installed-package smoke tests when packaging is affected.
7. README/changelog updates for public behavior.
8. A short review of security, data loss, compatibility, and migration risks.

# Deferred beyond `0.2.10.0`

- Cluster scheduler and resource manager.
- Distributed workers.
- Redis/Celery or equivalent queue infrastructure.
- Multi-user authentication and permissions.
- Collaborative table editing.
- Spreadsheet formulas, charts, and scripting.
- A separate React/Vue frontend.
- Full Google Sheets feature parity.
- Direct modifications to AVICA without a separate approved migration plan.
