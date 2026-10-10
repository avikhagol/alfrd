<img src="brand/alfrd-mark.svg" alt="ALFRD" width="44" align="right">

# Running steps per target (plans)

A **plan CSV** has one row per target and one column per step. `todo` runs the cell, an empty cell
or `skip` doesn't. ALFRD writes `running`, `done`, `failed`, `blocked` (after a failed step),
`interrupted` or `cancelled` back into the file:

```text
TARGET_NAME,FILENAMES,PROJECT_CODE,WORKDIR,preprocess_fitsidi,fits_to_ms,avica_avg
J0742+103,"bv019a.idifits,bv019b.idifits",BV019,,done,running,todo
```

## Commands

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

Each argv item is filled separately (no shell). Quote words with spaces in the Studio command editor.
Available placeholders:

- `{project_dir}` (alias `{root}`): absolute path of the project open in ALFRD.
- `{cwd}`: command working directory, set by `execution.cwd` (defaults to the project directory).
- `{target}`, `{project_code}`, `{workdir}`: values from the current plan row. `{workdir}` is the
  `WORKDIR` cell, usually `wd` or `wd_1` for AVICA, not a full path or the project directory.
  It is unavailable until that cell is populated (often by an earlier AVICA step).
- `{step}`, `{from_step}`, `{steps}`: current step, first step, and comma-separated unit steps.
- `{targets}`: current target, or comma-separated targets in batch mode.
- `{plan_csv}`, `{plan_id}`: plan CSV path and run identifier.
- Any plan CSV column, such as `{FILENAMES}`, `{PROJECT_CODE}`, or a custom column.

Row placeholders are unavailable in batch mode. A missing or empty value stops the command
before it starts. For a script in the open project, use e.g.
`python "{project_dir}/scripts/prepare.py" --target {target}`.

A step can override `execution.status_from` with `status_from: exit_code`, `result_csv`, or `both`.
In Studio, open **Edit step → More options → Status from** and choose **Command exit code only**
for custom scripts that do not write AVICA results. Exit code 0 succeeds; any nonzero exit code
fails. Other steps keep the plan default (`both` in the AVICA template).

```yaml
workflows:
  - name: avica
    steps:
      - fits_to_ms
      - id: calc_flux_err
        cmd: [python, "{project_dir}/scripts/calc_err_flux.py", "{target}"]
        status_from: exit_code
```

## Running a plan

```bash
alfrd plan new --from-csv targets.csv --from fits_to_ms   # or --targets a,b --files x.idifits
alfrd plan run --dry-run                                  # the commands, in order
alfrd plan run                                            # starts a background runner
alfrd plan status                                         # grid, running commands, queue
alfrd plan status --json                                  # the same as a stable document (scripts, Claude)
alfrd plan wait --until done --timeout 3600               # block until it finishes (exit code says how)
alfrd plan pause
alfrd plan resume --retry-failed                          # omit --retry-failed to leave failed cells alone
alfrd plan cancel
```

With `execution.concurrency` above 1, rows run in parallel unless they conflict: the avica template
serializes rows that share a FITS file name (`execution.serialize_on: [files]`), since those write
the same files; the same target with other FITS files runs in parallel.

In the Studio (`alfrd serve`), open **Workflow → Run…**. The **Schedule** tab shows the
targets × steps grid and the execution order (running, queued with ETAs, failed, done). The graph
and list show the plan's progress.

## Closing the terminal, reboots

Runs keep going when the Studio, `alfrd serve` or the terminal is closed. The runner is a detached
process, and every command writes straight to its log under `.alfrd/plans/<id>/`. A new runner
re-adopts commands that are still alive. After a reboot, `alfrd serve` or `alfrd plan reconcile`
marks the plan *interrupted*, and **Resume** continues from the first unfinished cell. Result CSVs
are found by name: `result_<target>_<code>_<workdir>.csv` (newer AVICA) and `<target>_result.csv`.

## Following a plan from a script

Use `alfrd plan status|wait|events|log --json` or `GET /api/v1/projects/<p>/plans/latest` on
`alfrd serve`: a read-only, versioned document with a one-line summary, failures with reasons and a
resume cursor. HTTP requests need `Authorization: Bearer <token>` with the server's access token
(see [Opening the Studio](studio-guide.md#opening-the-studio-access-token) and `alfrd url`); other
hosts use `ALFRD_API_TOKEN`. See [Plan status API](status-api.md).

To get a message when a plan finishes or fails, see [Notifications](notifications.md).
