# Multiple scheduled messages per chat

Implementation checkout: `/mnt/6438D98627D1388F/Intelligence/gh/alfrd`.
Task artifact: `/mnt/6438D98627D1388F/Intelligence/agents/alfrd_brainstorm/docs/multi-schedule.md`.

## Inputs and preservation

Read the composer verification, heartbeat stability, lazy loading, fast switching, and test-slowness guidance before changing implementation. The four supplied SHA-256 values matched the task-project files exactly. The two selected skill files were also read; UI/backend planning guidance informed the contracts below. No external LLM was used.

Existing changes in `src/alfrd/gui/studio_chat.py`, `src/alfrd/web/js/components/skills_explorer.js`, `tests/test_skill_activations.py`, and untracked `tests/studio_js/skill_role_assignment.test.mjs` were left in place. None of these files was edited by this task. Nothing was committed, reset, or stashed.

## Data model and lifecycle

Continue using each session's atomic `schedule.json`, schema version 1. Its existing `batches` list now supports multiple open batches rather than enforcing one. Existing single-batch files need no migration. `current_id` remains a compatible default composer target, and falls back to an open batch when its old target becomes terminal. Pending batches are ordered by UTC instant, then creation time and ID; each retains its own `sch_<hex>` ID, numbered items, item versions, attachments, time zone, dispatch journal and request ID.

An optional `creations` mapping records idempotent client IDs that selected an already-existing batch at the same instant. Original create IDs, message IDs, and mutation operation receipts remain durable. Replaying an acknowledged operation returns the receipt without repeating a transition. Reusing an ID with a different payload or using a message ID against another batch is rejected.

Open states remain scheduled, waiting, error, and dispatching. Mutations are allowed only while scheduled, waiting, or error; submitted, cancelled, expired, and dispatching batches cannot be changed. Empty deadlines expire without creating requests. Dispatch recovery still completes an already-claimed journal exactly once after a restart. A sweep considers all pending batches independently and reloads the journal between candidates so recovery cannot be overwritten with an earlier in-memory copy. Each populated batch produces its own orchestrated request and synthetic message. Existing holds for active requests, paused/cancelled workflows, and unavailable execution service remain in effect; a due batch stays visibly waiting until the hold clears.

All writers keep the existing session file lock. Session-wide optimistic `expected_schedule_revision` checks protect noncommutative edits and time changes; atomic append is serialized under that same lock. Revision projection and `chat.schedule.*`/session events continue invalidating session data. Session deletion checks all open batches.

## API

Base path: `/api/studio/projects/<project>/chat/sessions/<sid>`.

| Action | Contract | Result / validation |
| --- | --- | --- |
| Read | `GET /schedule` | Existing `schedule` default and `last_schedule`, plus authoritative ordered `schedules[]`, revision, server clock and service state. |
| Create/select time | `POST /schedule` with `run_at`, `timezone`, `client_schedule_id`, `expected_schedule_revision` | A new time creates a batch. The same UTC instant selects the existing open batch, retaining all items. `selected_schedule_id` identifies the target for subsequent append; `appended: true` identifies a same-time selection. |
| Append | `POST /messages` with `text`, optional attachments/client message ID, `schedule_id`, `expected_schedule_revision` | Appends a numbered item to that specific pending batch. An omitted target keeps the existing default-target behavior. Explicit terminal/unknown targets never fall through to immediate send. |
| Edit/remove item | `PATCH` / `DELETE /schedule/<bid>/items/<iid>` with operation ID and expected revision | Edit accepts text and attachment refs. Existing size, empty-message, and attachment validation applies per batch. |
| Change time | `PATCH /schedule/<bid>` with new time/zone, operation ID and expected revision | Rejects past or offset-free timestamps and invalid zones. A collision with another open time returns 409 with guidance to append to that existing batch; it does not silently merge or discard edited items. |
| Run now | `POST /schedule/<bid>/run` with operation ID and expected revision | Requires nonempty editable batch and available execution service; uses the existing due-dispatch/recovery path, returning 202. |
| Cancel | `POST /schedule/<bid>/cancel` with operation ID and expected revision | Cancels only this batch; other pending batches remain available. |

Invalid time/content requests return 400. Stale revisions and invalid transitions return 409 with authoritative schedule state and the existing error envelope. A retry after a lost mutation response preserves its original operation ID, payload and URL. The browser also rebases a stale append against any still-open batch in `schedules[]`, including a non-default target.

## UX and refresh behavior

The composer HTML, schedule/attach icon buttons, placeholder routing, textarea auto-grow, and collapsed schedule mount are unchanged. The existing schedule panel contains one compact bordered block per pending batch, displaying time, zone, state, countdown and numbered items. Each item has an edit glyph; each block has append and change-time glyphs with `aria-label` and `title`, plus inline Run now and Cancel controls. No global run/cancel bar or large composer form was added.

Flow: schedule icon → existing date/time picker → type/send into the selected block. To add to another block, activate its `＋` button, which selects that target and focuses the existing composer. The target is marked with “Composer target” and `aria-pressed`. “Schedule another message” opens the same picker for a new time; choosing an existing instant targets that block for append. Cancelled/dispatched targets leave the fresh composer usable; an in-flight or failed-send draft retains its original explicit target through the existing draft retry mechanism.

Errors stay in the existing alert or picker alert. Picker cancellation/Escape and focus restoration retain their previous behavior. Busy mutations are serialized; run controls are disabled during an unsaved item edit. Heartbeats preserve the exact editor node, text, selection, focus and height, even when the saved batch becomes terminal. Unchanged focus-release redraws no longer notify the composer, avoiding draft resets during a fill/input event. Open date/time dialogs remain protected. Selection belongs to the shared project/session controller, survives ordinary refreshes, and cannot be overwritten by a lower-revision response. Retained views refresh schedule state on resume; paused polling and editor DOM continue using the existing suspend/resume lifecycle.

## Files changed by this task

Paths relative to the implementation checkout:

- `src/alfrd/chat.py`
- `src/alfrd/web/js/data/chat_schedule.js`
- `src/alfrd/web/js/components/chat_schedule.js`
- `src/alfrd/web/js/components/composer_draft.js`
- `src/alfrd/web/css/chat-composer.css`
- `tests/test_chat_schedule.py`
- `tests/test_chat_schedule_api.py`
- `tests/studio_js/chat_schedule.test.mjs`
- `tests/studio_js/multi_schedule.browser.test.mjs`
- `tests/studio_js/skills_explorer.browser.test.mjs` (fixture session only)
- `docs/multi-schedule.md` (copy of this task artifact)

## Observable tests

Added Python tests:

- `test_multiple_schedules_append_same_instant_and_dispatch_separately`: two durable batches, equivalent-offset same-instant selection, append to the older block, create replay, deadline isolation and two separate request IDs.
- `test_multiple_schedule_mutations_are_isolated_and_revision_safe`: stale revision, past time, collision, edit, change time, cancellation isolation, run-now on the remaining batch, and terminal transition/append refusal.
- `test_multi_schedule_api_targets_validation_and_transitions`: real Flask routes for two batches, same-time selection, explicit appends, invalid times, rescheduling, run-now, cancellation, and terminal rejection.

Added JS/browser tests:

- `multiple schedule targets survive refresh, isolate mutations and reject stale cache state` (`chat_schedule.test.mjs`): selected target, explicit mutation URL/revision, lower-revision rejection, refresh retention, and terminal-target cleanup.
- `multiple scheduled blocks: append, new time, edit retention, change time, run and cancel` (`multi_schedule.browser.test.mjs`): real Chromium, both shared composers, multiple blocks, accessible glyphs, per-block controls, targeted append, same-time append, background editor/caret retention, schedule refresh after suspend/resume, individual run/cancel and unchanged composer-row structure.

Existing `chat_schedule.browser.test.mjs` remains unchanged and passes its composer icon/attachment, collapsed-details, routing, auto-grow/reset, picker preservation, queue/edit/remove/run/cancel checks. Existing scheduler tests retain process-lock contention, crash recovery at six journal stages, item/prompt limits, attachment validation and workflow holds.

## Validation

Focused Python scheduler/API run: **33 passed, 0 failures**, 4.73 seconds.
Focused existing and new Chromium flows before the full run: **2 passed, 0 failures**, 3.03 seconds wall reported by Node. An initial restricted-sandbox attempt could not bind the fixture listener (`EPERM`); browser runs use approved execution outside that socket sandbox. No approval-review rejection occurred.

The initial full nonserial run completed in **367.88 seconds**: **1,841 passed, 4 existing skipped, 11 expected failures**, with the sole Python failure in the wrapper for the JS suite. Node reported 779 passed, 4 existing skipped and 2 failed entries: one actual Skills Explorer fixture timeout plus its parent subtest. All schedule tests passed. The pre-existing Skills Explorer save implementation uses `server.mutate`, but its browser fixture had no mutation-enabled `server.session`. Added that fixture session (including its CSRF token) on initial setup and reload, retaining every assertion and all pre-existing product changes. The affected full browser file then passed **15 tests**, zero skips/failures, **26.68 seconds**. The unrelated product files were not edited.

The independent serial group passed **7 tests**, zero failures, **21.16 seconds**. This covers all 1,864 collected Python cases together with the nonserial group. The final complete Studio rerun passed **12 Python tests**, zero failures, in **119.48 seconds**. Its included **complete JS/browser suite passed 781 tests, zero failures, 4 existing skipped placeholders**, 785 Node tests in **115.207 seconds**. The Studio rerun replaces the sole failed wrapper result from the initial nonserial run; unaffected Python cases were not redundantly rerun.

Final complete coverage across the nonserial group, serial group, and corrected Studio rerun: **1,849 Python tests passed, 4 existing skipped, 11 existing expected failures, zero remaining failures**, covering all **1,864 collected cases**. No skip/xfail marker was added, removed, or changed. `git diff --check` passes and the index remains empty. No commits were created. The mailbox was read before handoff and contained no messages. The nonserial Python group runs with four workers and includes the complete bounded JS/browser runner once; the serial Python group follows it. Browser configuration uses installed Playwright and the explicit Chromium executable, preserving existing skips and xfails.

```sh
rtk proxy env PATH="/tmp/alfrd-multi-node:$PATH" ALFRD_PLAYWRIGHT=/home/avi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome .venv/bin/python -m pytest -q -n 4 -m 'not serial' --durations=15
rtk proxy env ALFRD_PLAYWRIGHT=/home/avi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome .venv/bin/python -m pytest -q -m serial --durations=15
rtk proxy env PATH="/tmp/alfrd-multi-node:$PATH" ALFRD_PLAYWRIGHT=/home/avi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome .venv/bin/python -m pytest -q tests/test_studio.py --durations=5
rtk git diff --check
```

Logs: `/tmp/alfrd-multi-python.log`, `/tmp/alfrd-multi-js.log` (second TAP reporter from a temporary Node wrapper; normal arguments/exit code retained), and `/tmp/alfrd-multi-serial.log`. The wrapper exists only under `/tmp`; it does not change repository tooling. After discovering that the intentional launch-failure regression also invokes Node, reporter injection was restricted to the full multi-file suite so its sentinel cannot overwrite the final TAP log. Additional logs: `/tmp/alfrd-multi-skills-fixture.log` and `/tmp/alfrd-multi-studio-final.log`.
