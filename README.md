# ALFRD

**A**utomated **L**ogical **FR**amework for **D**ynamic script execution.

Run pipeline steps. Track their progress in a table. See it all in a web UI.

---

## Contents

- [Install](#install)
- [Quick start: the web UI](#quick-start-the-web-ui)
- [`alfrd serve` examples](#alfrd-serve-examples)
- [Tell the Studio about your pipeline (`alfrd.yaml`)](#tell-the-studio-about-your-pipeline-alfrdyaml)
- [Run steps from Python](#run-steps-from-python)
- [Track progress in a table](#track-progress-in-a-table)
- [Runtime database](#runtime-database)
- [Public API](#public-api)
- [Google Sheets setup](#google-sheets-setup)
- [Credits](#credits)

---

## Install

```bash
pip install "alfrd[gui]"     # with the web UI (recommended)
pip install alfrd            # core only
```

From source:

```bash
git clone https://github.com/avialxee/alfrd
cd alfrd
pip install ".[gui]"
```

Needs Python 3.10+.

---

## Quick start: the web UI

Go to the folder that holds your `alfrd.yaml`. Run:

```bash
alfrd serve
```

Your browser opens **ALFRD Studio**.

- It reads the project in that folder.
- It never runs your pipeline. It only reads files.
- The URL is `http://127.0.0.1:5000/studio/`.

What you get:

| View | Shows |
|---|---|
| **Overview** | All targets × steps. Status, MS path, notes. |
| **Workflow** | Steps as a list or graph. Step parameters and logs. |
| **Metadata** | Metadata health per step. Config. Input files. |
| **Results** | Timings and progress. Latest run or full history. |
| **Logs** | Every log file, grouped. Click to open. Expand to full screen. |
| **Settings** | Edit and save `alfrd.yaml`. |

No server? Use browser mode:

```bash
alfrd studio        # opens http://127.0.0.1:8080/, then Import → Open project folder
```

Full guide: [docs/studio-guide.md](https://github.com/avialxee/alfrd/blob/HEAD/docs/studio-guide.md)

---

## `alfrd serve` examples

```bash
# Open the project in the current folder
cd /data/vasco_0.3
alfrd serve

# Open a project somewhere else
alfrd serve --project /data/vasco_0.3

# Another port, no browser pop-up (e.g. over SSH)
alfrd serve --port 8050 --no-browser

# Try it with demo data
alfrd serve --demo

# Keep the runtime database in a chosen file
alfrd serve --runtime-db ~/alfrd/runtime.sqlite

# Also show every project connected before
alfrd serve --all-projects
```

Projects are remembered in `~/.alfrd/runtime.sqlite`:

```bash
alfrd projects list              # what is remembered
alfrd projects forget OLD_NAME   # remove one (files stay on disk)
```

Or in the Studio: **⚙ Settings → Known projects → Forget**.

Stop the server: **Ctrl+C**, or the **⏻ Quit** button (top right, same machine only). Quit also closes the tab when the browser allows it (the tab `alfrd serve` opened).

Working on a remote machine? Forward the port:

```bash
ssh -L 5000:127.0.0.1:5000 user@cluster   # then run `alfrd serve --no-browser` there
```

Good to know:

- Saving `alfrd.yaml` or `avica.inp` from the UI only works from the same machine (loopback).
- The old dashboard is still at `/dashboard/`.
- `alfrd gui` is the same as `alfrd serve`.

---

## Tell the Studio about your pipeline (`alfrd.yaml`)

The Studio has no built-in pipeline. `alfrd.yaml` describes it.

Minimal:

```yaml
name: my-project
workflows:
  - name: main
    steps: [prepare, calibrate, image]
```

Richer (AVICA example):

```yaml
name: avica-t-0.3
template: avica                      # AVICA defaults: labels, stages, metadata, logs

project_settings:
  field_aliases:                     # old names → new names
    vasco_avg: avica_avg
    "vasco*": "avica*"

overview:
  ms_path:                           # "MS Storage Path" column
    - "{workdir}/wd_{band}_{target}/VLBI_{band}.ms"

stages:
  - {id: ingest, title: Ingestion}

workflows:
  - name: avica
    steps:
      - id: preprocess_fitsidi
        stage: ingest
        category: Preprocessing
        label: FITS-IDI data ingestion
        metadata:                    # → Metadata health
          - {file: fitsfiles_used.avica, require: [filepath]}
      - id: avica_avg
        logs:                        # → step logs
          - "{workdir}/wd_{band}/avica_avg_*log*"
      - rpicard
```

Placeholders you can use:

- `{workdir}` – a work folder, e.g. `reductions/RDV41/wd`
- `{band}`, `{target}`, `{step}`, `{logs}`, `{meta_dir}`, `{target_dir}`
- `*` and `?` – wildcards inside one folder

Tips:

- A step can be just a name, or a mapping that overrides the template.
- Artifacts with `kind: log` show up in the **Logs** view.
- The AVICA template lives in `src/alfrd/web/assets/templates/avica.yaml`.
- Check a file: `alfrd manifest validate alfrd.yaml`

AVICA helpers:

```bash
alfrd avica summary                     # cache `avica pipe config --summary`
alfrd avica scan . --bundle scan.json   # pack a remote tree for the Studio
```

---

## Run steps from Python

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

Decorator style (older, still supported):

```python
# pipe.py
from alfrd.plugins import register

@register("A hello world function")
def step_hello_world(name):
    print(f"hello, {name}")
```

```bash
alfrd init MYPROJ
alfrd add pipe.py MYPROJ
alfrd run step_hello_world MYPROJ name=World
alfrd run step_hello_world MYPROJ config.txt    # config.txt: name = World
```

---

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

---

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

---

## Public API

Stable in `0.2.x` (tested in `tests/test_public_api.py`):

- `alfrd` – `PipelineCore`, `PipelineContext`, `PipelineStepBase`, validators, results, events.
- `alfrd.config` – `BaseConfig`, `Config`.
- `alfrd.manifest` – `load_manifest`, `validate_manifest`, `ProjectManifest`, …
- `alfrd.repository` – `RepositoryService`, `add_repository`, …
- `alfrd.core.logframe` – `LogFrame`, `LogFrameAdapter`, `LogFrameEventSink`.
- `alfrd.runtime` – `RuntimeStore`, `RuntimeService`, `RuntimePipelineRunner`, models.
- `alfrd.gui` – `create_app()`, catalog readers, `/api/*`, `/studio/`.
- `alfrd.plugins` – `register`, `validate`, `validator`.

Deprecated (still work, warn):

- `alfrd.Pipeline` / `PipelineRun` → use `PipelineCore`.
- `alfrd.core.workflow.WorkflowManager` → use `PipelineCore`.
- `alfrd.core.logger` → use `alfrd.core.logging`.

---

## Google Sheets setup

Only needed for Google Sheets. Full guide: [Google: create credentials](https://developers.google.com/workspace/guides/create-credentials).

1. Open https://console.cloud.google.com/ and create a project.
2. Search **Google Sheets API**. Enable it.
3. **Create credentials → Application data**.
4. Name the account. Role: **Editor**. Finish.
5. Open the account. Copy its email.
6. **Keys → Add key → JSON**. Save the file.
7. Share your sheet with that email as **Editor**.

The Sheets API is free. Google may still ask for billing details.

---

## Credits

If you use ALFRD, please link to this repository in a footnote.

ALFRD was built in the SMILE project ("Search for Milli-Lenses"). SMILE is funded by the European Research Council (ERC), HORIZON ERC Grants 2021, grant agreement No. 101040021.
