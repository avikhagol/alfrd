# v0.4.0.0 wave 3 status

Branch `v0.4.0.0-wave3`. `origin/v0.4.0.0-wave3` was pushed at b96b280 by someone other than the integrate task (the reflog says "update by push"). The fix and status commits after b96b280 are local only.

## Commits (one per backlog id)

| id | commit | subject |
|----|--------|---------|
| F0b | 12c3641 | finish F0 legacy and v1 fixtures |
| L2 | dcc62a0 | R/overview projection and hash-checked artifacts/raw |
| U4 | f1f7d87 | aggregate telemetry attempt to task to request |
| T1 | f32991c | flat transcript render |
| T2 | 96a3eb2 | inline decisions and one Details disclosure |
| L1 | b4ab030 | inspector shell hidden by default |
| A4 | 8188629 | composer attachment tray |
| S3 | 3cc3e05 | one settings form |
| T4 | 256d1dd | Copy and Find in viewers |
| D1 | 06d0e0f | focus-visible token |
| D3 | d607a74 | status icon set |
| G2 | fbf46b6 | graph lanes per request |
| G3 | 74c4eb4 | viewport preservation, rev guard, deferred updates |
| T4 fix | 6b52b73 | lazy viewers import, startup payload |

## Test results (run at 6b52b73, after the T4 lazy-import fix)

Python: `uv run --offline pytest -q` -> 1517 passed, 4 failed, 4 skipped, 11 xfailed (523 s). One of the 4 (`test_hub_initial_scan_discovers_once_and_retains_start_cursor`) is a flake: it passes alone (1 passed in 0.33 s) and did not fail at 74c4eb4. The other 3 are the known baseline failures below.

Studio JS: `node --test tests/studio_js/*.test.mjs` -> 440 tests, 421 pass, 1 fail, 18 skipped. The 1 failure is the known `notifications.test.mjs` baseline failure. One test was added (`viewer_find.test.mjs`: inline snippet awaits the lazy text tools).

### Known baseline failures (3, all still failing)
- `tests/test_studio.py::test_lazy_payload_under_106kb_compressed` (321167 bytes gzip against a 300 KiB limit). Not touched by wave 3; the lazy cap is a separate decision.
- `tests/test_studio.py::test_js_parsers_with_node` (`shown is not defined`).
- JS `notifications.test.mjs`: "app route selects the linked project before opening its run and deduplicates rendering" (`shown is not defined`; the same root cause as `test_js_parsers_with_node`).

### Resolved since 74c4eb4
- `test_lazy_modules_are_not_imported_statically`: `logview.js` now imports `viewers.js` dynamically in `openFull`, and both callers await it (6b52b73).
- `test_startup_payload_under_178kb_compressed`: the static import was the cause. Startup payload fell from 201717 to 197335 bytes gzip and the test passes.
- `test_wheel_installs_and_public_imports_and_cli_work_outside_checkout`: not a wave-3 regression, so no code commit. See "Still failing" below.

### Still failing, with reasons
- `test_wheel_installs_and_public_imports_and_cli_work_outside_checkout`: environmental. The test builds its venv with `venv.EnvBuilder` defaults (`symlinks=False`), which copies uv's standalone Python. The copy cannot find its stdlib, so `uv pip install` exits 2. The wave-2 commit 12c3641 fails identically and `tests/test_wheel.py` is unchanged since before 0.4. The wheel itself builds and installs into a symlinked venv. Optional fix: `symlinks=(os.name != "nt")` at `tests/test_wheel.py:110`. Not applied.
- `test_hub_initial_scan_discovers_once_and_retains_start_cursor`: flaky under load in the full run; passes alone. Not investigated further.

The baseline list comes from the U4 and G3 handoffs; I did not run the suite on an earlier commit.

## Skipped checks
- Runtime and browser checks were skipped while ENV1 is open. This includes T4 Find (`bindTextTools` Range selection and scrolling), which has no DOM or browser test.

## Still open
- ENV1: browser and socket checks; needs the user's decision Q6. Blocks D6 and R1.
- SP1: runs the installed model CLIs on real images and costs money; needs the user's go-ahead. Blocks A3.
- D4: sweep over icon buttons and placeholder strings; only useful after wave 3 and 4 UI has landed, so it goes at the end of wave 4.

## Known gaps reported by tasks
- T4 (out of the task's file scope):
  1. `chat_view.js` (`openLog: (rel) => ...`) and `plans.js` `openLog(ctx, project, rel)` drop the `meta` argument. The unit/attempt label and latest flag therefore do not show in the chat yet.
  2. The inline Prompt and Reply `<pre>` blocks in `chat_view.js` (`transcriptHtml`) do not go through `openFull`, so they have no Copy or Find.
  3. The "latest attempt" logic in the `chat_activity.js` click handler has no unit test.
  4. Find is not exercised in a browser.
- U4: `session_state` advances every unit's sidecar on each poll. This is bounded, but it is a new cost on the state path. Units without a sidecar get one built on first call, which writes into the logs folder.
- `docs/release-0.4-contracts.md` is untracked and was deliberately not committed.
