# Chat UX addendum: compact navigation and resizable panels

Date: 2026-10-27. Task: `cux.design`. Inspected branch `v0.4.0.0-chatux`, HEAD `0d7dcab`, including commit `8283e65`.
Status: implementation contract; documentation only in this task.

This supersedes the activity-count presentation and cross-project chat-count fetching in [the project-switch addendum](2026-10-chat-ux-project-switch.md). Its project/session/draft restoration contract remains. The conversation, permission and composer requirements in [the base design](2026-10-chat-ux.md) and [review goals](2026-10-chat-ux-goals.md) remain applicable.

Input evidence: the supplied JPG was found under the orchestration workspace's `.alfrd/chat/20261026-002711-fb98a3/attachments/`; its SHA-256 matches `b1c8df741f7d8f8a29ab8b6c133c2d1b8ec120d0044823150898de55636bc6ba`. It shows the unwanted per-project activity lines and `All projects (19)` with `Chat activity incomplete`.

## 1. Projects and Jobs

Keep the existing Projects picker, including project names, root disambiguation, selected checkmark, filtering, keyboard navigation and genuine unsaved/loading badges. Remove every chat-activity decoration: zero/nonzero counts, needs-you counts, loading/unavailable/stale activity messages and `Chat activity incomplete`. Keep the useful `All projects (N)` option itself. Do not replace those lines with another per-option activity count.

### Existing components and the counting gap

* `src/alfrd/web/js/app.js`, `renderShell()` creates `#btn-jobs` beside the Project/Target controls; `renderJobs()` paints `Jobs · N running`, optional waiting/other counts and `#jobs-live` announcements. It is a **global** indicator over loaded project caches, independent of selected project. There is no separate jobs-only project dropdown.
* `src/alfrd/web/js/components/jobs_tray.js`, `jobRows()` / `openJobs()` renders that button's lazy Jobs tray. Each row carries its project key and plan ID; Open project uses `ctx.openRun(project)`, and View log uses project-qualified paths. Plugin work is an additional existing row.
* `src/alfrd/web/js/components/plans.js`, `loadPlan()` stores status in `cache: project -> entry`; `activeJobs()` currently returns only `entry.status.plan` in running/paused/interrupted state. Thus one selected plan per project is counted, even when several plans exist. Selecting a historical plan can also hide ongoing jobs from that projection. `schedulePoll()` polls busy projects independently every three seconds.
* `src/alfrd/gui/studio_plans.py:plans_list()` serves `/api/studio/projects/<project>/plans[?id=...]`. `src/alfrd/runtime/scheduler.py:plan_status()` already returns separate `plans` and `chat_plans`. `chat_runs()` includes the newest 20 chat plans **plus every active chat plan**, with title, `chat.session`, status and activity phase. `_listed()` likewise retains active workflow plans beyond its history limit. `list_plans()` excludes chat by default; preserve that separation in workflow selection and `activePlan()`.
* Chat already creates `.alfrd/plans/chat-<sid>/plan.json` in `chat.py:create_session()`, with `run_kind: chat` and `chat.session`. `post_message()` calls `resume()`, which resumes the scheduler plan. Chat task runs therefore already have a job identity; no new execution engine or duplicate job record is needed.

### Required integration

Change the Jobs projection in `plans.js` to union all active workflow summaries, all active `chat_plans`, and any selected full-plan record, deduplicated by `(canonical project identifier, plan id)`. Keep detailed selected-plan state separate from this summary projection. Count one scheduler plan per job, including one `chat-<sid>` job for a busy session with several tasks/requests; never count every task, session with a draft, or pending question as a separate job. Two active sessions in one project must produce two jobs. The same plan ID in two project roots produces two jobs.

Use existing Running/Waiting/Paused/Interrupted presentation. An unpaused running plan with no wait condition contributes to running; scheduled/manual/review/decision waits contribute to waiting; paused/interrupted contribute to other. Terminal plans and idle newly created sessions contribute nothing. A finished session with unresolved historical mail does not become a running job. Keep Needs me available for attention independently of Jobs.

**A small backend summary change is required for correct waiting labels**, rather than guessing from titles or `pending_user`. Extend existing plan summaries in `scheduler.py` with the minimal scheduling/wait fields needed by the common Jobs label helper. `chat_runs()` already supplies `phase`; add an explicit decision-wait phase for a live chat plan whose outstanding work is waiting for a user, reading request/decision/mailbox metadata only. If other work is actually executing in that same plan, Running takes precedence. Preserve paused/interrupted priority. Do not read conversation bodies, graph, artifacts or logs to count jobs. Return the same summary fields in the no-workflow-plan response. No new endpoint is needed. Keep workflow defaults and controls unchanged, and do not include chat plans in workflow grids.

Update `app.js:jobLabel()` to accept normalized summaries as well as the selected full status. `jobs_tray.js` should show a chat title and project and open its project-qualified Chat session; workflow rows retain existing Open project behavior. Summary-only rows must not pretend to have selected-plan units: use the runner log fallback until opened. Keep plugin jobs counted exactly once.

Discovery must work without visiting Chat or selecting each project: reuse the existing catalog/background `loadPlan()` path in `app.js`. Extend `schedulePoll()`'s busy predicate to include active chat summaries, including when `status.plan` is null. Invalidate affected project plans on shared `alfrd-events` (including chat events), with one coalesced trailing refresh for events during an in-flight load. Reuse the tab-wide SSE/fallback and busy-plan polling; add no chat-count timer, stream, or session detail subscription. On visibility return/resync, refresh summaries for permitted catalog projects, with bounded concurrency. Prune removed/reconnected project identities and reject stale responses. Existing errors must remain distinguishable from a verified empty result; do not put an activity-error line back in the picker.

### Remove the obsolete work from 8283e65

In `data/chat_projects.js`, remove picker `decorate()`/`redecorate()` integration, activity aggregation, `?view=activity` requests, background `catalog()`/`refresh()` fan-out, and picker-open count refresh. Retain/refactor its shared **selected-project session-list** loader, subscriptions, invalidation generations, deduplication and selected-project inbox publication used by page/dock. Fetch full list snapshots only for the active concrete project; clear them appropriately on connection/project identity changes. No count fetch is needed for All projects.

Remove `chat.py:session_activity()` and its strict-only `_attention_records()` path, and `studio_chat.py:chat_sessions()`'s `view=activity` branch. Retain `_session_attention()` for ordinary list fields (`active_requests`, `pending_user`) used by Needs me, deletion safety and running dots. Do not strip those fields simply because they are no longer printed. Keep `studio_live.py`'s `chat.session.changed` metadata events: creation, naming and deletion still need invalidation. Remove `picker.js`'s new decoration API if it has no remaining caller; preserve its existing set/focus/query behavior.

Preserve `app.js:switchProject()`'s Chat route behavior from 8283e65: switching A → B while on Chat stays on Chat and drops A's sid from the destination route. Explicit project-qualified session links win, then a valid `ui:chat.session[project]`, then the existing active/first/empty fallback in `data/chat_sessions.js:destinationSession()`. Switching back restores A's session and its draft. Retain project/session-qualified composer storage in `data/chat_draft.js`, `components/composer_draft.js` and `chat_dock.js` remount cleanup. Keep an open dock open on project change, restoring the destination session/draft; switching never stops background runs. All projects keeps the concrete-project chooser instead of constructing a cross-project conversation.

## 2. Remove the header Chat shortcut

Remove both redundant Chat entry controls beside Search from `app.js:renderShell()`: the textual `#chat-inbox` link and `#btn-chat-dock` icon button, their direct listeners and render/update references. Remove the `#chat-inbox` painting path from `mountChatProjects()`; preserve the Workspace Chat link and its useful Needs me badge. The left navigation, command palette and existing Alt+Shift+A shortcut continue to open Chat/dock. Remove dangling `aria-controls`/focus targets; closing the dock should fall back to its actual opener or the Workspace Chat link. Search remains in place.

## 3. Resize the left navigation/sessions and right panels

The shell Workspace rail (`app.js`, `css/studio.css`, `--rail`) and Chat sessions column (`chat_view.js`, `css/chat.css`, `.chat-sessions`) are distinct nested left surfaces today. Make both resizable so “left nav/sessions” works in each view. The right chat dock already has `chat_dock.js:grip()` / `.cd-grip`; improve that implementation rather than adding another handle. Apply the same interaction to the full Chat inspector (`#chat-inspector`, `.chat-side`) while it occupies a docked column.

| Surface | Default / minimum / maximum (CSS px) | Persistence through `utils/dom.js:storage` |
| --- | --- | --- |
| Expanded Workspace rail | 240 / 180 / 360 | Merge `navWidth` into `ui:shell.v2`; preserve `sidebarCollapsed` |
| Chat sessions | 220 / 160 / 360 | `ui:chat.sessionsWidth` |
| Right chat dock | 380 / 300 / 720 | Existing `ui:chat.dock.width`; preserve `open` |
| Docked Chat inspector | 320 / 280 / 560 | `ui:chat.inspectorWidth` |

These become `alfrd-studio:<key>` localStorage entries through the existing wrapper, per browser rather than per project. Reject nonfinite/non-numeric stored widths and clamp valid numbers. Use CSS custom properties for layout, replacing every fixed sessions/inspector grid track at desktop breakpoints; update the shell main/console offsets and dock main margin from the same applied width. Collapse still uses the existing compact rail width and remembers expanded width separately.

Each boundary gets a visible-on-hover/focus 2px rule in a 12px hit area, `cursor:col-resize`, pointer capture and `touch-action:none`. Do not cover session actions or scrollbar hit targets. Mount a small shared `components/panel_resize.js` helper with explicit destroy cleanup. Drag measures width relative to the panel boundary, including layout offsets; right-panel dragging left increases width. Apply changes without rerendering the thread, resetting scroll, losing focus, altering selection or touching drafts. Save on pointerup; pointercancel/lost capture cleans up and restores the pre-drag size without a write. Do not accumulate handlers on remount.

The handle is focusable with `role=separator`, `aria-orientation=vertical`, a surface-specific name, `aria-controls`, and updated `aria-valuemin/max/now` plus `aria-valuetext="220 pixels"`. Left/Right physically move the boundary by 20px (Shift: 80px): right increases a left panel, left increases a right panel. Home sets minimum, End maximum (normalize the dock's currently reversed Home/End convention). Enter resets to default; double-click does the same and saves it. Escape during drag restores the starting width. Show keyboard/reset instructions in its title; provide a strong focus ring and a visible boundary in forced colors. Keyboard changes save immediately; avoid a screen-reader live announcement on every pointer movement.

Clamp effective widths against available space, reserving at least 360px for the center conversation plus actual gaps/padding and other visible columns. A viewport clamp must not overwrite the preferred stored width. When combined minima cannot fit, switch optional right panels to the existing modal sheet behavior and then sessions to its existing sheet; keep Workspace's responsive collapse behavior. Base sheet decisions on usable main width as well as viewport breakpoints, so an expanded rail and open dock cannot crush the conversation. In overlay/mobile/collapsed modes hide and untab handles and ignore desktop width overrides, retaining preferences for the next wide layout. At 360px and 200% zoom use the existing full sheet layout, no horizontal page overflow, and 44px action targets.

## 4. Session rows: name and running dot only

`chat_view.js:drawSessions()` should render one single-line escaped title with `title` containing the full saved name; `min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis`. Remove the two-line clamp in `chat-dock.css`, previews, timestamps, IDs, message counts, attention counts, Active/Needs you labels, spinner badges and the active-chat border. Preserve selected highlighting and `aria-current`.

A currently running session gets one static 6px green dot using the existing theme success token, with a non-color accessible “Running” description. Drive it from `plan_status === running` (the same executing-plan identity as Jobs), not `activeSession()`, which also counts historical pending mail. Paused/interrupted/terminal sessions have no running dot; decision-waiting sessions whose plan remains running keep the dot as ongoing work, with detailed wait state in Jobs/conversation. No flashing animation. Needs me still filters by pending user input.

The resting row contains only the name and optional dot. Move Rename/Delete out of the row's always-present action group into a selected-conversation overflow menu in `drawHead()`, available by keyboard and touch with existing confirmation and active-delete restrictions. The inline rename form may temporarily replace its row during editing. Retain filter/loading/empty/error feedback outside normal rows. Update `data/chat_sessions.js:paintSessionList()` to reconcile dot, tooltip and accessible state for a focused retained row; its current badge-only patching would leave stale content. Search continues using the existing list snapshot, without extra transcript requests. The dock's native session select shows saved names; keep its shared running indicator outside the option text.

## 5. Stable deterministic names

Implement entirely in `chat.py` for HTTP and CLI callers. `create_session()` currently assigns a nonempty `Chat <date>` placeholder even when no explicit title was supplied; distinguish it from an explicit title with persisted `title_source: placeholder|prompt|explicit`. `rename_session()` marks explicit. Preserve explicit titles and the existing 120-character manual-title limit.

On the first successfully accepted user prompt, inside `post_message()`'s existing session lock and after validation/idempotent replay checks, generate and persist the prompt title once. Use the backend's `_MENTION` routing grammar to remove a leading routing mention; normalize other recognized agent mention tokens to a canonical lowercase `@agent` form without rewriting emails or ordinary text. Normalize Unicode to NFC, remove control characters, and collapse all whitespace/newlines to single spaces. Take at most 48 Unicode code points, cutting at the last whitespace boundary within that limit; append an ellipsis only when truncated (reserve one of the 48 characters for it). If the first word alone exceeds the budget, cut that word safely rather than returning empty. Escape at presentation, not by storing HTML entities. No LLM call, title summarization request or browser-specific algorithm.

Example: `@codex   Fix the\n project switcher` saves `Fix the project switcher`. An attachment-only first message saves `Image conversation`; an empty new session displays `New chat` until a prompt arrives. Freeze either generated name, record its source, and never replace it on subsequent messages, retries, reload, project changes, or request completion. Explicit rename always wins, including concurrent send/rename, because both use the session lock. Keep message/client-id replay semantics unchanged; a crash between message append and title write must repair from the first durable user message under that lock, never from a later prompt. Bump session revision and emit the existing session-metadata invalidation when the saved title changes.

For legacy sessions without `title_source`, treat nonempty titles as explicit unless exactly matching the old generated placeholder derived from that session's `created` value. On the next locked session mutation, backfill eligible placeholders from the first durable user message (bounded streaming, stop at the first user message); persist the result and source. Do not mutate storage or scan every transcript during list/Jobs GETs. Until backfilled, show the stored legacy title. A manually assigned title identical to the old placeholder is inherently ambiguous; preserve it if provenance cannot be established rather than replacing a known manual rename.

## 6. Compact heading and actions

In `chat_view.js:mount()`, remove the visible `Chat` h2 above Needs me, retaining the nav's `aria-label="Chat sessions"` and a stable accessible focus target for sheet opening. Keep Needs me and search controls.

Use only the existing `icon()` SVG set from `utils/dom.js`; decorative SVGs are hidden from accessibility APIs. Keep native buttons and current action selectors:

| Action | Icon | `title` and `aria-label` |
| --- | --- | --- |
| `data-chat=new` | `plus` | Start a new chat session |
| `data-chat=inspect` | `sidebar` | Show or hide the inspector; retain shortcut in title |
| `data-chat=check` | `check` | Check for interrupted work and resume it; paused runners stay paused |

Retain Inspect's `aria-expanded`, `aria-controls` and shortcut. Check keeps its request guard, disabled/busy state and `#chat-check-feedback` live region. Update both `drawHead()` and `checkNow()` success/error/finally paths: they currently replace text with Checking…/Check now and must not destroy the icon. While checking use the existing sync icon with accessible Checking label and `aria-busy`; announce results in the feedback region. All three remain at least 32px desktop and 44px touch targets with visible focus.

## File ownership and implementation validation

All paths below are relative to the repository root. This task changes only this design file. The implementation should change:

* `src/alfrd/web/js/app.js`: remove header Chat controls, preserve switching, wire global Jobs updates and expanded rail width.
* `src/alfrd/web/js/components/plans.js`, `jobs_tray.js`: complete project-qualified jobs projection, shared labels, background refresh and chat navigation.
* `src/alfrd/runtime/scheduler.py`, `src/alfrd/gui/studio_plans.py` if route plumbing is needed: minimal wait-summary enrichment, retaining separated workflow/chat lists and project authorization.
* `src/alfrd/web/js/data/chat_projects.js`, `components/picker.js`, `src/alfrd/gui/studio_chat.py`, `src/alfrd/chat.py`: remove unused compact activity contract; retain selected-list attention data; persist naming in backend mutation paths.
* `src/alfrd/web/js/components/chat_view.js`, `chat_dock.js`, `data/chat_sessions.js`: compact rows/actions, title/dot reconciliation, accessible resizers and cleanup. Preserve `data/chat_draft.js` / `components/composer_draft.js` behavior; changes there should only be needed if restoration tests expose a defect.
* New `src/alfrd/web/js/components/panel_resize.js`; `src/alfrd/web/css/studio.css`, `chat.css`, `chat-dock.css`: panel widths, handles, responsive constraints, ellipsis and dot.
* Retain `src/alfrd/studio_live.py` metadata events; adjust only if naming invalidation needs coverage. No change to execution, approval or permission policy.

Add/update meaningful tests during implementation:

| Tests | Required evidence |
| --- | --- |
| `tests/studio_js/chat_project_switch.test.mjs`, `chat_sessions.browser.test.mjs`, `inbox_m6.test.mjs` | No picker activity labels or activity GETs on boot/open/switch/resync; retain shared selected-list deduplication, Needs me, A→B→A session/draft restoration and stale-response protection, including page/dock transitions and failed destination loads. |
| `tests/test_chat_session_manage.py`, `tests/test_inbox_m6.py` | Remove obsolete compact activity assertions; retain full-list attention and linked-decision deduplication. Add title generation, normalization, truncation, explicit/legacy title preservation, attachment-only, concurrent rename/send, idempotent replay and interrupted-write repair cases with no LLM invocation. |
| `tests/studio_js/chat_runs.test.mjs`, `plan_switcher.test.mjs`; new `jobs.test.mjs` | Multiple workflows plus multiple chat jobs in one project, chat-only project, pinned historical selection, same ID across projects, selected-summary deduplication, waiting/paused/terminal transitions, plugin count, background completion, removal and project-qualified navigation. Workflow selection remains unchanged. |
| `tests/test_chat_workflow_runs.py`, `tests/test_plan_status_api.py`, `tests/test_studio_live.py` | Chat metadata/wait phase in summaries including no-workflow response; all active jobs beyond history limits; no transcripts/artifacts read for Jobs; title-change invalidation and scoped project access. |
| New `tests/studio_js/panel_resize.test.mjs` and `panel_resize.browser.test.mjs` | Bounds/invalid storage, pointer directions and capture cancellation, keyboard/Home/End/Enter, double-click reset, persistence/reload, collapse/expand, remount cleanup, dynamic clamp without lost preference and storage denial. Browser assertions for focus, ARIA values, no horizontal overflow, sheets and transcript/draft stability. |
| `tests/studio_js/chat_ux_dock.test.mjs`, `chat_redesign.browser.test.mjs`, `header_pickers.test.mjs` | Single-line rows/full tooltip/dot updates while focused; no metadata/action clutter; accessible overflow actions; heading/header shortcut removal; icon-only actions including Check's busy/error/finally paths and keyboard-only access. |

Review both themes at 360, 900, 1280 and 1440 CSS px, narrow/wide saved widths, 200% zoom, forced colors, reduced motion, keyboard-only interaction and combined panels. Preserve permissions, cancellation and rename/delete functionality; visual simplification must not hide actionable prompts in the conversation.

## Payload budget

Keep the existing hard caps from `tests/test_studio.py`: startup **< 204,800 B gzip** and total lazy assets **< 409,600 B gzip**, plus `test_lazy_modules_are_not_imported_statically`. Do not raise caps. Measure on the implementation checkout, not from historical audit numbers.

No library, font, icon package or LLM client is needed. Reuse existing SVG icons/CSS tokens, plan summaries, storage wrapper and lazy Jobs tray. Removing labels/decorators and compact count logic funds the small resize helper. Import the resize helper lazily for the shell rail and from lazy chat components; do not pull Chat modules into startup. Keep responsive chat styles in existing lazy chat CSS. Backend title/summary changes add no web bundle bytes. API summaries contain bounded metadata per job, retain all active entries beyond history limits and never embed messages/graphs/logs. Removing catalog-wide activity GETs reduces runtime traffic; it is separate from the compressed asset caps.

Run the three named payload/lazy-import tests and the affected Python/JS/browser suites after implementation. Record actual startup and lazy gzip totals and changed test results in the implementation handoff. No source/tests were edited and no implementation tests were run for this documentation-only addendum.
