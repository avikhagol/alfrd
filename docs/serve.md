<img src="brand/alfrd-mark.svg" alt="ALFRD" width="44" align="right">

# Running `alfrd serve`

`alfrd serve` starts the Studio for the project in the current folder and opens it in your browser
(`http://127.0.0.1:5000/studio/`). For what each view shows, see the [Studio guide](studio-guide.md).

## Studio screenshots

<table>
  <tr>
    <td><a href="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/images/workflow.png"><img src="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/images/workflow.png" alt="Workflow view: the eight steps of one target with their parameters and status; open full-size image"></a></td>
    <td><a href="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/images/results.png"><img src="https://raw.githubusercontent.com/avikhagol/alfrd/HEAD/docs/images/results.png" alt="Results view: elapsed time, step durations and outcome per step; open full-size image"></a></td>
  </tr>
  <tr>
    <td align="center"><sub>Workflow: steps, parameters and status for one target</sub></td>
    <td align="center"><sub>Results: timings and outcomes across all targets</sub></td>
  </tr>
</table>

## Examples

Follow the configuration instructions for [avica](https://avikhagol.github.io/avica-demos).

```bash
# Open the project in the current folder
cd /data/avica_0.3
alfrd serve

# Open a project somewhere else
alfrd serve --project /data/avica_0.3

# Another port, no browser pop-up (e.g. over SSH)
alfrd serve --port 8050 --no-browser

# Try it with demo data
alfrd serve --demo

# Keep the runtime database in a chosen file
alfrd serve --runtime-db ~/alfrd/runtime.sqlite

# Also show every project connected before
alfrd serve --all-projects

# A parent folder of several projects: opens every sub-folder with its own
# alfrd.yaml (2 levels deep; --discover-depth N, or --no-discover)
cd /data/data_reductions      # pipe_comparison/alfrd.yaml, alma/alfrd.yaml, …
alfrd serve

# Check the project folder less often (default 2 s while busy; 0 = no live updates)
alfrd serve --live-interval 5
```

The Studio updates by itself (**● Live** in the top bar): changed files are re-read, and open logs
follow the file as it grows. Live polling pauses while the tab is hidden. See
[Live updates](studio-guide.md#live-updates).

No server? Use browser mode:

```bash
alfrd studio        # opens http://127.0.0.1:8080/, then ⇅ Import / Export → Import… → Open project folder
```

## Projects

Projects are remembered in `~/.alfrd/runtime.sqlite`:

```bash
alfrd projects list              # what is remembered
alfrd projects forget OLD_NAME   # remove one (files stay on disk)
```

Or in the Studio: **⚙ Settings → Known projects → Forget**. Forgot one by mistake? **↻ Rediscover**
there brings back the serve folder (or the projects found under it) and anything forgotten since the
server started.

No `alfrd.yaml` in an AVICA folder (`avica.inp`, `avica.logs/`, `reductions/`)? `alfrd serve` uses the
built-in default (an AVICA manifest, `name` = folder name). `alfrd manifest default -o alfrd.yaml`
writes it so you can edit it.

Connect more projects: **⇅ Import / Export → Import… → ALFRD server → Browse…** walks the server's
folders (not your laptop's), marks folders with an `alfrd.yaml`, and connects one or all of them. A
folder without `alfrd.yaml` connects too, with the default one.

## Stopping and remote machines

Stop the server: **Ctrl+C**, or the **⏻ Quit** button (top right, same machine only). Quit also
closes the tab when the browser allows it (the tab `alfrd serve` opened).

Working on a remote machine? Forward the port:

```bash
ssh -L 5000:127.0.0.1:5000 user@cluster   # then run `alfrd serve --no-browser` there
```

Good to know:

- The first visit needs the access token in the link `alfrd serve` prints. See
  [Opening the Studio](studio-guide.md#opening-the-studio-access-token).
- Saving `alfrd.yaml` or `avica.inp` from the UI only works from the same machine (loopback).
- `alfrd gui` is the same as `alfrd serve`.
