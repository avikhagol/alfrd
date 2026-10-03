# Changelog

## [Unreleased]

#### Fixed

- **Failed launches trigger recovery once.**
  - Why it matters: failure actions no longer run twice.
  - How to use: retry a failed turn normally.
- **Loops accept either manifest filename.**
  - Why it matters: hidden manifests no longer crash loops.
  - How to use: keep alfrd.yaml or .alfrd.yaml.
- **Invalid manual replies keep waiting.**
  - Why it matters: missing headings no longer fail the plan.
  - How to use: fix the listed headings and resubmit.
- **Loop turns cannot be skipped.**
  - Why it matters: every agent receives the preceding reply.
  - How to use: retry turns, or cancel the plan.

#### Added

- **Turn headings and instructions can be customised.**
  - Why it matters: workflows can use their own reply structure.
  - How to use: set loop.headings and loop.contract in YAML.
- **Project settings include folder and shell permissions.**
  - Why it matters: agents can access your chosen working folders.
  - How to use: open Agents / human review, apply, then save.
- **Manual replies can be submitted from the command line.**
  - Why it matters: chat replies work without Studio.
  - How to use: run alfrd plan response with your Markdown file.
- **Handoffs loads large replies in pages.**
  - Why it matters: opening the dialog stays quick.
  - How to use: expand a turn, then Load more or Copy.
- **Handoffs protects unsaved edits.**
  - Why it matters: switching files or refreshing preserves your choice.
  - How to use: save edits, or confirm discarding them.
- **Schedule shows turns, models and elapsed time.**
  - Why it matters: progress and waiting states are easier to follow.
  - How to use: follow Turn N of M; submit waiting replies.

#### Changed

- **New scaffolds follow the workflow's handoff filenames.**
  - Why it matters: reversed workflows receive the right initial task.
  - How to use: create a project; Codex uses next-step-codex.md.
- **Loop creation previews the number of turns.**
  - Why it matters: iteration counts show their actual workload.
  - How to use: select agent-loop and adjust Iterations.
- **Loop Run hides settings that cannot apply.**
  - Why it matters: rejected options no longer distract users.
  - How to use: run the loop with its fixed settings.
- **Creation warns about unused iteration options.**
  - Why it matters: non-loop templates no longer silently ignore them.
  - How to use: choose a known template with --template.
- **Shared limits preserve the 10 MiB reply allowance.**
  - Why it matters: submission and publication use matching limits.
  - How to use: keep replies below 10 MiB.
- **Studio's loaded-on-demand size allowance is 62 KiB compressed.**
  - Why it matters: paging and settings fit the measured allowance.
  - How to use: startup loading remains unchanged.

#### Next

- Send reviewed replies back for another attempt.
- Retry replies with missing headings automatically.
- Use live status events instead of polling.
- Compare edited replies against the original agent reply.
- Save activity metadata less often.
- Make tool-result clipping configurable.
- Show estimated remaining time for agent turns.
- Support multiple task rows and workflows.
- Share the supported-agent list with Studio.

## [0.2.0.9]

#### Added

- **Human adjustments:** enable human review in Project settings → Agents / human review. Workflow exposes the same checkboxes for choosing which agent turns need review. The next agent waits until you approve or adjust the response; the original response is kept.
- **Agent models:** choose a Claude or Codex model in Project settings. Schedule and Handoffs distinguish the requested model from the model reported by the CLI.
- **Task editor:** edit `task.md` from Settings or Workflow. Save can also seed the first agent's handoff for a new run, with conflict protection and history.

#### Fixed

- **Claude activity logs:** tool calls, tool results, and streamed text now appear in the unit log. Raw events are archived separately; only the successful final response becomes the Markdown handoff.

#### Changed

- Review waiting survives runner loss and supports cancellation and runtime limits. Review applies to the handoff, not to code changes already made by the agent.
- The on-demand Studio payload cap is 60 KiB gzip to include the agent and task dialogs. The startup cap stays at 165 KiB.

#### Added (project creation and execution)

- Project scaffolding with `alfrd projects create` and Studio settings → New project: basic and agent-loop templates, initial task, iteration count, and runtime registration. Existing files are preserved.
- Bounded Claude Code ↔ Codex workflows using `workflows[].repeat.iterations` (1–100). Five iterations run ten turns, starting with Claude planning and ending with a Codex closing report. The existing scheduler, plan CSV, pause/resume/cancel, retry, detached shim and status API remain the execution path.
- Entrypoint `stdin_file`, `output_file`, `output_capture`, `cwd`, environment and timeout settings. Prompts use immutable snapshots; final responses are captured separately from diagnostic logs, validated, archived, and atomically published to the configured recipient's Markdown file.
- Loop status metadata and cursor changes through the read-only CLI/HTTP status API, a Studio iteration indicator and Handoffs dialog, Markdown history and stale-edit protection, workspace locking that survives runner death, and manual chat responses (`manual: true` on an entrypoint).
- Runtime registration preserves repeated workflow order. Projects with no targets are selectable in Studio.

## [0.2.0.8]

#### Added

- **Plan status API** (`alfrd.plan_status/1`, read-only, for Claude and other harnesses; `docs/status-api.md`). `alfrd plan status --json [--detail summary|rows|full] [--since CURSOR]`, `alfrd plan wait --until done|failed|any-change`, `alfrd plan events [--follow]` (JSON Lines) and `alfrd plan log --target T --step S` work without a server; `alfrd serve` adds `GET /api/v1/projects/<p>/plans[/<id>]` (long poll with `wait`, SSE `…/events`, `…/log`). One document for all of them: plan, runner liveness (stale when the runner died), counts, progress with ETA, running commands, failures with reasons, rows waiting on a conflict, a generated one-line `summary`, a resume `cursor`, and entity paths. Exit codes 0 finished / 1 failures / 2 running / 3 paused-interrupted-cancelled / 4 not found / 5 stale. JSON Schema `alfrd/schemas/plan_status.v1.json`. Loopback only unless `ALFRD_API_TOKEN` is set (then a bearer token). No MCP server.
- **Concurrent targets, serialized on conflict**: with `execution.concurrency > 1`, rows run in parallel unless they conflict. `execution.serialize_on` lists the values rows hold (`target`, `files` = the FITS file names as a set, or any plan CSV column); `serialize_match: all` or `any` when several are listed. The avica template serializes on `files`: rows that share a FITS file name (they write the same files); the same target with other FITS files runs in parallel. A row holds them from its first step until it has nothing left to run, a blocked row does not hold up later rows, conflicting rows keep CSV order, and a restarted runner keeps the holds of adopted commands. Why a row waits is shown on the plan grid (*waiting* chip) and in the status API; the dry run counts conflict groups ("12 rows, 4 conflict groups, up to 3 at once"). Without a declared `serialize_on`, rows of one work dir (project code / work dir) still run one at a time as a safety net.
- **Step picker** in the Run dialog and Add target: *All steps* (tri-state) with a count, *Invert*, collapsible stage groups with their own tri-state box, shift-click and Shift+arrow ranges, *From … to …*; steps always in workflow order; Start / Add disabled with no step; the Run selection is remembered per project.
- **Version history of alfrd.yaml** (Project settings → *History*): every Studio or CLI save and every change on disk seen by the live watcher is a version in `.alfrd/history/` (last 200; `history: {files, keep, git_commit}` in alfrd.yaml adds e.g. `avica.inp`). Diff any two, *Restore* (saved as a new version), `git log` of the file when the project is a git repo. **Conflict guard**: a save of a file that changed on disk since it was loaded is refused (409) and the Studio shows the difference, with *Overwrite with mine* / *Load the disk version*.
- **Command palette** (Ctrl/Cmd+K, or the search button): projects, targets, project codes, work dirs, steps, logs, views, notes and actions (Run…, pause / resume / cancel plan, add target, re-scan, live on/off, history, settings) with a fuzzy match that favours word starts; recent items first; `?` or Tab searches file contents.
- **Resource usage per step**: the command wrapper samples every process of the command from /proc every `execution.usage_interval` s (5; CPU → cores, PSS memory, I/O bytes, work dir size at start and end). MPI ranks on the same machine count too, also when the launcher moves them to their own session or daemonizes them: the wrapper is a child subreaper and also follows its session and an `ALFRD_UNIT` environment marker; CPU of ranks that end between samples is kept (no double counting). It keeps sampling when the runner dies, writes `<unit>.usage.jsonl` and a summary in the exit file and the unit. Studio: live sparklines from a command row, the cell menu or the Workflow inspector; *Usage* in the Schedule toolbar gives median / peak per step. In `plan status --detail full`. Linux only (wall time elsewhere); MPI ranks on other nodes are not seen.
- **Full-text search** of logs, CSVs, alfrd.yaml, avica.meta files and notes (palette `?`): an SQLite FTS5 index per project in `~/.alfrd/search/`, built in the background and kept current from live updates (growing logs indexed from where they were left); hits open the lines around them. Benchmark on vasco_0.3 volumes: 116 MB of logs index in 1.9 s to 17 MB (15 %), queries p95 26 ms (`docs/search.md`). Falls back to a bounded scan without FTS5.
- **Notes** (annotations) in `alfrd.notes.jsonl` next to alfrd.yaml (append-only events; travels with the data and scan bundles): anchored to a target, cell, step or log line, with tags, resolve / reopen, orphan detection. Markers in Overview, the plan grid, Results and log headers; a notes panel per target; palette items; searchable; other Studios update through live events.
- **Entity paths** (`alfrd.entities`, `data/entities.js`): `{project, target, project_code, workdir, band, step, file, line}` with a stable URL form, used by the status API, search hits, notes and the generic view.
- **Template-driven Metadata view** (`alfrd.layout_generic`, JSON Schema `alfrd/schemas/template_views.v1.json`): a template or alfrd.yaml declares its folder `hierarchy` (levels, `{from: avica.x}` patterns, `targets_from`; a file names one target, by its most specific pattern) and `views.metadata` panels, each with a `scope` (a hierarchy level or `target`): `file_status` (the steps' `metadata:` files under the panel's `base`, default `{meta_dir}`, checked for required keys), `files` (a folder's files grouped by the step that writes them, JSON parsed, other targets' files left out), `json_fields`, `csv_table`, `text`, `image`, and client panels drawn by a Studio module (`avica_config`, `avica_inputs`). `GET /api/studio/projects/<p>/view?entity=…`. With `alfrd serve` the Metadata view is now drawn only from these panels (one card each, tabs per work dir / chip / night); the avica template declares Metadata health, avica.meta, Result CSV, diagnostics plots, AVICA configuration and rPicard inputs. A non-AVICA test project (`tests/fixtures/imaging_tree`: nights / chips, described only by its alfrd.yaml) and golden / parity tests against the AVICA reader cover it. The AVICA folder-mode view moved to a lazy module (`metadata_avica.js`).

#### Changed

- `alfrd plan status` no longer reconciles by default (reads never change anything); `--reconcile` does. `--no-reconcile` is still accepted.
- The on-demand (lazy) Studio payload cap is 56 KB gzip (was 40); the startup payload stays under 165 KB.
- Plans record `serialize_on`, `serialize_match` and `usage_interval` when they are created.
- **Studio settings** redesigned: facts, Projects, Preferences, Browser data and Server sections that wrap at any width (no sideways scrolling); projects are cards with a shortened folder path (full path on hover), the opened / shown / hidden badge and a Forget icon.
- **Schedule toolbar**: the plan picker, status, progress and counts, one primary action for the plan's state (Pause, Resume, Run remaining or New run…), Retry when something failed, and **More** (Add target, New run, Usage, Runner log, Refresh, Cancel). Plan CSV, mode × concurrency, on-failure policy and runner go on one muted line.
- **Execution order** is a compact list (target and step, status icon, time, log; start, pid and code in the tooltip) that docks at the bottom or at the side and folds away; the choice is remembered in the browser. *Done* starts folded.

#### Fixed

- Switching the header's project (or opening a project in Studio settings and coming back) now shows that project's workflow graph and list, results and metadata; a Re-scan keeps the header's project and target.

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
