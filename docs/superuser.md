# Superuser settings (`/superuser`, `alfrd su`)

One settings file per user decides what every `alfrd serve` / `alfrd gui` of that user shows and
allows. Running servers apply a change on their next request; no restart is needed.

- **Studio:** open `http://<host>:<port>/superuser` (or `#/superuser`, or Studio settings → Server →
  *Superuser settings…*). Only a browser on the server machine can open or change it, with the usual
  access token.
- **Command line:** `alfrd su show`, `alfrd su set key=value …`, `alfrd su reset`, `alfrd su path`,
  `alfrd su status`.
- **File:** `~/.config/alfrd/superuser.yaml` (the platform's user config folder; `ALFRD_SUPERUSER_FILE`
  overrides it). It is plain YAML and can be edited by hand.

```yaml
version: 1
features:
  experimental: false        # Chat mode preference and the agent-loop template
studio:
  hidden_sections: []        # workflow, metadata, results, logs, chat, config (Overview always stays)
  hidden_templates: []       # basic, avica, agent-loop (New project in the Studio)
  live_interval: null        # seconds between folder checks while busy; 0 = off; null = --live-interval
lockdown:
  read_only: false           # refuse every change (pausing, cancelling and stopping work stay allowed)
  plugins: true              # install / update / remove / enable / disable / configure plugins
  plugin_actions: true       # plugins' actions that change files or external data
  project_create: true       # New project, Open/Connect, Rediscover, new folders
  project_delete: true       # Forget and Delete projects
  runs: true                 # start / resume workflow runs, reviews, turn overrides
  chat: true                 # chat sessions, messages, decisions, retries, mailbox replies
  file_edits: true           # alfrd.yaml, tasks, targets, notes, handoffs, setup forms, history restore
limits:
  chat_max_cost_usd: null    # caps chat.bounds.max_cost_usd of every project
  chat_max_replans: null     # caps chat.bounds.max_replans
  chat_allowed_agents: []    # new chat work only for these agents (empty: all)
```

## How the settings combine

- **Command-line flags** stay stronger. `--experimental-features` turns experimental features on for
  that server. `--no-gui-install` and `--no-plugin-actions` turn those off, whatever the file says.
  `plugins.json` (`gui_install`, `plugin_actions`) can also turn them off.
- **Locks** are enforced by the server: a locked request gets 403 with `reason: superuser`. Stopping
  running work is never locked: Pause, Cancel, Stop on a unit and Cancel on a chat task. The superuser
  page itself always works, so a read-only Studio can be unlocked from it.
- **Hidden sections** leave the rail, and their links open Overview. Hiding Chat also turns Chat mode
  off.
- **Chat limits** apply to every project. A request's cost and replan bounds are the smaller of the
  project's and the superuser's. With `chat_allowed_agents`, other agents stay configured, so running
  sessions keep working. They get no new work: plans, `@mentions`, fallbacks and reassignments only
  use the allowed agents, and a message that needs a disallowed orchestrator is refused. Write to an
  allowed agent with `@agent` instead.
- **Live interval:** a change applies to projects watched from then on. Projects already open follow
  after a reload.
- **A broken file** (bad YAML, a wrong value) never unlocks anything. The last good settings stay in
  force, or a read-only Studio when there are none. The page shows the error; saving there replaces
  the file, after a confirmation. `alfrd su set` refuses until the file is fixed or `alfrd su reset`.

## Statistics and ops

The page also shows:

- the number of projects, workflow runs and chat sessions, active runs, live runners and running
  agents/commands;
- every **active run of every project** (workflow runs and chat sessions), with its runner and the
  commands running now: **Stop** one command, **Pause** / **Resume** or **Cancel** the run;
- the **other `alfrd serve` processes** of this user (from their server files: URL and pid, never the
  token);
- this server's version, Python, host, uptime, CPU, memory, load and runtime database.

`alfrd su status` prints the servers and active runs in the terminal.

API (loopback + CSRF): `GET/POST /api/studio/superuser` (`{settings: {"section.key": value}}`, or
`{settings: {...whole...}, replace: true}`), `POST /api/studio/superuser/reset`,
`GET /api/studio/superuser/ops`, and `POST /api/studio/superuser/ops` with
`{project, plan, action: pause|resume|cancel|stop-unit, unit?}`. `/api/studio/session` reports
`superuser` (hidden sections, read-only, allowed actions) and `can_superuser`.
