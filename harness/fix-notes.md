# Harness fix notes

Date: 2026-11-02
Repository: `/mnt/6438D98627D1388F/Intelligence/gh/alfrd`
Branch: `feature/harness-mode`, starting at `9cfc3a8`
Input: `harness/verify-report.md`

## Disposition

No production code was changed. The environment-related failures pass when the
tests have their required localhost and build-cache access. The real payload
defect remains deferred below; this is not a green-suite certification.

| Report item | Result and evidence |
| --- | --- |
| D1: wheel build/install errors | Re-run outside the sandbox passed both wheel tests. The original build fails because uv cannot create a lock in the read-only `/home/avi/.cache/uv`; a fresh writable cache in offline mode has no setuptools build dependency. No packaging change is warranted by these failures. |
| D1: progressive browser-launch cleanup assertion | Re-run outside the sandbox passed. Inside the sandbox the HTTP fixture fails before Chromium launch, so it never reaches the expected `launch regression sentinel`. No cleanup change is needed. |
| D1: static-server socket failure | Re-run outside the sandbox passed. |
| D1: CLI HTTP-readiness and server-restart failures | Re-run outside the sandbox: **2 passed in 3.36s**. |
| D1: five socket-dependent setup errors | All five pass in the unrestricted full suite below. |
| D1: lazy payload | **DEFERRED**: 446,583 gzip bytes against a strict 440,320-byte ceiling, an overage of 6,263 bytes. See rationale below. |
| D2: old default-port server | Expected safe incompatibility diagnostic, as the verifier explicitly states. `test_attach_and_old_server` passes in the CLI suite. Leave the existing server running; do not change compatibility checks merely to make this invocation attach. |

## Re-test evidence

All commands run from the repository above, using
`/home/avi/intelligence/gh/alfrd/.venv/bin/python`.

Outside the sandbox, with localhost sockets and the normal uv cache available:

```bash
rtk proxy /home/avi/intelligence/gh/alfrd/.venv/bin/python -m pytest -q tests/test_wheel.py tests/test_studio_runner.py tests/test_studio.py::test_static_handler_serves_modules_with_js_mime tests/test_harness.py tests/test_harness_cli.py tests/test_harness_api.py
```

Result: **27 passed in 16.80s**.

```bash
rtk proxy /home/avi/intelligence/gh/alfrd/.venv/bin/python -m pytest -q tests/test_cli.py::test_browser_opens_only_after_http_readiness tests/test_plugin_jobs.py::test_real_server_restart_keeps_the_session
```

Result: **2 passed in 3.36s**.

Payload and static-import checks, with the original limits unchanged:

```bash
rtk proxy /home/avi/intelligence/gh/alfrd/.venv/bin/python -m pytest -q tests/test_studio.py::test_startup_payload_under_170kib_compressed tests/test_studio.py::test_lazy_payload_under_430kib_compressed tests/test_studio.py::test_lazy_modules_are_not_imported_statically
```

Result: **1 failed, 2 passed in 0.77s**. Startup is 166,413 gzip bytes;
lazy assets are 446,583 gzip bytes. Only the lazy-size assertion fails.

## Full suite

Command (run once in the sandbox and once outside it):

```bash
rtk proxy /home/avi/intelligence/gh/alfrd/.venv/bin/python -m pytest -q
```

Sandbox result: **5 failed, 1,893 passed, 4 skipped, 11 xfailed, 7 errors in
787.58s**. This reproduces the verifier's exact failure/error set. In addition
to the lazy payload, failures are CLI readiness, server restart, static serving,
and the progressive fixture sentinel. Setup errors are three local-LLM chat
tests, two webhook notifier tests, and both wheel tests.

Unrestricted result: **1 failed, 1,904 passed, 4 skipped, 11 xfailed in 737.01s**
(exit status 1). The sole failure is
`tests/test_studio.py::test_lazy_payload_under_430kib_compressed`; all eleven
environment-related failures/errors from the sandbox run pass. The full suite
therefore remains non-green solely because of the deferred payload defect.

Raw evidence beside the orchestration copy of these notes:

- `harness/fix-pytest-sandbox.log`
- `harness/fix-pytest-unrestricted.log`

## Deferred work and reason

The lazy payload needs at least 6,264 bytes removed to satisfy the strict `<`
assertion in this working tree. This includes a genuine committed-branch overage:
compressing tracked HEAD assets with the current startup closure gives 444,691
bytes, already 4,371 bytes above the ceiling. It is not solely caused by the
pre-existing dirty files.

The checkout also contains unrelated Chat frontend edits and an untracked
`chat_lists.js`. A safe reduction requires a separate asset/refactoring pass and
browser validation against those changes. No simple local Harness-only deletion
was identified that preserves the verified replay, accessibility, and lifecycle
behavior. Broad minification or removing template guidance/asset metadata just to
cross the cap would expand this fix beyond the requested simple code changes.
The cap and its accounting remain unchanged. This defect is explicitly deferred
for that reason; the feature should not be described as fully verified until it
is resolved.

## Commit scope

Only these notes are committed as repository `harness/fix-notes.md` on
`feature/harness-mode`. Existing working-tree changes are left untouched.
Neither `docs/agent-loops.md` nor `src/alfrd/agent_io.py` is included; their file
hashes were unchanged across the re-runs. `git diff --check` passes.
