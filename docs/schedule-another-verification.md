# Verification: scheduling another message while one is pending

Verified 2026-10-31 by `sonnet` (verification role) against the working tree of `/mnt/6438D98627D1388F/Intelligence/gh/alfrd`, per `docs/schedule-another-fix.md`. No source was changed.

## Verdict

**Fix confirmed. The user's scenario works in the code and tests. The running GUI must be restarted to pick it up.** No reinstall is needed.

## Code evidence: no refusal for distinct schedules

- `grep schedule_already_open` over `src/`, `tests/` and static JS finds **no occurrence**. The only matches are in `docs/schedule-another-fix.md` and describe the old behavior.
- `create_schedule`, `src/alfrd/chat.py:4243–4260`:
  - Line 4243 runs only the revision check. Line 4244 normalizes the time.
  - Lines 4245–4252: a pending batch with the **same** `run_at` is selected and appended to. It raises only if that batch is `dispatching` (`schedule_dispatching`).
  - Lines 4253–4260: any **distinct** time appends a new batch with a fresh `sch_<uuid>` ID and makes it current. There is no `current_id` guard.
- Every remaining `ScheduleConflictError` in `chat.py` (lines 4171, 4232, 4237, 4240, 4248, 4272, 4275, 4278, 4282, 4310, 4328, 4335, 4352, 4361) concerns one of the following. None is a refusal to create a second schedule at a different time.
  - a stale revision
  - an ID reused with a different payload
  - the schedule service being disabled
  - a batch already dispatching
  - a batch that is no longer editable
  - re-timing onto an existing time (4352)

## The user's scenario

A first schedule with a two-bullet prompt, then "Schedule another message" for a later time.

- Python: `tests/test_chat_schedule.py:328` `test_schedule_another_after_bullets_has_independent_controls` (4 parametrizations: run/cancel × first/second).
  - Queues `- a\n- b` as one item (line 330, asserted at 339–340).
  - Creates a second schedule at a later time while the first is pending (331–335) and gets a distinct ID (335).
  - Both schedules are listed (338). The bullet text stays in one item and in the dispatched prompt (339–341).
  - Edit, re-time and append work on each schedule independently (342–351).
  - Run or cancel on one leaves the other unchanged (352–359).
- Browser (Chromium): `tests/studio_js/multi_schedule.browser.test.mjs:11` "multiple scheduled blocks: append, new time, edit retention, change time, run and cancel".
  - Sends `- a\n- b` (line 93) and checks the **"Schedule another message"** label (95).
  - The bullets remain a single queued item (101–102).
- Per the fix doc, with the old `current_id` guard restored in memory, the 4 new Python cases failed with `schedule_already_open`. I did not repeat that check, so it is the fixer's claim rather than mine.

## Suite results

All runs used the repository `.venv` and the Playwright/Chromium paths from the fix doc. The browser tests skip silently without them.

| Suite | Result |
|---|---|
| `tests/test_chat_schedule.py` | **34 passed** |
| Python full, `-n 4 -m 'not serial'` | 1845 passed, 1 failed, 4 skipped, 11 xfailed (1861 cases, 337 s) |
| Python serial group | **7 passed** |
| JS/browser full (`node --test tests/studio_js/*.test.mjs`) | 785 tests: 777 passed, 4 failed, 4 skipped (the 4 failures are reruns below) |

The 5 failures in the full runs (1 Python, 4 JS) are not new. Each passed when rerun alone on the idle machine. The first run was concurrent with the JS suite and a 4-worker pytest run.

- `tests/test_agent_controls.py::test_review_wait_respects_total_runtime_budget` is a timing test. It failed under load and passed alone (1 passed).
- JS `chat_redesign.browser.test.mjs` (the 1440px heartbeat subtest) and `chat_scroll.browser.test.mjs` (1440px) failed with `page.goto: Timeout 7000ms exceeded`, a load timeout. Rerun alone, both files passed 19/19 tests.

Net: **0 unresolved failures and 0 new failures.** Neither of the two files the fix touched failed outside a load-induced timeout. Counting each case once with the reruns substituted, Python is 1846 passed (plus the 7 serial), 4 skipped and 11 xfailed. The JS suite is 781 passed and 4 skipped, which matches the fixer's count.

## Earlier features still passing

All are in the full JS run, with the relevant ones also in the isolated reruns:

- **Minimal composer:** "markup: single line, placeholder helper and icon-only schedule/attach in row order" (ok 134) and "only the composer files and its stylesheet carry composer rules…" (ok 138).
- **Shared composer and dock schedule controls:** "shared Chat and narrow dock composer: schedule, add, edit, remove, run and cancel" (ok 93) and "draft retries preserve the original schedule target…" (ok 96).
- **Per-block schedule controls and refresh survival:** "multiple scheduled blocks…" (ok 317) and "multiple schedule targets survive refresh, isolate mutations and reject stale cache state" (ok 99).
- **Heartbeat stability:** "refresh patches only changed markup and preserves native and project dropdowns" (ok 262) and the chat_redesign heartbeat subtests, which passed on the isolated rerun.
- **Projects selector:** "active-first destination preserves remembered and explicit project-qualified identity" (ok 81) and "switch A/B/A, empty project and live snapshots never leak sessions across projects" (ok 82).
- **Lazy load:** "lazy shell fetches only selected lists, with no picker decoration or activity GETs" (ok 85) and "fresh selected lists skip settled duplicate reads…" (ok 86).
- **Fast switching:** "retained switches reuse nodes, preserve drafts/dropdowns/scroll, reject late fetches, refresh away and evict" (ok 169) and "collections: a slow reply for the previous target is dropped after a quick switch" (ok 165).

## What the user must do

1. Stop the running GUI normally (Ctrl-C in its launching terminal, or via whatever launched it).
2. Relaunch it with `alfrd gui` from the same directory, keeping the original options.
3. Reload the Studio tab.
4. **No reinstall is needed.** The installation is editable and points at this checkout. No pending schedules or data need migrating.

Per the fix doc, PID 89110 started 2026-10-30 23:50:57, before `src/alfrd/chat.py` was last modified (02:20). It therefore still runs the old backend and will keep returning `schedule_already_open` until restarted. I did not touch the running GUI.

After the restart, `GET …/schedule` should return a `schedules` array, which is empty when nothing is pending.

## Caveats

- The changes are uncommitted in the working tree.
- I did not exercise the live GUI. Evidence is from code reading and the test suites.
