# ALFRD Studio guide

The Studio is ALFRD's web UI. It is plain HTML/CSS/JS in `src/alfrd/web/`.

- No internet needed. Nothing loads from other sites.
- It only reads your files. It never runs your pipeline.

---

## Start it

```bash
alfrd serve                          # server mode, project in this folder
alfrd serve --project /data/proj     # server mode, another folder
alfrd serve --demo                   # with demo data
alfrd studio                         # browser mode, no Flask needed
alfrd studio --demo                  # browser mode with demo data
alfrd studio --export site/          # copy the static files (any web host)
```

Open it over HTTP. `file://` does not work (browser rule for ES modules).

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

## Live updates

No need to click **Re-scan**. The **● Live** badge (top bar) shows the state. Click it to turn live updates on or off (also in ⚙ Settings).

- **Server mode:** `alfrd serve` checks the files `alfrd.yaml` declares every 2 s while things change, every 5 s when quiet. It only reads file sizes and dates, and never walks into `*.ms`. The Studio then re-reads just the changed files.
- **Browser mode (Chrome/Edge):** the opened folder is checked every 5 s (30 s when quiet).
- **Logs:** an open log that is on screen, and the full-screen log, follow the file as it grows. Only the new bytes are fetched. At most 3 logs at a time. Scroll up to pause on a spot; **↓ New output** jumps back. **Follow** in full screen does the same.
- **Hidden tab:** nothing runs. Back on the tab, the Studio catches up.
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

1. Click **Import → Open project folder**.
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
| `step_defaults.logs` | Logs added to every step |
| artifacts with `kind: log` | Extra groups in the Logs view |
| `overview.ms_path` | Overview "MS Storage Path" column |
| `project_settings.field_aliases` | Old names → new names |

The AVICA template: `src/alfrd/web/assets/templates/avica.yaml`.
Your `alfrd.yaml` overrides any key in it.

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

- **Overview** – targets × steps. Filters, search, groups, CSV export. Drawer with the first failure, files and notes.
- **Workflow** – list view first. Graph view, validation, simulation (visual only). Inspector: parameters, bindings, logs.
- **Metadata** – metadata health per step. `avica.meta` grouped by step. AVICA config. rPicard inputs.
- **Results** – time and progress. **Recent only** (default) or **Full history**.
- **Logs** – every declared log. Grouped by step or artifact. Click to open. `⤢` for full screen.
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

Commands:

```bash
alfrd avica summary [ROOT]              # run and cache `avica pipe config --summary`
alfrd avica summary --no-run            # show the cache
alfrd avica scan [ROOT]                 # JSON of the tree
alfrd avica scan ROOT --bundle scan.json
```

---

## Where data lives

- Browser mode: `localStorage` of that site. **Settings → Clear saved Studio data** removes it.
- **Settings → Reset view state** forgets filters, zoom and folded panels.
- **Export → Studio snapshot** moves a session to another machine.

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
python -m pytest -q
node --test tests/studio_js/parsers.test.mjs
```
