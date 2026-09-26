# ALFRD dashboard guide

Start the local dashboard with `alfrd serve` (`alfrd gui` is an alias). It uses the canonical runtime SQLite database by default, prints the Studio and dashboard URLs, and opens the Workflow Studio after HTTP startup. `/` redirects to `/studio/` (see [studio-guide.md](studio-guide.md)); this server-rendered dashboard remains at `/dashboard/`. Use `--runtime-db PATH` to select another database, or `--no-browser` for headless use. Debug browser opening happens only in the reloader child; debug mode is restricted to loopback interfaces.

The dashboard is usable without network access: its styles and interaction code are packaged with ALFRD. On a narrow display, tables scroll horizontally rather than hiding values.

## Connect a project

Choose **Connect project** and enter an existing project directory, `alfrd.yaml`, or `.alfrd.yaml`. The service looks only at that selection and records the connection in the runtime database. It does not create directories, write a manifest, import project Python, or execute a workflow.

If a form cannot be accepted, the page explains why and retains the submitted values. A successful connection returns to the project page with a confirmation.

When both manifest spellings exist, `alfrd.yaml` takes precedence, including when `.alfrd.yaml` is selected explicitly. GUI connections reject manifest file symlinks. Reconnecting the same name/directory is idempotent; the same name at another directory is a conflict. Project names must start with a letter or digit and otherwise contain only letters, digits, dots, underscores or hyphens. Entrypoints are registered as one-step runtime workflow definitions, not executed.

Connections and artifact registration require a loopback client and Host plus a session CSRF token. `serve` disables mutations entirely on non-loopback bindings (including `0.0.0.0` and `::`). This is not multi-user authentication: do not publish a dashboard containing private metadata on an untrusted network. For a custom Flask deployment behind a reverse proxy, set `RUNTIME_MUTATIONS_ENABLED=False` explicitly and provide deployment authentication; do not rely on a proxy rewriting all clients as loopback.

## Register an artifact

From a project page, choose **Register artifact**. Select a run, optionally select one of its steps, and enter a path relative to that run's working directory. The artifact must already exist. ALFRD rejects paths outside that directory, hidden files, and invalid run or step selections. Registration records metadata only; it does not upload or expose a download URL.

Runs must already exist in the runtime DB, for example from a CLI run or historical import. There is no run creation or execution hidden in this form. File contents and the source tree are not changed; registration stores a digest and metadata in SQLite without writing a run manifest. Containment and sensitive-path checks apply to resolved symlink targets too. Directory registrations store directory metadata, not recursive file contents.

## Declarative summaries

Both manifest filenames accept optional `summaries`. Configuration rows are an explicit snapshot, not a claim that ALFRD has evaluated your application configuration. Values must be scalar (text up to 4096 characters, number, boolean or null). Keep secrets out of snapshots. Result columns are an allowlist over recorded runtime state; no commands, Python imports, templating expressions or arbitrary data-file reads are supported.

```yaml
version: 1
name: reduction-demo
entrypoint:
  - name: reduce
    cmd: [my-consumer, reduce]
summaries:
  config:
    title: Configuration snapshot
    rows:
      - step: reduce
        parameter: integration_seconds
        source: input/core
        value: 50
      - step: reduce
        parameter: enabled
        source: default
        value: true
  result:
    title: Latest recorded results
    columns: [dataset, step, status, attempt, duration_seconds, finished_at, artifact_count]
```

Validate it with `alfrd manifest validate /path/to/project/alfrd.yaml`. Other allowed result columns are `result_summary` and `error_summary`. Omit `result.columns` for the default complete selection. Results show the newest run per workflow/dataset and its newest attempt for each step. Older run artifacts remain available in the artifact list. Missing summaries have an empty state; malformed manifests display validation errors without executing or importing consumer code. Connection does not resynchronize an existing workflow definition after manifest edits.

The presentation is grounded in AVICA's actual `pipe config --summary` Step/Parameter/Source/Value table (`src/avica/cli_new.py`, `pipe_config`) and `pipe result` latest-attempt status/timing summaries (`src/avica/pipe/report.py`). ALFRD does not depend on or invoke AVICA to display them.

## Matrix detail

Select a matrix cell (or focus it and press Enter/Space) to inspect runtime detail. The dialog can be closed with its close button, by clicking the backdrop, or with Escape, returning focus to the cell. Polling retains active filters and reloads when the matching dataset rows change. Wide tables scroll within their cards. A clear message is shown when refresh or detail loading fails.
