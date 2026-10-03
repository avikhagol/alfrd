# Project creation and agent loops (0.2.0.9)

Create a project without starting an agent:

```bash
rtk alfrd projects create ./my-project --template agent-loop \
  --task "Implement the widget and verify it" --iterations 5
rtk alfrd plan run --root ./my-project --dry-run
rtk alfrd plan run --root ./my-project
rtk alfrd plan status --root ./my-project --json
```

For a blank project, omit `--template` (defaults to `basic`). `--name` overrides
the folder name; `--task-file task.md` reads a task file. `--db` selects the local
runtime database. Studio settings → **New project** exposes the same creation
service. Neither creation path overwrites existing project files or launches
commands.

One iteration runs the workflow's steps in order once. The agent-loop template
has Claude then Codex, so five iterations produce **ten turns**. Claude's first
turn plans the task; subsequent turns execute the incoming handoff and plan the
next task. The last Codex turn supplies a closing report.

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
        handoff: {input: next-step-claude.md, output: next-step-codex.md}
      - id: codex-turn
        entrypoint: codex
        handoff: {input: next-step-codex.md, output: next-step-claude.md}
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
`i001-codex-turn`, …). A loop requires one task row, every turn selected, step
mode and concurrency one. Existing plan controls apply. Pause lets the active
turn finish. Cancel stops it. Retry/resume preserves successful turns and creates
a new attempt/archive for failed work. A workspace lock is retained by the shim
if the runner dies; recovery waits for that command before advancing the loop.
The workspace lock uses POSIX `flock`; platforms without it do not provide the
cross-plan workspace exclusion guarantee.

The workflow manifest is hashed at plan creation. If it changes mid-plan, ALFRD
stops before launching another turn. Restore the recorded manifest to resume, or
create a new plan for the edited workflow. Agents may already have edited source
files before interruption; review those files before retrying.

## Studio and manual chat turns

### Edit the goal

Use **Workflow → Edit task** or **Settings → Edit task.md**. Saving records history
and detects stale edits. **Use this goal for the next run** also replaces the
initial handoff (normally `next-step-claude.md`); finish or cancel an active plan
first. Uncheck that option to save only `task.md` while a plan is active. Frozen
prompts already launched are unaffected. For a completed plan, choose Run → New
plan, one task row and all turns, so the cells start as `todo` again.

### Human in the loop

Enable **Human adjustments** in Settings → Agents / human review, then check
the agent turns you want to review. Workflow → **Human adjustments / agents**
exposes the same per-step checkboxes and saves them directly. Configure before
creating a plan; changing its manifest mid-run stops it at a turn boundary.

The runner waits after a successful, validated response, before publishing it
or starting the next agent. Schedule shows `awaiting_review`; choose **Review
response**, edit the Markdown if needed, then **Approve and continue**. The
original is archived as `response-agent.md`, and the reviewed response becomes
`response.md`. Stale or duplicate approvals are rejected. Review does not undo
source edits already made by an agent. It survives runner loss, and waiting
counts toward both the turn timeout and total runtime limit. Pause retains the
checkpoint; approval finishes the turn, and Resume starts subsequent work.

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
