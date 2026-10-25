# v0.4.0.0 wave 3 status

Branch `v0.4.0.0-wave3`. Not pushed: the branch has no upstream and no remote branch contains it.

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

## Test results (run at 74c4eb4)

Python: `uv run --offline pytest -q` -> 1516 passed, 5 failed, 4 skipped, 11 xfailed (565 s).

Studio JS: `node --test tests/studio_js/*.test.mjs` -> 439 tests, 420 pass, 1 fail, 18 skipped.

### Known baseline failures (4 of the 6 failures)
- `tests/test_studio.py::test_lazy_payload_under_106kb_compressed` (321167 bytes gzip against a 300 KiB limit)
- `tests/test_studio.py::test_js_parsers_with_node` (`shown is not defined`)
- JS `notifications.test.mjs`: "app route selects the linked project before opening its run and deduplicates rendering" (`shown is not defined`; the same root cause as `test_js_parsers_with_node`)

### NEW failures (not in the baseline)
- `tests/test_studio.py::test_lazy_modules_are_not_imported_statically`: `js/components/viewers.js` is now statically imported (`logview.js:19`, added by T4 256d1dd). It must stay lazy, so T4 needs to load it with a dynamic import.
- `tests/test_studio.py::test_startup_payload_under_178kb_compressed`: the startup payload is 201717 bytes gzip against a 193 KiB limit. This was not in the baseline list. I did not bisect it, but the wave's JS growth (17 files, +834/-200 lines since F0b) is the likely cause, and T4's static `viewers.js` import probably adds to it.
- `tests/test_wheel.py::test_wheel_installs_and_public_imports_and_cli_work_outside_checkout`: `uv pip install` of the wheel exited with status 2. I did not capture the cause. It runs in this session's `uv --offline` environment, so it is probably an environment or network issue. I did not run it on a clean baseline, so this is unconfirmed.

I did not run the suite on an earlier commit to confirm the baseline. The baseline list comes from the U4 and G3 handoffs.

## Skipped checks
- Runtime and browser checks were skipped while ENV1 is open. This includes T4 Find (`bindTextTools` Range selection and scrolling), which has no DOM or browser test.

## Still open
- ENV1
- SP1
- D4

## Known gaps reported by tasks
- T4 (out of the task's file scope):
  1. `chat_view.js` (`openLog: (rel) => ...`) and `plans.js` `openLog(ctx, project, rel)` drop the `meta` argument. The unit/attempt label and latest flag therefore do not show in the chat yet.
  2. The inline Prompt and Reply `<pre>` blocks in `chat_view.js` (`transcriptHtml`) do not go through `openFull`, so they have no Copy or Find.
  3. The "latest attempt" logic in the `chat_activity.js` click handler has no unit test.
  4. Find is not exercised in a browser.
- U4: `session_state` advances every unit's sidecar on each poll. This is bounded, but it is a new cost on the state path. Units without a sidecar get one built on first call, which writes into the logs folder.
- `docs/release-0.4-contracts.md` is untracked and was deliberately not committed.
