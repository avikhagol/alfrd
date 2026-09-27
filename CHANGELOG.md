# Changelog

## [0.2.0.5]

#### Added

- **Real runs instead of the simulation.** Workflow → **Run…** (and `alfrd plan new|run|status|pause|resume|cancel`) runs the steps of a plan CSV (one row per target, one column per step, cells `todo` / `done` / `failed` / ...) with the commands from alfrd.yaml (`entrypoint:` + `execution:`, per-step `entrypoint` / `cmd` / `timeout`). The avica template ships `avica pipe run --t {target} --f {FILENAMES} {step}` (modes step / target / batch). No shell. Placeholders are filled per argument, and missing values are refused before anything starts. Dry run lists the commands in order.
- Runs survive the Studio, `alfrd serve` and the terminal. A detached runner per plan keeps its state in `.alfrd/plans/<id>/` (not in the runtime DB). Each command runs in its own session and logs straight to a file. A new runner re-adopts live commands, finalizes finished ones from their exit file, and continues the queue. `alfrd serve` reconciles at start, and a plan whose commands died (reboot) is shown as *interrupted*, with Resume.
- A step's status is also checked against AVICA's result CSV (`execution.status_from: both`), because `avica pipe run` exits 0 after a failed step. The work dir and project code found there are written back to the plan CSV.
- Workflow → **Schedule**: targets × steps grid (click a cell: log, retry, skip) and the execution order (running, queued with ETAs from past step durations, failed, done). Graph nodes and the list show plan counts. The inspector shows the cell, its command and its log. User edits to the plan CSV (skip / todo / new rows) are honoured while it runs.
- Supports `result__<target>__<project_code>__<workdirname>.csv` or any similar pattern for result CSV names.

- `alfrd serve` in a folder that is not a project opens every sub-folder with its own `alfrd.yaml` / `.alfrd.yaml` (2 levels deep by default). Options `--discover/--no-discover`, `--discover-depth N`. Never searched: `*.ms`, `raw/`, `tmp_*`, `calibration_tables`, dot-folders, and the inside of a project. The first project (by folder) is opened; the others are in the project picker. Discovered folders are **Rediscover** candidates.
- Import → Connect → **Browse…** (server mode): click through the folders of the machine running `alfrd serve` (useful over an SSH tunnel), with a badge on ALFRD projects, **Connect**, **Connect all projects here** and **Use this folder**. New endpoint `GET /api/studio/fs/list` (loopback + CSRF token, like writes).
- Log Stream tabs: the `>_` button on a log, or **Minimize to Log Stream** in full screen, docks it as a tab of the Log Stream panel. It keeps following the file while you use Overview, Workflow, … Up to 6 tabs, remembered across reloads. The panel can be resized (drag its top edge, or arrow keys).
- Import → Connect (typed path, **Browse…** → **Connect this folder**, and the dashboard's Connect form) accepts a folder without `alfrd.yaml`: it is connected with the default manifest (`name` = folder name), like `alfrd serve` in such a folder. A parent's `alfrd.yaml` is never used for a sub-folder.

#### Changed

- Studio size budget raised from 150 to 165 KB gzip (it was at 149 KB; the run UI adds ~12 KB).

#### Fixed

- A project connected from the Studio stayed in view only until the page was reloaded (it was added to the scope in the browser, not on the server).

[0.2.0.5]: https://github.com/avikhagol/alfrd/compare/v0.2.0.4...v0.2.0.5
[0.2.0.4]: https://github.com/avikhagol/alfrd/compare/v0.2.0.3...v0.2.0.4
[0.2.0.3]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.3  
[0.2.0.2]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.2
[0.2.0.1]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.1
[0.2.0.0]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.0