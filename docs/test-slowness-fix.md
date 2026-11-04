# r0009 verification slowness investigation

Repository: `/mnt/6438D98627D1388F/Intelligence/gh/alfrd`. No commits. Existing dirty files and untracked work were retained; edits were limited to the files listed below.

## What the original log establishes

Source: `.alfrd/plans/chat-20261028-231956-8e65ce/logs/0029-r0009.verify-sonnet.log` in the brainstorm project.

- Total agent lifetime: **1,708 seconds (28m28s)**, terminated with exit 143. Start was `2026-10-30T00:18:18`; the log does not timestamp individual tool calls, so an exact wall-time attribution to every command is impossible.
- The only completed full Python run took **587.37 seconds**: 1 failed, 1,722 passed, 4 skipped, 11 xfailed. It ran serially, although the repository already documents xdist and isolated serial tests. Plain `pytest` was tried first, using Python 3.10's old user-site ALFRD and lacking Flask; it failed collection. That was interpreter/setup churn, not a slow test.
- The failure was `test_stall_check_plan_reply_adds_tasks_and_message`. A **targeted**, not full-suite, retry passed all 24 tests in **10.46 seconds**. The captured runner log shows the initial plan and stall check starting together, consistent with a timing-dependent dependency-validation race. This is not evidence that pytest needed another full-suite run; none is recorded.
- Several shell forms were rejected, a monitor call failed schema validation, and `node --test <directory>` failed because Node expected a module/file. These added agent/tool overhead; the log does not provide individual durations for it.
- The correctly globbed JS/browser command exceeded its **600-second** foreground deadline and moved to the background. It never produced a suite summary in this log. The last visible output was test 407, a preferences test; that does **not** establish that preferences itself hung, because Node reports files in order.
- The final `while pgrep -f "node --test" ...` loop matches the containing shell's own command line, so it can wait forever even after Node exits. It also does not recover the actual test exit status. This explains the agent-side wait independently of the browser bug. That command ended with 137, and ALFRD then terminated the agent.

587.37 + 10.46 + at least 600 = at least **1,197.83 seconds** of recorded test execution/waiting. The remaining at most 510.17 seconds includes setup, rejected commands, inspections and the final wait; the log is not sufficiently timestamped to divide it further. The JS command may also have continued while subsequent commands ran, so these are foreground accounting bounds, not a complete CPU-time timeline.

## Concrete browser failure and reproduction

The retained `/tmp/verify-js.log` from the original run explicitly records missing-default-browser failures in `gsheet_panel.browser.test.mjs` and `keep_view.browser.test.mjs`: Playwright wanted `chromium_headless_shell-1234`, although the command supplied `ALFRD_CHROMIUM_EXECUTABLE` pointing to the installed Chromium 1248 executable. Four tests ignored that override.

More seriously, `progressive_load.browser.test.mjs` opened an HTTP listener, attempted that same default launch, and registered server cleanup **only after** successful launch. A launch failure left the server alive. Node waited indefinitely for this worker and withheld subsequent file results. Reproducing that file with the original code emitted the missing-executable failure after **30.54 ms**, but still required an external **30-second** deadline to exit (124). This is a leaked-server hang, not a necessary browser sleep.

After the fix, the same real Chromium test passes and exits in **7.05 seconds wall / 6.658 seconds Node duration**. A regression test deliberately injects a throwing Playwright launch and requires failure exit 1 before the bounded runner's deadline; it passed. The three runner regression checks passed in **3.88 seconds** in targeted verification.

The complete browser suite now completes. One real, deliberately long test remains: `chat_ux_thread.test.mjs` runs a 60-second continuity scenario and asserts at least 60,000 ms elapsed. It took **82.19 seconds** including setup in the successful full JS run. This requirement was retained. Other long top-level browser groups were dock integration (49.62 s), Skills Explorer (44.08 s), redesigned chat (34.30 s), workflow editor (27.18 s), and Find (25.73 s); groups run concurrently, so their times must not be summed for wall time.

## Changes

- `tests/studio_js/{progressive_load,keep_view,viewer_find,gsheet_panel}.browser.test.mjs`: consistently honor `ALFRD_CHROMIUM_EXECUTABLE`. Progressive loading registers server cleanup before launch and closes active connections on teardown.
- `scripts/run_studio_tests.py`: portable file expansion from the repository root, four Node workers by default, streaming output, a 240-second overall deadline, exit-status propagation, and POSIX process-group cleanup on timeout/interruption. This bounds hangs without removing browser checks. Optional file arguments support targeted diagnosis.
- `tests/test_studio.py`: use that same runner for the existing full JS check. The outer safety timeout is 255 seconds, leaving the runner time to enforce its deadline and reap descendants. The previous 120-second timeout killed only the parent Node process and was marginal even for legitimate full browser coverage.
- `tests/test_studio_runner.py`: regression tests for failure status, descendant cleanup, and browser-launch-failure HTTP cleanup.
- `tests/studio_js/notifications.test.mjs`: supply the existing navigation guard in its VM sandbox. The initial full JS reproduction completed in 119.74 seconds but failed because the existing uncommitted `app.js` now calls `skillsCanLeave`; its isolated route fixture lacked that dependency. Existing assertions are unchanged. This is separate from the historical hang.
- `docs/studio-guide.md`, `pyproject.toml` comment, and `skills/verification-checklist/SKILL.md`: clarify that Python parallelism is opt-in, use the repo interpreter, bound concurrency, separate time-sensitive tests, avoid rerunning JS already included in pytest, retain complete logs, and wait on process handles rather than command-text matches. No scheduler policy was changed and no evidence establishes a built-in ALFRD prompt that explicitly ordered the original bad wait loop.

## Validation and timings

| Run | Before | After |
| --- | --- | --- |
| Progressive loading browser test | Failed at 30.54 ms, leaked server; killed at 30 s (124) | Pass, exits in 7.05 s wall |
| Full JS/browser suite | Historical run exceeded 600 s without summary | 130.12 s wall; 760 passed, 0 failed, 4 pre-existing skipped placeholders |
| Full Python coverage | Historical serial run 587.37 s, one transient failure | 335.76 s + 27.70 s wall (363.46 s total); all full-suite coverage passes |

Successful standalone JS log: `/tmp/alfrd-js-final.log`. Reproduction and targeted logs: `/tmp/alfrd-js-baseline-local.log`, `/tmp/alfrd-js-after.log`, `/tmp/alfrd-progressive-after.log`, `/tmp/alfrd-controls-before.log`, `/tmp/alfrd-runner-regression.log`. The focused agent-controls file passed 25 tests in 28.30 s (29.19 s wall), with the slowest calls 5.67 s (task editor), 3.54 s (lingering-agent cleanup), and 3.12 s (runtime budget); these are bounded work, not the 600-second browser hang.

Initial full serial profiling attempts were deliberately stopped: the constrained run had socket/environment errors; a second serial run overlapped investigation/other suites and was redundant with final complete coverage. Their partial times are not used as clean before/after speed measurements. Suite sizes and browser availability also differ from r0009, so the historical Python duration is contextual, not a controlled speedup benchmark.

Final Python validation (all existing tests, including slow wheel checks and actual configured Chromium):

- `-n 4 -m "not serial" --durations=30`: **1,827 passed, 4 skipped, 11 xfailed**, 334.51 s pytest / **335.76 s wall**. Log: `/tmp/alfrd-py-parallel-final.log`.
- `-m serial --durations=30`, after the parallel run finished: **7 passed**, 26.18 s pytest / **27.70 s wall**. Log: `/tmp/alfrd-py-serial-final.log`.
- Combined: **1,834 passed, 4 existing skips, 11 expected failures, zero unexpected failures**, **363.46 s total command wall time**. The historical 587.37 s is pytest's reported duration, whereas the final aggregate pytest-reported duration is 360.69 s. Different suite size, browser coverage and overlapping investigative load prevent claiming a controlled percentage improvement.
- The included JS check took **110.68 s** in final pytest; it executed the same complete Node suite with real Playwright/Chromium. Its separate standalone timing was 130.12 s under different investigative load.
- Python's other longest items: installed-wheel smoke call 16.76 s plus setup 13.63 s, wheel-resource setup 12.17 s, scheduled clock wait 11.58 s, dead-webhook/review acceptance 10.90 s, scheduled-plan/start-now 8.97 s, bounded stall-check-limit scenario 8.13 s, turn checklist 7.60 s, runner-death/readoption 6.95 s. Thus hundreds of seconds of serial integration work plus the browser suite explain Python wall time; there is no reproduced single unbounded Python test. The final serial checks peak at 4.08 s for two real idle periods and retain CPU/elapsed-time assertions.
- `rtk git diff --check` passed. Three new runner regressions are included in the full passing suite. The historically intermittent stall-check test also passed in final full verification; no assertions or scheduler concurrency were changed to hide it.
- Mailbox was read before completion; no pending messages. No commit was created.

Commands from the ALFRD repo root (all browser runs used local loopback/process access):

```sh
export ALFRD_PLAYWRIGHT=/home/avi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs
export ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome
rtk .venv/bin/python scripts/run_studio_tests.py
rtk .venv/bin/python -m pytest -q -n 4 -m "not serial" --durations=30
rtk .venv/bin/python -m pytest -q -m serial --durations=30
rtk git diff --check
```

The final pytest commands themselves run the JS suite once; the standalone JS run here was needed to reproduce and independently time this investigation's affected suite. Future routine verification should avoid duplicating it. No existing test was deleted, skipped, or weakened for speed. Four inspector browser placeholders already unconditionally skipped as ENV1 remain unchanged; actual Chromium coverage ran in all the optional browser files configured through Playwright. Existing Python skips/xfails remain visible in the final results.
