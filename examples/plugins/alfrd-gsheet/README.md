# Google Sheet step sync

Writes each finished plan step's status, timing, usage and other fields into
a Google Sheet row for that target. It can also read notes or todo/skip
requests from the sheet into the plan. Mirroring runs in the plan runner's step
hooks. The plugin also provides the `alfrd gsheet` CLI, a Studio panel and a
palette check.

Status: offline transport tests, the scheduler integration, an isolated
install and the Studio browser tests pass. Live verification against a real
Google Sheet is still pending.
Installing the plugin registers hooks; projects without a mapping make no API
calls and do not need credentials.

## Setup walkthrough

1. **Install** the plugin and restart `alfrd serve`:
   `alfrd plugin install ./examples/plugins/alfrd-gsheet`.
2. **Create a service account** in a Google Cloud project (IAM & Admin →
   Service accounts), add a JSON key, and download it. Enable the
   **Google Sheets API** in the same project. The **Google Drive API** is only
   needed if you tick **Check edit permission with the Drive API**.
3. **Share the spreadsheet** with the key's `client_email`
   (`…@….iam.gserviceaccount.com`) as an **Editor**. Use a dedicated sheet or
   a copy until you trust the mapping.
4. **Configure**: in Settings → Plugins → Google Sheet → Configure, paste the
   whole JSON key and set the default spreadsheet id or URL. Press **Test**. It
   writes nothing; it reports Editor access and the number of worksheets.
5. **Generate a mapping**:
   `alfrd gsheet init <project> [--worksheet Targets]`. The sheet needs a header
   row and a column whose values match the plan's target names.
   Edit the file to add fields, formats or inbound rules (see below).
6. **Validate**: `alfrd gsheet validate <project>`, or **Google Sheet: validate
   mapping** in the Studio palette. Fix each reported column or step.
7. **Preview, then backfill**: `alfrd gsheet push <project> --dry-run` shows the
   cells it would write. Run without `--dry-run` to fill in steps that finished
   before the mapping existed. From then on, every launched step syncs on its
   own.
8. Optionally add the [Studio panel](#studio-panel-and-validation).

To stop syncing a project, set `enabled: false` or delete `alfrd.gsheet.yaml`.
The setting applies from the next launched unit.

## Development

From the alfrd repository, run the offline tests with the host environment:

```sh
uv run python -m pytest -q examples/plugins/alfrd-gsheet/tests
```

No service account, Google libraries or network are needed to run these tests.
The optional real-auth signing test skips when Google's libraries are absent.
To include it without accessing a real sheet:

```sh
uv run --with 'google-auth[requests]==2.61.0' --with 'requests==2.32.5' \
  python -m pytest -q examples/plugins/alfrd-gsheet/tests
```

The engine accepts a `SheetsClient`; its protocol is in `alfrd_gsheet/client.py`.
The transport dependencies are pinned in `pyproject.toml`; alfrd and its
PyYAML/typer dependencies come from the host, not a second alfrd install.

Install from the repository:

```sh
alfrd plugin install ./examples/plugins/alfrd-gsheet
```

## Settings and Test

In Settings → Plugins → Google Sheet → Configure, paste a service-account JSON
key into **Service-account key JSON** and set **Default spreadsheet id or URL**.
Enable the Sheets API in the key's Google Cloud project and share the
spreadsheet with the key's `client_email` as an Editor. Credentials stay
in the host's private plugin settings file; public settings show only whether
the key is set. Do not put the key in the project mapping.

**Test** reads spreadsheet metadata and lists worksheets without writing; a
successful result reports the worksheet count. By default it checks read access
only, and edit access is verified on the first write. Tick **Check edit
permission with the Drive API** to have Test also ask Drive whether the account
can edit; that needs the Drive API enabled in the same project. Syncing itself
uses only the Sheets API.
Read-only access asks you to grant Editor permission; 403/404 errors ask you to
check sharing, the spreadsheet id and enabled APIs. Test requires a default
spreadsheet even when individual mappings specify their own ids.

Defaults: `value_input_option: RAW`, `conflict_policy: skip`,
`request_timeout: 15`, `dry_run: false`, `check_drive: false`. `USER_ENTERED` lets Sheets interpret
values as formulas/numbers/dates. Request timeout must be positive and finite.
Credentials are cached per process, while each API call uses a fresh session.
Authentication, lock waits, up to three attempts for 429/5xx, and exponential
backoff with jitter share the caller's absolute deadline. Socket timeouts are
capped by the request timeout and remaining budget. A wall-time guard also
bounds authentication/library waits; an already-sent HTTP write may finish
after timeout, so a timeout does not prove the write was rejected. No subsequent
retry or API request starts after the deadline.

## Mapping file

Save `<project>/alfrd.gsheet.yaml`. This file is separate from `alfrd.yaml` and
is re-read before each launched unit. A unit keeps the mapping that it started
with. Missing files, `enabled: false`, and units with no matching rules do
nothing. Skipped, blocked and never-launched units get no lifecycle calls.

```yaml
version: 1
enabled: true
spreadsheet_id: 1AbCdEfGhIjKlMnOpQrStUvWxYz
worksheet: Targets                 # or gid: 0 instead of worksheet
header_row: 1
rows:
  key_column: TARGET_NAME
  code_column: PROJECT_CODE        # optional; distinguishes repeated targets
  missing_row: skip                # skip | append
read_range: A1:ZZ                  # optional; must include header_row
verify_before_write: true
outbound:
  - step: calibrate
    column: calibrate
    field: status
  - step: calibrate
    column: calibrate RAM
    field: usage.peak_mem
    format: bytes
  - step: '*'
    column: '{step} finished'
    field: finished
    format: datetime
    when: [done, failed]
  - step: calibrate
    column: calibrate summary
    field: template
    template: '{status} ({duration_s:.0f}s)'
inbound:
  - column: notes
    to: plan_column
    plan_column: notes
  - column: image requested
    to: plan_cell
    step: image
```

Headers match after trimming whitespace and ignoring case. A matching header
takes precedence over a literal column letter, including letters beyond Z.
Ambiguous headers and repeated row identities fail validation. Outbound rules
cannot change the target/code columns. Wildcards expand over project steps;
every substituted header must exist. Duplicate `(step, column)` targets are
rejected, including duplicates expressed once as a header and once as letters.

Inbound `plan_column` must name an existing extra column. It cannot change a
workflow step or the target, code, workdir or files columns. Inbound `plan_cell`
accepts only `todo` and `skip`. It calls the host's CSV updater under the plan
lock with an `only_if` guard excluding `running`. No step in the about-to-run
unit is touched; inbound requests affect later steps. Dry run changes neither
the sheet nor the plan CSV.

## Fields and formats

Fields: `status`, `exit_code`, `error`, `started`, `finished`, `duration_s`,
`log_path`, `log_path_rel`, `total_cost_usd`, `model`, `outcome`, `unit_id`,
`plan_id`, `target`, `code`, `usage.<key>`, `agent_usage.<key>`, `results.<key>`,
and `template`. Status comes from the individual row/step cell, falling back to
the unit status when unavailable. Agent/results dotted paths may be nested.

Usage keys: `wall_s`, `cpu_s`, `avg_cores`, `peak_mem`, `peak_mem_kind`,
`peak_rss`, `max_rss`, `read_bytes`, `write_bytes`, `samples`, `interval_s`,
`limited`, `counted`, `cgroup_memory_peak`, `workdir`, `workdir_start`,
`workdir_end`. Compatibility aliases: `wall` → `wall_s`, `cpu` → `cpu_s`,
`cores` → `avg_cores`, `io_read` → `read_bytes`, `io_write` → `write_bytes`.

| Format | Example |
| --- | --- |
| `raw` (default) | `done`, `0`; None/NaN becomes empty |
| `bytes` | `1048576` → `1 MiB` |
| `seconds` | `12.5` → `12.5s` |
| `duration` | `3661` → `01:01:01` |
| `datetime` | ISO timestamp (retains timezone) |
| `percent` | `0.125` → `12.5%` |
| `json` | Compact JSON with sorted object keys |

Templates use the same fields and Python format specifications. For example,
`{status} ({duration_s:.0f}s)`. Missing values are empty; a numeric specification
on a missing value is a validation/formatting error and suppresses that sync.

## Writes and conflicts

Ordinary units read once before launch. After completion, an empty diff makes
no further API calls. Changed cells are checked in one `batchGet` (unless
`verify_before_write: false`) and sent in one `batchUpdate`, each range naming
one cell. Appending a missing target takes an additional full used-range read
under the sheet lock to choose vacant rows safely. Existing rows retain their
original snapshot for conflict checks.

Policies: `skip` omits cells edited since the snapshot; `overwrite` writes
anyway; `fail-soft` writes nothing for that unit and reports a hook warning.
Append refuses a row whose identity or header changed before writing, even
under `overwrite`. Re-adopted units take a fresh snapshot and do not replay
inbound changes.

The engine has a 25-second total budget per call, leaving margin for the
30-second public hook timeout. All transport methods receive the same absolute
deadline, and both sheet and plan lock acquisition are bounded. POSIX flock
serializes processes under `<project>/.alfrd/locks/`; on Windows the sheet lock
serializes threads in this process only, matching the host's flock limitation.

History is written to `.alfrd/gsheet/sync.jsonl`, one entry per outcome or failed
before call. It includes changed cell addresses, counts, conflicts and safe
error text. Values, settings and context payloads are omitted to keep secrets
out of history. The core reports failures in `runner.log` and
`plugin.hook_failed` without changing the unit's status.

## Command line

All commands take the project folder. Errors print one `gsheet: …` line on
stderr and exit 1. Credentials and Google response bodies are never printed.

| Command | What it does |
|---|---|
| `alfrd gsheet init <project> [--spreadsheet ID\|URL] [--worksheet NAME] [--header-row N] [--force]` | Reads the header row (default: first tab, row 1) and writes a starter `alfrd.gsheet.yaml`. Each step id that matches a column name gets a `status` rule. The file also includes commented usage/time/log examples. Without `--spreadsheet`, the default spreadsheet from Settings is used and not written to the file. The command refuses to overwrite the file without `--force`. |
| `alfrd gsheet validate <project> [--offline]` | Checks the mapping against the project's steps, then against the live header (one read). `--offline` skips the sheet. A missing or disabled mapping reports "Sync is off" and exits 0. |
| `alfrd gsheet diff <project> --unit ID [--plan ID]` | Replays one finished unit's after-sync against the current sheet. Prints `address: 'old' → 'new'` for each cell that would change. Writes nothing, including history. |
| `alfrd gsheet push <project> [--targets T1,T2] [--steps s1,s2] [--plan ID] [--dry-run]` | Backfills from finished units in all plans (or `--plan`). It takes the newest unit per target and step and uses the same diff and conflict policy as the hooks: one read, one conflict read, one write. The outcome is recorded in history as unit `push`. The `dry_run` setting also turns a push into a preview. |
| `alfrd gsheet status <project> [-n 20] [--json]` | The most recent `sync.jsonl` entries (UTC time, unit, steps, result, cells, conflicts, error). |

## Google quotas

Requests per launched unit:

| Case | Reads | Writes |
|---|---|---|
| Unit whose mapped cells don't change | 1 | 0 |
| Ordinary unit | 2 (snapshot + conflict check) | 1 `batchUpdate` |
| `verify_before_write: false` | 1 | 1 |
| Appending a missing target row | +1 full used-range read | (same write) |
| Mapping with `gid` instead of `worksheet` | +1 metadata read | — |
| `dry_run` | 1 | 0 |

`push` makes one read, one conflict read and one write for any number of
units. `validate` makes one read; **Test** makes one metadata read and one
Drive permission read. A batch write counts as one request, however many
cells it changes.

The Sheets API limits requests per minute both per Cloud project and per
user (here, the service account). The published defaults are 300 per
project and 60 per user, counted separately for reads and writes. Check
current values under APIs & Services → Google Sheets API → Quotas. About 30
units finishing in one minute under one service account can therefore reach
the per-user read limit. 429 responses are retried up to three times within
the 25-second budget, and then the sync for that unit fails and is recorded.
Use `alfrd gsheet push` later to fill in what was missed, or give busy
projects separate service accounts.

## Troubleshooting

Failures never change a step's result. They appear in `runner.log`, as a
`plugin.hook_failed` notification, in `alfrd gsheet status` and in the Studio panel.

| Message | Cause and fix |
|---|---|
| `Google denied access (403, Sheets API): share the sheet …` | Share the spreadsheet with the key's `client_email` as Editor. |
| `Google denied access (403): the Drive API is disabled in the key's Google Cloud project …` | Only Test's edit check uses Drive. Open the printed link and enable it (allow a few minutes), or untick **Check edit permission with the Drive API**. |
| `Google denied access (403): the Sheets API is disabled …` | Enable the Sheets API with the printed link. |
| `Google denied access (403): … quota exceeded` | Too many requests per minute; it clears by itself. |
| `Google Sheet not found (404) …` | Wrong spreadsheet id or URL, or the sheet isn't shared with the service account. |
| `The service account has read-only access …` | It is shared as Viewer or Commenter; change it to Editor. |
| `Google rejected the service-account credentials (401).` / `Service-account authentication failed …` | The key was deleted or disabled, or the account was removed. Create a new key and paste it again. |
| `Service-account credentials must be valid JSON.` / `… must contain a service-account client_email and private_key.` | Paste the whole downloaded JSON file, not an OAuth client or an API key. |
| `Set a default spreadsheet id or URL before running Test.` | Test needs the default spreadsheet, even when mappings name their own. |
| `Google Sheet unavailable after 3 attempts (HTTP 429)` | Quota exceeded; see [Google quotas](#google-quotas). Backfill later with `push`. |
| `Google Sheet request deadline exceeded.` / `… sync time budget exhausted` | The network is slow or Google responded slowly. Lower `request_timeout` so retries fit in the budget. A timed-out write may still have landed; check with `diff`. |
| `Google Sheet sync lock timed out` | Another unit or `push` held the sheet lock for the whole budget. It usually clears; otherwise look for a stuck `alfrd gsheet` process. |
| `unknown sheet column 'X'` / `N problems: unknown column …` | A header was renamed or deleted, or a `{step}` wildcard names a column that doesn't exist. Rename the header or fix the rule; then run `validate`. |
| `ambiguous sheet column …` | Two headers have the same name after trimming and ignoring case. Rename one, or use the column letter. |
| `duplicate sheet row identity …` | The key column repeats a target. Add `rows.code_column` or remove the duplicate row. |
| `unknown step 'x'` | The rule names a step that isn't in the project's workflow. |
| Cells listed under *conflicts* and not written | Someone edited the cell while the step ran (`conflict_policy: skip`). Rerun `push` once the edit is resolved, or choose `overwrite`. |
| Nothing is written | Check that `enabled: true` is set and the target is in the key column. With `missing_row: skip`, absent targets are ignored, and the `when` filters must match. A `Dry run` chip or setting writes nothing. |
| Panel says "No step has finished since sync was set up." | No hooked unit has finished yet. The legacy `alfrd runtime` worker path has no hooks; use `push`. |

## Studio panel and validation

After installing and restarting Studio, the panel appears automatically for any
project with an `alfrd.gsheet.yaml`, including disabled or invalid mappings. An
explicit `views.metadata` entry is optional; existing entries keep working and
are not duplicated:

```yaml
views:
  metadata:
    - panel: gsheet_sync
      title: Google Sheet
      scope: project
```

The panel reads only `alfrd.gsheet.yaml` and `.alfrd/gsheet/sync.jsonl`. Opening it
makes no Google requests. It shows the newest sync per step, ten initially, with
**Show all** to expand the table. Counts are unit totals when a unit covers several
steps. History scanning is limited to the latest 1 MiB; `alfrd gsheet status` can
inspect older records. Interrupted or malformed history lines are ignored.

Use **Google Sheet: validate mapping** in the command palette for an explicit live,
read-only check of the selected project. It shares validation with the CLI and
reports mapping problems or connection errors in a toast. The request uses the
host's authenticated, loopback and CSRF-protected project-check endpoint.

Because the panel reads no global settings, its dry-run chip reflects the most
recent recorded sync. A sheet id inherited from Settings becomes a link after the
first recorded sync. A mapping with a numeric `gid` links directly to that tab;
worksheet titles link to the spreadsheet because resolving a title to a gid would
require a Google metadata request. Validate after changing global settings. The
panel checks intrinsic mapping syntax; workflow step and live-column checks belong
to the explicit validation command.

## Studio

The project home shows a **Google Sheet** card (it appears after restarting
`alfrd serve` with the plugin enabled). Everything below works without a terminal
and never edits `alfrd.yaml`.

- **Attach Google Sheet** (or the palette command **Google Sheet: attach…**) opens a
  three-step dialog: paste a sheet URL or ID (or use the Settings default), **Load**
  it, pick the tab and header row, then the target-name column (guessed from
  `TARGET_NAME`, `target`, `source`, `name`) and an optional project-code column.
  The first key values and how many match the project's plan targets are shown before
  you attach. A URL with `#gid=` preselects that tab. Without a service-account key
  the card links to Settings → Plugins → Google Sheet instead.
- **Edit mapping** (or **Google Sheet: open mapping editor**) edits outbound rules
  (step, column, field with usage/results key, format, when, template), inbound rules
  and options. Rows can be added, duplicated, moved and deleted; **Add status columns
  for all steps** adds the `status` rules `init` would add. Problems from live
  validation (600 ms after a change) appear next to the control and in a summary;
  **Save** stays disabled until they are fixed. Saving rewrites the file in a stable
  order and removes comments, so the first save asks for confirmation. If the file
  changed on disk since it was opened, Save offers **Reload** instead of overwriting.
- **Validate**, **Preview** (cell, target, step, column, old → new; the first 500 are
  listed) and **Backfill…** (preview first, then "Write N cells to <tab>?"). With
  Settings dry run on, backfill writes nothing and says so.
- **Sync enabled** switch and **Detach…**: disable sync and keep the file, or delete
  `alfrd.gsheet.yaml` (sync history stays).

The card, attach and editor call protected project actions. `plugin_actions: false` in
`plugins.json` (or `alfrd serve --no-plugin-actions`) disables Attach, Save, the switch,
Backfill and Detach; Validate and Preview stay available. Mapping edits check a SHA-256
revision under `.alfrd/locks/alfrd.gsheet.yaml.lock` and replace the file atomically;
CLI init uses the same lock, so hand edits and the Studio never silently overwrite
each other.
