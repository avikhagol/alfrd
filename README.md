# ALFRD

**A**utomated **L**ogical **FR**amework for **D**ynamic script execution.

Run pipeline steps. Track their progress in a table. See it all in a web UI.

> Contact me if you have any questions: [akumar@ia.forth.gr](mailto:akumar@ia.forth.gr)

---

## Contents

- [Install](#install)
- [Quick start: the web UI](#quick-start-the-web-ui)
- [`alfrd serve` examples](#alfrd-serve-examples)
- [Tell the Studio about your pipeline (`alfrd.yaml`)](#tell-the-studio-about-your-pipeline-alfrdyaml)
- [Run workflow steps per target (plans)](#run-workflow-steps-per-target-plans)
- [Run steps from Python](#run-steps-from-python)
- [Track progress in a table](#track-progress-in-a-table)
- [Runtime database](#runtime-database)
- [Public API](#public-api)
- [Google Sheets setup](#google-sheets-setup)
- [Credits](#credits)

---

## Install

### Recommended

install [uv](https://docs.astral.sh/uv/) first, then `alfrd`.

```bash
uv tool install alfrd
```

### From PyPI

```bash
pip install "alfrd"     # with the web UI (recommended)
pip install alfrd            # core only
```

From source:

```bash
git clone https://github.com/avialxee/alfrd
cd alfrd
pip install "."
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
- It can schedule and run AVICA pipeline.
- The URL is `http://127.0.0.1:5000/studio/`.

What you get:

| View | Shows |
|---|---|
| **Overview** | All targets × steps. Status, MS path, notes. |
| **Workflow** | Steps as a list or graph. Step parameters and logs. |
| **Metadata** | Metadata health per step. Config. Input files. |
| **Results** | Timings and progress. Latest run or full history and AVICA pipeline results. |
| **Logs** | Every log file, grouped. Click to open. Expand to full screen, or dock it as a Log Stream tab that keeps following on every view. |
| **Settings** | Edit and save `alfrd.yaml`. |

No server? Use browser mode:

```bash
alfrd studio        # opens http://127.0.0.1:8080/, then Import → Open project folder
```

Full guide: [docs/studio-guide.md](https://github.com/avialxee/alfrd/blob/HEAD/docs/studio-guide.md)

---

## `alfrd serve` examples

Follow the configuration instructions for [avica](https://avikhagol.github.io/avica-demos)

```bash
# Open the project in the current folder
cd /data/avica_0.3
alfrd serve

# Open a project somewhere else
alfrd serve --project /data/avica_0.3

# Another port, no browser pop-up (e.g. over SSH)
alfrd serve --port 8050 --no-browser

# Try it with demo data
alfrd serve --demo

# Keep the runtime database in a chosen file
alfrd serve --runtime-db ~/alfrd/runtime.sqlite

# Also show every project connected before
alfrd serve --all-projects

# A parent folder of several projects: opens every sub-folder with its own
# alfrd.yaml (2 levels deep; --discover-depth N, or --no-discover)
cd /data/data_reductions      # pipe_comparison/alfrd.yaml, alma/alfrd.yaml, …
alfrd serve

# Check the project folder less often (default 2 s while busy; 0 = no live updates)
alfrd serve --live-interval 5
```

The Studio updates by itself (**● Live** in the top bar): changed files are re-read, and open logs follow the file as it grows. Nothing runs while the tab is hidden. See [docs/studio-guide.md](docs/studio-guide.md#live-updates).

Projects are remembered in `~/.alfrd/runtime.sqlite`:

```bash
alfrd projects list              # what is remembered
alfrd projects forget OLD_NAME   # remove one (files stay on disk)
```

Or in the Studio: **⚙ Settings → Known projects → Forget**. Forgot one by mistake? **↻ Rediscover** there brings back the serve folder (or the projects found under it) and anything forgotten since the server started.

No `alfrd.yaml` in an AVICA folder (`avica.inp`, `avica.logs/`, `reductions/`)? `alfrd serve` uses the built-in default (an AVICA manifest, `name` = folder name). `alfrd manifest default -o alfrd.yaml` writes it so you can edit it.

Connect more projects: **Import → ALFRD server → Browse…** walks the server's folders (not your laptop's), marks folders with an `alfrd.yaml`, and connects one or all of them. A folder without `alfrd.yaml` connects too, with the default one.

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

## Run workflow steps per target (plans)

A **plan CSV** has one row per target and one column per step. `todo` runs the cell, an empty cell or `skip` doesn't. ALFRD writes `running`, `done`, `failed`, `blocked` (after a failed step), `interrupted` or `cancelled` back into the file:

```text
TARGET_NAME,FILENAMES,PROJECT_CODE,WORKDIR,preprocess_fitsidi,fits_to_ms,avica_avg
J0742+103,"bv019a.idifits,bv019b.idifits",BV019,,done,running,todo
```

The commands come from `alfrd.yaml`. The `avica` template already has them:

```yaml
entrypoint:
  - {name: avica-step, cmd: [avica, pipe, run, --t, "{target}", --f, "{FILENAMES}", "{step}"]}
execution:
  step_entrypoint: avica-step   # or workflows[].entrypoint, or a step's own entrypoint / cmd
  mode: step                    # step: one call per target x step | target: one per target | batch: one per plan
  concurrency: 1
  on_failure: stop_target       # stop_target | continue | stop_plan
  status_from: both             # avica pipe run exits 0 after a failed step: the result CSV row decides too
```

Each argv item is filled separately (no shell). `{target}`, `{step}`, `{from_step}`, `{targets}` and `{plan_csv}` are available, and so is every plan CSV column. A value that is missing stops the command before it starts.

```bash
alfrd plan new --from-csv targets.csv --from fits_to_ms   # or --targets a,b --files x.idifits
alfrd plan run --dry-run                                  # the commands, in order
alfrd plan run                                            # starts a background runner
alfrd plan status                                         # grid, running commands, queue
alfrd plan status --json                                  # the same as a stable document (scripts, Claude)
alfrd plan wait --until done --timeout 3600               # block until it finishes (exit code says how)
alfrd plan pause | resume [--retry-failed] | cancel
```

With `execution.concurrency` above 1, rows run in parallel unless they conflict: the avica template serializes rows that share a FITS file name (`execution.serialize_on: [files]`), since those write the same files; the same target with other FITS files runs in parallel.

To follow a plan from a script or an AI assistant, use `alfrd plan status|wait|events|log --json` or `GET /api/v1/projects/<p>/plans/latest` on `alfrd serve`: a read-only, versioned document with a one-line summary, failures with reasons and a resume cursor. HTTP requests need `Authorization: Bearer <token>` with the server's access token (see [Opening the Studio](docs/studio-guide.md#opening-the-studio-access-token) and `alfrd url`); other hosts use `ALFRD_API_TOKEN`. See [docs/status-api.md](docs/status-api.md).

In the Studio (`alfrd serve`), open **Workflow → Run…**. The **Schedule** tab shows the targets × steps grid and the execution order (running, queued with ETAs, failed, done). The graph and list show the plan's progress.

Runs keep going when the Studio, `alfrd serve` or the terminal is closed. The runner is a detached process, and every command writes straight to its log under `.alfrd/plans/<id>/`. A new runner re-adopts commands that are still alive. After a reboot, `alfrd serve` or `alfrd plan reconcile` marks the plan *interrupted*, and **Resume** continues from the first unfinished cell. Result CSVs are found by name: `result_<target>_<code>_<workdir>.csv` (newer AVICA) and `<target>_result.csv`.

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

## Project creation and agent handoffs

Create a blank project with `alfrd projects create ./my-project`, or a five-cycle
Claude Code ↔ Codex workflow with:

```bash
rtk alfrd projects create ./my-project --template agent-loop --task "Implement the widget" --iterations 5
rtk alfrd plan run --root ./my-project --dry-run
rtk alfrd plan run --root ./my-project
```

Studio settings → **New project** creates the same scaffold. Schedule → **More →
Handoffs** shows and edits Markdown handoffs. [Configuration and manual chat
turns](docs/agent-loops.md).
