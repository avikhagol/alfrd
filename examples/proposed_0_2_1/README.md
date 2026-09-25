# Proposed ALFRD 0.2.1.0 usage examples

> **Design-only examples:** These files describe the consolidated `0.2.1.0` user experience before implementation. The proposed commands and Python APIs do not all exist yet. They are intentionally concrete so they can be reviewed and changed first.

## Example repository

```text
my_pipeline/
├── alfrd.yaml
├── datasets.csv
├── pipeline.py
└── outputs/
```

The consumer repository owns `alfrd.yaml`. ALFRD owns its database and writes a resolved `.alfrd/run.json` inside each run directory.

## 1. Validate without importing project code

```bash
alfrd manifest validate ./alfrd.yaml
alfrd manifest show ./alfrd.yaml
```

Expected behavior:

- Parse YAML.
- Validate Project Manifest Schema v1.
- Check required fields and placeholder syntax.
- Do not import `pipeline.py`.
- Do not execute an entrypoint.
- Report missing paths as warnings when they are expected runtime outputs.

## 2. Attach and synchronize a repository

```bash
alfrd project add .
alfrd project inspect demo-science
alfrd project sync demo-science
alfrd project list
```

`project add` registers the manifest location. `project sync` refreshes ALFRD's stored project/workflow/dataset/artifact definitions after `alfrd.yaml` changes. Neither command rewrites repository source files.

## 3. Import or inspect datasets

```bash
alfrd dataset import demo-science ./datasets.csv
alfrd dataset list demo-science
alfrd dataset show demo-science target-a
```

The manifest declares `TARGET_NAME` as the primary key. Each CSV row becomes one sheet row; workflow steps become generated status columns.

## 4. Inspect workflows before running

```bash
alfrd workflow list demo-science
alfrd workflow show demo-science calibration
alfrd workflow check demo-science calibration --dataset target-a
```

`workflow check` resolves parameters and inputs but does not start a process.

## 5. Start and monitor a run

```bash
# One dataset
alfrd run start demo-science calibration --dataset target-a

# Selected datasets
alfrd run start demo-science calibration \
  --dataset target-a \
  --dataset target-b

# Every eligible row
alfrd run start demo-science calibration --all

alfrd run list --project demo-science
alfrd run show RUN_ID
alfrd run logs RUN_ID --follow
```

The command returns a `RUN_ID` immediately after the local worker starts. Workflow code runs in a subprocess, not in the CLI or Flask request process.

## 6. Resume, retry, and cancel

```bash
# Resume all incomplete datasets from their first incomplete step
alfrd run resume RUN_ID

# Retry one failed step for one dataset
alfrd run retry RUN_ID \
  --dataset target-a \
  --step calibrate

# Request safe cancellation
alfrd run cancel RUN_ID
```

Default policy:

- A failed step skips later steps for that dataset.
- Other datasets continue.
- Resume does not repeat successful steps.
- Retry is explicit and recorded in the audit trail.

## 7. Inspect artifacts

```bash
alfrd artifact list RUN_ID
alfrd artifact list RUN_ID --dataset target-a
alfrd artifact list RUN_ID --dataset target-a --step calibrate
alfrd artifact show ARTIFACT_ID
alfrd artifact open ARTIFACT_ID
```

Artifacts may come from manifest path/glob rules or explicit `ArtifactRef` objects returned by steps. ALFRD normalizes both sources into the same stored representation.

## 8. Start the web GUI

```bash
alfrd serve --host 127.0.0.1 --port 8040
```

Proposed pages:

```text
/projects
/projects/demo-science
/projects/demo-science/datasets
/projects/demo-science/workflows/calibration
/runs/RUN_ID
/runs/RUN_ID/datasets/target-a
```

The run page shows a matrix like:

| Dataset | Prepare | Calibrate | Summarize |
|---|---|---|---|
| target-a | Succeeded, 1 artifact | Failed, 2 artifacts | Skipped |
| target-b | Succeeded, 1 artifact | Running | Pending |

Selecting a cell shows:

- status and attempt number,
- start/end/duration,
- validation messages,
- error summary and traceback,
- logs,
- artifacts produced by that dataset and step,
- permitted resume/retry actions.

## 9. Python pipeline API

See `pipeline.py` for the proposed typed API. Its design goals are:

- typed classes are primary,
- step and validation results are structured,
- context is instance-scoped,
- steps can return artifacts directly,
- decorated legacy functions are adapted at the boundary.

## 10. AVICA compatibility target

After ALFRD's public contracts stabilize, AVICA should be able to keep compatibility aliases such as:

```python
from alfrd.core.pipeline import PipelineCore as AvicaPipelineCore
from alfrd.core.pipeline import BatchResult as AvicaResult
from alfrd.core.logframe import LogFrame as LogFramework
from alfrd.core.config import BaseConfig, Config, CONFIG_MAPPING
```

AVICA's current `alfrd.yaml` remains readable during migration. AVICA-specific steps, validators, CASA handling, defaults, and artifact inspectors remain in AVICA.

## Decisions to review before implementation

1. Are grouped commands (`project add`, `run start`) preferable to the current flat commands (`add`, `run`)?
2. Should `alfrd project add .` default to repository-local execution, symlink registration, or copying?
3. Should datasets be imported into SQLite, read live from CSV, or support both explicitly?
4. Should `run start` return immediately or stream output by default?
5. Should retrying a step invalidate all later successful steps?
6. Which manifest fields should be mandatory in v1?
7. Should declarative artifact globs run after every step or only at dataset completion?
8. Should the GUI permit dataset-field editing or remain execution/status-only initially?
9. Should `ArtifactRef.path` permit paths outside the dataset/run root when explicitly allow-listed?
10. Should optional project inspectors run in the workflow subprocess or a separate restricted inspection process?
