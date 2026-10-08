# Project creation and agent loops (0.2.2)

Create a project without starting an agent:

```bash
rtk alfrd projects create ./my-project --template agent-loop \
  --task "Implement the widget and verify it" --iterations 10 --sequence claude,codex
rtk alfrd plan run --root ./my-project --dry-run
rtk alfrd plan run --root ./my-project
rtk alfrd plan status --root ./my-project --json
```

For a blank project, omit `--template` (defaults to `basic`). `--name` overrides
the folder name; `--task-file task.md` reads a task file. `--db` selects the local
runtime database. Studio settings → **New project** exposes the same creation
service. Neither creation path overwrites existing project files or launches
commands.

One iteration is **one agent turn**: `iterations` is the total number of turns,
and the project's value is the maximum any task may use. The agent sequence is
one pass and repeats until that total is reached, possibly stopping mid-pass;
the default `[claude, codex]` with 10 iterations gives five Claude and five Codex
turns. The first turn plans the task; later turns execute the incoming handoff
and plan the next task; the last turn supplies a closing report.

## Agent sequences

```yaml
workflows:
  - name: agent-loop
    repeat:
      iterations: 9                                   # total turns
      sequence: [claude, claude, claude, codex, codex, claude]
      # passes: 2                                     # instead of iterations: whole passes
      # sequence: [[claude, codex], [claude, claude, codex]]   # custom passes; the last repeats
      # handoff: "{target}/next-step-{agent}.md"      # the default
```

Sequence items are entrypoint names (or workflow step ids that name an
`entrypoint`). Turn N reads `next-step-<its agent>.md` and writes
`next-step-<next agent>.md`, so consecutive turns of one agent and any number
of agents work. Step ids are `t001-claude`, `t002-claude`, …; the contract line
says `iteration=N/total`. A role's full instructions appear on its first turn
with that agent, its summary afterwards.

ALFRD is agent agnostic. Built-in adapters know Claude (stream-json activity,
`--fallback-model`, tool permissions, cost) and Codex (sandbox, folders, token
report). Any other CLI that reads the prompt on stdin and prints or writes the
response works through the `generic` adapter; its usage is recorded as
unavailable. Set `adapter:` on an entrypoint to choose one explicitly and
`model_option: --model` to let `model:` pass a model to a generic CLI:

```yaml
entrypoint:
  - {name: gemini, cmd: [gemini], adapter: generic, model_option: --model,
     stdin_file: "{prompt_file}", output_file: "{response_file}", output_capture: stdout}
```

Projects made before 0.2.2 (`repeat: {iterations: K}` with `steps:
[claude-turn, codex-turn]`) keep working unchanged: there, iterations still
count passes and ids stay `i001-claude-turn`. Studio's settings show which
meaning a project uses. `.alfrd-task.json` files carry `"version": 2` when they
count turns; older ones (passes) are converted when read.

## Fallback models

`fallback_models: [sonnet, haiku]` on an entrypoint (or a turn). Claude receives
the first as `--fallback-model` and switches by itself. Other agents are
relaunched for the same turn with the next model after a runtime failure
(non-zero exit or no response) — never after an invalid response, a rejected
review, a timeout or a cancel. Each relaunch is a new attempt of the same
logical turn (`retry_of`), and the model used is recorded.

## Configuring commands

### Models and Claude activity

Project settings → **Agents / human review** has a model field for each Claude
or Codex entrypoint. Apply the form, then **Save alfrd.yaml**. Blank uses the CLI's
default. In YAML, `model: <name-or-alias>` on an entrypoint supplies `--model` and
replaces any model argument already in `cmd`. CLI settings and authentication
remain the responsibility of that CLI. No model catalog is hardcoded.

Schedule and Handoffs show the **reported** model when the CLI supplies it,
otherwise the **requested** model or “not reported”. Claude's stream reports
resolved models; Codex's normal `model:` log header is used when available.
ALFRD cannot infer a model hidden by a custom wrapper.

Claude handoff commands automatically use `--output-format stream-json
--verbose --include-partial-messages`. The detached shim logs text, tool calls,
and tool results; saves raw events beside the unit log as `.events.jsonl`; and
writes only a successful final `result` into `response.md`. This also upgrades
existing `claude -p --output-format text` projects without a manifest change.

The template writes its configuration into `alfrd.yaml`; edit commands there.
For example:

```yaml
entrypoint:
  - name: claude
    cmd: [claude, -p, --output-format, text]
    stdin_file: "{prompt_file}"
    output_file: "{response_file}"
    output_capture: stdout
  - name: codex
    cmd: [codex, exec, --sandbox, workspace-write, --skip-git-repo-check,
          --output-last-message, "{response_file}", "-"]
    stdin_file: "{prompt_file}"
    output_file: "{response_file}"
    output_capture: file
execution:
  cwd: .
  mode: step
  concurrency: 1
  status_from: exit_code
  on_failure: stop_plan
  timeout: 3600
  # max_runtime: 18000  # optional total wall-clock seconds, including pauses
workflows:
  - name: agent-loop
    repeat: {iterations: 5}
    steps:
      - id: claude-turn
        entrypoint: claude
        handoff: {input: "{target}/next-step-claude.md", output: "{target}/next-step-codex.md"}
      - id: codex-turn
        entrypoint: codex
        handoff: {input: "{target}/next-step-codex.md", output: "{target}/next-step-claude.md"}
```

`next-step-codex.md` is the recipient file for Codex. Agent and file names are
configuration, not runner constants. Entrypoint commands run as argv, without a
shell. `stdin_file` supplies an opened file descriptor. `output_capture: stdout`
captures stdout alone; `file` expects the command to write `output_file` itself.
Diagnostics remain in the existing unit logs. Non-loop commands can use literal
file paths and per-entrypoint `cwd`, `env`, and `timeout`. Relative paths resolve
against the project root. Agent loops use one shared `execution.cwd` and the
snapshot placeholders shown above.

The CLI executables must already be installed/authenticated. Configure models,
tool permissions, and approval settings in their argv/config before an unattended
run. ALFRD does not bypass their permission checks. The generated Codex entrypoint
supports newly created folders without initializing Git. No Git commits or pushes
are performed by scaffolding or handoff publication.

## Handoffs and recovery

At launch, ALFRD freezes the current input under
`.alfrd/plans/<id>/handoffs/<unit>/prompt.md` and adds turn instructions. The
response is saved beside it as `response.md`. The command must exit successfully
and supply Markdown (maximum 10 MiB) with these headings:

- Goal
- What was completed
- Files changed
- Checks and results
- Instructions for the next agent
- Acceptance criteria
- Blockers

ALFRD publishes a validated response atomically to the outgoing recipient file,
records its hash and history, then permits the next turn. A failure leaves the
previous recipient file intact and stops the plan. If the outgoing file was
edited during the turn, publication fails and preserves that edit. Frozen input
and response snapshots remain in the plan archive.

Repeated steps are expanded into stable CSV columns (`i001-claude-turn`,
`i001-codex-turn`, …). A loop requires exactly one task row selected, every turn selected, step
mode and concurrency one. Existing plan controls apply. Pause lets the active
turn finish. Cancel stops it. Retry/resume preserves successful turns and creates
a new attempt/archive for failed work. A workspace lock is retained by the shim
if the runner dies; recovery waits for that command before advancing the loop.
The workspace lock uses POSIX `flock`; platforms without it do not provide the
cross-plan workspace exclusion guarantee.

The workflow manifest (excluding history tracking settings) is hashed at plan creation. If it changes mid-plan, ALFRD
stops before launching another turn. Restore the recorded manifest to resume, or
create a new plan for the edited workflow. Agents may already have edited source
files before interruption; review those files before retrying.

## Studio and manual chat turns

### Edit the goal

Use **Workflow → Edit task** or **Settings → Edit task.md**. Saving records history
and detects stale edits. **Use this goal for the next run** also replaces the
initial handoff (normally `<target>/next-step-claude.md`); finish or cancel an active
plan for that task first. Other tasks remain editable. Uncheck that option to save
only `<target>/task.md` while its plan is active. Frozen
prompts already launched are unaffected. For a completed plan, choose Run → New
plan, the selected task row and all turns, so the cells start as `todo` again.

### Human in the loop

Enable **Human adjustments** in Settings → Agents / human review, then check
the agents whose turns you want to review, or open **Per-turn settings** to
choose single turns: a person writes the turn (chat) and/or reviews it, and an
optional start delay. These are saved as `workflow.turns` in alfrd.yaml.

**A running plan** is changed in **Handoffs → Turns of this run** (or `alfrd
plan turn PLAN STEP --review|--manual|--after|--model`). The change applies to
that plan only (`.alfrd/plans/<id>/overrides.json`). Review can be added even to
the turn that is running now: whether a turn needs review is decided when its
command ends. Human-writes, delay and model change only turns that have not
started. No plan CSV columns are added: the review is a gate inside the turn's
own cell.

After a successful, validated response the runner holds the handoff before
publishing it or starting the next agent. Schedule shows `awaiting_review`;
choose **Review response**, edit the Markdown if needed, then **Approve and
continue**, or **Reject and stop** (outcome `rejected`; nothing is published and
the plan stops like a failed turn). The original is archived as
`response-agent.md`, and the reviewed response becomes `response.md`. Stale or
duplicate decisions are refused. Review does not undo source edits already made
by an agent. It survives runner loss (a held turn needs a runner, not a
process), and waiting counts toward the total runtime limit. Pause retains the
checkpoint; approval finishes the turn, and Resume starts subsequent work.

To hear about a pending review (or a failed, finished or silent turn) without
watching the Studio, add a notification route: `notify.routes` in alfrd.yaml
or `~/.config/alfrd/notify.json`, and `notify.idle_after` for `turn.idle`. See
[notifications.md](notifications.md).

### Delays between steps

`after: "+1h"` (also `90m`, `2h30m`, seconds; at most 7 days) on a workflow step,
entrypoint or turn starts that step that long after the previous step of the
row finished. The time is derived from the recorded finish, so it survives a
runner restart. Status `waiting[]` shows `kind: delay` with `until`; the runner
keeps running meanwhile. **Run now** (or `--after 0`) starts it immediately.
Delays work for any workflow, not only agent loops.

YAML defaults and overrides:

```yaml
project_settings:
  human_review: true
# On a workflow step (applies to each repetition):
# human_review: false  # skip review for this step
```

Start from Workflow → Run using the scaffolded plan CSV. Schedule shows the
iteration, agent, and phase. **More → Handoffs** shows frozen prompts/responses
and an editor for recipient files with history and stale-save detection. Pause
before editing a prompt for a future turn. The dialog reflects its opening
snapshot; reopen it to refresh the turn history.

Set `manual: true` on an entrypoint **before creating the plan** to run that
agent's turns through chat. ALFRD starts a detached waiting turn, exposes
`phase: awaiting_response`, and lets you copy the frozen prompt into chat. Paste
the resulting Markdown into Handoffs → Submit a chat response. The same response
validation and handoff publication apply. Existing timeouts and cancellation also
apply while waiting.

The read-only status document adds `loop` with iteration/count, agent, phase,
input/output filenames, unit id, and the latest artifact metadata. These fields
participate in cursor changes. Existing HTTP status, long polling, SSE and log
endpoints retain their authentication and project scope rules. Studio creation,
handoff saves and response submissions use the existing loopback + CSRF gate.

The filesystem scheduler state is authoritative. RuntimeService registers the
project and ordered workflow definition; turn archives/artifact metadata live
with scheduler units. There is no second database execution loop.

## Turn contracts and responses

The default headings and phase instructions remain unchanged. Override them in
YAML when your workflow needs another response structure:

```yaml
loop:
  headings: [Outcome, Checks, Next]
  contract:
    first: "Inspect the goal and plan the next task."
    middle: "Implement and check the incoming task."
    final: "Report the result and remaining blockers."
  task_row: task
```

Omitted phases use the default instructions. The prompt and required headings
are frozen together for each turn. Manual submissions and human approvals use
those headings; invalid submissions keep waiting. Changing the manifest requires
a new plan. The size limit remains 10 MiB.

Submit a manual response from Schedule → **Submit response**, or:

```bash
rtk alfrd plan response PLAN_ID UNIT_ID response.md -C ./my-project
```

Handoffs loads 256 KiB pages. **Copy prompt** copies the complete prompt.
Artifact **Copy** fetches and copies the complete artifact.
**Refresh** reloads turns and prompts after checking for unsaved edits.
Review loads the complete response before approval becomes available.
New scaffolds use `next-step-codex.md`; existing file names remain valid.

## Folders and Bash permissions

Project settings → **Agents / human review** now includes folder and shell
settings. Apply, then save the manifest. They apply to new plans:

```yaml
project_settings:
  agent_access:
    folders: [../reference-notes]
    bash_commands: ["git status *", "pytest *"]
    sandbox: workspace-write
```

Folders resolve relative to the project folder. Claude receives `--add-dir`;
Codex receives `--add-dir`, which grants write access under workspace-write.
Claude Bash patterns become `--allowedTools` entries for automatic approval.
Codex uses its CLI sandbox for shell and file permissions; choose read-only or
workspace-write. Blank keeps the existing CLI sandbox. These settings do not
bypass CLI permission checks. Custom command wrappers keep their own arguments.


## Agent personalities

Define named personas under `project_settings.personas`. Each key uses 1–40
lowercase letters, digits, underscores or hyphens, starting with a letter or
digit. Each persona has a one-line `label` (1–60 characters) and `instructions`
(a string of at most 4000 characters; empty is allowed).

```yaml
project_settings:
  personas:
    manager: {label: Manager, instructions: "Plan the work and acceptance checks."}
    developer: {label: Developer, instructions: "Implement the plan and run checks."}
    reviewer: {label: Reviewer, instructions: "Review the patch for correctness."}
workflows:
  - name: agent-loop
    repeat: {iterations: 2}
    roles: [manager, developer, reviewer, developer]
    steps:
      - id: claude-turn
        entrypoint: claude
        handoff: {input: "{target}/next-step-claude.md", output: "{target}/next-step-codex.md"}
      - id: codex-turn
        entrypoint: codex
        handoff: {input: "{target}/next-step-codex.md", output: "{target}/next-step-claude.md"}
```

Keep the template's entrypoints and execution settings. The four turns above
are Claude as Manager, Codex as Developer, Claude as Reviewer, and Codex as
Developer. Roles belong to turns, so the same agent can hold different roles.
A role entry can also be a list, such as `[manager, reviewer]`, for combined
roles, or `null` for a turn without a role. A shorter `roles` list cycles in run
order. Without a nonempty workflow `roles` list, each step's optional `role`
(key or list of keys) applies in every iteration. Unknown keys fail validation.

Assigned labels and instructions appear in the turn prompt, with the next
agent and its role labels. Turns without roles keep their original prompts and
step identifiers. Persona edits stop an active loop at the next turn boundary,
because the manifest changed; use the edited configuration for a new plan.

In Studio, open **Agents and human review**. Under **Personalities**, add, edit
or remove keys, labels and instructions. **Turn roles** has a checkbox per
personality for each turn, allowing several roles together. Apply settings to
save the definitions and assignments. Studio saves the shortest repeating
role cycle and clears obsolete per-step role overrides.


### Multiple tasks in one project

The task name is `TARGET_NAME` and its folder name. Names contain 1–64
characters from `[A-Za-z0-9._-]`, cannot start with `.`, and default to `task`.
A new project contains `task/task.md`, `task/next-step-claude.md`, and
`task/next-step-codex.md`. `FILENAMES` defaults to `task.md`, relative to that
task folder. The plan CSV remains shared, with one row per task; each plan
records its selected target and filters its queue to that row. Plan archives
remain in the project's `.alfrd/plans/`, keyed by plan ID and selected target.
Optional task iteration settings live in `<target>/.alfrd-task.json` and are
snapshotted into the plan, so tasks may use different turn counts — e.g.
`small-task` 2 and `long-task` 5. The project's iterations are the maximum;
creating a task or starting a plan above it is refused, and Overview flags a
task left above a lowered maximum without rewriting it.

With `loop.workspace: worktree` (the default for new projects) each task gets a
git worktree at `<target>/workspace` on branch `alfrd/<target>`, made from the
current `HEAD` when the task is created (or when its first plan starts). Its
agents run there, so tasks run in parallel as separate plans without editing
the same files. The folder is listed in `.git/info/exclude`, so the main
checkout stays clean. ALFRD never commits, merges or pushes in a worktree; merge
`alfrd/<target>` yourself. A project outside git, or a repository without a
commit, keeps one shared working tree (`workspace_warning` on the plan). Renaming
a task re-links its worktree.

Overview lists the tasks of a loop project above its run history: turns used of
the maximum, worktree branch and uncommitted changes, number of runs and the
latest status. Click a task to show only its runs.

Use the task picker to edit an existing task, create **New task**, or **Rename**
an idle task. A running task does not block creating, editing, or starting a
different task. Renaming a task with an active plan is refused; finish or cancel
that plan first. Renaming an idle task moves its folder and updates `TARGET_NAME`,
any folder-prefixed `FILENAMES`, history patterns, and file history archives.

Folder-aware manifests use `{target}` in `handoff.input`, `handoff.output`,
artifact `path_pattern`, and view `source`. History uses `*/task.md` and
`*/next-step-*.md` patterns so all task folders are tracked automatically.
Paths and target names are checked before reading or writing, including symlink
escapes. Existing manifests without `{target}` retain their root handoff paths
and the existing single-task editor; multiple-task creation requires the new
folder layout.

`loop.max_input_chars` defaults to 40000 and bounds the incoming handoff.
ALFRD removes its leading metadata comment and shortens verbose progress
sections when necessary, while preserving Goal, Instructions for the next
agent, Acceptance criteria, and Blockers in full. If those required sections
alone exceed the configured limit, ALFRD rejects the prompt with a clear error
rather than silently losing instructions. The archived source remains complete.
Full persona instructions appear only on that agent's first iteration; later
turns use each role's optional `summary`, falling back to its first sentence.

## Attempt records and the baseline report

Every agent turn's unit records, besides status and usage:

| Field | Meaning |
|---|---|
| `logical_turn_id`, `attempt_number`, `retry_of` | one id per logical turn; retries in the same plan inherit it, and a new plan inherits it only when created with `--retry-failed` or `--retry-of PLAN` |
| `treatment`, `run_kind` | labels from `alfrd plan run --treatment … --run-kind …` (defaults `baseline`, `production`) |
| `outcome`, `outcome_reason` | `accepted` (validated and published), `failed_validation`, `rejected`, `abandoned` (cancelled), `failed_runtime`; null while unresolved |
| `usage_source` | `claude_result`, `codex_event`, `codex_total_only`, `unavailable` |
| `agent_usage.input_uncached_tokens` | input not served from cache, comparable across providers (Codex reports cached input inside `input_tokens`); null when unknown |
| `handoff.raw_input_chars`, `trimmed_handoff_chars`, `prompt_chars`, `sections_truncated` | sizes before metadata stripping, after compaction, and of the final prompt; `[]` = nothing shortened |

Plan totals now add `input_uncached_tokens`; older Codex records are derived
from their stored counts.

`alfrd plan baseline -C DIR [--from PLAN/UNIT|TURN] [--count 10] [--exclude
TURN=evidence] [--coverage 0.8] [--json]` selects consecutive logical turns in
production order (baseline + production labels only), includes every attempt,
skips only turns you exclude with outage evidence, and reports per-turn and
per-attempt usage, acceptance, retries, truncation, and per-category coverage,
subtotals, tokens per accepted result and spread over complete turns. A field
reported by fewer than 80% of attempts is marked unusable and the report is
**not ready** (exit code 1). Units recorded before 0.2.2 have no lineage and are
counted as unlabeled, not guessed.

