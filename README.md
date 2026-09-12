# ALFRD : Automated Logical FRamework for Dynamic script execution(ALFRD)

This program is written for the [SMILE project](https://smilescience.info) supported by the ERC starting grant, particularly with following purposes in mind:

- Communicate with google spreadsheets(or local csv file) and update progress periodically when required.
- Execute a pipeline workflow that dynamically operates based on the progress and requirements recorded in the spreadsheet or CSV file.

# Contents:
- [ALFRD : Automated Logical FRamework for Dynamic script execution(ALFRD)](#alfrd--automated-logical-framework-for-dynamic-script-executionalfrd)
- [Contents:](#contents)
  - [1. Create API credentials on Google Cloud](#1-create-api-credentials-on-google-cloud)
  - [2. Installing](#2-installing)
  - [3. Using ALFRD](#3-using-alfrd)
    - [3.1 - Basic Usage](#31---basic-usage)
      - [3.1.1 Example: Google Spreadsheet - Initializing and creating instance](#311-example-google-spreadsheet---initializing-and-creating-instance)
      - [3.1.2 Example: Google Spreadsheet - Update the data](#312-example-google-spreadsheet---update-the-data)
      - [3.1.3 Example: CSV - Update the data (more soon)](#313-example-csv---update-the-data-more-soon)
    - [3.2 - Advance](#32---advance)
      - [3.2.1 Example : Execute functions using alfrd](#321-example--execute-functions-using-alfrd)
      - [3.2.2 Example : Pipeline step execution/update using the Spreadsheet/CSV](#322-example--pipeline-step-executionupdate-using-the-spreadsheetcsv)
  - [Runtime persistence API](#runtime-persistence-api)
  - [Public API surface (0.2.1.0)](#public-api-surface-0210)
  - [4. Attribution](#4-attribution)
  - [5. Acknowledgement](#5-acknowledgement)


-------


## 1. Create API credentials on Google Cloud


A similar guide is available [here](https://developers.google.com/workspace/guides/create-credentials) or [https://developers.google.com/workspace/guides/create-credentials](https://developers.google.com/workspace/guides/create-credentials)

NOTE: In order to successfully create a google console project a billing detail is usually required. But the sheets API service is available for free, refer [here](https://developers.google.com/sheets/api/limits)

- step 1:
  Go to https://console.cloud.google.com/

- step 2: 
	click on the drop-down to create a new project 
		- can choose organization or leave on default

- step 3
  Search in the top bar (or press / ) and type : "Google sheets api"
  
- step 4
  In the search results - under Marketplace select the first result which should be the same : Google sheets api
  
- step 5
  Enable the service In the product details page 
  
- step 6
  Now select create credentials > Application Data
  
- step 7
  Create an account name
  Select create and continue
  
- step 8
  Search and select "Editor" in role > Continue
  
- step 9
  Skip next optional step
  Select Done
  
- step 10
  The Credentials are successfully created
  Select "Credentials" on the left menu
  
- step 11
  select/edit account that was just created
  also copy the email address that is shown
  
- step 12
  Select keys tab > Add keys > Create New Keys > JSON > save
  
- step 13
  Go to the Google spreadsheet and "share" the sheet to the email address that was copied, as Editor.

## 2. Installing

- Install using the pip package manager:
  ```bash
  pip install alfrd
  ```
- Alternatively Download [ALFRD](https://github.com/avialxee/alfrd) and unzip / Or 
    ```bash
   git clone https://github.com/avialxee/alfrd
   cd alfrd/
   pip install .
    ```
this should install alfrd and all the dependencies automatically.


## 3. Using ALFRD

ALFRD can be used for structuring the pipeline/workflow steps, such that each step (e.g., Step A, Step B, Step C) is represented as a column (A, B, C) in a table, with the workflow executing these steps sequentially according to their order in the table.

### 3.1 - Basic Usage

ALFRD relies on pandas dataframe to read/write tabular data.

####  3.1.1 Example: Google Spreadsheet - Initializing and creating instance

Google Sheets support is provided as an optional storage adapter implementing
the `LogFrameAdapter` protocol (`df`, `update`, `update_cell`). ALFRD no longer
ships a bundled Google Sheets client class; consumers wire their own adapter
(for example using `gspread`, installed via the `alfrd[google]` extra) and
pass it as `gsc=` to `LogFrame`.

```python
from alfrd.core.logframe import LogFrame

# `sheet_adapter` implements the LogFrameAdapter protocol: .df, .update(df),
# .update_cell(df, rows, columns). See alfrd.core.logframe.LogFrameAdapter.
lf = LogFrame(gsc=sheet_adapter)
```


####  3.1.2 Example: Google Spreadsheet - Update the data

The instance of LogFrame can be used to manipulate the dataframe and sync it
back to the adapter.

```python

lf.df.loc[0, 'TSYS'] = True

lf.update_sheet(count=1, failed=0, csvfile='df_sheet.csv')                     # if updating the sheet fails, a copy of the dataframe is saved locally at the csvfile path.

```

####  3.1.3 Example: CSV - Update the data (more soon)

It is also possible to use just the CSV file as an alternative to the Google Sheet.

```python
from alfrd.core.logframe import LogFrame

lf = LogFrame('in.csv')
lf.df.loc[0, 'TSYS'] = True
lf.df.to_csv('out.csv')

```

### 3.2 - Advance

#### Canonical typed pipeline API

`PipelineCore` executes typed steps in registration order with instance-local
context. Parameters resolve from `step_name.parameter`, global explicit
parameters, context, merged configuration, and finally the callable default.
One dataset failure skips only that dataset's remaining steps.

```python
from alfrd import PipelineContext, PipelineCore, PipelineStepBase

class Prepare(PipelineStepBase):
    name = "prepare"

    def execute(self, dataset_id, output_dir="results"):
        return f"{output_dir}/{dataset_id}"

result = PipelineCore(
    [Prepare()],
    context=PipelineContext({"output_dir": "products"}),
).run([{"dataset_id": "target-a"}, {"dataset_id": "target-b"}])
```

Typed validators subclass `PipelineStepValidatorBase`; attach instances with
the step's `before` and `after` arguments. `PipelineStepValidatorResult`, plain
Booleans, and Boolean/result lists are normalized consistently. Execution
returns `BatchResult`/`StepResult` objects and can emit lifecycle events or use
`CrashSnapshotAdapter` and `ResultCSVAdapter` without a database dependency.

#### 3.2.1 Example : Execute functions using alfrd

Create `pipe.py` and use the register decorator for creating a pipeline step.

```python 
# pipe.py
from alfrd.plugins import register


@register("A hello world function")
def step_hello_world(name):
  print(f"hello, {name}")

```

Now we should create a pipeline project and add the script to the project, this can be done as follows:

```bash
alfrd init PROJECT_NAME # change the PROJECT_NAME to something desirable
alfrd add /path/to/pipe.py PROJECT_NAME
```

this will create a symlink to the project directory found at  `~/.alfrd/projects/PROJECT_NAME/pipe.py`
That's it! You have created a pipeline flow, now execute the created script by running the following:

```bash
alfrd run step_hello_world PROJECT_NAME name=World
```
> NOTE: you can create a config file e.g config.txt and modify the above as follows: \
>   `alfrd run step_hello_world PROJECT_NAME config.txt` \
> and in the config.txt write something like:
```
# config.txt
name = World
```

#### 3.2.2 Example : Pipeline step execution/update using the Spreadsheet/CSV

Now modify the `pipe.py` and use the validator decorator for defining functions which can be executed just before and after the main pipeline step. 
The parameters accessed between the pipeline steps and validators can be defined by the `Pipeline.params`. 
Also one can use the config file to initialize the parameter values in the execution time.

```python 
# pipe.py

from alfrd.core.logframe import LogFrame

from alfrd import Pipeline
from alfrd.plugins import validator, validate, register

@validator(desc="Update values on the log/CSV table", run_once=False, after=True)
def update_sheet(lf, success_count, failed_count):
    lf.update_sheet(success_count, failed_count)
  
@validator(desc="Connect an adapter and return an instance for the runtime parameter", run_once=True)
def connect_sheet(sheet_adapter):
    Pipeline.params['lf'] = LogFrame(gsc=sheet_adapter)

@validate(by=[connect_sheet, update_sheet])
@register("A hello world function")
def modify_tsys(lf):
  lf.df.loc[0, 'TSYS'] = True
  Pipeline.params['success_count'] = 1
  Pipeline.params['failed_count'] = 0

```

the above can be executed as follows:
```bash
 alfrd run modify_tsys PROJECT_NAME sheet_adapter=/path/to/adapter/config
```

> NOTE: `alfrd.Pipeline` (and the underlying `alfrd.plugins.PipelineRun`) are
> a deprecated compatibility facade over `alfrd.core.pipeline.PipelineCore`
> retained for `0.2.x`; see [Public API surface](#public-api-surface-0210)
> for the frozen/deprecated boundary.

## Runtime persistence API

`alfrd.runtime` provides the storage and execution-state boundary used by future
pipeline integrations. It is deliberately separate from the legacy `Workflow`
loop and the optional Flask application.

```python
from alfrd.runtime import RuntimeService, RuntimeStore

store = RuntimeStore("alfrd-runtime.sqlite")
store.initialize()
runtime = RuntimeService(store)
```

The service persists projects, versioned workflow and step definitions,
independent datasets, runs, step attempts, artifacts, and audit events. Every run
also has an atomically refreshed `<run directory>/.alfrd/run.json`, which can
restore a missing database run with `runtime.recover_manifest(run_directory)`.
Use `resume_run` to add attempts only for unfinished steps on the same run and
`retry_run` to create a linked run. `LocalSubprocessWorker` executes one selected
step without shell expansion; sequencing remains the caller's responsibility.

Dataset CSV files use the columns `external_id`, `name`, `uri`, and `metadata`,
where `metadata` is a JSON object. Dataset identity is scoped to a project.

### Integrated service boundaries

`RuntimeService.register_manifest()` discovers and validates `alfrd.yaml`
without importing consumer Python. Manifest entrypoints become persisted command
workflows. Optional artifact declarations describe expected outputs without
being confused with a produced `ArtifactRef` or a persisted runtime `Artifact`:

```yaml
name: example
entrypoint:
  - name: build
    cmd: [python, -m, example]
artifacts:
  - name: report
    path_pattern: products/{dataset_id}.json
    media_type: application/json
```

`RuntimePipelineRunner` executes typed steps whose names and order match a
persisted workflow. Its `RuntimeEventSink` maps pipeline lifecycle events and
`ArtifactRef` values into durable runs, step executions, checksummed artifacts,
audit events, and run manifests. Add `LogFrameEventSink(logframe)` to the
runner's `event_sinks` to project terminal step status into a pandas or Polars
`LogFrame`.

The Flask application remains read-only. Its bundled Flask-SQLAlchemy models
are a standalone metadata catalog for compatibility; they are not runtime
execution models. Applications using durable runtime state should pass
`RuntimeCatalogReader(runtime_service)` as `CATALOG_READER`, avoiding a second
copy of project/workflow metadata while retaining the same HTTP routes.

## Public API surface (0.2.1.0)

This section freezes the `0.2.x` compatibility surface for the consolidated
`0.2.1.0` release candidate. Anything listed as **stable** is covered by
`tests/test_public_api.py`; a compatible change may add fields/methods but
must not remove or rename what is listed. Anything listed as **deprecated**
still works, emits `DeprecationWarning`, and is scheduled for removal in a
future major release, not during `0.2.x`.

### Stable — `alfrd` (top-level)

`ALFRD_CACHE_DIR`, `ALFRD_CONFIG_DIR`, `ALFRD_DIR`, `PROJ_DIR`,
`get_alfrd_dir`, `get_project_dir`, `__version__`, `B`, `X`, `c`.

### Stable — typed pipeline engine (`alfrd.core.pipeline`, re-exported from `alfrd`)

`PipelineCore`, `PipelineContext`, `PipelineStepBase`,
`PipelineStepValidatorBase`, `PipelineStepValidatorResult`, `StepResult`,
`DatasetResult`, `BatchResult`, `ArtifactRef`, `ColName`,
`CrashSnapshotAdapter`, `ResultCSVAdapter`, `write_crash_snapshot`,
`append_step_result_csv`, execution events (`RunStarted`, `DatasetStarted`,
`StepStarted`, `StepSucceeded`, `StepFailed`, `StepSkipped`,
`DatasetFinished`, `RunFinished`), `FunctionPipelineStep`,
`FunctionPipelineStepValidator`, `PipelineError`.

### Stable — configuration (`alfrd.config`)

`BaseConfig`, `Config`, `CONFIG_MAPPING`.

### Stable — Project Manifest (`alfrd.manifest`, re-exported from `alfrd.core.manifest`)

`ProjectManifest`, `Entrypoint`, `SchemaDefinition` (alias `ProjectSchema`),
`ArtifactDefinition`, `ManifestError`, `ManifestNotFoundError`,
`discover_manifest`, `load_manifest`, `parse_manifest`, `validate_manifest`,
`get_manifest_schema`, `MANIFEST_FILENAME`, `MANIFEST_VERSION`.

### Stable — repository registration (`alfrd.repository`, re-exported from `alfrd.core.repository`)

`RepositoryService` (alias `Repository`), `RepositoryRecord`,
`RepositoryNotFoundError`, `add_repository`, `sync_repository`,
`inspect_repository`.

### Stable — LogFrame (`alfrd.core.logframe`, re-exported from `alfrd.lib`)

`LogFrame`, `LogFrameAdapter` (protocol), `LogFrameEventSink`.

### Stable — durable runtime (`alfrd.runtime`)

`RuntimeStore`, `RuntimeService`, `Status`, `InvalidTransition`,
`RuntimeNotFound`, `SchemaVersionError`, `SCHEMA_VERSION`,
`LocalSubprocessWorker`, `RuntimeOperations`, `StepWorker` (protocols),
`RuntimePipelineRunner`, `RuntimeEventSink`, `CompositeEventSink`,
`artifact_ref_from_model`, ORM models `Project`, `WorkflowDefinition`,
`Dataset`, `StepDefinition`, `Run`, `StepExecution`, `Artifact`,
`AuditEvent`.

### Stable — web application (`alfrd.gui`, optional `alfrd[gui]`)

`create_app()` application factory; `CatalogReader` protocol,
`SqlAlchemyCatalogReader`, `RuntimeCatalogReader`
(`alfrd.gui.services`); read-only routes under `/api/*` and `/dashboard/*`
plus `/health` and `/version`; `alfrd serve` / `alfrd gui` CLI commands.

### Stable — legacy decorator adapters (`alfrd.plugins`)

`register`, `validate`, `validator` decorators and the module-level
`REGISTERED_STEPS`, `VALIDATORS`, `VALIDATE_BEFORE`, `VALIDATE_AFTER`
registries remain a supported compatibility adapter boundary into
`PipelineCore` (via `PipelineCore.from_legacy_registries`), not the engine's
internal state model.

### Deprecated (still functional, emits `DeprecationWarning`)

- `alfrd.plugins.PipelineRun` and the module-level `alfrd.Pipeline` singleton
  it backs — use `alfrd.core.pipeline.PipelineCore` directly.
- `alfrd.core.workflow.WorkflowManager` and its `Workflow` alias — use
  `PipelineCore` directly, or `alfrd.runtime.RuntimePipelineRunner` for
  persisted execution.
- `alfrd.core.logger` — use `alfrd.core.logging` (`logger = getLogger("alfrd")`).

### Explicitly out of scope for `0.2.x` compatibility guarantees

- `alfrd.gui.model.tables` internal SQLAlchemy row shapes (`ProjectDB`,
  `WorkflowDB`, etc.) are a standalone catalog projection, not the runtime's
  source of truth; prefer `RuntimeCatalogReader` over depending on these rows
  directly.
- Flask template internals under `alfrd/gui/templates/` may change without a
  major version bump.

## 4. Attribution

When using ALFRD, please add a link to this repository in a footnote.

## 5. Acknowledgement

ALFRD was developed within the "Search for Milli-Lenses" (SMILE) project. SMILE has received funding from the European Research Council (ERC) under the HORIZON ERC Grants 2021 programme (grant agreement No. 101040021).