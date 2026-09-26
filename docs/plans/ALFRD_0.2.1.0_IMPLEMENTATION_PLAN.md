# ALFRD 0.2.1.0 Stable Baseline Implementation Plan

> **For Hermes:** Use the software-development-lifecycle workflow to implement this plan task-by-task. Do not commit, publish, or modify another repository.

**Goal:** Turn the existing mixed tracked/untracked ALFRD work into a reproducible Python 3.10–3.13 baseline whose tests, wheel, public imports, CLI, and packaged GUI resources work from clean environments.

**Architecture:** Preserve the current decorator-based project/workflow implementation and GUI scaffold; this milestone changes stabilization boundaries only. Route package messages through Python's standard `logging` hierarchy without configuring application handlers, resolve project storage at runtime so tests can redirect it, and express package/install guarantees as pytest and wheel-content smoke tests.

**Tech Stack:** Python 3.10–3.13, setuptools/PEP 621, Typer, pytest, uv, GitHub Actions.

---

## Baseline inventory

- Branch: `0.2.0`, tracking `origin/0.2.0`.
- Pre-existing tracked edits: `.gitignore`, `LICENSE`, `README.md`, `pyproject.toml`, `src/alfrd/__init__.py`, `src/alfrd/core/project.py`, `src/alfrd/lib.py`, `src/alfrd/plugins.py`, `src/alfrd/util.py`; deletion of `src/alfrd/core/logger.py` and `tests/test_alfrd.py`.
- Pre-existing untracked work: project/design documents, `apptainer.def`, roadmap, CLI/core/workflow/inspection/logging/GUI code, project/workflow tests and their temporary helper files.
- Baseline test command cannot run because the active Hermes interpreter has no pytest. The existing test files also create files in the checkout and use the real `~/.alfrd/projects` path.
- Baseline wheel builds as `0.2.0.0`, includes the shadowing `alfrd/core.py`, and omits all templates and `schema.sql`.
- Baseline logging is broken: callers use `livelogger.log(...)`, while `logging.py` only defines `logger(...)` and `info(...)`.
- Baseline CI uses `actions/checkout@v5` instead of `actions/setup-python` for Python setup.

### Task 1: Replace custom logging control with standard logging

**Objective:** Use `logging.Logger` throughout ALFRD and leave handler, formatter, and level policy under application control.

**Files:**
- Modify: `src/alfrd/core/logging.py`
- Create: `tests/test_logging.py`
- Modify: `README.md`

**Steps:**
1. Add tests asserting that the package logger is a standard `logging.Logger`, uses a `NullHandler`, and emits project records capturable through the `alfrd` hierarchy.
2. Run `uv run --extra dev pytest tests/test_logging.py -q` and confirm the standard-logger tests fail against `LiveLog`.
3. Replace `LiveLog` call sites with module loggers from `logging.getLogger(__name__)`; map warnings, errors, and exceptions to their native methods.
4. Remove `LiveLog` and `livelogger` from public imports, keep only a deprecated module-path shim to the standard logger, and document application-side configuration.

### Task 2: Isolate project and workflow tests

**Objective:** Ensure tests never read, create, symlink into, or delete the real `~/.alfrd/projects` tree and do not leak registries/modules across cases.

**Files:**
- Modify: `src/alfrd/__init__.py`
- Modify: `src/alfrd/core/project.py`
- Modify: `src/alfrd/cli.py`
- Create: `tests/conftest.py`
- Rewrite: `tests/test_project.py`
- Rewrite: `tests/test_workflow.py`
- Remove generated fixtures from collection: `tests/tmp_pyt.py`, `tests/tmp_workflow.py`, `tests/tmp_helpers/`

**Steps:**
1. Add isolated tests using `tmp_path` and `monkeypatch.setenv("ALFRD_HOME", ...)`, including assertions that the configured root is beneath the pytest temp directory.
2. Add an autouse fixture that clears global registries and purges temporary plugin modules before and after each test.
3. Run focused tests to expose static `PROJ_DIR` and logging failures.
4. Resolve ALFRD/project directories through runtime helper functions while keeping exported path constants for compatibility.
5. Make plugin loading use unique module identities and clean temporary `sys.path` entries.
6. Run `uv run --extra dev pytest tests/test_project.py tests/test_workflow.py -q`.

### Task 3: Remove the `core.py` package shadow

**Objective:** Keep the user's prototype for later work without shipping a module that competes with the `alfrd.core` package.

**Files:**
- Relocate: `src/alfrd/core.py` → `docs/experiments/logframework_prototype.py`
- Create/modify: `tests/test_public_api.py`

**Steps:**
1. Add an import-origin assertion that `alfrd.core` resolves to `alfrd/core/__init__.py`.
2. Move the prototype verbatim outside `src/`.
3. Confirm no `alfrd/core.py` appears in a built wheel.

### Task 4: Correct release metadata and CI

**Objective:** Publish one four-component development version and test exactly the supported Python range.

**Files:**
- Modify: `pyproject.toml`
- Modify: `.github/workflows/test_onpush.yaml`
- Modify: `.github/workflows/publish-pypi.yml`
- Modify: `CHANGELOG`

**Steps:**
1. Set project version to `0.2.1.0` and `requires-python = ">=3.10"`.
2. Keep classifiers only for Python 3.10, 3.11, 3.12, and 3.13; remove obsolete compatibility dependency markers.
3. Define a GitHub Actions 3.10–3.13 matrix using `actions/setup-python@v5`, install `.[dev]`, and run pytest/build/smoke checks.
4. Modernize release checkout/setup actions without changing publication triggers or credentials.
5. Record the release baseline in `CHANGELOG`.

### Task 5: Package GUI resources

**Objective:** Include templates, a static stylesheet, and SQL schema in both wheel and source distribution.

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/alfrd/gui/templates/dashboard/layout.htm`
- Create: `src/alfrd/gui/static/alfrd.css`
- Test: `tests/test_package_data.py`

**Steps:**
1. Add failing source-tree resource tests using `importlib.resources` for every template, stylesheet, and `gui/model/schema.sql`.
2. Configure setuptools package data for `gui/templates/**/*.htm`, `gui/static/**/*`, and `gui/model/*.sql`.
3. Move the inline baseline CSS to the packaged stylesheet and reference it with Flask `url_for`.
4. Build the wheel and programmatically assert all expected resource paths are present.

### Task 6: Repair README and define public imports

**Objective:** Ensure every documented import is available in the base installation and remove examples for the unavailable Google `GSC` API.

**Files:**
- Modify: `README.md`
- Modify: `src/alfrd/__init__.py`
- Modify: `src/alfrd/core/__init__.py`
- Create: `tests/test_public_api.py`

**Steps:**
1. Replace broken `GSC` examples and stale claims with currently available CSV, decorator, `Project`, and `Workflow` imports.
2. Export the supported baseline API explicitly with `__all__`.
3. Test every documented public import and `__version__ == "0.2.1.0"`.
4. Run focused public-API tests.

### Task 7: Add source and installed CLI smoke tests

**Objective:** Verify the Typer entry point and commands load without touching user data.

**Files:**
- Create: `tests/test_cli.py`
- Create: `tests/test_wheel.py`
- Modify: `pyproject.toml` if test-only dependencies require tightening.

**Steps:**
1. Use Typer's test runner for `--help` and command help/source smoke checks under an isolated `ALFRD_HOME`.
2. Build a wheel in a pytest temp directory.
3. Create a clean virtual environment, install the wheel, and execute public-import and `alfrd --help` subprocess smoke checks from outside the checkout.
4. Verify installed resources through `importlib.resources` inside that environment.

### Task 8: Full verification and review

**Objective:** Exercise every exit criterion that does not conflict with the instruction not to commit.

**Files:** All changed files; no commit.

**Steps:**
1. Run `uv run --extra dev pytest -q` from the source tree.
2. For Python 3.10, 3.11, 3.12, and 3.13, create fresh uv environments, install the project with dev dependencies, and run tests.
3. Run `uv build --out-dir <temporary-directory>` and inspect wheel metadata/resources.
4. Install the wheel in a fresh environment and run public imports, version/resource checks, and `alfrd --help`/command-help smoke checks from outside the checkout.
5. Run `git diff --check`, inspect the complete diff and final `git status --short --branch`, and review security, data-loss, compatibility, and migration risk.
6. Report the explicit exit-criterion conflict: the tree cannot be clean and intentionally committed because the user prohibited commits. Leave all integrated work uncommitted and do not publish.
