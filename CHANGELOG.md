# Changelog

## [0.2.0.7]

#### Added

- **Target list** `alfrd.targets.csv` (alfrd.yaml `targets:`): sources with their FITS file names and project code, kept across plans. Import it from Overview → **Targets** or the Run dialog (column mapping, preview, merge / replace, line-numbered problems), or with `alfrd targets import FILE [--replace] [--dry-run]` / `alfrd targets show`. The Run dialog and `alfrd plan new --from-targets` take FILENAMES / PROJECT_CODE from it. Endpoints `GET/POST /api/studio/projects/<p>/targets`, `POST …/targets/preview` (writes need loopback + CSRF).
- **rPicard diagnostics in Results**: a new artifact kind `collection` (a folder of viewable files, `path_pattern` + `include` / `exclude` / `pinned` / `depth` / `run_order`). The avica template declares `rpicard_diagnostics` (`{workdir}/wd_{band}_{target}/diagnostics_*`). The card is collapsed and reads nothing until opened. It then shows run / band / code pickers, pinned summaries, per-folder thumbnail grids (60 at a time, loaded as they scroll into view), a lightbox, a sortable `fringes_overview.csv` table, and **Compare** for two runs side by side. `.ps` is download only, and SVG / HTML are never inline. Read-only endpoints `…/collections`, `…/collections/<name>/files`, `…/collections/<name>/file`. A file is served only if it belongs to a declared run below an AVICA work dir, and symlinked run folders are not followed.
- Overview → **Code** filter (AVICA project code).
- Overview selection: **Run…** (Run dialog with the checked targets preselected) and **Remove from targets file** (confirm; `POST …/targets/remove`, `alfrd targets remove NAME|NAME@CODE …`).

#### Changed

- Installation by default now includes the `gui` extras, with `flask` and `flask_sqlalchemy`.

- Overview: the browser-only "Re-queue" (which only marked a step queued in the browser) is gone. The drawer keeps **Retry step** for runtime-managed runs only.

- Overview toolbar grouped into **Status** (the counters are now the only status filter, with *No work folder*; the old *Needs attention* quick filter is gone, use the **Attention** counter), **Find** (search, code, project; the project filter is only shown when several projects are loaded) and **View**. A line under it shows removable chips for the active filters, "showing N of M" and *Clear all*. Typing in search keeps focus (only the grid is redrawn).
- Studio size budget split: the startup payload (index.html, studio.css and everything `js/app.js` imports) stays under 165 KB gzip. Features opened on demand (`import()`: targets dialog, diagnostics, `css/lazy.css`) and the templates are capped at 40 KB. A test checks the lazy modules are never imported at startup.

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

[0.2.0.6]: https://github.com/avikhagol/alfrd/compare/v0.2.0.5...v0.2.0.6
[0.2.0.5]: https://github.com/avikhagol/alfrd/compare/v0.2.0.4...v0.2.0.5
[0.2.0.4]: https://github.com/avikhagol/alfrd/compare/v0.2.0.3...v0.2.0.4
[0.2.0.3]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.3  
[0.2.0.2]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.2
[0.2.0.1]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.1
[0.2.0.0]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.0