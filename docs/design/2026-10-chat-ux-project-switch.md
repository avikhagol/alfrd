# Chat UX addendum: project switcher and active sessions

Date: 2026-10-27. Task: `pswitch.design`. Investigated branch: `v0.4.0.0-chatux`.
Status: proposed implementation contract; this task changes documentation only.
Companion: [Chat UX design](2026-10-chat-ux.md).

## Decision

Use the existing header Project picker for chat as well as workflows. When Chat is enabled in server mode, each project option gains `N active chats`, with `M need you` when applicable. Selecting a project while on Chat keeps the user on Chat, shows only that project's sessions, and highlights active sessions. Switching never stops work in the outgoing project.

Ownership already exists through project-local storage and project-scoped URLs. Add a compact, opt-in activity projection to the existing sessions endpoint for cross-project counts; retain the default sessions response for the selected project's list. Reuse the existing tab-wide live subscription. Add no new recurring browser polling loops. Move the current shell inbox controller behind a lazy import to fund the small shell integration with zero net startup growth.

## Existing implementation and precedent

All paths below are relative to the repository root.

| Concern | Existing files and functions |
| --- | --- |
| Header project selection | `src/alfrd/web/js/app.js`: `renderShell()` mounts `mountPicker()` on `#pick-project`; `renderHeader()` supplies `ctx.projects()`, `projectLabels()`, project roots, unsaved and loading badges; `switchProject()` changes `state.selectedProject`, promotes project loading, saves/restores workspace state and updates history. `activeProject()` maps `all` to null; `ctx.scopedTargets()` filters targets by project. |
| Picker interaction | `src/alfrd/web/js/components/picker.js`: `mountPicker()`, `filterItems()`, `moveIndex()`, `typeahead()`. Items already accept `badge` and `detail`. `set()` updates an open menu while retaining its query, active item and scroll. Keyboard selection, focus return and accessible badge descriptions already exist. |
| Project catalog | `src/alfrd/web/js/data/server.js`: `listProjects({scoped:true})` loads `GET /api/projects`, respects `server.session.projects`, and normalizes `identifier` into the frontend project key. `app.js` preserves names separately from identifiers. Project loading is progressive, not a prerequisite for choosing a project. |
| Workflow runs and Jobs | `src/alfrd/web/js/components/plans.js`: `loadPlan(ctx, project)`, project-keyed cache, `planOf()`, `activePlan()`, `activeJobs()`, `schedulePoll()`. `server.js`: `planStatus(project,id)` uses `GET /api/studio/projects/<project>/plans[?id=...]`. `src/alfrd/gui/studio_plans.py`: `plans_list()` resolves the project root and returns scheduler state. `activeJobs()` selects cached plans in `running`, `paused`, or `interrupted`. |
| Jobs navigation | `app.js`: `jobLabel()` and `renderJobs()` paint the global Jobs count; `src/alfrd/web/js/components/jobs_tray.js`: `jobRows()` retains each row's project identity, `openJobs()` offers Open project through `ctx.openRun(project)` and project-qualified View log. Existing plan polling runs independently per project every three seconds while busy; changing selection does not stop it. |
| Shared live updates | `src/alfrd/web/js/data/live.js`: `createLive()` uses one `/api/studio/events?projects=...` SSE connection, with `/api/studio/changes` as its existing fallback. `app.js`: `projectsFollowed()` includes the catalog, `onAlfrd` dispatches `alfrd-events`, `onResync` refreshes stale projects. `src/alfrd/studio_live.py`: `LiveHub`, `AlfrdWatcher.probe()` tail workflow and chat plans, retaining completed tails for final events and sharing version/cursor history across transports. `src/alfrd/gui/studio.py`: `live_events()`, `_live_projects()`, `_poke()`. |

There is no separate Jobs-only project picker and no existing active-job badge on every project option: the precedent is the shared Project picker plus the global Jobs tray and project-keyed run cache. Mirror that identity, navigation and background-work behavior; do not derive chat counts from `activeJobs()`, which tracks scheduler plans rather than conversational attention.

### Chat storage and lists today

`src/alfrd/chat.py` defines `Session(root,id)`. `Session.dir` is `<root>/.alfrd/chat/<sid>/`, containing `session.json`, `messages.jsonl`, requests, tasks and decisions. `create_session()` also creates `.alfrd/plans/chat-<sid>/plan.json`, whose `root` is the resolved project root and whose `chat.session` identifies the session. `session.json` stores id, title, created and plan; it does **not** store a project identifier. The project is implicit in the containing root. There is no global session table or ownership migration to perform.

`chat.list_sessions(root)` currently returns reverse folder/id order (creation order, not last-message order), with last message, message count, `active_requests`, `pending_user`, and `plan_status`. `REQUEST_ACTIVE` is `planning`, `running`, `needs_decision`, `summarizing`. Pending mail excludes informational/team-only/resolved items; legacy pending user decisions count too, except decisions linked to mailbox messages, which would double-count the same question.

`src/alfrd/gui/studio_chat.py`: `chat_sessions()` serves `GET /api/studio/projects/<project>/chat/sessions`; detail and mutations are scoped beneath the same project URL. `_project_root()` resolves the runtime project selector to its root. A session id alone is never a global identity: use `(project identifier, session id)` in caches, routes and drafts.

`src/alfrd/web/js/components/chat_view.js`: `render()`, `mount()`, `loadSessions()`, `openSession()`, `drawSessions()` already use `ctx.activeProject()` and remember session selection in `ui:chat.session[project]`. Full-page search and Needs me use `src/alfrd/web/js/data/chat_sessions.js:filterSessions()` and `src/alfrd/web/js/data/inbox.js:needsMe()` over the list snapshot only. Search matches title, id and last-message preview. `src/alfrd/web/js/data/chat.js` supplies request-state/progress helpers; it must remain lazy.

The compact dock (`src/alfrd/web/js/components/chat_dock.js`) currently has a native session select and an active-request dot; **search and Needs me are in the full Chat sessions sidebar**, not the dock. Both surfaces share per-project remembered session identity and composer drafts. `app.js` currently statically imports `createInbox()` from `data/inbox.js`; it polls only the selected project's sessions every ten seconds, publishes `alfrd-inbox`, and kicks on events. Page and dock also refresh on `alfrd-events`. `data/chat_live.js:subscribeActivity()` and `subscribeMailbox()` already deduplicate detail subscriptions by project/session/request; they are for the viewed conversation, not cross-project counting.

## Exact meaning of active

For a session summary `s`:

```text
active(s) = s.active_requests > 0 OR s.pending_user > 0
project.active_sessions = number of distinct sessions satisfying active(s)
project.needs_user_sessions = number of distinct sessions with pending_user > 0
```

Count sessions once, even when they have several requests and questions. `pending_user` remains a count of individual questions/permissions in existing Inbox UI; it must not be relabeled as a session count.

* Planning, running and summarizing count even without a live OS process at that instant. This is a durable request-state definition, not process liveness.
* A request awaiting a decision (`needs_decision`) counts. Blocked tasks whose request is still active, or whose unresolved question/permission needs the user, therefore count.
* A historical dependency-blocked task in a finished/failed/stopped/cancelled request with no pending user input does not count. `blocked` is a terminal task status in the backend, not by itself evidence of an ongoing conversation.
* An unresolved user question counts even if its owning request is no longer active. Resolution/expiry/cancellation removes that contribution on the next authoritative refresh.
* Recent messages, an open tab, selection, drafts, and `plan_status` alone do not count. No recency window or expiry timer is introduced. Recovery remains responsible for correcting stale request statuses; the picker must not silently turn an apparently stuck request into idle.
* Missing/malformed fields from an older server mean unknown activity, not a verified zero. Preserve the existing conservative Needs me behavior for unknown `pending_user`.

Use this predicate consistently in Python compact summaries and the lightweight list helper; do not import the large `data/chat.js` module into startup to reuse constants.

## API and data flow

Extend only the existing `chat_sessions()` GET with `?view=activity`:

```json
{"activity":{"active_sessions":2,"needs_user_sessions":1}}
```

No default-response changes, project-filter parameter, new global route, session project field, schema migration, or startup catalog/session payload additions. The URL supplies the canonical project key. The controller associates the response with that captured key, never a display name or whichever project happens to be selected at completion.

In `chat.py`, extract a shared per-session request/pending-user summary helper used by `list_sessions()` and a new `session_activity(root)` aggregate. The compact path reads session metadata, request statuses and mailbox/decision summaries; it must not call `list_sessions()` and then discard messages, nor read transcripts, graph, logs, attachments, usage or all message bodies. Empty/missing chat storage yields explicit zero counts. Errors must produce the usual error response, not partial counts silently reported as complete. Preserve linked-decision deduplication. This remains a read projection with the same mailbox expiry semantics as the existing list.

New lazy `data/chat_projects.js` owns a tab-wide, project-keyed activity cache and a shared selected-project sessions snapshot. Load it once when Chat is enabled in server mode, including when the dock is closed and another section is visible. Initialize counts from the scoped catalog with at most four concurrent compact GETs; use the existing full sessions GET only for a concrete selected project. Publish existing `alfrd-inbox` snapshots and expose subscriptions consumed by page/dock. Derive selected-project counts from a fresh full snapshot rather than immediately making a duplicate compact GET. Deduplicate overlapping list loads, collapse invalidations while one is in flight, and guard all responses by project, connection/catalog generation and invalidation generation. An event during a GET requires one trailing refresh rather than treating that GET as definitely current.

Remove the existing shell inbox recurring timer as part of moving its controller to the lazy module. Keep `pendingCount`, `needsMe`, `countText` exports compatible for consumers/tests. Page/dock use the shared list subscription instead of independent list fetches; their selected-session detail/activity/mailbox mechanisms continue unchanged. No count fetch may open a detail subscription for a background session.

## Switcher and selection behavior

1. Append activity text to existing item badges: `unsaved · Loading… · 2 active chats · 1 need you`. Preserve root detail, duplicate-name disambiguation, catalog order and all projects, including idle ones. For confirmed zero use `0 active chats`; unknown uses `Chat activity loading…` or `Chat activity unavailable`, never zero. The All projects option may show the sum only when every included project is known; otherwise say `Chat activity incomplete`. Accessible option descriptions include the full count text.
2. While the current route is Chat, project selection calls `switchProject(project,{restoreView:false,push:true})`. Build the destination Chat route without the outgoing sid. Preserve normal workspace view restoration for selection outside Chat. No second picker is added inside the sessions sidebar.
3. On arrival choose the destination's valid remembered session; otherwise the first active session in existing server order, otherwise the first session, otherwise the empty state. A valid explicit project-qualified deep link takes precedence. Do not auto-select a different session because counts change, search changes, or a new request starts. Active sessions gain a textual `Active`/`Needs you` badge and subtle styling; the current-session marker remains distinct. Preserve list order and focused rows during live updates.
4. Search and Needs me apply after project scoping. Keep queries per project; keep Needs me as a user-chosen filter. Do not automatically turn it on. If the remembered/current session is hidden by a local filter, retain its transcript and explain that it is outside the current filter, with Clear filters. Counts in the project picker always cover the entire project, not search results.
5. A switch from A to B saves A's draft/selection and immediately clears A's rendered transcript, task inspector and session-specific actions. Show B's loading state until its own data arrives; never send a message to A while the header says B. The dock follows B in the same way. A's jobs and requests keep running. Returning to A restores its remembered session and draft. Use tuple-keyed drafts already supplied by `composer_draft.js`.
6. Do not reinterpret `#/chat/<A-sid>?project=B` as a session in A or silently search other projects: show B's ordinary missing-session state, with Refresh sessions/New session. Sidebar links always include their owner project. Explicit project switching strips stale sid/request/task selection before choosing B's remembered session, even if A and B contain identical session ids.
7. All projects keeps the existing Chat project-required state and offers the shared picker; it does not merge conversations or choose a project automatically. Counts remain visible in the picker. No composer is enabled without a concrete project. A removed project evicts its cache; normal shell fallback to All projects applies.

## Loading, empty, error and live states

* Counts loading must not block project selection, workflow startup or a valid cached conversation. Project B never displays A's count as its own. Load failures affect only their project; retain the last count marked stale, or show unknown if there was no successful snapshot. A lazy-module failure leaves the project picker usable and offers Retry in Chat.
* No sessions: “No conversations in <project>” with New session and Setup. Existing sessions but zero active: keep history visible and show “No active chats in <project>”. A search/Needs me miss says “No matching sessions” with Clear filters, not New project. The destination list has `aria-busy` and a loading status; errors have Retry and cannot masquerade as empty lists.
* Retry, opening the picker, returning to visibility, project/catalog change, successful local mutation, and shared live resync may request fresh counts. These are event-driven one-shot loads, deduplicated by the shared controller. With live disabled/disconnected show stale activity and manual Retry; do not introduce a compensating timer.
* Follow `alfrd-events` for **all scoped projects**, not only the selected project, and invalidate compact counts for `chat-*` plans. Refresh the selected list through the same shared controller. Debounce bursts with one-shot scheduling; ignore unrelated workflow/log events. Keep the existing single SSE connection and its fallback. Existing `subscribeActivity`/`subscribeMailbox` continue serving visible detail surfaces only.
* Close the idle-session invalidation gap in `studio_live.py:AlfrdWatcher.probe()`: maintain a lightweight fingerprint of session directory membership and `session.json` stat values. After the initial baseline, publish synthetic `alfrd` records (`kind: chat.session.changed`, `plan: chat-<sid>`, project key) for creation, rename and removal, including sessions with no active plan tail. Run this in the existing watcher, using its existing forward/version/history path, not a new watcher thread or browser loop. `_poke()` accelerates mutation visibility. Initial baselines are covered by initial GETs. This catches CLI/other-tab changes as well as local mutations and survives deletion of the session's plan directory.
* `app.js`'s existing `onResync` hook must also invalidate lazy chat summaries for that project, covering hello/version gaps and SSE/poll reset. Retain its normal tree-refresh action. On visible reconnection, refresh counts even when no event was replayed. The server currently caps `_live_projects()` at 20: projects beyond the actual watched set must be marked as snapshot-only/stale between explicit refreshes; do not promise live counts for unwatched projects or open extra streams to evade that cap.

## Exact implementation files

These are future implementation edits, **not edits made by this task**.

| File | Planned change |
| --- | --- |
| `src/alfrd/chat.py` | Extract shared attention summary; implement compact aggregate with the predicate above. |
| `src/alfrd/gui/studio_chat.py` | `chat_sessions()` dispatches opt-in `view=activity`; preserve default behavior and errors. |
| `src/alfrd/studio_live.py` | Extend existing `AlfrdWatcher` session-metadata invalidation through existing cursor history. |
| `src/alfrd/web/js/data/chat_projects.js` (new, lazy) | Shared counts/list cache, bounded initial loads, events, lifecycle, stale/error/retry and generation guards; shell inbox controller. |
| `src/alfrd/web/js/data/inbox.js` | Remove/migrate `createInbox` polling controller; retain pure summary/filter exports. |
| `src/alfrd/web/js/app.js` | Remove static inbox import/controller/painting, replace with cached dynamic initialization and lazy controller calls; pass picker instance and project catalog through an explicit adapter; keep Chat on project switch; forward resync. |
| `src/alfrd/web/js/components/picker.js` | Add a small option-decoration hook to the existing `set()` path so lazy activity badges compose with normal shell badges and open-menu updates. Avoid DOM scraping, MutationObserver redecoration or replacing picker handlers. |
| `src/alfrd/web/js/data/chat_sessions.js` | Pure active predicate and destination-session choice helpers. |
| `src/alfrd/web/js/components/chat_view.js` | Shared list subscription, active-row highlighting, destination selection and filter/empty states. |
| `src/alfrd/web/js/components/chat_dock.js` | Consume same list snapshots, consistent active/attention labels and cross-project reset. No new dock search feature required. |
| `src/alfrd/web/css/chat-dock.css` | Existing sessions-sidebar active/attention row styling; preserve selected and focus states. |

The lazy controller owns header inbox DOM painting after initialization and supplies the picker decorator; `renderHeader()` must not rebuild/erase those decorations. Dispose controller listeners and clear counts when Chat becomes disabled or the server identity changes. Initialization must work on All projects before the Chat page/dock has ever opened. Do not change `index.html`, startup CSS, endpoint catalog responses or static-import chat modules to accomplish this.

## Payload budget and verification

Measured during this investigation using `.venv/bin/python -B` and `_startup_files()`, `_web_files()`, `_gz()` from `tests/test_studio.py` (per-file `gzip.compress(...,9)`):

| Asset class | Observed baseline | Estimated implementation delta | Gate |
| --- | ---: | ---: | ---: |
| Startup closure | 200,719 B | target -500 to 0 B net | < 204,800 B, and no net increase for this feature |
| Lazy aggregate | 363,283 B | +3,072 to +5,120 B | < 409,600 B |
| Startup JSON/API bodies | unchanged | 0 B | no new eager catalog/session fields or requests when Chat is disabled |
| On-demand activity JSON | new compact projection | roughly 60–100 B/project before HTTP headers | one snapshot per project, no messages; bounded concurrency |

These are design estimates, not measurements of unimplemented code. Moving shell inbox code out of `app.js` and removing its static import makes `data/inbox.js` lazy (currently 760 B gzip), providing room for the dynamic-init stub, picker hook and resync adapter. Preserve zero net startup growth by measuring the combined change; merely using `import()` is not proof. If glue exceeds the reclaimed budget, move more of the existing Chat-specific shell painting into the same lazy controller. Do not relax caps or change their accounting. Enabling Chat may fetch lazy assets/counts during initial rendering; keep these off the default startup critical path, and report that enabled-mode traffic separately.

Concurrent work already changes chat source/tests and the parent design. The baseline is a point-in-time observation; remeasure after merge. No new packages, fonts or icon assets are needed.

## Tests to add or extend

* `tests/test_inbox_m6.py`: shared predicate/compact counts for each active request status, pending question/permission, expiry/resolution/cancellation, linked legacy decision deduplication, terminal historical blocked tasks, recent idle sessions and multiple requests counted once. Assert compact aggregation never reads message bodies or detail graphs/logs.
* `tests/test_chat_session_manage.py`: default list API stays compatible; compact GET zeros and errors; two project roots containing identical sids remain isolated; no owner field/migration needed.
* `tests/test_studio_live.py`: existing watcher emits versioned/replayable chat invalidation for idle creation/rename/deletion and CLI changes, no initial-baseline event storm, finished request transitions and cursor-reset resync. No new watcher or subscription per session.
* `tests/studio_js/chat_project_switch.test.mjs` (new): predicate and destination choice, partial/unknown/stale counts, bounded fanout, deduplicated requests and events, event-during-fetch trailing refresh, server/catalog/project generation races, removal, disabled mode, hidden/visible resync. Fake timers assert no new recurring count/list polling and no per-session detail subscriptions.
* `tests/studio_js/header_pickers.test.mjs`: compose activity with unsaved/load badges; preserve filtered menu, keyboard active option, focus and root descriptions across live `set()` updates; verify badge accessible text.
* `tests/studio_js/chat_ux_dock.test.mjs`: selected-project search + Needs me composition, query persistence, hidden-current explanation and empty/error distinctions. Despite the filename, inspect and cover both page and dock behavior rather than assuming the dock owns search today.
* `tests/studio_js/chat_sessions.browser.test.mjs`: switch A→B→A from Chat and with dock open; background work persists; remembered choices/drafts restored; stale response cannot paint/send against the wrong project; no remembered session selects active-first; explicit deep links, same sid in two roots, deleted current session, All projects, Back/Forward and reload. Verify Chat route is retained despite destination workspace previously showing Workflow, and selected session is not replaced by live activity changes.
* `tests/studio_js/inbox_m6.test.mjs`: update shell-controller expectations for lazy/event-driven operation while preserving pure counts and existing `alfrd-inbox` contract. Keep question counts distinct from active-session counts.
* Run existing `tests/test_studio.py` payload/static-import checks unchanged, existing `tests/studio_js/live.test.mjs` transport tests, and `tests/studio_js/progressive_load.browser.test.mjs` to guard project startup. Assert the new lazy controller and helpers never enter the startup static closure. Measure request traffic with Chat disabled and enabled; counts must not download background transcripts.

## Documentation-task verification

This task writes only this addendum. Existing source/test edits on the branch belong to concurrent work and are left untouched. Implementation and behavioral tests above are specified for the follow-on task, not claimed as executed here. The payload baseline was measured read-only; source/test hashes and working-tree status were checked before delivery.
