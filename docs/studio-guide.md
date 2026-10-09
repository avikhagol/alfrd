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
- **Settings** – edit, validate and save `alfrd.yaml`. Field alias form.

Status rule: successes only = completed. Failures only = failed. Both = partial.

---

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

