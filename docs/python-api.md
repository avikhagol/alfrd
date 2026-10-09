<img src="brand/alfrd-mark.svg" alt="ALFRD" width="44" align="right">

# Using ALFRD from Python

## Run steps

Typed pipeline:

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

- Steps run in order.
- One failed dataset skips only its own remaining steps.
- Parameters come from `step.param`, then global values, then defaults.

## Track progress in a table

`LogFrame` wraps a pandas table (CSV or Google Sheet).

```python
from alfrd.core.logframe import LogFrame

lf = LogFrame("in.csv")
lf.df.loc[0, "TSYS"] = True
lf.df.to_csv("out.csv")
```

Google Sheet: pass your own adapter (for example with `gspread`, via `alfrd[google]`).

```python
lf = LogFrame(gsc=sheet_adapter)
lf.df.loc[0, "TSYS"] = True
lf.update_sheet(count=1, failed=0, csvfile="backup.csv")   # backup if the sheet update fails
```

## Runtime database

Stores projects, runs, steps, artifacts and events in SQLite.

```python
from alfrd.runtime import RuntimeService, RuntimeStore

store = RuntimeStore("alfrd-runtime.sqlite")
store.initialize()
runtime = RuntimeService(store)
```

- `resume_run` – continue unfinished steps.
- `retry_run` – start a linked new run.
- Each run writes `.alfrd/run.json`. `recover_manifest()` restores a lost run.

CLI:

```bash
alfrd runtime start ...     # see: alfrd runtime --help
alfrd import avica-run reductions/ --project my-project --manifest alfrd.yaml
```

## Public API

Stable in `0.3.x` (tested in `tests/test_public_api.py`):

- `alfrd` – `PipelineCore`, `PipelineContext`, `PipelineStepBase`, validators, results, events.
- `alfrd.config` – `BaseConfig`, `Config`.
- `alfrd.manifest` – `load_manifest`, `validate_manifest`, `ProjectManifest`, …
- `alfrd.repository` – `RepositoryService`, `add_repository`, …
- `alfrd.core.logframe` – `LogFrame`, `LogFrameAdapter`, `LogFrameEventSink`.
- `alfrd.runtime` – `RuntimeStore`, `RuntimeService`, models.
- `alfrd.gui` – `create_app()`, catalog readers, `/api/*`, `/studio/`.

## Google Sheets credentials

Only needed for Google Sheets. Full guide:
[Google: create credentials](https://developers.google.com/workspace/guides/create-credentials).

1. Open https://console.cloud.google.com/ and create a project.
2. Search **Google Sheets API**. Enable it.
3. **Create credentials → Application data**.
4. Name the account. Role: **Editor**. Finish.
5. Open the account. Copy its email.
6. **Keys → Add key → JSON**. Save the file.
7. Share your sheet with that email as **Editor**.

The Sheets API is free. Google may still ask for billing details.

To sync a plan with a sheet from the Studio, see the Google Sheet plugin in [Plugins](plugins.md).
