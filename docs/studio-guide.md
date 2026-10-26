<img src="brand/alfrd-mark.svg" alt="ALFRD" width="44" align="right">

# ALFRD Studio guide

Results counts waiting turns by their latest attempt, keeps refreshing during manual responses and reviews, and offers **CSV results / collections** for imported results. Archived reply links open and scroll to the selected turn. Header controls wrap in narrow windows; remote browsers see why project creation is disabled.

Agent-loop projects have a dedicated **Results** view backed by scheduler records,
including turns, elapsed time (with manual/review waiting), archived replies and
a run selector. Use **Read responses / handoffs** to expand a response, load more
or copy it. No results CSV import is needed. Connect through `alfrd serve` to read
these records.

Create a project with **Projects** (the folder icon at the left of the header project picker); its **Open existing project…** opens a folder that already has `alfrd.yaml`. The picker itself switches projects: long names are shortened in the middle, and the list shows them in full with their folder.
In **Project settings**, use **All settings** to edit every key in `alfrd.yaml` as a
form (checkboxes, choices, numbers, lists; known keys that are not set show their
defaults, and ↺ removes a key so its default applies), or **Edit YAML file** for raw
text. Both views share an unsaved draft. Saving validates and writes `alfrd.yaml`; field edits
reformat only the changed YAML section.

The Studio is ALFRD's web UI. It is plain HTML/CSS/JS in `src/alfrd/web/`.

- No internet needed. Nothing loads from other sites.
- It only reads your files. It never runs your pipeline.

---

## Start it

```bash
alfrd serve                          # server mode, project in this folder
alfrd serve --project /data/proj     # server mode, another folder
alfrd serve                          # in a parent folder: every sub-folder with alfrd.yaml (2 levels)
alfrd serve --demo                   # with demo data
alfrd studio                         # browser mode, no Flask needed
alfrd studio --demo                  # browser mode with demo data
alfrd studio --export site/          # copy the static files (any web host)
```

Open it over HTTP. `file://` does not work (browser rule for ES modules).

### Opening the Studio (access token)

`alfrd serve` creates a random access token, prints the link with it and opens it:

```
ALFRD Studio: http://127.0.0.1:5000/studio/?token=…
```

The first visit trades the token for a session cookie and removes it from the address bar. Without it the server answers 401 and shows a page saying how to get the link. Lost the link, or opening it in another browser?

```bash
alfrd url                  # link for the server on port 5000
alfrd url --port 5055      # another port
```

The token is in `~/.config/alfrd/server-<port>.json` (readable only by you, removed when the server stops). A restart makes a new token, so open the new link; the Studio shows the access page instead of stale data. Scripts send `Authorization: Bearer <token>` (see [status-api.md](status-api.md)).

| Option | |
|---|---|
| `--token TEXT` / `ALFRD_TOKEN` | Pin the token (the same across restarts; for reverse proxies and scripts) |
| `--no-token` | No token check. Only allowed on loopback (`127.0.0.1`, `::1`, `localhost`) and prints a warning: any program or account on this machine can then read your projects and start plans |

**Other machines.** With a non-loopback `--host` the token stays required, but plain HTTP sends it in clear text, and `alfrd serve` warns about it. Prefer an SSH tunnel and keep the server on loopback:

```bash
ssh -L 5000:127.0.0.1:5000 user@server    # then open the alfrd url link on your machine
```

or put an https reverse proxy in front. The cookie is marked `Secure` when the request arrives over https. A proxy that terminates TLS forwards plain HTTP: start the server with **`ALFRD_TRUST_PROXY=1`** so it reads the proxy's `X-Forwarded-For`, `-Proto`, `-Host` and `-Prefix` (one proxy hop). Then the cookie is `Secure`, links keep the proxy's host and prefix, and the client address is the real one, so loopback-only actions are refused to remote browsers. Set it only behind a proxy that overwrites these headers; otherwise any client could fake them.

---

## Two modes

| | Browser mode | Server mode |
|---|---|---|
| Start | `alfrd studio`, GitHub Pages | `alfrd serve` |
| Data | Folder you open in the browser | Project folder on the server |
| Logs | Read from your disk | Fetched from the server |
| Save `alfrd.yaml` | Into the opened folder (Chrome/Edge) or download | Written on the server (keeps `alfrd.yaml.bak`) |
| Write `avica.inp` | Same as above | Written on the server |

Server writes need a browser on the same machine (loopback) plus a CSRF token.

---

## Projects in server mode

- **One project:** start `alfrd serve` in its folder (or `--project DIR`).
- **Several projects under one folder:** start it in the parent. Every sub-folder with its own `alfrd.yaml` / `.alfrd.yaml` is opened (2 levels deep; `--discover-depth N`, `--no-discover`). Skipped: `*.ms`, `raw/`, `tmp_*`, `calibration_tables`, dot-folders, and the inside of a project. The walk stops after 2000 folders (a note is printed).
- **No `alfrd.yaml`?** An AVICA folder (`avica.inp`, `avica.logs/`, `reductions/`) opens with the built-in default manifest (`name` = folder name; `ALFRD_DEFAULT_MANIFEST=/file.yaml` picks another). Project settings shows *default — not saved*; **Save** writes a local `alfrd.yaml`. Discovery only counts local files.
- **Connect more:** ⇅ Import / Export → Import… → ALFRD server → **Browse…** lists the server's folders (not your laptop's — right for an SSH tunnel). Projects get a badge with their name. **Connect**, **Connect all projects here**, **Connect this folder** (no `alfrd.yaml`: uses the default; a parent's `alfrd.yaml` is never used), or **Use this folder** to fill the path. Keys: ↑/↓ move, Enter opens, Backspace goes up, Esc closes. Loopback browser only (CSRF-checked); hidden otherwise.
- **Forget / Rediscover:** ⚙ Settings → Known projects. *Forget* removes a project from the list (files stay). **↻ Rediscover** brings back the serve folder, the projects found under it, and anything forgotten since the server started.
- **Delete permanently:** the Remove dialog, after you type the project name exactly, deletes ALFRD's files — `alfrd.yaml` (and `.bak`), the plan, targets and notes files, `.alfrd/` (plans, history, locks), task bookkeeping (`<task>/.alfrd-task.json`) and runtime-run state (`runs/<id>/.alfrd`) — and forgets the project. Your task files, handoffs, worktrees, results and data stay. Tick **Delete all files and folders** (off by default) to delete the whole project folder instead; that is refused for a symlinked folder, a top-level folder, your home folder (or one containing it), the folder holding the runtime database, or a folder containing another registered project, and the dialog shows the size and warns about a git repository first. Both are refused while runs are active.
- **Setup wizard:** a template can declare a setup wizard (`quickstart:` in alfrd.yaml). Overview → Get started → *Open the setup wizard* opens it as a dialog: the AVICA template fills in `avica.inp`, the agent-loop template the agents' commands, models, sequence and review in `alfrd.yaml`. Path fields have a Browse… button for server folders.
- `--all-projects` shows every project ever connected.

---

## Live updates

No need to click **Re-scan**. The **● Live** badge (top bar) shows the state. Click it to turn live updates on or off (also in ⚙ Settings).

- **Server mode:** `alfrd serve` checks the files `alfrd.yaml` declares every 2 s while things change, every 5 s when quiet. It only reads file sizes and dates, and never walks into `*.ms`. The Studio then re-reads just the changed files.
- **Browser mode (Chrome/Edge):** the opened folder is checked every 5 s (30 s when quiet).
- **Logs:** an open log that is on screen, and the full-screen log, follow the file as it grows. Only the new bytes are fetched. At most 3 logs at a time. Scroll up to pause on a spot; **↓ New output** jumps back. **Follow** in full screen does the same.
- **Log Stream tabs:** the `>_` button on a log (or **Minimize to Log Stream** in full screen) docks it in the Log Stream panel at the bottom, like a terminal tab in VS Code. Switch to Overview or Workflow and it keeps following. Up to 6 tabs, remembered after a reload; **×** stops following, **—** hides the panel (the footer shows “N followed”). Drag the panel's top edge (or focus it and use ↑/↓) to resize.
- **Hidden tab:** nothing runs. Back on the tab, the Studio catches up. (With browser notifications on, the server stream stays open so they still arrive.)
- **Which run:** Workflow opens on the newest working run (a turn running, or waiting for review or a response), else the newest active one, else a scheduled one, not just the newest run. With more than one active run, an "N active runs" switcher appears above the runs; scheduled runs read "Scheduled HH:MM". A run you pick stays shown; when it ends while another works, the Studio switches and says so.
- **Notifications:** ⚙ Settings → Notifications lists the notification routes with **Send test**, and *Show browser notifications* (asks for permission on click) shows reviews, failed/finished runs and idle turns while a tab is open. Links such as `#/workflow?project=<id>&plan=<run>&unit=<turn>` open that run and turn. See [notifications.md](notifications.md).
- Your place is kept: selection, open groups and logs, scroll, the field you are typing in. Unsaved workflow edits are never replaced.

| Badge | Meaning |
|---|---|
| ● Live | Following changes |
| Paused | Tab hidden |
| Reconnecting | Server not reachable; retries by itself |
| Live paused | Browser mode: click to allow reading the folder again |
| Live off | Turned off, or `alfrd serve --live-interval 0` |

Slow or huge tree? A check never takes more than 1/20 of the time: a check that takes 0.5 s runs every 10 s. Tune it with `alfrd serve --live-interval SECONDS` (0 = off). It works on NFS/sshfs mounts too (no inotify).

---

## Open a project (browser mode)

1. Click **⇅ Import / Export → Import… → Open project folder**.
2. Pick the folder with `alfrd.yaml`.

- **Chrome / Edge:** reads only what `alfrd.yaml` points to. Fast. Remembers the folder for **Re-scan** and live updates.
- **Firefox / Safari:** lists the whole folder first. Slower.
- **Remote tree:** run `alfrd avica scan <folder> --bundle scan.json` there. Import `scan.json`.

Never opened: measurement sets (`*.ms`), `raw/`, scratch folders.

---

## `alfrd.yaml` drives everything

Nothing about a pipeline is built into the Studio.

| Key | Controls |
|---|---|
| `template: avica` | AVICA views + default step definitions |
| `stages:` | Groups of steps (`{id, title}`) |
| step `label`, `category`, `stage`, `description`, `icon` | How a step looks |
| step `metadata:` | Metadata health checks |
| step `logs:` | Step logs (Workflow → Logs, Logs view) |
| step `skip: true` | Leaves a template step out: not shown, not in new plan CSVs, never run; the steps around it are chained |
| `step_defaults.logs` | Logs added to every step |
| artifacts with `kind: log` | Extra groups in the Logs view |
| `overview.ms_path` | Overview "MS Storage Path" column |
| `project_settings.field_aliases` | Old names → new names |

The AVICA template: `src/alfrd/web/assets/templates/avica.yaml`.
Your `alfrd.yaml` overrides any key in it, including single step definitions under `steps:`.

To drop a step you don't use, set `skip: true` in either place:

```yaml
steps:
  phaseshift: {skip: true}        # the project's own step definitions
workflows:
  - name: avica
    steps: [preprocess_fitsidi, fits_to_ms, {id: phaseshift, skip: true}, avica_avg]
```

A `depends_on` (or `after: <step>`) naming a skipped step is ignored. On a step, `after:` with a number or
duration (`+1h`, `90m`) is a start delay, not a dependency. Plan CSVs made before the change keep their column; mark its cells `skip`.

### Example step

```yaml
workflows:
  - name: avica
    steps:
      - id: avica_snr
        stage: meta
        label: SNR check
        metadata:
          - {file: "refants_{band}_{target}.avica", label: Reference antennas, require: [refant]}
        logs:
          - "{workdir}/wd_{band}/avica_snr_*log*"
```

### Placeholders

- `{workdir}` – a work folder (matches `avica.workdir`), e.g. `reductions/RDV41/wd`
- `{meta_dir}` – `{workdir}/avica.meta`
- `{logs}` – `avica.logs`
- `{target_dir}`, `{band}`, `{target}`, `{project_code}`, `{n}`, `{step}`
- `*`, `?` – wildcards inside one folder

### Field aliases

```yaml
project_settings:
  field_aliases:
    vasco_avg: avica_avg
    "vasco*": "avica*"      # prefix rule
```

Used for step names, result CSV rows and folder names.
Edit them in **Settings → Project settings**.

---

## Views

- **Overview** – targets × steps. The toolbar has three groups: **Status** (the counters are the status filter, plus *No work folder*), **Find** (search, AVICA project code, project) and **View** (group by, summary/details, columns). Active filters show as removable chips with *Clear all*. **Targets** → *Import targets CSV…* / *Download targets CSV*. Check targets to get **Run…** (a new plan from them, server) and **Remove from targets file** (asks first; result CSVs, work dirs and the plan CSV are not touched). The drawer's **Retry step** is only there for runtime-managed runs. CSV export. Drawer with the first failure, files and notes.
- **Workflow** – list view first. Graph view, validation, simulation (visual only). Inspector: parameters, bindings, logs.
- **Metadata** – metadata health per step. `avica.meta` grouped by step. AVICA config. rPicard inputs.
- **Results** – time and progress. **Recent only** (default) or **Full history**. Below: one card per `kind: collection` artifact (rPicard diagnostics). Nothing is read until you press **Show** (server mode).
- **Logs** – every declared log. Grouped by step or artifact. Click to open. `⤢` for full screen, `>_` to follow it in a Log Stream tab.
- **Chat** – talk to the project's agents (server mode); see [Chat with the agents](#chat-with-the-agents).
- **Settings** – edit, validate and save `alfrd.yaml`. Field alias form. **Agent chat** edits the `chat:` block.

Status rule: successes only = completed. Failures only = failed. Both = partial.

---

## Edit the workflow

In **Workflow**, choose **Edit workflow**. Changes stay in a project draft until
you press **Save**. **Undo** reverses the last change; **Discard** leaves the file
unchanged. **YAML** previews what Save will write. Editing does not run commands.

- **Steps:** add a step, edit its label and command, skip it without deleting it,
  move it earlier or later, or delete it. The step dialog also sets its stage,
  dependencies, time limit, start delay, category, description and log patterns.
  Graph view offers these controls through **Step actions** after selecting a node.
  Steps using “after previous” follow the new order; chosen dependencies stay fixed.
  Deleting a step lists affected dependents before removing their references to it.
- **Workflow settings:** choose the default command for steps without their own,
  targets at once (1–64), and what happens on failure. Set a default time limit,
  explicitly choose no limit, or inherit it. Log patterns can inherit or use an
  explicit list, including an empty list. Execution and log defaults apply across
  the project; existing plans keep their saved settings. **Apply to draft** makes
  one undoable change, and applying unchanged settings leaves the draft clean.
- **Stages:** rename, reorder or add stages. IDs stay unchanged when renamed,
  and steps keep their stage assignments. Stages cannot be deleted here.
- **Agent loops:** use the turn-order chips to add, move or remove agent turns,
  and set **Total turns**. **Agents, roles & review…** opens the agent settings.
  Workflow settings shows the required concurrency and failure behavior read-only;
  Stages is hidden for loops.

For an empty workflow, **Add first step**, **Agent loop template** (only with
`alfrd serve --experimental-features`) and **AVICA template** start a draft. Template cards write nothing until Save; Discard returns
to the empty state with the original file intact.

Save validates the draft and rewrites only changed top-level YAML sections.
Unchanged sections retain their text and comments, and unrelated settings are kept.
Comments inside a rewritten section may be reformatted. Browser and server saving
follow the rules in [Two modes](#two-modes).

---

## Work on the canvas

Everything the step forms do is also available on the **Graph** itself; edits go to the same draft
(Save writes the active `alfrd.yaml` with the stale-file guard, and is refused with a clear message
when the workflow would contain a dependency cycle, a step waiting on itself or an unknown step —
the server checks this for every save, not only the Studio's).

- **Right-click empty space → Add step here.** The new step is placed there and opens in the
  inspector's **Configure** tab: label, name, command or entrypoint, the steps it waits for, inputs,
  outputs, stage, time limit, start delay, status source and skip. **Apply to draft** makes one
  undoable change; **More options…** opens the full step form. Leaving a step with unapplied
  Configure changes asks first.
- **Right-click a step** for Configure, Connect, Disconnect…, Add a step after it, Skip / unskip,
  Delete, and run actions (below).
- **Connect** by dragging from the dot at the bottom of a step onto another step (that step then
  waits for the first). A step that would create a cycle is marked red while you hover it, and the
  drop is refused with the cycle, e.g. *prep → step → prep*. **Disconnect** from the step menu, or
  click an arrow.
- **Run actions** (`alfrd serve`, same machine): **Run <step>…**; for the header target's cell in
  the run shown: **Stop** (that command only), **Retry**, **Skip**, **Open its log**. The empty-space
  menu and the toolbar have **Run workflow…**, **Schedule a run…**, **Pause**, **Resume** /
  **Start now** and **Cancel**, each shown only when it applies.
- Arrows and edge colours show status and dependencies; the selected step's arrows are drawn bolder,
  and a step's tooltip lists what it waits for.

Every right-click action has a keyboard equivalent with the canvas focused: arrow keys select a step,
**Shift+F10** or the **Menu** key opens its menu, **N** adds a step after it, **E** configures it,
**C** starts connecting (arrows pick the step that should wait, **Enter** connects, **Esc** cancels),
**Delete** deletes it (asks first), **+ / −** zoom, **0** fits, **F** toggles fullscreen.

### Compact mode and fullscreen

**Compact** (toolbar button, palette, or Alt+Shift+C — ⌥⇧C on macOS) keeps the header and sidebar and
shows only the canvas with the run controls, zoom and an **Exit compact** button; the summary, the
mini-map, the list/runs switch, the footer and the log strip are hidden (the Configure inspector
still opens when you configure a step). The choice is remembered in this browser across reloads.
**Fullscreen** (toolbar button or **F**) is separate: it shows the Workflow view with the browser
fullscreen; Esc or **F** leaves it. Neither changes the selected target or affects a running plan.

## Superuser settings

`/superuser` (Studio settings → Server → *Superuser settings…*) sets what every `alfrd serve` of this
user shows and allows: experimental features, hidden sections and templates, read-only mode and locked
actions, chat limits. It also has statistics and an ops panel with every active run and running agent.
Only from the server machine. Details: [superuser.md](superuser.md).

## Chat with the agents

**Chat** in the sidebar (server mode; `#/chat/<session>?p=<project>`) is a conversation with the
project's orchestrator and its specialist agents. It is experimental: the **Chat mode** preference exists
only when the server runs with `alfrd serve --experimental-features`. It is then off by default: turn on
**Chat mode** in Studio settings → Preferences (per browser). Off, the Chat entry, the dock button, Alt+Shift+A, the palette
entries and chat runs in Workflow are hidden. How requests run, the protocol and the bounds are
in [orchestration.md](orchestration.md).

- **Sessions** are listed on the left; **New** starts one. The open session is in the URL, so Back /
  Forward and a reload return to it. Hover a session (or focus it) for **Rename** (also F2; Enter saves,
  Esc cancels) and **Delete** (also the Delete key; asks first). Deleting removes the session's folder in
  `.alfrd/chat/` and its plan in `.alfrd/plans/chat-<id>/` (messages, tasks, logs, mailbox) for good; it
  is refused while a request is still active, so cancel its tasks first. The sidebar button next to
  **New** folds the list away (remembered in this browser); **Chats** in the header brings it back. On a
  phone **Chats** opens a modal list with trapped focus; Escape returns to Chats. **Search sessions**
  filters local title, ID and available preview, together with **Needs me**, without changing the
  selected conversation or its draft. Failed renames keep the typed name for another save.
- **Composer:** Enter sends, Shift+Enter adds a line. A message that starts with `@agent` goes
  straight to that agent (no orchestrator call); typing `@` at the start lists the configured agents
  with their labels and roles (arrows, Enter or Tab inserts a hint; submit again to execute a slash
  command; Esc closes hints first). IME composition never sends. The input grows with the draft.
  Send stays separate from **Stop request** while work runs. Stop confirms a concrete request and
  task list, keeps completed work and draft text, and reports partial failures; **Retry Stop** only
  retries that list. Each cancellation requires a fresh conversation snapshot; a failed refresh
  leaves the task untouched for retry. During planning it explains when no task can yet be stopped. Global shortcuts stay off
  while you type. A leading `/` opens the commands: `/setup` (who does what, with which model:
  orchestrator and escalation models, role → agent, model, fallback agent, availability, and **New agent**:
  a Codex / Claude Code / Antigravity agent with its own model, e.g. `@luna` on Codex `gpt-5.6-luna`, or a
  preset for Ollama / Hugging Face / any OpenAI-compatible endpoint), `/help`, `/new`, `/graph`.
- **Activity:** every running call (orchestrator planning included) has a card under the transcript
  with its model, elapsed time and the end of its log, following new lines; **Open log** opens the full
  log (it can be minimized to the Log Stream). Task messages link to their log and reply.
- **Transcript:** your messages, the orchestrator's plan and final answer, each agent's result
  summary with its artifacts (**View** fetches that version of the file, `path@sha256`, a page at a
  time), and ALFRD's notes (errors, budget stops). **Full transcript** loads a task's attempts and,
  per attempt, its prompt or reply only when you open it.
- **Task graph:** the request's tasks on the canvas renderer of the Workflow view (status colours,
  owner, dependencies; delegated sub-tasks dotted; orchestrator calls with an accent border and dotted
  links to the tasks they planned). **Full screen** (button, **F** on the graph, or `/graph`) opens it
  over the whole window; Esc returns. Click a task, or focus the graph and use the
  arrows and Enter, for its details: acceptance criteria, budget, dependencies, attempts and usage,
  result, artifacts, and **Cancel** (queued, waiting or running tasks) or **Retry** (failed, blocked,
  cancelled or stopped tasks; the tasks it blocked run again too).
- **Blockers and decisions:** a task that needs you shows its question, the options and an answer
  field; **Send decision** lets it continue. Blockers addressed to the orchestrator are marked as such.
- **Progress:** the newest active request's tasks done, cost and time against its bounds (meters turn
  amber at 80 % and red at the limit), tokens and orchestrator calls, plus the session totals. Cost
  shows "—" for agents whose CLI does not report it.
- **Updates** arrive through the live event stream (the badge says *Live* or *Disconnected*);
  visible recorded activity still polls at a bounded 2 s cadence. Mailbox polls every 2 s while
  active/pending, otherwise every 10 s; hidden views pause. With live updates off, an active request
  is also re-read every 15 s. Refresh re-reads it at once. Inline questions and permissions preserve
  unsent answers across updates. **Action details** exposes exact supplied arguments and action hash
  beside Approve once / Deny. Replies use server versions; changed permissions require review again.

### Chat dock

The chat icon in the top bar (or **Alt+Shift+A**, also from the dock's own message field) opens a
dock on the right of every project view: Workflow, Results, Overview, Schedule, plugin pages. It shows
the session you last opened (a selector switches or starts one), its transcript, the composer (same
`@agent` completion; Enter sends, Shift+Enter adds a line) and one progress line for the active
request: tasks done, cost against the bound, elapsed time, tokens and warning badges. **Open full**
closes the dock and opens the same session on the Chat page (graph, decisions, sessions), preserving its draft. Open or closed and the width (drag its left edge,
or arrow keys on it) are remembered in this browser. In compact workflow mode the dock stays hidden
unless you open it by hand. Windows 900 px wide or narrower show it as an overlay on top of the view
(Escape or Close returns focus to the invoker; the background is inert and Tab stays inside). It loads on the first open, updates from the same live event stream
as the Chat page, and is a labelled landmark (`aria-expanded` on its button).

Each chat session is also a run: Workflow → Runs lists it under *Chat sessions* (tasks × agents grid,
logs, usage, Pause / Resume / Cancel, **Open in Chat**), and with it picked Graph and List show its
tasks. It never counts as the active workflow run. Each task shows its attempts with **Open log**
and its handoff folder, and **Promote to workflow** (Chat page) previews the request's tasks as
workflow steps, then saves them into the active `alfrd.yaml` with the usual history, changed-on-disk
and cycle checks.

Token and cost report: Chat shows, per task, request and session, new input, cache read, cache write,
output and cost; Schedule shows the same per turn and for the plan (Agent tokens line, Usage table).
Warning budgets `execution.warn_turn_cost_usd` and `chat.warn_task_cost_usd` mark turns or tasks that
cost more with a badge and a `budget.warning` event; nothing is stopped.

**Settings → Agent chat → Chat agents & limits** edits the `chat:` block of the active `alfrd.yaml`:
the agents (label, entrypoint, model, adapter, persona, roles; add your own or re-add a preset), the
orchestrator, tasks at once, recent messages in planning, the final-summary call and the per-request
bounds (depth, retries, cost, time, tasks). **Apply to draft** puts it in the Settings editor;
**Apply and save** also saves through the usual path (version history, and the changed-on-disk check:
if the file changed since it was loaded you see the difference instead of overwriting it).

## New project on a canvas

**New project → Design on canvas…** (or `#/new-project`) plans a project before creating it:

1. **Folder**, **name**, **template** (basic or avica; agent-loop too with `alfrd serve --experimental-features`) and
   **configuration file** (`.alfrd/alfrd.yaml` by default, or a root `alfrd.yaml`).
2. **Start** with the template's workflow (its steps and dependencies, made explicit; skipped steps are
   left out) or an empty canvas.
3. On the canvas: **Add step** (N), select a step to set its id, label, agent (one of the template's
   entrypoints, or its own command) and persona, and the steps it waits for. Connect by dragging a
   step's bottom handle onto another, or with **C**, the arrows and Enter; click an arrow to
   disconnect; Delete removes the selected step. A connection that would make a cycle is refused with
   the cycle. Agent-loop templates show their turn sequence instead: add, remove or change turns, and
   give the initial task and iterations.
4. **Targets:** the first targets (rows of `alfrd.targets.csv`), or for an agent loop the first
   task's folder name.
5. **Validate** lists what must be fixed; **Create project** validates again on the server, writes the
   files (in `.alfrd/` unless you chose the root file) and opens the project on **Workflow** with the
   first target selected.

The draft is kept in this browser (it survives a reload) until it is created or discarded. The short
**Quick dialog…** remains for folder, name and template only.

## Keyboard and browser

Press **?** (or choose *Keyboard shortcuts* in the palette or the footer) for the full list, written
with your platform's keys (⌘ ⌥ ⇧ on macOS, Ctrl Alt Shift elsewhere). Global shortcuts — **?**,
Ctrl/⌘+K (palette), Ctrl/⌘+Alt+N (new target), Alt+Shift+S (sidebar), Alt+Shift+C (compact), Alt+Shift+A (chat dock) — do
nothing while you type in a field. Browser shortcuts are left alone.

The URL holds the section, project and target (`#/workflow?p=<project>&t=<target>`). Choosing a
section, project or target adds a history entry, so **Back** / **Forward** step through them, and a
reload restores them. (Because of that, **Quit** may not be able to close the tab; it then says so.)

Unsaved edits — the `alfrd.yaml` editor in Settings, a workflow draft and unapplied Configure
changes — are kept per project while you move around the Studio (leaving the view shows a reminder),
and the browser asks before a reload or closing the tab would lose them. A lost connection to
`alfrd serve` shows a notice at the top of the page until it is back.

## AVICA specifics

Folder layout:

```
alfrd.yaml
avica.inp
avica.summary.json        ← `alfrd avica summary`
avica.logs/
reductions/<TARGET>_result.csv
reductions/<CODE>/wd/     ← project code work dir (wd_1, wd_2, …)
    avica.meta/
    input_template/
    wd_<band>/
    wd_<band>_<TARGET>/
```

- **ALFRD project** = the `name` in `alfrd.yaml`.
- **Project code** = the `<CODE>` folder (BV019, RDV41, …).
- `target_dir` comes from the summary, then `avica.inp`, then `alfrd.yaml`.
- Work dirs that mention a target attach automatically. **Attach folder** adds others.

Parameters:

- The Workflow inspector shows each step's parameters from `alfrd avica summary`.
- **Apply** writes `<step>.<param> = value` to `avica.inp`.

rPicard inputs:

- Keys overridden by `picard_input_template_update` are **bold**. The old value is struck through.

Target list (`alfrd.targets.csv`):

- Sources with their FITS file names and project code, one row per (target, code). Declared by `targets:` in alfrd.yaml (`csv:` path, `columns:` header names accepted on import, case-insensitive). Written with the plan CSV's column names (`TARGET_NAME,FILENAMES,PROJECT_CODE`).
- Kept across plans. `alfrd.plan.csv` stays the state of one run and is what the runner reads. The Run dialog and `alfrd plan new --from-targets` fill FILENAMES / PROJECT_CODE from it.
- Overview reads it like every other root CSV. When it and the plan CSV disagree, the targets file wins for Overview.
- Import (Overview → **Targets**, the Run dialog, `alfrd targets import FILE [--replace] [--dry-run]`): column mapping, a preview (new / updated / unchanged / removed), **Merge** (default: an empty FITS cell never erases a known one) or **Replace**. Rows with problems (`#…` or `@` in a name, a repeated target+code) block the import, with their line numbers. Browser mode writes into the opened folder, or downloads the file.

rPicard diagnostics (Results):

- The avica template declares `rpicard_diagnostics` (`kind: collection`, `path_pattern: "{workdir}/wd_{band}_{target}/diagnostics_*"`). Each matching folder is one run, newest first (by the date in its name).
- **Show** lists the runs of the selected target (× shows every target). Pick code/work dir, band and run. `SUMMARY_*.pdf`, `fringes_overview.csv.*` and `flags.list` are pinned on top. Each folder (ACCOR, C_BP, GAIN, …) opens as a thumbnail grid, 60 at a time, loaded as they scroll into view. Click a plot for full size (←/→ walk the folder).
- **Compare** shows a second run next to the first. Opening a folder opens it on both sides.
- `fringes_overview.csv.*` opens as a sortable table, text files in a viewer. PDFs open in a new tab. `.ps` is download only. Files are served only when they are part of a declared run (`include` / `exclude` / `depth`). SVG and HTML are never shown inline.
- More collections: add `kind: collection` entries under `artifacts:` in alfrd.yaml.

Commands:

```bash
alfrd targets show [-C ROOT]            # the target list
alfrd targets import FILE [--replace] [--dry-run]
alfrd targets remove NAME [NAME@CODE …]
alfrd plan new --from-targets           # a plan from every row of the target list
alfrd avica summary [ROOT]              # run and cache `avica pipe config --summary`
alfrd avica summary --no-run            # show the cache
alfrd avica scan [ROOT]                 # JSON of the tree
alfrd avica scan ROOT --bundle scan.json
```

---

## Where data lives

- Browser mode: `localStorage` of that site. **Settings → Clear saved Studio data** removes it.
- **Settings → Reset view state** forgets filters, zoom and folded panels.
- **⇅ Import / Export → Studio snapshot** moves a session to another machine.

---

## GitHub Pages

`.github/workflows/pages.yml` publishes `src/alfrd/web/`.

1. Repo **Settings → Pages → Source: GitHub Actions**.
2. Push to `main`.

The hosted Studio is browser mode. People open their own folders.

---

## Development

```
src/alfrd/web/
├── index.html
├── css/studio.css
├── js/app.js              state, shell, import, saving
├── js/components/         overview, canvas, metadata, results, logs, logview, alfrd_config, attach
├── js/data/               defs (alfrd.yaml), model, importers, folder_scan, avica, demo, server
├── js/utils/              yaml_parser, csv_parser, dom
└── assets/templates/      avica.yaml
```

Python side: `alfrd/studio_defs.py`, `alfrd/avica_layout.py`, `alfrd/gui/studio.py`.

Tests:

```bash
python -m pytest -q -n auto -m "not serial"   # everything else, in parallel (about a minute)
python -m pytest -q -m serial                 # tests that measure time or CPU, alone
node --test tests/studio_js/*.test.mjs        # the Studio's tests only (pytest runs them too)
```

Add `-m "not slow and not serial"` to skip building and installing the wheel while iterating.

## Setup forms (`quickstart:`)

```yaml
quickstart:
  setup:                                  # form id; several forms are allowed
    type: form
    title: Set up AVICA
    description: Shown above the fields.
    target: {file: avica.inp}             # a key = value file in the project folder, or alfrd.yaml
    fields:
      folder_for_fits: {type: path, label: Raw FITS-IDI folder, required: true, help: Folder with the FITS files.}
      casadir: path                       # short form: just the type
      use_local_antab: {type: toggle, default: true}
      rpicard.mpi_cores: {type: number, min: 1, max: 64}
      mode: {type: select, options: [auto, off]}
      artifact_dirs: {type: list}
```

| Type | Control | Written as |
|---|---|---|
| `textbox` (`text`), `textarea` | text | text |
| `number` | number (`min`, `max`) | int or float |
| `toggle` (`checkbox`) | checkbox | `True` / `False` |
| `path` | text + Browse… (server folders); `pick: program` checks the server's PATH instead | text; `must_exist: true` refuses missing paths, otherwise a note |
| `select` | `options:` | the chosen option |
| `list` | comma-separated text | a list |

Other field settings: `label`, `help`, `placeholder`, `required`, `default` (shown when the file has no value).
For a `key = value` file the field key is the key (`<step>.<param>` allowed); only changed values are written and other lines and comments stay. For `alfrd.yaml` the key is a dotted path, where a list segment is an index or an item's `name` (`entrypoint.claude.model`, `workflows.0.repeat.iterations`); an emptied optional field removes the key. Only the changed top-level sections are rewritten, the result must validate and load, or nothing is saved. A project's `quickstart:` overrides its template's forms one by one. API: `GET /api/studio/projects/<p>/quickstart`, `POST …/quickstart/<form>` with `{"values": {...}}` (loopback + CSRF).

