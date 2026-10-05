---
name: alfrd
description: Drive ALFRD (plan CSVs, targets, alfrd.yaml, AVICA runs, agent loops, Studio API) from an agent with the fewest tokens. Use when a folder has alfrd.yaml / alfrd.plan.csv / .alfrd/, or the user asks to run, monitor, debug or configure ALFRD or AVICA pipeline steps or Claude/Codex agent loops.
---

# ALFRD for agents

ALFRD runs pipeline steps per target from a **plan CSV** (rows = targets,
columns = steps) with the commands declared in **alfrd.yaml**, in a detached
runner that outlives the terminal and `alfrd serve`. Every status command has a
compact, versioned JSON form made for you. Prefer it over reading files.

`-C DIR` (`--root`) is the project folder (holds `alfrd.yaml`); default `.`.
`[PLAN]` defaults to the latest plan.

## Token rules (read first)

1. **Start with one call:** `alfrd plan status -C DIR --json` (default
   `--detail summary`). Read `summary` (one sentence), `counts`, `failures[]`,
   `waiting[]`. Do not ask for `--detail rows|full` unless you need the grid;
   then page it with `--limit 20 --offset N`.
2. **Branch on the exit code, not the text:** 0 finished · 1 finished with
   failures · 2 running · 3 paused/interrupted/cancelled · 4 not found ·
   5 runner dead (stale; `alfrd plan reconcile -C DIR` or `status --reconcile`).
3. **Never poll in a loop.** Block instead:
   `alfrd plan wait -C DIR --until done|failed|any-change --timeout 1800 --json`.
4. **Keep the cursor.** Pass the previous `cursor` as `--since`; an unchanged
   plan answers `changed: false` and nothing else.
5. **Logs:** `alfrd plan log -C DIR --target T --step S -n 50 --json`
   (ANSI stripped, ≤200 lines / 64 KiB). Never `cat` a CASA / AVICA log; start at
   `-n 30` and widen only if the error is not there.
6. **Never read** `*.ms`, `*.ms.avg`, FITS / `*.idifits`, images, `*.ps`,
   `calibration_tables/`, `.alfrd/search/`, or `uv.lock`. Use the Metadata view
   or search (below) to find what's inside a tree.
7. **Dry-run before running:** `alfrd plan run -C DIR --dry-run` prints every
   command in order, and marks cells whose placeholders are missing (✗) before
   anything starts.
8. Don't open `src/alfrd/web/js/*` or `cli.py` to learn usage: `alfrd <group> --help`
   is shorter.

## Task → command

| Want | Command |
|---|---|
| Is anything running / what failed | `alfrd plan status -C DIR --json` |
| Why a cell failed | `failures[].reason` + `log`, then `alfrd plan log … --target T --step S -n 50 --json` |
| Wait for the end | `alfrd plan wait -C DIR --until done --timeout 3600 --json` |
| Stream changes | `alfrd plan events -C DIR --since CURSOR [--follow --timeout 600]` (JSON Lines) |
| Target list | `alfrd targets show -C DIR` · `alfrd targets import FILE -C DIR --dry-run` then without it |
| New plan | `alfrd plan new -C DIR --from-targets --from fits_to_ms [--to rpicard]` (or `--targets a,b --files x.idifits`, `--from-csv F`, `--from-results`, `--steps s1,s2`) |
| Add one target to a live plan | `alfrd plan add-row -C DIR -t T -f "a.idifits,b.idifits" --project-code CODE [--steps …]` |
| Start | `alfrd plan run -C DIR [-j N] [--on-failure stop_target\|continue\|stop_plan] [--retry-failed]` |
| Pause / resume / stop | `alfrd plan pause` · `alfrd plan resume [--retry-failed]` · `alfrd plan cancel --yes` |
| After reboot / crash | `alfrd plan reconcile -C DIR`, then `alfrd plan resume` |
| Check alfrd.yaml | `alfrd manifest validate DIR/alfrd.yaml` |
| No alfrd.yaml (AVICA tree) | `alfrd manifest default -o alfrd.yaml` (edit, then validate) |
| Studio (web UI) | `alfrd serve --project DIR --no-browser [--port 5000]` (no `-C` here) |
| Agent loop: run one task | `alfrd plan run -C DIR --target T` (see Agent loops) |
| Change a turn of a running loop | `alfrd plan turn PLAN t003-codex -C DIR --review / --manual / --after +1h / --after 0 / --model M / --clear` |
| Approve / reject a held handoff | Studio Handoffs, or `alfrd plan reject PLAN UNIT --reason "…" -C DIR` |
| Token baseline (10 production turns) | `alfrd plan baseline -C DIR [--from PLAN/UNIT] [--count 10] [--exclude TURN=evidence] [--json]` (exit 1 = not ready) |

## Ask the user before

`plan run`, `plan resume --retry-failed`, `plan cancel`, `plan new --force`,
`plan turn`, `plan reject`, Studio *Delete permanently* (deletes ALFRD's files;
with *Delete all files and folders* the whole project folder), setup-form saves, `targets import --replace`, `targets remove`, and
edits to `alfrd.yaml` or `avica.inp`. Runs start real CASA/MPI jobs or agent
turns that can take hours.

Safe without asking: every `status | wait | events | log | baseline`, `--dry-run`,
`targets show`, `manifest validate`.

## Editing state

- **Plan CSV** (`execution.plan_csv`, default `alfrd.plan.csv`): editing cells
  to `todo` / `skip` / empty or adding rows is fine while a plan runs (the
  runner re-reads it before each command). Prefer `plan add-row` over hand
  edits. Cell values ALFRD writes: `running done failed blocked interrupted cancelled`.
- **Never edit** `.alfrd/plans/<id>/` (runner state), `.alfrd/history/`, or
  `alfrd.notes.jsonl` by hand (append-only event log; use the API below).
- **alfrd.yaml**: every save is versioned in `.alfrd/history/`. Change one key
  with a targeted edit, then `alfrd manifest validate`. A step can be a bare
  name or a mapping that overrides the template.

## alfrd.yaml essentials

```yaml
name: my-project
template: avica            # AVICA defaults: steps, logs, metadata, entrypoints, views
workflows:
  - name: avica
    steps: [preprocess_fitsidi, fits_to_ms, phaseshift, avica_avg, avicameta_ms,
            avica_snr, avica_fill_input, avica_split_ms, rpicard]
execution:                 # keys override the template one by one
  mode: step               # step | target | batch
  concurrency: 1           # rows at once
  serialize_on: [files]    # rows sharing any FITS file name run one after another
  serialize_match: all     # all | any (when several keys are listed)
  on_failure: stop_target  # stop_target | continue | stop_plan
  status_from: both        # exit_code | result_csv | both (avica exits 0 on failure)
entrypoint:
  - {name: avica-step, cmd: [avica, pipe, run, --t, "{target}", --f, "{FILENAMES}", "{step}"]}
```

Setup forms: `quickstart: {setup: {type: form, target: {file: avica.inp | alfrd.yaml}, fields: {KEY: {type: textbox|textarea|number|toggle|path|select|list}}}}`
(details: `docs/studio-guide.md` → Setup forms). The AVICA template's form writes `avica.inp`; the agent-loop's writes the agents in `alfrd.yaml`.

Drop a template step: `steps: {phaseshift: {skip: true}}` (or `{id: phaseshift, skip: true}` in the
workflow list); it disappears from the workflow and new plan CSVs.

Placeholders: `{target} {step} {from_step} {targets} {plan_csv} {root}` plus
any plan CSV column (`{FILENAMES}`, `{PROJECT_CODE}`, `{WORKDIR}`). No shell:
one argv item each; a missing value refuses the command. Path patterns also
take `{workdir} {band} {meta_dir} {target_dir} {logs}` and `*`/`?`.
Full template with comments: `src/alfrd/web/assets/templates/avica.yaml`.
Non-AVICA trees: `hierarchy` + `views.metadata` (`docs/template-views.md`).

## Agent loops

`template: agent-loop`: agents take turns on one task, each reading the previous
turn's handoff (`{target}/next-step-<agent>.md`) and writing the next one.

```yaml
loop:
  workspace: worktree       # each task works in its own git worktree: {target}/workspace, branch alfrd/<task>
workflows:
  - name: agent-loop
    repeat:
      iterations: 10        # TOTAL turns (agent runs) = the project maximum per task
      sequence: [claude, claude, codex]   # one pass, repeats; may stop mid-pass
      # passes: 3           # instead of iterations: whole passes
      # sequence: [[claude, codex], [claude, claude, codex]]   # custom passes; the last repeats
    turns:                  # single turns, by number or id (t004-codex)
      3: {human_review: true}             # hold the handoff for a person
      t004-codex: {manual: true, after: "+1h"}   # a person writes it; start 1 h after turn 3
entrypoint:
  - {name: claude, cmd: [claude, -p], model: opus, fallback_models: [sonnet]}
  - {name: gemini, cmd: [gemini], adapter: generic, model_option: --model}   # any stdin→stdout CLI
```

- Step ids are `t<NNN>-<agent>`; older projects (`repeat.iterations` + `steps`)
  still count passes and use `i<NNN>-<step>`.
- Tasks: `{target}/task.md` + `{target}/.alfrd-task.json` (`{"iterations": N, "version": 2}`
  = turns, at most the project maximum). Tasks run in parallel as separate plans.
  ALFRD never commits, merges or pushes a worktree; the user merges `alfrd/<task>`.
  Outside git, tasks share one tree (`workspace_warning` on the plan).
- Review is decided when a turn's command ends, so `plan turn … --review` works
  on the running turn. Other overrides only on turns not yet started. They live
  in `.alfrd/plans/<id>/overrides.json` (this plan only; `alfrd.yaml` unchanged).
- Fallback: Claude gets `--fallback-model`; other agents are relaunched with the
  next model after a runtime failure (never after an invalid or rejected response).
- Each turn records `logical_turn_id`, `attempt_number`, `retry_of`, `outcome`
  (`accepted failed_validation rejected abandoned failed_runtime`), `usage_source`,
  `agent_usage.input_uncached_tokens`, and `handoff.raw_input_chars /
  trimmed_handoff_chars / prompt_chars / sections_truncated`.

## Without a server (Python, read-only)

```python
from alfrd import search                      # full-text over logs, CSVs, alfrd.yaml, avica.meta, notes
hits = search.scan(root, "SEFD EF", limit=10, budget=3.0)["hits"]   # [{rel, line, snippet, ...}]
ctx  = search.context(root, hits[0]["rel"], hits[0]["line"], around=20)

from alfrd import notes                        # annotations anchored to entity paths
notes.listing(root)                            # includes orphaned notes
notes.create(root, {"target": "J0742+103", "step": "rpicard"}, "EF flagged 03:10-03:40", ["rfi"])
```

Search words match as prefixes (3+ chars); all words must be in the same ~40-line
block. Target names like `J0742+103` are one word. Add a note only when the user asks.

## With `alfrd serve` (HTTP, loopback)

```
GET /api/v1/projects/<p>/plans/latest?detail=summary&since=CUR&wait=60   # long poll ≤60 s
GET /api/v1/projects/<p>/plans/latest/log?target=T&step=S&lines=50
GET /api/v1/projects/<p>/plans/latest/events?since=CUR                   # SSE
GET /api/studio/search?projects=<p>&q=WORDS&limit=20
GET /api/studio/search/context?project=<p>&path=REL&line=N&around=20
GET /api/studio/projects/<p>/view?entity=target=J0742%2B103              # evaluated Metadata panels
GET /api/studio/projects/<p>/notes
GET /api/studio/projects/<p>/tasks                                       # turns, worktree, latest run per task
GET /api/studio/projects/<p>/plans/<id>/turns                            # per-turn settings + what can still change
GET /api/studio/projects/<p>/quickstart                                  # template setup forms + current values
```

Same document as the CLI. Other hosts need `ALFRD_API_TOKEN` +
`Authorization: Bearer …`. Studio writes (notes, targets, alfrd.yaml) need
loopback + a CSRF token: use the Python calls or CLI instead of POSTing.

## Where things live

| Path | What |
|---|---|
| `alfrd.yaml` | project manifest (steps, commands, execution, views) |
| `alfrd.targets.csv` | target list: TARGET_NAME, FILENAMES, PROJECT_CODE (kept across plans) |
| `alfrd.plan.csv` | one run's grid; ALFRD writes cell states back |
| `.alfrd/plans/<id>/` | runner state, per-command logs, `<unit>.usage.jsonl`, `overrides.json` |
| `<task>/`, `<task>/workspace/` | agent-loop task: handoffs, `task.md`, `.alfrd-task.json`; its git worktree |
| `alfrd.notes.jsonl` | notes (append-only) |
| `reductions/<CODE>/<wd>/` | AVICA work dirs; result CSVs `result_<target>_<code>_<wd>.csv` or `<target>_result.csv` |
| `~/.alfrd/runtime.sqlite`, `~/.alfrd/search/` | known projects, search indexes (not in the project) |

Entity paths name things across all APIs:
`{project, target, project_code, workdir, band, step, file, line}` (URL form
`target=J0742%2B103&step=rpicard`). `failures[].entity` and search hits carry one.

## Debug a failed cell (minimal path)

1. `alfrd plan status -C DIR --json` → take `failures[0]` (`target`, `step`, `reason`, `exit_code`).
   Nothing failed but nothing runs? Check `waiting[]`: `kind: delay` (a step's `after`;
   `plan turn … --after 0` runs it now) or `loop.phase: awaiting_review` (a person must approve).
2. `alfrd plan log -C DIR --target T --step S -n 40 --json` → find the error line.
3. Still unclear: search (`search.scan(root, "<error word> T")`) and read ±20 lines.
4. Propose a fix; after the user agrees, `alfrd plan resume -C DIR --retry-failed`.
5. `alfrd plan wait -C DIR --until failed --timeout 1800 --json` to confirm it passes that step.
