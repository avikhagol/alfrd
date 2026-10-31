# Scheduling another message while one is pending

## Root cause and runtime evidence

The GUI process is running the backend loaded **before r0005**, while its static JavaScript can be served from the updated editable checkout. Restart the GUI; no reinstall or schedule-data migration is needed for this installation.

The exact original exception is in **`src/alfrd/chat.py:4232–4233` at HEAD `dec2b23516951755c6ba4fecebd703bfdcc39155`** (use `rtk git show HEAD:src/alfrd/chat.py`). `create_schedule` checked `if data['current_id']` and raised `ScheduleConflictError("schedule_already_open", store)`. This was a session-wide single-schedule restriction, not a concurrent-editor guard. It ran before inspecting prompt content. The text is absent from the current working-tree source because the existing, uncommitted r0005 implementation already removed it.

Evidence collected on 2026-10-31, local timezone Europe/Athens:

- Running GUI: PID **89110**, started **2026-10-30 23:50:57**, command `/home/avi/.local/share/uv/tools/alfrd/bin/python3 /home/avi/.local/bin/alfrd gui`.
- `src/alfrd/chat.py` modification time: **2026-10-31 02:20:38 +0200**, after that process started.
- A read-only authenticated `GET http://127.0.0.1:5000/api/studio/projects/alfrd_brainstorm/chat/sessions/20261028-010036-48c7a9/schedule` returned keys `last_schedule`, `rev`, `schedule`, `schedule_revision`, `schedule_service`, `server_now`, with **no `schedules` array**. The current `schedule_state` at `src/alfrd/chat.py:4150` includes that array. This confirms the live backend still exposes the old contract. No live schedule was created or mutated during diagnosis.
- The launcher shebang points to the uv interpreter above. A fresh import using that exact interpreter resolves `alfrd.chat.__file__` to this checkout's `src/alfrd/chat.py`; `inspect.getsource(create_schedule)` contains the r0005 multi-schedule logic. Its `alfrd-0.4.0.0.dist-info/direct_url.json` records `editable: true` and this checkout's file URL.
- `.venv/bin/python` also imports this checkout. Bare `python` is unavailable; system `python3` cannot import ALFRD. Bare `pip show -f alfrd` reports an unrelated legacy **0.0.5** installation under `/home/avi/.local/lib/python3.10/site-packages` containing only `__init__.py`, `lib.py`, and `util.py`; it is not what the launcher runs.
- `/home/avi/intelligence/gh/alfrd` resolves to `/mnt/6438D98627D1388F/Intelligence/gh/alfrd`, not a second checkout.
- Searched ignored/built files as well as source (excluding `.git` and `node_modules`), including `build/lib`, verification environments, the launcher environment, static JS, and `already_open`/`ALREADY_OPEN` fragments. No current ALFRD implementation constructs this error dynamically or contains the old guard. Unrelated PyYAML binaries contain the fragment. Static files come from the package directory (`src/alfrd/web/__init__.py:66`), allowing a fresh frontend to coexist with an old imported backend.
- Schedules are session `schedule.json` journals (`src/alfrd/chat.py:405–426`), not the runtime catalog database. All four journals in the task project's `.alfrd/chat` were checked: none contains this exception. The reported session was at revision 10, with two submitted batches and no current pending batch. The default runtime database `/home/avi/.alfrd/runtime.sqlite` also contains zero occurrences of the exact error bytes. No state files were edited.

Both supplied artifacts were read and their SHA-256 values matched. `docs/multi-schedule.md` matched `acadd564d340004752d5bd4e4e67a54043f18737b7f8bf73c2b312c2a5697e56`. The handoff was absent from the implementation checkout but found under the task project's `.alfrd/plans/chat-20261028-010036-48c7a9/handoffs/0022-r0005.multi-schedule-codex/response.md`, matching `3e45abbd06c0cc166db7f45381e133b30b1c36e349dfb007a0dbd5410917c9a4`.

## Fix and preserved behavior

Retained the existing r0005 fix at `src/alfrd/chat.py:4243–4266`: revision checks remain serialized under the session lock; a distinct time appends a fresh batch with a fresh ID; the same instant selects its existing batch for append. Explicit schedule IDs target item append/edit, time change, run and cancel. Prompt text does not participate in selecting a schedule. The line breaks in `- a\n- b` remain inside one queued item and inside its dispatched prompt.

No additional product-code change is necessary. Existing draft/editor serialization remains intact. This task only extends regression tests and adds this report. The minimal composer, schedule icon, `To orchestrate…` placeholder, auto-grow, heartbeat behavior, project persistence, lazy loading and fast switching were not edited. All pre-existing tracked/untracked changes remain; nothing was committed, reset or stashed.

## Regression tests

- Added `test_schedule_another_after_bullets_has_independent_controls` in `tests/test_chat_schedule.py:328`, parameterized for run/cancel against either first or second schedule (four cases). Queue the exact two-bullet text, create a later schedule while the first remains pending, verify distinct durable IDs and one bullet-containing item, edit/re-time/append to both, then run or cancel one and assert the other is unchanged. Running dispatches only the selected prompt.
- Extended `tests/studio_js/multi_schedule.browser.test.mjs` with the exact two-bullet composer input and explicit **Schedule another message** label check. Chromium verifies two distinct visible blocks, accessible edit/time/append glyphs and run/cancel controls on each, independent edits/time changes/appends, editor/caret retention through refresh, same-time append, run-first/cancel-second isolation, and unchanged composer-row structure.
- Verified the new Python regression detects the original bug: a temporary pytest plugin under `/tmp` restored only the original `current_id` guard in memory. All four new cases failed at second creation with **`schedule_already_open`**. The repository implementation was never replaced for this check.

## Validation

Focused Python scheduler suite: **34 passed**, 5.88 seconds. Focused Chromium regression: **1 passed**, no skips/failures, 4.00 seconds including launch.

The complete nonserial Python run (four workers) exercised **1,861 cases** in **406.58 seconds**: 1,826 passed, 4 existing skipped, 11 existing expected failures, and 20 plugin-test outcomes failed (18 failures, 2 fixture errors). All schedule tests and the JS wrapper passed. These 20 outcomes were caused by the invocation omitting `/home/avi/.local/bin` from PATH: plugin installation could not find uv and fell back to `.venv/bin/python -m pip`, but that venv has no pip. This was a test invocation error, not a product failure or a flaky test. Restoring the inherited PATH and rerunning **all three affected files** (`test_plugin_installer.py`, `test_plugin_examples.py`, `test_plugin_jobs.py`) produced **37 passed, zero failures/errors**, in **22.39 seconds**, replacing every failed outcome. No dependency or test implementation was changed to obtain that result.

The included **complete JS/browser suite passed 781 tests, zero failures, 4 existing skips**, 785 total, in **149.325 seconds**. TAP log: `/tmp/alfrd-schedule-another-js.log`. Playwright and Chromium were explicitly configured; no browser coverage was silently skipped. No flaky result was observed.

The independent serial group passed **7 tests**, zero failures, in **22.63 seconds**. Final aggregate, replacing the 20 environment-error outcomes with their successful reruns: **1,853 Python cases passed, 4 existing skipped, 11 existing expected failures, zero unresolved failures/errors**, covering all **1,868 collected cases**. The unchanged skip/xfail markers were retained. This aggregate counts each Python case once; the 37-case affected-file rerun overlaps 17 previously passing cases.

`rtk git diff --check` passes; the index is empty. Compared every pre-existing tracked diff with the initial snapshot: all product-code diffs remain byte-for-byte identical; only `tests/test_chat_schedule.py` gained this task's additional test. The initially untracked multi-schedule browser test was extended in place. The mailbox was read before final handoff and contained no messages. Nothing was committed.

Commands (from the implementation checkout; fixture servers/browser launches require access to local sockets):

```sh
# Full nonserial run; this initial PATH caused the plugin-only environment errors described above.
rtk proxy env PATH=/tmp/alfrd-multi-node:/home/avi/.nvm/versions/node/v22.22.2/bin:/usr/local/bin:/usr/bin:/bin ALFRD_PLAYWRIGHT=/home/avi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome .venv/bin/python -m pytest -q -n 4 -m 'not serial' --durations=15
# Corrected environment, complete affected-file rerun.
rtk proxy env PATH="/tmp/alfrd-multi-node:$PATH" ALFRD_PLAYWRIGHT=/home/avi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome .venv/bin/python -m pytest -q tests/test_plugin_installer.py tests/test_plugin_examples.py tests/test_plugin_jobs.py --durations=5
rtk proxy env PATH="/tmp/alfrd-multi-node:$PATH" ALFRD_PLAYWRIGHT=/home/avi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome .venv/bin/python -m pytest -q -m serial --durations=5
rtk git diff --check
```

The pre-existing `/tmp/alfrd-multi-node/node` wrapper forwards Node's normal arguments and exit status, adding a TAP file reporter only for the full JS invocation. No repository tooling was changed.

## Required restart

After this orchestration task finishes, stop the existing GUI normally (Ctrl-C in its launching terminal, or stop the process through the tool that launched it), then relaunch it from the same directory with **`rtk alfrd gui`**. Retain any original environment/options. Reload the Studio tab. Do not launch a second process on port 5000 while the old one still owns it.

This is an editable installation of the correct checkout, so **reinstalling is unnecessary**. Restarting is required to replace the already-imported Python functions. Pending schedules remain in their journals; do not delete or recreate them. A diagnostic check after restart is that GET `/schedule` includes `schedules: []` even when nothing is pending. The GUI was left running because restarting it during its own active orchestration could interrupt the task and other work.
