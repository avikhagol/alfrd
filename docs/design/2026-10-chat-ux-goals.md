# Chat UX screenshot audit and additional goals

Date: 2026-10-26. Owner: Codex, design. Branch: `v0.4.0.0-chatux`.

This is an audit of the current UI, not an implementation or a replacement specification. [2026-10-chat-ux.md](2026-10-chat-ux.md) remains authoritative, including its decisions, file ownership, integration contracts, accessibility requirements and payload gates. The goals below supplement each stream's acceptance criteria; they do not authorize cross-owner edits.

## Evidence and method

Captured the real `chat_view.js` and `chat_dock.js` modules using the HTTP fixture harness from `tests/studio_js/chat_redesign.browser.test.mjs`, Playwright and installed Chromium headless shell 1243. No substitute component styling was added. The fixture supplies one completed request, user messages, an orchestrator message, messages from specialists `codex` and `sonnet`, fenced JavaScript, and a running request with three tasks (done/running/queued), a dependency and a pending permission. It also supplies recorded public output and two team messages. No approval or command was dispatched.

The standalone host reserves the full viewport for Chat; it loads Studio, lazy, theme and chat CSS. Theme is also set through `document.documentElement.dataset.theme`. Desktop dock uses the default requested width of 380 px; existing responsive CSS changes its actual width below the desktop breakpoint. The empty area beside desktop dock is the fixture's neutral background, not evidence of a broken project view. Studio navigation, notices, console and OS keyboard are not mounted in this harness.

All captures use a 980 px viewport height, device scale 1, and Playwright `fullPage: true`. Because the real transcript scrolls internally, a full-page PNG does not expand every message: each matrix cell has five captures, preserving the same UI and changing only transcript scroll position. Base filename is the beginning of the conversation; `-code`, `-permission`, `-tasks` and `-latest` expose the corresponding content. There are **60 PNGs** in [/tmp/alfrd-chatux/before/](/tmp/alfrd-chatux/before/).

| Theme | Width | Full page, initial transcript | Dock, initial transcript |
| --- | ---: | --- | --- |
| Obsidian Orbit | 1440 | [page](/tmp/alfrd-chatux/before/page-obsidian-orbit-1440.png) | [dock](/tmp/alfrd-chatux/before/dock-obsidian-orbit-1440.png) |
| Obsidian Orbit | 900 | [page](/tmp/alfrd-chatux/before/page-obsidian-orbit-900.png) | [dock](/tmp/alfrd-chatux/before/dock-obsidian-orbit-900.png) |
| Obsidian Orbit | 360 | [page](/tmp/alfrd-chatux/before/page-obsidian-orbit-360.png) | [dock](/tmp/alfrd-chatux/before/dock-obsidian-orbit-360.png) |
| Daylight Orbit | 1440 | [page](/tmp/alfrd-chatux/before/page-daylight-orbit-1440.png) | [dock](/tmp/alfrd-chatux/before/dock-daylight-orbit-1440.png) |
| Daylight Orbit | 900 | [page](/tmp/alfrd-chatux/before/page-daylight-orbit-900.png) | [dock](/tmp/alfrd-chatux/before/dock-daylight-orbit-900.png) |
| Daylight Orbit | 360 | [page](/tmp/alfrd-chatux/before/page-daylight-orbit-360.png) | [dock](/tmp/alfrd-chatux/before/dock-daylight-orbit-360.png) |

Reproduction script: [/tmp/alfrd-chatux/capture.mjs](/tmp/alfrd-chatux/capture.mjs). [metrics.json](/tmp/alfrd-chatux/before/metrics.json) records document width, transcript dimensions, button dimensions, timestamp display and fixture text for all 12 combinations. [source-manifest.json](/tmp/alfrd-chatux/before/source-manifest.json) records web asset SHA-256 values at the final capture checkpoint, `132cf10`. The checkout initially contained existing source/test changes, subsequently checkpointed externally; this audit did not write source/tests or create a commit.

## Assessment against the specification and Stitch

The current implementation has a useful structural foundation: a continuous chronological thread, separate sessions, an optional inspector, bounded internal transcript scrolling, persistent composer placement, request-linked activity, native disclosures and inline permission forms. Both shell themes render; all 12 measured document widths match the viewport, with no page-level horizontal overflow. The running fixture reports the true `1/3` completed-task count. The permission form remains outside request Details, and no approval is preselected. These are foundations to preserve.

The visible result still resembles an administrative output viewer more than a multi-agent conversation. Speaker changes are conveyed mostly by small gray labels; agent identities have no initials or stable visual marks, timestamps disappear, code remains literal text, and opening an active request exposes large output blocks. Dense operational controls compete with a surprisingly spacious composer. The problem is hierarchy and information allocation, rather than an incorrect choice of dark versus light theme.

| Spec area | Required versus observed |
| --- | --- |
| Tokens | The shell palettes already exist and should remain. Chat-scoped semantic aliases, the specified shared type/spacing vocabulary and per-agent identity colors are not yet established in the baseline `chat.css`. Dark nested panels are recognizable, while Daylight surfaces are close enough that card identity needs stronger structure, not a copied dark palette. No contrast pass is claimed by this screenshot audit. |
| Thread | Continuous answers and human-readable protocol result summaries exist. The required shared identity renderer, visible local times, sanitized message Markdown and code-copy presentation are not visible in these captures. Date separators need a separate multi-day fixture. Page calls Claude the orchestrator, while dock only shows its display name. |
| Composer | Shared markup, image attachment controls, native textarea and Send exist. The specified compact helper, explicit target hint, empty-send affordance and separate request Stop are absent from the captured running state. Sending/retry, IME, draft recovery and upload races require behavioral acceptance tests; screenshots cannot establish them. |
| Activity | Request summary, true count, Usage, Details, task actions and output exist. The required task islands, dependency summaries and per-attempt output disclosures are not present: output and raw protocol occupy tall stacked rows inside request Details. |
| Dock/sessions/mailbox | Desktop dock and responsive page navigation exist, with inline permission actions. Session search is absent. Narrow dock header/progress content is crowded; permission context and inspectable action hash fall short. Modal focus, inert background, reply races and route/draft continuity remain unverified here. |

Reviewed all four supplied Stitch `screen.png` files under `docs/stitch_alfrd_multi_agent_visualizer/` in the brainstorming workspace:

- `alfrd_chat_harness`: useful visible identity, inset technical content and coherent composer enclosure. Its cropped four-column conversation is a source limitation; retain the spec's existing Studio shell and optional inspector.
- `parallel_agent_islands`: useful distinct task boundaries, owner hierarchy and aligned metrics. Adopt that legibility inside request Details, not its fabricated percentages, throughput or Pause Swarm.
- `requests_task_graph_walkthrough`: useful readable done/running/queued labels and prominent intervention hierarchy. Keep graph secondary and permission answers inline as specified; do not add Promote, Apply to Sandbox or unrelated actions.
- `runtime_telemetry_logs`: useful separation of prose, metrics and output with an explicit follow state. Do not import the wave chart, global telemetry dashboard or SSH bridge.

Stitch supplies dark reference designs only. Daylight acceptance must use the resolved light tokens in the spec. This audit adds no font, icon package, navigation column, endpoint or inferred runtime metric.

## Numbered defects

Severity: **high** obstructs a core reading/control task or informed permission review; **med** adds substantial scanning or interaction friction; **low** is visual inconsistency. Every defect has exactly one accountable workstream. Shared integration still follows the existing contracts.

| # | Concrete defect and consequence | Severity | Owner | Screenshot evidence |
| --- | --- | --- | --- | --- |
| 1 | Speaker headers are uniformly small, muted text without initials, `@key` or specialist role. Repeated provider labels require reading each header to tell specialists apart. Dock also loses the page's explicit orchestrator label. | med | thread | [Page speakers](/tmp/alfrd-chatux/before/page-obsidian-orbit-1440.png), [dock speakers](/tmp/alfrd-chatux/before/dock-daylight-orbit-360-code.png) |
| 2 | All fixture message times are hidden at rest (`display:none` in metrics), so touch readers cannot scan chronology. There is no visible timestamp beside any speaker. | med | thread | [900 px thread](/tmp/alfrd-chatux/before/page-daylight-orbit-900.png) |
| 3 | Fenced JavaScript appears as literal backticks and ordinary body text, wrapping code at prose width, without language header or Copy code. This breaks the distinction between instructions and executable examples. | high | thread | [Page code](/tmp/alfrd-chatux/before/page-obsidian-orbit-1440-code.png), [dock code](/tmp/alfrd-chatux/before/dock-daylight-orbit-360-code.png) |
| 4 | An active request opens large recorded-output blocks and raw protocol without individual output disclosures. Two tails overwhelm the task overview; the initial 360 px page transcript has 3,100 px of scroll content for 506 px of visible log space. | high | activity | [Expanded task output](/tmp/alfrd-chatux/before/page-daylight-orbit-1440-tasks.png), [narrow output](/tmp/alfrd-chatux/before/page-obsidian-orbit-360-tasks.png) |
| 5 | Tasks read as separator rows in one panel rather than distinct islands. Owner is appended to the title button, status/metrics drift to the side, and the queued integration task gives no visible explanation of its fixture dependencies. | med | activity | [Task rows](/tmp/alfrd-chatux/before/page-obsidian-orbit-900-tasks.png), [queued task](/tmp/alfrd-chatux/before/page-daylight-orbit-360-latest.png) |
| 6 | The completed request summary stays a single clipped line at 360 px and in the default desktop dock. Its final duration/cost information runs past the visible button edge even though the document itself does not overflow. | high | activity | [360 px summary](/tmp/alfrd-chatux/before/page-obsidian-orbit-360.png), [dock summary](/tmp/alfrd-chatux/before/dock-obsidian-orbit-1440.png) |
| 7 | Input, attachment guidance and Send look like separate blocks. Empty status/error space leaves a large dead band above Send; on mobile the long helper competes with the action across multiple lines. The composer consumes space without strengthening its visual grouping. | med | composer | [Desktop composer](/tmp/alfrd-chatux/before/page-daylight-orbit-1440.png), [phone composer](/tmp/alfrd-chatux/before/page-daylight-orbit-360.png) |
| 8 | Placeholder/helper copy repeats routing rules while no explicit current target is shown. Image guidance is truncated on phones, and dock omits the Shift+Enter instruction. It is harder to predict recipient and input behavior before sending. | med | composer | [Phone page](/tmp/alfrd-chatux/before/page-obsidian-orbit-360.png), [phone dock](/tmp/alfrd-chatux/before/dock-obsidian-orbit-360.png) |
| 9 | Empty text with no attachments still presents an enabled primary Send. It offers an action the empty state should visibly disallow. This finding concerns the affordance; no empty request was submitted. | med | composer | [Empty composer](/tmp/alfrd-chatux/before/page-daylight-orbit-900.png) |
| 10 | No separate Stop request control is visible while the request has running/queued cancellable tasks; cancellation is buried in task output as individual Cancel buttons. | high | composer | [Running request/composer](/tmp/alfrd-chatux/before/page-daylight-orbit-360-latest.png), [task Cancel](/tmp/alfrd-chatux/before/page-daylight-orbit-1440-tasks.png) |
| 11 | At 360 px the page header uses three rows for title, live/inspect controls and check status. The dock puts Close alone on another row. Administrative chrome takes reading height away from the conversation. | med | dock | [Page header](/tmp/alfrd-chatux/before/page-obsidian-orbit-360.png), [dock header](/tmp/alfrd-chatux/before/dock-daylight-orbit-360.png) |
| 12 | Dock progress wraps into insufficient vertical space: only a sliver of the amber decision badge is visible immediately above the transcript. The important intervention indicator is obscured at both default desktop width and phone width. | high | dock | [Default dock](/tmp/alfrd-chatux/before/dock-obsidian-orbit-1440.png), [narrow dock](/tmp/alfrd-chatux/before/dock-daylight-orbit-360-code.png) |
| 13 | Permission summary shows provider label, generic `permission_request · normal`, Action/Target/Scope and approval buttons, without visible task/request identifiers or an inspectable action hash. Source inspection confirms Full message only expands prose; arguments are appended separately to request raw output, and the supplied hash is sent but never rendered. The UI therefore separates approval from its exact inspectable scope. | high | dock | [Inline permission](/tmp/alfrd-chatux/before/page-daylight-orbit-360-permission.png), [dock permission](/tmp/alfrd-chatux/before/dock-obsidian-orbit-360-permission.png) |
| 14 | Session navigation offers Needs me but no search field. A single-session fixture cannot measure long-list performance, but the required local search affordance is absent. The “No messages” preview here reflects omitted session-list preview data, not a proven preview regression. | med | dock | [Sessions](/tmp/alfrd-chatux/before/page-obsidian-orbit-1440.png) |
| 15 | Phone Send and Add image have 30 px-high rendered controls rather than 44 px touch targets. Their compact desktop sizing is also applied in the phone composer. | high | composer | [Composer controls](/tmp/alfrd-chatux/before/page-daylight-orbit-360.png), [dock composer controls](/tmp/alfrd-chatux/before/dock-daylight-orbit-360.png); dimensions in metrics.json |
| 16 | Shared visual vocabulary is not yet coherent: request panels, user cards, textarea and small controls use visibly different rounding/density, while message prose is 16 px and task labels 13 px. Chat has no semantic token layer to make these choices consistent across both themes; area-specific application remains with each selector owner. | low | tokens | [Dark overview](/tmp/alfrd-chatux/before/page-obsidian-orbit-1440.png), [light overview](/tmp/alfrd-chatux/before/page-daylight-orbit-1440.png) |

## Additional goals by workstream

These are extra review outcomes for the audited fixture, beyond the base acceptance lists. They do not replace required behavior or create exceptions to touch size, colors, disclosure persistence, payload caps (startup < 204,800 B, lazy < 409,600 B gzip, per `tests/test_studio.py`; user decision 2026-10-27) or ownership. In particular, reduced clutter must not hide timestamps, answers, permission prompts or necessary recovery controls.

### tokens

- **T1 — Make token adoption auditable (16).** Include a compact computed-style report in delivery notes for both themes: shared body/meta/code type, the four shape roles (control/card/composer/pill), and canvas/card/nested/code surface pairs. Identify remaining legacy values so visual mismatches cannot be mistaken for completed token adoption. The report must distinguish decorative borders from actionable boundaries when reporting contrast.
- **T2 — Keep the same hierarchy when the theme changes (16).** Review matched before/after images at identical scroll positions; user tint must remain subordinate to text, nested activity must remain distinguishable from conversation, and primary action emphasis must survive in Daylight. Achieve this with the spec's aliases and palette, without extra shadows on ordinary cards. Tokens owns definitions/shared typography; each area applies them to its owned selectors.

### thread

- **TH1 — Demonstrate recognition across surfaces (1–2).** Use this fixture to compare the same orchestrator and two specialists side by side in page/dock delivery images. A reviewer must be able to match each speaker using visible key/name/role and initials without relying on provider label or color; the timestamp must remain visible with the pointer outside the transcript.
- **TH2 — Keep code recovery local (3).** Include the same JavaScript example plus a deliberately long line in the review fixture. Show that Copy code excludes the fences and UI labels, and that a copy failure leaves selectable code and its fallback message inside the code group without shifting the next speaker out of context. Preserve literal user JSON through the same comparison.

### composer

- **C1 — Reclaim the empty-state gap (7).** Empty upload/warning/error regions must consume no reserved vertical band when they have no message. At 360×980 the attachment row and helper/action row should form one connected composer group with the specified spacing; Send must not appear separated from its input by a blank status area. Restore space naturally when an actual status/error appears.
- **C2 — Review recipient comprehension, not just labels (8–9).** Compare empty, ordinary draft and direct-mention draft images in page/dock. The current recipient and send readiness must be apparent without reading placeholder text, and the short keyboard helper must remain fully readable with accepted attachments present. Keep all effective image-limit/error information available without enlarging the resting helper into a paragraph.
- **C3 — Reserve a stable running action row (10, 15).** Review Send and Stop together with a multi-line draft and an attachment error, at phone width and desktop dock width. Both controls must retain their labels/touch targets without covering draft text or collapsing errors. Host callbacks and cancellation remain dock-owned; this goal assigns only the shared control presentation and refresh behavior to composer.

### activity

- **A1 — Make three-task comparison possible before opening output (4–5).** For this fixture, the first view of active Details must expose all three task identities and done/running/queued states together at 1440×980, using the spec's grid and closed Recorded output. On phone/dock, each island's summary must fit within the visible transcript height; raw output must not need to be traversed to reach the next island. Explicitly opened output must still remain open across updates.
- **A2 — Separate summary priorities under width pressure (5–6).** At 360 px and default dock width, progress/state must wrap with duration/cost onto readable lines instead of clipping. The queued task must visibly name its recorded dependencies, while absent cost remains “— / Not reported.” Verify using both a short request ID and a long supported identifier; no fabricated percentages or zero values.

### dock

- **D1 — Budget header/progress space explicitly (11–12).** In the audited 360×980 fixture, keep page header within two control rows and the dock's Close action on the title/action row, allowing a separate session row if necessary. Long titles may wrap without overlapping controls. Grow dock progress naturally so every Needs me/decision indicator remains fully visible above the transcript. These density goals never override 44 px touch targets or the fixed responsive breakpoints.
- **D2 — Put review scope beside the decision (13).** Add an inline, clearly named action-details disclosure in the permission card that includes the supplied hash and exact supplied arguments. A reviewer must be able to inspect folders/commands and task/request context without opening request Details or scrolling past output. Test two pending permissions with similar subjects and different hashes so context cannot be confused. Do not invent expiry, preselect approval or add a second actionable form.
- **D3 — Prove navigation with realistic list content (14).** Supplement the one-session visual fixture with a local list containing repeated/long titles, missing previews and multiple pending sessions. Review search + Needs me together with the selected session off-screen; the selected conversation and draft must remain evident while results change. Search continues to use only locally available title/ID/preview data.

## Handoff and validation limits

Prioritize high defects that obstruct code reading, task overview, permission review and reachable controls before cosmetic tuning. Use the same fixture, viewport height, theme and transcript positions for after images. Keep earlier answers expanded, the inspector optional, and permission cards outside Details. Output starts closed only when new; the existing preservation rules take precedence over density goals.

This audit establishes visual evidence, DOM dimensions and focused source corroboration. It does **not** certify keyboard-only operation, modal focus/inertness, live-update selection/anchor retention, 200% zoom, physical mobile keyboard behavior, IME, clipboard success, attachment races, approval resolution/replay handling, or payload limits (startup < 204,800 B and lazy < 409,600 B; measured 200,719 B and 363,171 B on 2026-10-27). Those remain mandatory checks in the base spec; passing screenshot capture is not their substitute. No implementation tests were changed or run for this documentation-only task.

Only this goals document is added to the repository by the audit. Evidence and the fixture script live under `/tmp/alfrd-chatux/`. No source or test file was edited, and no commit was made.
