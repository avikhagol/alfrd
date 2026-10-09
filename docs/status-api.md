<img src="brand/alfrd-mark.svg" alt="ALFRD" width="44" align="right">

# Plan status API (`alfrd.plan_status/1`)

A stable, read-only way for Claude, scripts and other harnesses to follow a plan
run. The CLI, the HTTP endpoints and the Python function all build the same
document (`alfrd.api.status.plan_status`), so they never disagree.

- **Reads never change anything.** A dead runner is reported
  (`runner.alive: false`, `runner.stale: true`), not fixed. Add `--reconcile`
  (CLI) to re-attach first.
- **Stable.** `schema` is versioned; within v1 fields are only added. JSON
  Schema: `src/alfrd/schemas/plan_status.v1.json` (also `GET /api/v1/schema/plan_status`).
- Times are UTC (`…Z`), durations in seconds.

## CLI (no server needed)

```
alfrd plan status [PLAN] -C PROJECT --json [--detail summary|rows|full] [--since CURSOR] [--limit N --offset N] [--reconcile]
alfrd plan wait   [PLAN] -C PROJECT --until done|failed|any-change --timeout 3600 --json
alfrd plan events [PLAN] -C PROJECT [--since CURSOR] [--follow]        # JSON Lines
alfrd plan log    [PLAN] -C PROJECT --target T [--step S] [-n 50] --json
```

`PLAN` defaults to the latest plan. Exit codes (with and without `--json`):

| Code | Meaning |
|---|---|
| 0 | finished, no failed cells |
| 1 | finished with failures |
| 2 | still running |
| 3 | paused, interrupted or cancelled |
| 4 | plan or project not found |
| 5 | runner dead, status is stale |

## HTTP (served by `alfrd serve`)

```
GET /api/v1/projects
GET /api/v1/projects/<p>/plans
GET /api/v1/projects/<p>/plans/<id|latest>?detail=&since=&wait=&limit=&offset=
GET /api/v1/projects/<p>/plans/<id|latest>/events?since=&timeout=    (Server-Sent Events; id: = cursor)
GET /api/v1/projects/<p>/plans/<id|latest>/log?target=&step=&unit=&lines=
```

`<p>` is the project identifier or a unique project name. `wait=N` (≤ 60 s)
long-polls: it returns as soon as the plan changes after `since`.

`…/events?since=<seq>` with digits only (e.g. `since=0`) is not the SSE stream:
it returns the plan's structured events (`events.jsonl`) with a larger `seq`,
`{"schema": "alfrd.plan_events/1", "plan", "events": […], "seq"}`; pass `seq`
back as the next `since` (`&limit=` caps the count). Kinds and fields:
[notifications.md](notifications.md).

Access:

- **Loopback** (scripts on the same machine): send the server's access token,
  `Authorization: Bearer <token>`. `alfrd url --port <port>` prints it in the
  link (`…?token=<token>`), and it is the `token` field of
  `<config dir>/alfrd/server-<port>.json` (mode 0600; `~/.config/alfrd` on
  Linux). A pinned token (`alfrd serve --token …` / `ALFRD_TOKEN`) stays the
  same across restarts. A Bearer equal to `ALFRD_API_TOKEN` also works here.
- **Other hosts**: set `ALFRD_API_TOKEN` on the server and send
  `Authorization: Bearer <ALFRD_API_TOKEN>`. This token is valid for
  `/api/v1/*` only, not for the Studio or its data routes. Without
  `ALFRD_API_TOKEN` the status API answers 403 to other hosts.

Without a token the API answers 401 with `WWW-Authenticate: Bearer`; more than
10 wrong tokens per minute from one address get 429 with `Retry-After`.

```bash
T=$(python -c 'import json,platformdirs,pathlib;print(json.loads((pathlib.Path(platformdirs.user_config_dir("alfrd"))/"server-5000.json").read_text())["token"])')
curl -H "Authorization: Bearer $T" http://127.0.0.1:5000/api/v1/projects
```

## The document

| Field | |
|---|---|
| `plan` | id, csv, mode, status (`running paused finished failed cancelled interrupted`), control, concurrency, steps, times |
| `runner` | alive, host, pid, heartbeat_age_s, stale, owned_by_this_host |
| `counts` | todo, queued (next to start), running, done, failed, skipped (blocked by a failure), cancelled, interrupted |
| `progress` | cells_done, cells_total, eta_s (median step durations; null without history) |
| `running[]` | row, target, project_code, workdir, step, started_at, elapsed_s, median_s, pid, log, entity |
| `failures[]` | row, target, step, reason, exit_code, finished_at, log, entity |
| `waiting[]` | rows held back by `execution.serialize_on`, the work dir lock, or a step delay (`kind: delay`, `step`, `until`): row, reason, blocked_by |
| `rows[]` | `--detail rows|full`: the targets × steps grid (`full` adds each cell's command, duration, log, usage) |
| `summary` | one generated sentence, e.g. *Plan 20260929-0915 is running, running rpicard on J0742+103 (BV019/wd), 22/42 cells done, 1 failed (J1041+061 fits_to_ms: boom), ETA ~5 h.* |
| `cursor` | pass back as `since`: `changed: false` when nothing changed, else the document plus `events[]` since then |
| `exit_code` | the CLI exit code above |

`entity` is an entity path (`project`, `target`, `project_code`, `workdir`,
`step`, `file`, `line`; see `alfrd/entities.py`).

Log text is bounded: at most 200 lines / 64 KiB, ANSI codes stripped, and only
logs of the plan's own commands are read.

Pause / resume / cancel are not part of this API (use `alfrd plan pause|resume|cancel`).
