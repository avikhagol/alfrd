# Project chat and the orchestration protocol

A project chat lets you talk to an **orchestrator** agent, which turns a request into
tasks for **specialist** agents, or address one specialist directly with `@agent …`.
It runs on the existing plan runner (`alfrd.runtime.scheduler`): there is no second
engine. This page covers the configuration, how a request runs, the
`alfrd.orchestration/1` protocol, the API and the files.

## Configuration (`chat:` in the active alfrd.yaml)

Everything is optional; the values below are the defaults. Agents are *editable
assignments*: roles are words the orchestrator reads when it assigns work, not
capabilities ALFRD enforces.

```yaml
chat:
  orchestrator: claude        # the agent that plans requests
  orchestrator_model: sonnet  # model of plan calls (null: the orchestrator agent's own model)
  escalation_model: opus      # model of replans (blocker decisions) and plan repair (null: same as above)
  final_summary: false        # true: one more orchestrator call per finished request; false: the answer is built in code
  delegate_decisions: "off"   # questions | all: blockers that would ask you go to the orchestrator instead
  warn_task_cost_usd: null    # a task that costs more gets a badge and a budget.warning event (work goes on)
  concurrency: 2              # tasks running at once in a session
  history_messages: 8         # recent messages the planning prompt sees
  bounds:                     # per request (one user message)
    max_depth: 2              # orchestrator tasks are depth 1; a task may delegate one level deeper
    max_retries: 1            # automatic retries of a failed task
    max_cost_usd: 5           # from the providers' reported total_cost_usd
    max_seconds: 7200         # wall clock from the message
    max_tasks: 20
    max_replans: 3            # orchestrator wake-ups for blockers; past it, blockers go to the user
  watch:                      # scheduled checks (chat.check): recovery after a crash or reboot
    enabled: true             # superuser project toggle; keeps the configured interval when switched off
    interval_seconds: 300     # how often (0: never); a dead runner with tasks left is resumed
    stall_seconds: 1800       # a request whose tasks all ended, idle this long, is finished in code
    wake_orchestrator: false  # a stall may wake the orchestrator ...
    max_checks: 3             # ... at most this many times per request
  agents:
    claude: {label: Claude (Opus),   entrypoint: claude, model: opus,   roles: [orchestration], lean: true}
    sonnet: {label: Claude (Sonnet), entrypoint: claude, model: sonnet, roles: [test planning, verification, failure analysis], lean: true}
    codex:  {label: GPT (Codex),     entrypoint: codex,                 roles: [design, development]}
    agy:    {label: Antigravity,     entrypoint: agy, adapter: generic, roles: [research, image generation]}
```

* `entrypoint` names an `entrypoint:` of alfrd.yaml (its command, adapter, environment and
  timeout are used). When the project has none of that name, ALFRD uses a built-in command
  for `claude`, `codex` and `agy`. `cmd: [...]` gives an agent its own command instead.
* `model` is passed with the adapter's model option; `persona` names a
  `project_settings.personas` entry whose instructions go into that agent's task prompts.
* `agents: {agy: null}` removes a preset. Adapters: `claude` (stream-json: model, tokens
  and cost), `codex` (tokens from its log), `generic` (nothing assumed). AGY runs through
  the generic adapter: research and image generation work only as far as its CLI does.

## Scheduled recovery checks

`alfrd serve` checks each project in its scope at `chat.watch.interval_seconds`
(default 300 seconds). The serving process starts one daemon thread and stops it
with the server; the debug reloader's parent does not start another watcher.
Each enabled project is due once on startup, then at its own interval. An interval of
0 also disables scheduled checks; otherwise it must be at least 30 seconds. Toggle and
interval changes take effect within 30 seconds, including when all projects started disabled.

The project toggle is `chat.watch.enabled` (boolean, default `true`), stored in the active
`alfrd.yaml` alongside the other chat settings and saved through manifest history. In Studio,
select the project, then Settings → Preferences → Project → **Periodic chat checker**.
Only the existing superuser context (a browser on the server machine, with CSRF protection)
can save it; remote and browser-only sessions are refused. The local OS owner can also use:

```sh
alfrd su checker --root /absolute/project/path --disabled
alfrd su checker --root /absolute/project/path --enabled
```

API: `POST /api/studio/ui-flags` with
`{scope: "project", project: "<id>", changes: {"chat.watch.enabled": false}, expected_revision: <revision>}`.
Read the project revision from `GET /api/studio/ui-flags?project=<id>`; stale saves return 409.

Completely idle sessions are skipped on periodic ticks: no active request/run or live runner,
nonterminal tasks or units, pending decisions, or scheduled starts/delays. A skipped tick writes
no check report, revision, chat message, or event. Activity is reconsidered on the next tick.
Recovery for active work is unchanged.

A check delivers resolved mailbox replies, resumes a dead runner with work left
(preserving pause/cancel), and completes a request whose tasks have all ended and
which has been idle for `stall_seconds` (at least 60). It calls no agent itself;
resumed work uses the normal runner and may call agents.

With `wake_orchestrator: true` (default false), an orphaned unit or non-terminal
work idle for the stall period can queue an orchestrator row of kind `check`.
A dead runner gets a code recovery attempt first; if work remains stalled on a
later check, the orchestrator can be woken. Paused/cancelled sessions stay held.
The prompt includes findings, current stalled rows and idle time; its normal
`plan` reply can add new tasks, answer orchestrator decisions and post a message.
User decisions and access approvals remain with the user. Each wake posts a short
system message and the finding `orchestrator_woken`. The request stores its
`checks` count: at most `max_checks` (default 3, 0 disables calls), with no duplicate
pending check. After the limit, `check_limit_reached` leaves the stall for the
user. These calls use the escalation model and the existing request budgets.

In Studio, the chat header shows the last and next check times. **Check now**
runs the same recovery check and reports its findings, including when there are
no issues. Settings → Agent chat → **Scheduled checks** edits the interval and
stall period, **Wake the orchestrator when a request stalls**, and the maximum
orchestrator checks per request. Each performed check saves `.alfrd/chat/<sid>/check.json`;
session state includes it as `check`. Only checks with findings add a `chat.check` event.
Manual **Check now** and `alfrd chat check` remain available even when periodic checks are off,
and return a report for idle sessions too.

To check without a running server:

```sh
alfrd chat check --root /absolute/project/path
alfrd chat check --root /absolute/project/path --session SESSION_ID --json
```

For recovery after a reboot without Studio, install a cron entry (replace both
absolute paths with your installation and project paths):

```cron
*/5 * * * * /absolute/alfrd/.venv/bin/alfrd chat check --root /absolute/project/path >> /absolute/project/path/.alfrd/chat-check.log 2>&1
```

Cron determines its own interval; `interval_seconds: 0` only disables the server's
automatic checks. The CLI and **Check now** remain available.

## How a request runs

1. **A message** is stored in `messages.jsonl` and becomes a *request*.
   * `@codex add a health endpoint` → one task for `codex`, no orchestrator call.
   * Anything else → one orchestrator **plan** call. Its reply must contain a
     ```` ```json ```` block with a `plan` envelope (below). ALFRD validates it (schema,
     owners, dependencies, cycles, `max_tasks`). An invalid plan gets **one** repair call
     with the errors; if that fails too the user sees the error.
2. **Tasks become plan rows** in the session's plan CSV: one row per task, one step column
   per agent, a `todo` cell in the owner's column, plus `depends_on`, `max_retries` and
   `max_seconds` columns. The runner starts a row only when every row it depends on is
   done, blocks it when one failed or was cancelled, retries a failed attempt up to
   `max_retries` (the retry prompt says so), and caps a command at `max_seconds`.
3. **Results.** A specialist ends its reply with a `result` envelope. ALFRD records its
   status, summary and artifacts as `path@sha256` refs (it hashes the files itself), plus
   the provider's usage and cost. A reply without an envelope counts as `done` with the
   reply as its summary; an *invalid* envelope or `status: failed` is a failed attempt.
   `status: blocked` with blockers:
   * `needs: user` (default) → `decision.needed`; the task waits (cell `waiting`) until
     `POST …/decisions/<id>` answers it, then runs again with the decision in its prompt.
   * `needs: orchestrator` → one **replan** call that answers with `decisions` (and may add
     tasks); if it cannot decide, the blocker goes to the user. At most `bounds.max_replans`
     replans per request (`budget.exceeded`, `limit: max_replans`); then the user decides.
   * **Permission blockers** (`kind: permission`, a `requires` block, or a reason worded like a
     sandbox denial: "requires approval", "outside the allowed directories", "add-dir", …) always go
     to the user: the orchestrator cannot change an agent's folders or commands. See
     [Access and permission blockers](#access-and-permission-blockers).
   * **Delegated decisions.** With `chat.delegate_decisions: questions`, a blocker that would ask
     the user goes to a replan call instead (`delegated: setting` on the decision); with `all`,
     permission blockers do too, and the orchestrator may pick a grant option. One pending decision
     can also be handed over from the Chat page: `POST …/decisions/<id>` with `{"delegate": true}`
     (`delegated: user`; any of its options, grants included). Replans stay bounded by
     `max_replans`; repeats and mailbox PRM action-hash approvals stay with the user, even with `all`.
     Mailbox ASK questions can be delegated by either mode or per decision. Replies use the normal
     mailbox delivery path, with sender `kind: orchestrator`, `on_behalf_of: user` and `delegated: setting | user`
     recorded on the reply and resolved event. The orchestrator cannot approve an action hash.
     Each delegated choice is posted as a system message (`kind: delegated_decision`). In the Studio: Settings → Agent chat →
     *Blocked tasks* sets the mode; a decision card's **Let orchestrator decide** button hands one over
     (not shown for a repeat, a mailbox approval, or a decision the orchestrator already handed back).
   * The **same blocker again with the same access** (same owner, command and folders) is not sent
     to the orchestrator a second time: it goes to the user with a system message and event
     `task.stuck`.
   * `result.tasks` delegates sub-tasks one level deeper; beyond `max_depth` they are
     refused (`budget.exceeded`, `limit: max_depth`).
4. **Completion.** When every row of a request is finished, ALFRD makes one **summary**
   call only when `chat.final_summary` is on (default off) and posts its answer; otherwise
   it posts a summary written in code from the task results (outcome, one line per task,
   result files). `request.finished` closes the request.

**Models of orchestrator calls.** Plan calls (and the optional summary) use
`chat.orchestrator_model` (default `sonnet`). A **replan** for a blocker that names the
orchestrator and the **repair** of an invalid plan use `chat.escalation_model` (default `opus`).
Specialist tasks keep their agent's own `model`. Set either to `null` to use the orchestrator
agent's model. The model of every call is recorded on the task (`model`) and its unit
(`requested_model`).

**Lean startup.** An agent with `lean: true` (the `claude` and `sonnet` presets; off for agents you
add) starts Claude without MCP servers, user plugins/settings and skills; see
[agent loops](agent-loops.md#lean-agent-startup). Codex and generic agents ignore it.

**Cost warnings.** `chat.warn_task_cost_usd` flags a task whose attempts together cost more: the
task gets a `cost_warning`, a `budget.warning` event is written once, and the Chat page and dock show
a badge. It does not stop anything (the hard limit is `bounds.max_cost_usd`).

The orchestrator is woken **only** for a new message, a blocker addressed to it, and the
final summary. Waiting, completion, dependency resolution and retries are code: no LLM
is called to poll, acknowledge or forward anything.

**Bounds.** The runner checks every request's cost (sum of `total_cost_usd` of its turns)
and age on each tick. Over a bound it cancels what has not started, stops what runs
(SIGTERM, then SIGKILL after `kill_grace`), flags `budget.exceeded`, posts a system
message and finishes the request as `stopped` without a summary call. A task's own
`budget.max_cost_usd` is flagged when exceeded; `budget.max_seconds` is its time limit.

**Context.** An agent's prompt holds its task, the result summaries and artifact refs of
the tasks it depends on, and decisions made for it. Never the chat history and never
another task's transcript. The planning prompt alone sees recent messages (final answers
only) and earlier results as refs. Full transcripts stay inspectable: each attempt's
prompt and reply in `.alfrd/plans/chat-<session>/handoffs/<unit>/`, its output and the
Claude event stream in `logs/<unit>.log` / `.events.jsonl`.

**Recovery.** Everything is in files. A killed runner is replaced like any plan runner
(`reconcile`, or the next message): it re-adopts running commands, finalizes the ones that
ended, hands results an earlier runner missed to the chat (`recover`), and continues.

## Protocol `alfrd.orchestration/1`

Schemas: `src/alfrd/schemas/orchestration_{envelope,task,result,blocker,decision}.v1.json`
(JSON Schema 2020-12, `additionalProperties: false`). Within version 1 fields are only
added. A message with a different `protocol` is refused with
`unsupported protocol version …; this ALFRD speaks 'alfrd.orchestration/1' only`.

Envelope: `{"protocol": "alfrd.orchestration/1", "kind": "plan" | "result" | "summary" | "blocker" | "decision", …}`

| kind | fields |
|---|---|
| `plan` | `message?`, `tasks?: [task]`, `decisions?: [decision]` (at least one) |
| `result` | `result: {task_id?, status: done\|failed\|blocked, summary, artifacts?, usage?, blockers?, tasks?, outputs_changed?}` |
| `summary` | `message` |
| `blocker` / `decision` | `blocker` / `decision` |

Task: `id`, `title`, `owner` (agent key), `role?`, `description?`, `depends_on?` (ids),
`acceptance_criteria` (≥ 1), `inputs?` (artifact refs, or `{ref, note}`),
`expected_outputs?` (paths or `{path, description}`), `budget?: {max_cost_usd,
max_seconds, max_retries ≤ 5}`.

**Expected outputs are frozen.** When ALFRD adds a task it resolves each `expected_outputs` path to an
absolute path (a relative one is taken from the project root; a trailing `/` means a folder) and stores
it on the task record as `outputs: [{id: "o1", path, folder, written, description?, rev: 1, history: []}]`.
A path outside the project and the folders agents may write (`project_settings.agent_access.folders`) is a
plan error, so the orchestrator gets one repair; for sub-tasks a task delegates it makes that result
invalid. Prompts show the frozen paths (`- o1: /abs/path`), and before a task is accepted as done ALFRD
checks in code that each one exists; if any is missing the attempt fails ("expected output(s) missing: …")
and is retried within `max_retries`, with no LLM call.

**An agent can move an output.** A result may carry `outputs_changed: [{output: "o1", to: "/abs/new/path",
reason?}]`. ALFRD validates each `to` (absolute, inside the project or this task's writable folders,
including folders granted to it) and applies all of them or none: each moved output gets `rev + 1`, its old
path in `history` (`rev`, `path`, `at`, `by`, `unit`, `reason`), a `task.output_changed` event and a system
message (`kind: output_changed`). The missing-output check then uses the new path, and dependent tasks see
the current path of each output they depend on. A refused change ("output change(s) refused: …") is a
failed attempt, retried with the reason in the next prompt. A new path may not be the project or a
writable folder itself (it always exists, so it would pass the check unwritten), nor the output's current
path, nor the path another output of the task has after the moves (one file must not pass the check for
two outputs; swapping two paths is fine), and one output may be moved only once per result. Instead of the result field an agent may send mailbox code `OUT` (payload
`{output, to, from?, reason?}`, see [the agent mailbox](agent-mailbox.md)) as soon as it knows; the mail
that attempt sent is applied with the result, through the same checks. Moves (old → current path) are
listed in the replan and summary prompts and in ALFRD's own summary; the summary's files, promoted
workflow steps (`outputs`) and the Files tab use the current paths. Studio's task inspector lists each
output's current path, a "moved · rev N" badge and its earlier paths.

Blocker: `id`, `reason`, `task_id?`, `question?`, `options?`, `needs: user | orchestrator`,
`kind?: permission | input | other`, `requires?: {folders?, bash_commands?}`.
Decision: `id` (the blocker), `choice` and/or `answer`, or the orchestrator's `reassign: <agent>` /
`cancel: true`, `decided_by?`. Only a person grants access.

Example plan reply:

````
```json
{"protocol": "alfrd.orchestration/1", "kind": "plan", "message": "Two steps.",
 "tasks": [
  {"id": "impl", "title": "Add /health", "owner": "codex", "acceptance_criteria": ["GET /health returns 200"],
   "expected_outputs": ["src/app/health.py"]},
  {"id": "verify", "title": "Test /health", "owner": "sonnet", "depends_on": ["impl"],
   "acceptance_criteria": ["a test covers /health and passes"]}]}
```
````

### Artifact references

`<project-relative path>@<sha256 hex>` names one version of a file. They are resolved by
selective retrieval (`GET …/artifacts?ref=…&offset=…&length=…`): the path must stay in the
project root after resolving symlinks (`..`, absolute paths and links out are refused),
the file must still have that hash (409 otherwise), and one read returns at most 256 KiB.

## Setup (`/setup`) and slash commands

In the chat composer a leading `/` opens a command menu (never sent to the agents): `/setup` opens
**Setup** (who does what, with which model), `/help` lists the commands and agents, `/new` starts a
session and `/graph` opens the task graph full screen. Setup shows one row per role (orchestrator with its
model and escalation model, then each role word → agent), the agents with model (Ollama's installed
models are suggested from the probe), fallback agent and availability, and **Add agent from preset**.
**New agent** adds one more agent on a CLI with its own model: *Runs with* Codex, Claude Code, Antigravity
or one of the free-model presets, a name (`@luna`) and a model (`gpt-5.6-luna`); talk to it with `@luna …`
or give it a role. For Codex the model list offers what this machine's Codex knows (its `config.toml`
`model` and profiles, and `models_cache.json`, under `CODEX_HOME` or `~/.codex`); any name `codex -m`
accepts can be typed. It saves only the file's own `chat:` block (`POST …/chat/settings`, conflict-guarded). Model changes
apply to the next attempt, so after a limit you can switch a model and press **Retry**.

Agent fields added for this: `env` (variables for that agent's command, e.g. Claude on Ollama) and
`fallback` (another agent key, see below).

## Chat runs in Workflow

A session is a plan (`chat-<sid>`). Workflow → **Runs** lists chat sessions in their own group of the
run picker ("Chat · title · status"): the grid is tasks × agents with logs, usage and the runner log,
plus Pause / Resume / Cancel and **Open in Chat**. With a chat run picked, **Graph** shows its task graph
and **List** its tasks. Chat runs never count as the active workflow run. The status API lists them after
the workflow runs with `run_kind: "chat"`.

## Free and local models

Presets (offered in Setup, none active until added):

| preset | what | needs |
|---|---|---|
| `ollama` | Codex on a local Ollama: `codex exec --oss`, model `gpt-oss:20b` | `codex`, Ollama with the model pulled |
| `ollama-claude` | Claude Code against Ollama's Anthropic API: `env` `ANTHROPIC_BASE_URL=http://localhost:11434`, `ANTHROPIC_AUTH_TOKEN=ollama`, model `qwen3-coder` | `claude`, Ollama |
| `hf` | Codex with the Hugging Face router as provider (`-c model_provider="hf" -c model_providers.hf={…}`), model `openai/gpt-oss-120b` | `codex`, `HF_TOKEN` |
| `local-llm` | the built-in `python -m alfrd.llm_chat`: any OpenAI-compatible `/chat/completions` (Ollama `/v1`, HF router, OpenRouter); text only, no tools | an endpoint |

`alfrd.llm_chat` reads the prompt on stdin, streams the answer (echoed to the log as it arrives) and
writes it to stdout. Options or `env`: `--base-url` / `ALFRD_LLM_BASE_URL` (default
`http://localhost:11434/v1`), `--model` / `ALFRD_LLM_MODEL`, `--api-key-env` / `ALFRD_LLM_API_KEY_ENV`
(the *name* of the variable with the key, e.g. `HF_TOKEN`), `--max-tokens`. It exits 2 on HTTP 429 or a
quota error. With no tools it suits the orchestrator, research summaries and writing, not code changes.

`GET …/chat/probe` (and `alfrd chat probe`) reports, for each agent and preset, whether its program is
found (`shutil.which`; built-in Python commands are always available), whether Ollama answers at
`OLLAMA_HOST` (default `http://localhost:11434`, 1 s timeout) with its models, and whether `HF_TOKEN`
is set. Nothing else is contacted and no token is sent anywhere.

## Access and permission blockers

A task runs with `project_settings.agent_access` (folders → `--add-dir`, `bash_commands` →
`--allowedTools Bash(...)` for Claude; `sandbox` for Codex) plus what was granted to that task.
Headless agents cannot ask for approval mid-run, so anything else is denied at once; an approval
can only take effect at the **next launch**. Chat rows therefore always get `Bash(alfrd mailbox *)`
(the mailbox their prompt describes), and their prompt has a **Your access** section.

`agent_access` is normalized when it is read: `~` and relative paths are expanded, globs are
expanded (a pattern that matches nothing is dropped), each folder is passed both as written and
resolved (symlinks: `/home/…` and `/mnt/…`), a command without `*` (`npm`) also allows
`npm *`, and read-only helpers (`cd`, `pwd`, `ls`, `cat`, `head`, `tail`, `wc`, `sha256sum`) are added
(`default_readonly: false` turns that off). Claude Code checks each part of `a && b` / `a | b`.

A blocker that only access can fix gets options that change the next launch, ahead of the agent's own:

* **Grant this task access and re-run: <folders>; commands <…>** — from the blocker's `requires`, else
  the existing folders named in its reason. Recorded on the task (`access_extra`, `grants`), merged into
  the next launch, and written to the unit log (`# access added for this task: …`); event
  `task.access_granted`. Only a person can choose it.
* **Reassign this task to @<agent>** — agents with a different entrypoint (another Claude has the same
  sandbox). The old owner's cell becomes `skip`; event `task.reassigned`.
* **Cancel this task** — the row is cancelled and its dependents are blocked.

An agent option that starts with *Cancel* cancels, one like *Reassign to codex* reassigns. When a
permission blocker comes back with the same access, re-running it unchanged is refused ("Re-running
now would hit the same blocker again …"); grant, reassign, cancel, or widen `agent_access` first.

## Fallback on limits

When an attempt of a row owned by an agent with `fallback` fails and its reply or the end of its unit
log (64 KiB) reads like a limit — usage limit, rate limit, quota, 429, too many requests, overloaded,
credit balance, "hit your limit", model not found / unavailable — the row moves to the fallback agent:
the old owner's cell becomes `skip`, the fallback's `todo`, the task record gets `owner` and
`fallback_from`, and event `task.fallback` `{task, from, to, reason}` is written with a system message.
No retry is spent (the new agent's attempts count from 1); each agent is tried once per row, so
`a → b → a` stops. Orchestrator rows (plan, repair, replan, check, summary) fall back too; on another agent
they run on that agent's own model (`orchestrator_model` / `escalation_model` apply to the configured
orchestrator only). An orchestrator reply that exits 0 but holds only a short limit message (no JSON)
also falls back; a specialist task's exit-0 reply is never second-guessed.

## Activity and graph

`GET …/chat/sessions/<sid>/activity?lines=60` lists every running unit of the session (orchestrator
calls too) with `row, request, kind, owner, title, unit, attempt, model, started, seconds, log, bytes,
tail`. `tail` is the end of the unit log, read from the end, terminal escapes removed; JSON stream
events (Claude stream-json, Codex `--json`) are shown as compact lines (joined text, `→ Tool input`,
`[result]`). An agent's stdout is its reply when it has no output file, so progress shows there only
when it writes to stderr (as `alfrd.llm_chat` does).

The session state's `graph` has `nodes` with `kind` `task`, `plan`, `repair`, `replan`, `check` or `summary`
(orchestrator rows are titled e.g. "Plan r0002") and `edges` with `kind` `depends_on`, `delegated`
(task → sub-task) and `planned` (the orchestrator row → each task it created, from the task record's
`planned_by`). `mermaid` draws orchestrator rows as hexagons and `planned` edges as thick arrows.

## Studio API

All under `/api/studio/projects/<p>/chat`. POSTs need loopback + the CSRF token like every
other mutating route.

| route | body → answer |
|---|---|
| `GET /settings` | effective `chat:` settings and the protocol id |
| `GET /sessions` | `{sessions: [{id, title, created, plan, messages, last_message, active_requests, plan_status}]}` |
| `POST /sessions` | `{title?}` → 201 `{session}` |
| `GET /sessions/<s>` | `{session, messages, requests (with usage), tasks (status, cell, attempts, result, usage), graph {nodes, edges, mermaid}, blockers, decisions, usage, plan, settings}` |
| `POST /sessions/<s>/messages` | `{text}` → 201 `{message, request, task}` |
| `POST /sessions/<s>/decisions/<id>` | `{choice?, answer?}` → `{decision}` |
| `POST /sessions/<s>/tasks/<row>/cancel` / `retry` | → 202 `{task, requested}` / `{task, requeued}` |
| `POST /sessions/<s>/requests/<r>/promote` | `{preview: true}` (default) → `{steps, text, diff, base_hash, added_entrypoints}`; `{preview: false, base_hash}` saves into the active alfrd.yaml (history, cycle check; 409 with the current text when it changed since the preview) |
| `GET /sessions/<s>/artifacts?ref=&offset=&length=` | `{ref, path, sha256, size, offset, length, eof, truncated, content}` |
| `GET /sessions/<s>/tasks/<row>/transcript` | `{task, attempts: [{unit, attempt, status, started, finished, agent, model, error, prompt_bytes, response_bytes}]}` (sizes, no content) |
| `GET /sessions/<s>/tasks/<row>/transcript/<unit>/prompt\|response?offset=&length=` | one page (≤ 256 KiB) of that attempt's archived prompt or reply: `{content, offset, length, size, eof}` |

Events (`task.created`, `task.started`, `task.finished`, `task.blocked`, `decision.needed`,
`decision.resolved`, `request.finished`, `budget.exceeded`, `budget.warning`) go to the session plan's
`events.jsonl`, so the live hub and `GET /api/v1/projects/<p>/plans/chat-<s>/events` stream them.

CLI: `alfrd chat new|list|rename|delete|send|show|decide` (`-C <project>`). `delete` asks first (`--yes` to skip)
and is refused while a request is active. Studio API: `PATCH …/chat/sessions/<s>` `{title}`, `DELETE …/chat/sessions/<s>` (409 while active).

A task's `result` also records `at` (when it was recorded), so a client can place results among the
messages.

## Studio

**Chat** in the Studio sidebar (`#/chat/<session>?p=<project>`) is the UI for all of the above; the
[Studio guide](studio-guide.md#chat-with-the-agents) describes it. In short:

* sessions list and **New**; the session is part of the URL (Back/Forward, reload);
* a composer where Enter sends, Shift+Enter adds a line and `@` at the start completes agent keys with
  their labels and roles (a leading `@agent` is the direct-message form above);
* the transcript: messages plus each specialist's result summary and artifacts; **View** reads an
  artifact by its ref (selective retrieval) and **Full transcript** fetches a task's attempts, then a
  prompt or reply only when opened — transcripts are never part of the session state;
* the task graph, drawn with the Workflow view's canvas renderer (read-only), and a task panel with
  acceptance criteria, budget, attempts, usage, result, artifacts and **Cancel** / **Retry** when the
  API accepts them;
* blockers with answer controls (options and/or a written answer) → `POST …/decisions/<id>`;
* progress, cost and time of the active request against its bounds, tokens and orchestrator calls;
* updates from the existing live stream: the session plan's events trigger one re-read of the session
  state; no polling while events flow (with live updates off, an active request is re-read every 15 s).

* a **token report**: per task, request and session, new input, cache read, cache write and output
  tokens and cost (a part nobody reported shows nothing, never 0), and a warning badge over
  `chat.warn_task_cost_usd`;
* each task lists its attempts with **Open log** (the unit log) and the handoff folder
  (`.alfrd/plans/chat-<s>/handoffs/<unit>`); chat tasks never appear in the Schedule plan list;
* **Promote to workflow**: previews the request's tasks as workflow steps (agent entrypoint and model,
  prompt file, `depends_on` from the task graph) with the diff of alfrd.yaml, then saves through the
  version history; a dependency cycle or an alfrd.yaml that changed since the preview is refused.
  Agent-loop (`repeat`) workflows are not promoted into.
* the **chat dock** on every project view (see the Studio guide).

**Settings → Agent chat** edits this `chat:` block (agents with entrypoint / model / adapter / persona /
roles, orchestrator, final summary, concurrency, history, bounds) and saves the active alfrd.yaml with
the version history and the changed-on-disk guard.

## Files

```
.alfrd/chat/<session>/
    session.json, messages.jsonl, graph.json, <session>.tasks.csv
    requests/<rid>.json        mode, status, rows, bounds, orchestrator_calls
    tasks/<row>/task.json      the protocol task, status, result, units seen
    tasks/<row>/prompt.md      the prompt of its latest attempt
    decisions/<id>.json        blockers and their decisions
.alfrd/plans/chat-<session>/   the session's plan: units, logs, handoffs (transcripts), events
```

Chat plans are not workflow runs: `list_plans` leaves them out unless asked
(`include_chat=True`), so the Runs view is unchanged.

## Scheduled chat batches

Each chat session can have one open, one-shot schedule. Its durable queue lives in
`.alfrd/chat/<session>/schedule.json`. Sending while it is open adds an item without
creating a transcript message or executable plan row. Editing and removal use a
schedule-specific revision and idempotent operation IDs. Terminal batches and
removed items remain available for replay protection.

Under `/api/studio/projects/<project>/chat/sessions/<session>`:

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | `/schedule` | Read state / create an empty schedule |
| PATCH | `/schedule/<id>` | Change its UTC instant and display timezone |
| PATCH / DELETE | `/schedule/<id>/items/<item>` | Edit / remove a queued item |
| POST | `/schedule/<id>/run` | Run now, or retry a validation error |
| POST | `/schedule/<id>/cancel` | Cancel without executing pending items |

Creation needs `client_schedule_id`, `run_at` with an explicit offset, `timezone`
(IANA), and `expected_schedule_revision`. Other mutations need `operation_id`
and `expected_schedule_revision`. `/messages` accepts `schedule_id` and
`expected_schedule_revision`; a stale explicit target returns 409 and never falls
through to ordinary execution. A 201/200 queued response has `queued: true` and
null `request`/`task`. Conflicts return fresh schedule state.

Studio's independent one-second worker sweeps the current scoped project catalogue
immediately on startup. All overdue items become **one orchestrated request**,
including leading `@mentions`, with a single initial plan row. Active requests in
the same session and manual pause/cancel controls hold the batch; editing remains
available while waiting. Empty due batches expire. Non-loopback execution policy
holds schedules without creating executable rows.

A session file lock protects mutations and dispatch. A `dispatching` journal freezes
the prompt, item attachments, settings, bounds and skills before submission. The
request is reserved as `preparing`; recovery finds it by schedule ID, repairs only
missing message/task/prompt/CSV artifacts, and preserves progressed rows. CSV
publication follows all launch inputs. Repeated startup sweeps reuse that request.
This guarantees one durable request per batch, subject to the runner's existing
external-process recovery semantics. Studio must be running; missed deadlines are
caught up when it returns, without an age cutoff. Runtime bounds start at dispatch.
