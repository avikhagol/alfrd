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
        handoff: {input: next-step-claude.md, output: next-step-gpt.md}
      - id: codex-turn
        entrypoint: codex
        handoff: {input: next-step-gpt.md, output: next-step-claude.md}
```

`next-step-gpt.md` is the recipient file for Codex. Agent and file names are
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
