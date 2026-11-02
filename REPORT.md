# What I learned about Alfrd

Study of the working tree on `feature/harness-mode`, created from the current HEAD (`b6e2ffc`, branch `v0.4.0.0-chatux`; not a tag) on 2026-11-02. File:line references below are to that working tree, including pre-existing edits. This is research and a proposed design, not an implemented harness.

## CLI structure and where harness fits

`pyproject.toml:51` registers `alfrd = alfrd.cli:main`; `src/alfrd/cli.py:19` creates the Typer root `alfrd_cli`, and `cli.py:2157` is `main()`. Commands use decorators; grouped commands use `add_typer` (skills at `cli.py:25`, superuser at `cli.py:1720`, chat at `cli.py:1840`). `serve` (`cli.py:583`) and `gui` (`cli.py:634`) share `_serve_web` (`cli.py:324`): runtime catalog/SQLite, Flask app, authentication, project registration/scope, Waitress, browser launch, chat recovery/watch/scheduling. The separate `studio` command (`cli.py:701`) is a static server and is not the right harness host. Add one root `@alfrd_cli.command()` named `harness`, delegating to the existing web launcher with a harness start route/config, or attaching to an existing authenticated server.

## Superuser page, Chat mode and exact toggle checks

`src/alfrd/gui/studio_superuser.py:344` registers `/superuser`, redirecting to `/studio/#/superuser` while retaining the query. `src/alfrd/web/js/components/superuser.js:10` calls `/api/studio/superuser`; its settings UI renders `features.experimental` at **`superuser.js:103`**. The backend endpoints are in `gui/studio_superuser.py:61`; reads require local same-origin CSRF and writes retain mutation protection. `src/alfrd/superuser.py:51` defaults experimental features off; `superuser.py:90` resolves the per-user `superuser.yaml` (overridable via `ALFRD_SUPERUSER_FILE`), reloaded for subsequent requests.

There are two controls, not a single existing “chat mode” server flag. The exact server toggle check is **`src/alfrd/gui/security.py:28`**: `STUDIO_EXPERIMENTAL` OR `superuser.load()["features"]["experimental"]`. `gui/studio.py:107` exposes it as session `experimental`. The exact frontend Chat visibility check is **`src/alfrd/web/js/app.js:1454`**: `experimentalOn() && shown("chat") && readPrefs()["ui.experimental_chat"] === true`. Thus server experimental capability alone does not show Chat. The browser checkbox lives in `components/settings_dialog.js:338`, saves through `settings_dialog.js:369` / `app.js:1490`; its default is false (`src/alfrd/ui_flags.py:68`). `app.js:1469` also respects hidden sections. The proposed harness override must reach this final visibility check, without modifying saved user preferences or bypassing authentication, lockdown, CSRF, or permissions.

## Where alfrd.yaml chat options are read

**`src/alfrd/chat.py:204–218`**, `settings(root)`, reads `merged_manifest(root).get("chat")`, validates/merges it with `DEFAULTS` (`chat.py:81`, `merge_settings` at `chat.py:221`), then applies machine caps via `superuser.cap_chat_settings`. `src/alfrd/execution.py:243` resolves/merges the active manifest and template using the existing manifest helpers. `src/alfrd/config.py:39` is a generic consumer Config primitive, not the effective chat-settings loader. `gui/studio_chat.py:52` exposes effective chat settings; `chat.py:3612` supplies the editable settings view. Settings writes use manifest history and a base-hash conflict guard (`gui/studio_chat.py:63`).

## Frontend ownership and reuse points

All paths in this table are under `src/alfrd/web/js/`.

| Surface | Concrete owner |
| --- | --- |
| Chat session list | `components/chat_view.js:409` (`drawSessions`, links/rename/delete); session data from `data/chat_sessions.js`, project chat summaries from `data/chat_projects.js`. `components/chat_lists.js` handles Markdown list editing/rendering, not the session list. |
| Full chat panel | `components/chat_view.js:93` (`createChatView`), mount at `:229`; shared conversation `components/transcript.js` mounted at `chat_view.js:268`, plus `chat_message.js`, `chat_composer.js`, `chat_activity.js`, `chat_runs.js`. Compact assistant dock is `components/chat_dock.js`. |
| Project dropdown | `app.js:1933` button, `app.js:2011` mounts `components/picker.js:37` (`mountPicker`); project switching stays in app context. |
| Task graph, one request | `chat_view.js:395–405` supplies `data/chat.js:98` (`graphModel`), filtered by request, to `components/graph_view.js:21` (`mountGraph`). Uses existing `canvas.js` geometry/CSS. |
| Task graph, All requests | Same graph with null request; `components/inspector.js:110` scope selector, `createRequestScope`; `graph_groups.js` groups requests. Fullscreen graph updated at `chat_view.js:384`. |
| Request stats and All requests totals | `chat_view.js:612` (`drawProgress`), All requests branch `:621`, using `requestProgress` / `requestTotals` and `tokenLine` in `data/chat.js`; `usage_counts.js` and `usage_view.js` provide related detailed usage UI. |
| Blockers and decisions | `chat_view.js:649` (`drawDecisions`), panel heading at `:659`, using `state.blockers` / `state.decisions`, existing decision/mailbox APIs. |
| Log bar / expanded log stream | `app.js:2115` (`renderStrip`), `app.js:2167` (`renderConsole`); dock/open/follow is `components/logview.js`. `components/logs.js` is the dedicated Logs page. `chat_view.js:602` chat log is the conversation, a different surface. |

Most chat subpanels are private functions inside the view factory, not independent mountable exports. Reuse requires small extraction/injection seams; a new page cannot simply import `drawProgress` today.

## What exists on disk per run

`src/alfrd/chat.py:30–41` documents the session record layout, and `runtime/scheduler.py:145` (`PlanDir`) owns plan storage. A chat session is one plan shared by multiple requests, not one plan per request:

- `.alfrd/chat/<session>/session.json`, `messages.jsonl`, `requests/rNNNN.json`, `tasks/<row>/task.json` and latest `prompt.md`, `decisions/<id>.json`, `graph.json`, `<session>.tasks.csv`, `check.json`. Task records retain units, result/artifact refs, dependencies and owners; CSV cells drive scheduling.
- `.alfrd/plans/chat-<session>/plan.json` holds runner identity/heartbeat, bounds and status/history; `control.json`, `runner.lock`, `runner.log`, and `units/<unit>.json` hold control/process state and each attempt. Unit identity distinguishes retries of the same row.
- `handoffs/<unit>/prompt.md` and `response.md` preserve attempt transcripts. `chat.py:3185` / `:3201` expose transcript metadata and bounded parts; API routes are `gui/studio_chat.py:486` / `:495`. These chat response files are not all formal published loop handoffs.
- `logs/<unit>.log`, `.exit`, `.child`, `.agent.json` where applicable, resource `.usage.jsonl`, provider `.events.jsonl` where supported, and `.events.telemetry.json` sidecars. Resource samples are separate from LLM token usage. Unit `agent_usage`, `total_cost_usd`, `usage_source`, model/adapter and timing capture token/cost totals (`chat.py:1677`, `agent_io.py:156`). `telemetry.py:304` / `:330` incrementally cache provider counts; `model_prices.py` supplies pricing support in this working tree. Unknown costs/tokens must remain unknown, not zero.
- Plan-level `events.jsonl` is orchestration history, distinct from provider event files in `logs/`. Mailbox request records/context files and notification cursors also live under the plan. Actual racing-game plan children include `notify.attention.json` and `notify.cursor`.

## Live updates: indicator, job polling, log follow

`src/alfrd/studio_live.py:410` owns `LiveHub`; plan events are watched at `studio_live.py:337`. `gui/studio.py:535` serves `/api/studio/events` SSE and `:572` serves `/api/studio/changes` polling fallback. `web/js/data/live.js:33` creates the client, `:107` opens EventSource. It carries tree/log/runtime and Alfrd events, epoch/version resync and visibility/reconnect handling; app hooks at `app.js:1714–1730` refresh data or dispatch `alfrd-events` / `alfrd-chat-resync`. The global Live label is rendered by `app.js:1821–1845`; the chat header indicator is `chat_view.js:501`. “Live” indicates transport freshness, not agent progress.

Plan/job status is independently polled every 3 seconds while active (`components/plans.js:211–223`), skipped while hidden, and surfaced through `jobs_tray.js` / `app.js:2143`. Chat snapshots react to events and resync, with 15-second fallback checking / 60-second refresh in `chat_view.js:194`; `data/chat_live.js:36` polls busy activity at 2 seconds and mailbox at 2/10 seconds (`:119`). `components/logview.js:25–26`, `:300–345`, `:454` follows open/docked logs by byte offset with reset detection and 1–5 second backoff. It is nudged by live log growth. Keep all these distinctions in a harness; a stale transport is not a stuck task.

## Are new events.jsonl helpers needed?

**No new event-log implementation is needed.** `src/alfrd/events.py:15` already defines task/turn start, finish/failure, handoff, blocker, decision, limit and stuck kinds. `EventLog` (`:79`) handles seq, append/flush, malformed/torn lines and one rotated generation; `api/status.py:232` and `gui/api_v1.py:139` already expose structured events and SSE/cursor reads. Existing envelope: `{seq, at, kind, plan, project, target, unit, turn, turns, agent, roles, text, data}`; request/task identity is usually in **data**, not at the top level. `scheduler.py:1316` constructs it, `chat.py:1352` adds session, `:1404` emits task.started and `:1688` emits failed task.finished with error. Scheduler emits turn failures (`:2376`) and formal loop handoff.published (`:2264`). Chat graph dependency/result refs supply ordinary task-to-task handoff relationships even when no handoff.published exists. `request.finished` includes usage (`chat.py:2670`); task result payloads and unit snapshots also carry usage, but there is no universal timestamped plan-level usage sample event.

Add a read-only harness projection using these records. For reliable future replay, extend the existing event payloads with creation-time topology and finalized per-attempt usage, and optionally emit sampled usage observations through the same EventLog. Do not duplicate the runner or write a second events.jsonl. Existing rotation retains only current + `.1`; old replay can be incomplete and must advertise coverage. Final mutable snapshots cannot establish past costs, reassignment history or exact log activity. EventLog's seq allocation is read-then-append, not a cross-process transaction: serialize new writers through existing plan/session locking; do not have read endpoints append.

## Finished racing-game request r0007

**Plan folder:** `/mnt/6438D98627D1388F/Intelligence/agents/alfrd_brainstorm/.alfrd/plans/chat-20261031-143409-ec848d`

**Request record:** `/mnt/6438D98627D1388F/Intelligence/agents/alfrd_brainstorm/.alfrd/chat/20261031-143409-ec848d/requests/r0007.json`.

Verified `status=done`, created `2026-11-01T02:45:30`, finished `2026-11-01T12:29:09`; text requests high-end graphics/music and Crete/Lucknow/Mumbai environments. It has 18 specialist task rows (plus orchestration/replanning attempts). There are 103 request-tagged events in the retained plan log; request.finished seq 331 records 34 turns and $15.160262970000003. The checked log has no handoff.published kinds: do not require that event to draw its dependency handoffs. Other chats also contain r0007, so always identify it by project + session + request.

## Implementation design

The full file-by-file design, exact API JSON contract, config defaults, replay limits, stuck rules and component mounting plan are in `/mnt/6438D98627D1388F/Intelligence/agents/alfrd_brainstorm/harness/design.md`.

Proposed changes: root Typer `harness --project --chat --no-open [--attach URL]`; use the existing Flask/Waitress server and `/studio/#/harness` page, one effective `chat_enabled = harness_mode or superuser_toggle` check preserving the legacy toggle expression, validated `harness:` defaults, and a read-only state projection over existing plan events/units/chat records. Reuse project picker, chat view, graph, request scope/stats/decisions and log dock. Add only plain JS/CSS and Python modules/tests; no framework or build tool. Harness monitoring never runs recovery or a model automatically.

## Working-tree preservation

No staging, commits, stashing or reverts were performed. Preserve all pre-existing edits, especially `docs/agent-loops.md` (SHA-256 `da24dbb6096a39969eaf0957cd600906f61feea2406ee4ae7902e49166c58b26`) and `src/alfrd/agent_io.py` (`78e027ff509366ccf96747b81701d0e79cc719b9ad071cd8cc86c5842fb0d418`). These instructions apply to subsequent agents too.

---

# Harness mode: what was built

Branch `feature/harness-mode`. Commits, oldest first:

| Commit | Change |
|---|---|
| `c0fcf50` | Read-only harness config and replay state API |
| `e433ed4` | `alfrd harness` CLI with safe server attachment and chat override |
| `492d792` | Scoped harness snapshots adapted to the shared Chat graph and history |
| `9cfc3a8` | Harness page mounted with shared Chat and graph, plus replay observer views |
| `709f633` | Retest evidence and the deferred payload defect (`harness/fix-notes.md`) |
| (docs) | README Harness section and this report |

## Existing components reused

- `src/alfrd/events.py` (`EventLog`) and plan `events.jsonl`: no new event writer or helper.
- `src/alfrd/chat.py` (`chatOn` logic in the app, session/request/task records), `runtime/scheduler.py` (`PlanDir`, units).
- `src/alfrd/cli.py` `_serve_web`: the existing Flask/Waitress server, auth, project scoping and browser launcher.
- `src/alfrd/web/js/app.js` (shell, project dropdown, Jobs, Live indicator, log strip).
- `src/alfrd/web/js/components/chat_view.js` (sessions sidebar, conversation, task detail, `drawProgress`, `drawDecisions`), `transcript.js`, `chat_message.js`, `chat_composer.js`, `chat_sessions.js`, `chat_projects.js`.
- `src/alfrd/web/js/components/graph_view.js` and `data/chat.js#graphModel`: the task graph, unchanged.
- `components/panel_resize.js`, `data/server.js`, `utils/dom.js`, `utils/refresh.js`.

## Added or changed, and why

- `src/alfrd/harness.py`: read-only projection of existing task/request snapshots, unit attempts, plan events, usage and provider progress. Read-only so watching can never alter a run.
- `src/alfrd/harness_cli.py` and the `harness` command in `cli.py`: start a monitor-only server or attach to a capable one; incompatible servers are refused instead of replaced.
- State endpoint `GET /api/studio/projects/<project>/chat/sessions/<session>/harness/state` (`alfrd.harness/1`): request/all scope, `at=` replay, feed pagination, `Cache-Control: no-store`, existing auth plus served-project scope.
- `harness:` config keys with forgiving defaults (see README).
- `app.js`: `chat_enabled = harness_mode || superuser_toggle`, so Chat shows inside Harness without touching saved preferences.
- `chat_view.js`: an optional projection interface so the same renderers serve Harness; ordinary Chat unchanged.
- New `components/harness_view.js`, `data/harness.js`, `css/harness.css`: Agents, Timeline, Feed, Morning summary, replay clock and Control Room layout. Plain JS/CSS, no new dependency or build tool.
- Tests: `tests/test_harness.py`, `test_harness_api.py`, `test_harness_cli.py`, `tests/studio_js/harness.test.mjs`, `harness.browser.test.mjs`.

## Decisions made during the night

- Task/brief defaults and forgiving fallback for `harness:` won over the study's stricter proposal.
- Existing EventLog is enough; no producer changes, prompt capture or agent behaviour changes.
- Dependency edges are shown as partial snapshot evidence and kept distinct from formal `handoff.published` events (r0007 has none).
- Unknown usage stays null, never zero; legacy history is declared partial via the API's coverage.
- Stuck detection lives only in the read-only module; heartbeats and repeated identical usage do not reset it.
- Replay shows recorded messages only and keeps composer and decision controls inert.
- Remote `--attach` does not copy local credentials; token-protected remote servers return an access diagnostic.
- The fix pass changed no production code. The payload overage was deferred rather than fixed with broad minification or removing guidance.
- Unrelated dirty files in the checkout, including `docs/agent-loops.md` and `src/alfrd/agent_io.py`, were never staged; their hashes are unchanged.

## What does not work yet

From `harness/verify-report.md` and `harness/fix-notes.md`:

1. **Full suite is not green.** Unrestricted run: 1,904 passed, 1 failed, 4 skipped, 11 xfailed. The one failure is `tests/test_studio.py::test_lazy_payload_under_430kib_compressed`: 446,583 gzip bytes against a 440,320-byte limit (6,263 over). Tracked HEAD assets alone are already 4,371 bytes over, so this is not only the dirty files. Deferred; the feature must not be called fully verified until it is resolved.
2. Inside the sandbox the same suite shows 5 failures and 7 errors (socket permission, uv cache lock). All pass outside it; this is environmental.
3. Running `alfrd harness` on the default port while an old server is there fails with `Running server lacks harness state v1; upgrade/restart it`. This is intended; restart the server or use `--port`.
4. Historical fidelity is partial: no per-agent usage for old runs, unknown runner health and longest stuck interval in replay, event log retains only the current and one rotated generation. r0007 has no formal publications, so handoff highlighting could not be demonstrated on it.
5. Agent columns use the first matching role in `role_order` among the agent's tasks, since the API omits explicit roles. Request labels can be id-only.
6. Remote token-protected `--attach` is unsupported.
7. This project's manifest fails catalog validation (`chat.skill_selection` unrecognized). This predates the work and was bypassed in smoke tests with an isolated catalog.

## Test status

- Harness tests: 21 Python tests pass; 36 targeted JavaScript tests pass.
- Real r0007 over HTTP: 6 agents, 34 timeline segments, 183 feed events, 27 dependency edges, $15.16 and 24,353,087 tokens recorded. Replay at `2026-11-01T03:59:00Z`: 4 agents, 26 segments, 135 events.

## Next steps

1. Cut at least 6,264 gzip bytes from the lazy assets in a separate pass, with browser validation, and re-run the full suite.
2. Emit creation-time topology and finalized per-attempt usage in existing event payloads so future replay is complete; optionally sample usage through the same EventLog.
3. Authenticated remote attach.
4. Phase 2: a 3D view of the agent team (stations, handoffs as moving artifacts) fed by the same state API and replay clock.
5. Controlling agents from Harness (pause, retry, reassign, answer blockers). Harness is observe-only today; this needs explicit mutation endpoints, the existing authorization and CSRF gates, and an audit event for each action.
6. Fix the project manifest `chat.skill_selection` validation issue.
