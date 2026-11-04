# Studio Skills Explorer

Implemented in /mnt/6438D98627D1388F/Intelligence/gh/alfrd.
Changes are uncommitted; pre-existing skills/native-wiring changes were preserved.

## Delivered

- Added **Skills Explorer** to Studio's main navigation, with a lazy-loaded view
  at `#/skills?p=<project>` and the standard project chooser.
- Reads `GET /api/studio/projects/<project>/chat/skills/inventory`; lists all
  discovered skills grouped in precedence order: `.alfrd/skills`,
  `.claude/skills`, `skills/`. Each entry shows name, description and provider
  delivery badges, including Codex's injection fallback.
- Search filters IDs, names, descriptions, paths and provider policy without
  rebuilding the search input or losing its focus.
- Details use the existing `/chat/skills/preview?id=...` endpoint and shared
  escaped `previewHtml` renderer. They include absolute path, manifest bytes,
  SHA-256, reference-file paths and provider policy. All dynamic metadata and
  errors are escaped too; skill HTML is displayed as text.
- Provider copy distinguishes selected-skill policy from actual launch delivery
  and explains retained injection and settings/failure overrides.
- Added loading, empty, no-match, unavailable-server/no-project and retryable
  error states. Refresh reloads inventory and the selected preview. A manifest
  hash change between inventory and preview prompts a metadata refresh.
- Native buttons support Tab, Enter/Space and Up/Down/Home/End navigation;
  selected state, labelled regions, busy states and live status are exposed to
  assistive technology. Layout stacks on narrow screens.
- Request identity guards prevent late previews from replacing another skill's
  or project's details. Unchanged Studio rerenders preserve search/details.
- The explorer is read-only. It makes no selection or settings mutations;
  existing selection APIs and bounds remain in chat settings.
- Documented usage and delivery semantics in docs/studio-guide.md.

## Files owned by this task

- src/alfrd/web/js/app.js
- src/alfrd/web/js/components/skills_explorer.js
- src/alfrd/web/css/skills_explorer.css
- tests/studio_js/skills_explorer.browser.test.mjs
- docs/studio-guide.md
- notes/skills-explorer-report.md (also copied to the required task workspace)

## Validation

- Focused real-Chromium explorer suite: **6 passed**, no skips.
  Covers navigation registration and six manifests, all three roots, search,
  keyboard activation/navigation, escaped hostile previews and error messages,
  metadata/references/delivery, live rerenders, responsive bounds, loading,
  empty/error/retry states, late response ordering and project isolation.
- Full Studio JavaScript suite, with Playwright/Chromium enabled:
  **749 passed, 4 skipped, 0 failed** (753 tests, 94.5 seconds).
  Command:
  `ALFRD_PLAYWRIGHT=/home/avi/node_modules/playwright/index.mjs ALFRD_CHROMIUM_EXECUTABLE=/home/avi/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome node --test --test-concurrency=4 tests/studio_js/*.test.mjs`
  Full log: /tmp/skills-explorer-studio-tests.log.
- `.venv/bin/python -m pytest tests/test_skills.py tests/test_skills_k4.py tests/test_skill_library.py -q`:
  **30 passed**.
- Actual `skill_inventory(Path.cwd())` discovers seven skills: alfrd plus all six
  new skills (artifact-handoff, blocker-reporting, failure-triage,
  mailbox-protocol, plan-json-author, verification-checklist).
- `git diff --check`: passed.
- Browser fixtures required local socket/Chromium access and ran successfully
  through the execution approval mechanism. An initial delayed-response fixture
  incorrectly removed a route before fulfilling it; corrected the fixture and
  reran both focused and full suites successfully.

## Limits

Reference files are listed by path; their contents are not loaded. Markdown is
shown as escaped source rather than formatted HTML. Inventory refresh is
explicit via Refresh skills. Delivery badges describe policy, not provider
activation or a live unit's native-exposure result.

