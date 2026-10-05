# ALFRD Studio: October 2026 UI batch

Status: design handoff ready. Owner: Product Designer (Codex). Next owner: Senior Developer (Claude). Date: 2026-10-05.

## Scope and decisions

This document specifies every ticket in E1–E4. Implementation order is **E2 → E4 → E1 → E3**: first protect reading continuity and edits, then improve navigation, then simplify the agent-loop overview. This turn changes documentation only. Reuse the existing DOM, CSS, `icon()`, `.btn`, `.chip`, `.card.sec`, `.set-sec-h`, native inputs and native `<details>`; add no framework.

Preserve the user's uncommitted `src/alfrd/web/assets/templates/agent-loop.yaml` change and `src/alfrd/web/js/app.js.rej`. Do not delete, overwrite, commit or push either. The repository is `/mnt/6438D98627D1388F/Intelligence/gh/alfrd`.

Decisions:

- Collapse the desktop sidebar to a **64 px icon rail**. Keeping destinations visible supports recognition and spatial memory; hiding navigation would require another action for every destination change.
- Settings uses **four segmented tabs**, always in the same order: Projects, Preferences, Browser data, Server. One panel is visible at a time.
- **Omit Upload history in this batch**, including its empty slot. `history.py`/`studio_history.py` manage file revisions; `agent_loop.py` manages handoffs. There is no run-history import contract in those modules or `studio_plans.py`. Creating plan rows is not importing historical execution records. The brief explicitly allows removing this control. Export and past-run access remain required.
- Do not confirm ordinary task editor closure: retain the draft automatically. Confirmation is reserved for explicitly discarding a draft.
- F1 is a placeholder backlog ticket only, with no token UI or implementation in this batch.

## Shared visual and accessibility contract

Keep Obsidian Orbit: `--bg`, `--surface`, `--surface-2`, `--subtle`; text uses `--text`, `--text-2`, `--text-3`. Use `--accent-text`/`--accent-bg`/`--accent-border` for selected controls, `--warn-*` for drafts and `--fail-*` for errors. Small white primary labels use the existing `--accent-control` token. Do not invent new colors. Preserve Inter/system sans, JetBrains Mono for paths and IDs, 14/20 body, 13 px controls, 12/16 helper text, 20/28 modal headings. Use tabular numerals for KPIs and counts.

Spacing follows `--s-1` through `--s-6`: 8 px between controls, 12 px between related rows, 16 px card/modal padding, 24 px between unrelated groups; corners use `--r`. Hover uses `--subtle` and `--border-strong`; focus uses a visible 2 px outline with 2 px offset. Selected state includes shape/border plus text or `aria-selected`, never color alone. Disabled controls retain a nearby explanation where the reason is material. Aim for 44 px hit areas for new icon controls and narrow/touch layouts; preserve established 34 px desktop text buttons where space requires it, with adequate separation.

New SVGs inherit `currentColor`, use `.ic`, are decorative (`aria-hidden="true"`); their button/link owns the accessible name. Every field has a label. Errors attach through `aria-describedby` and `aria-invalid`; submission errors use `role="alert"`. Nonurgent completion uses `role="status"`. Do not announce the entire UI on every poll. Preserve focus/caret on background updates. Check text contrast at 4.5:1, large text at 3:1, and focus/essential control boundaries at 3:1 against adjacent surfaces; existing faint decorative borders alone are insufficient for new state indicators.

Modals retain existing focus trapping, labelled dialog and return-focus behavior. Native buttons support Enter/Space. Native summaries support keyboard toggling; do not place extra interactive controls inside a summary unless existing click behavior is preserved. Respect `prefers-reduced-motion`; none of these features needs an animation. At 200% zoom the header and toolbars wrap, and only genuinely wide tables scroll horizontally.

## E1 — Navigation and shell

### T1.1 — Create a server folder

**Location:** `js/components/folder_browser.js`; API adapter in `js/data/server.js`; the small endpoint addition in `gui/studio.py`.

**Flow:** Browse → navigate to parent → New folder → type name → Create folder → remain at the parent with refreshed listing → open the new folder or choose it. Creation itself does not connect a project or create `alfrd.yaml`.

**Layout:** Keep parent button, breadcrumb, hidden-files checkbox and close button. Add `.btn.sm` with `icon("plus")` and **New folder** before the hidden checkbox. Breadcrumb has `min-width:0; flex:1` and wraps or scrolls within its own width; the toolbar uses `flex-wrap:wrap`. Show the button only when both `listing.writable === true` and `session.mutations_enabled === true`; an unknown writability value is treated as unavailable. Browsing and Use this folder can remain available when creation is unavailable.

Click inserts one inline `.field` under `.fsb-bar`, before `.fsb-list`: visible **Folder name** label; `.input` with placeholder **e.g. analysis**; `.btn.primary` **Create folder** and `.btn` **Cancel** in an 8 px gap row. At narrow widths input occupies the full row and actions wrap beneath it. Focus the input. Supporting copy: **Create inside [current server path]** (path wraps in mono). The surrounding browser remains a region, not a second modal. Never nest a `<form>` inside the create-project form: use a grouped row and `type="button"` controls, handling Enter only in this input.

**States and copy:**

| State | Appearance and behavior |
| --- | --- |
| Empty | Create disabled; no error before interaction. On attempted submission: “Enter a folder name.” |
| Invalid name | Reject `/`, empty/whitespace-only, `.` and `..`, NUL and names that are not a single directory component. Copy: “Use a folder name, without / or a parent-folder reference.” Do not silently alter the entered name. Backend decides platform-specific restrictions. |
| Duplicate | Match visible listing names for early feedback: “A folder named ‘[name]’ already exists here.” Keep input and focus. Hidden/truncated listings and existing files mean server validation is authoritative. |
| Submitting | Label “Creating…”; disable input, submit, cancel and path navigation; set group `aria-busy`. Do not allow duplicate requests. |
| Success | Remove input row; refresh current parent without losing scroll; announce “Folder ‘[name]’ created.” Focus its Open button. If hidden/truncated filtering excludes it, announce creation and offer “Open created folder” using returned path. |
| 403 permissions | “You don’t have permission to create a folder here.” Keep name and show inline; refresh capability so stale New folder disappears. |
| 403 session/CSRF | “Folder creation isn’t allowed in this session. Reload Studio and try again.” Preserve name; no automatic retry. |
| 409 exists | Same duplicate message, preserve name; refresh listing. |
| Other failure | “Couldn’t create the folder. [server message]” plus retry through Create folder; no success toast. |

Esc cancels the inline entry first and returns focus to New folder; a subsequent Esc closes the browser. Enter submits the entry and must not submit the outer New project form. Preserve text on failure. If capability changes while the entry is open, keep the name readable, disable submit and explain why.

**Backend interface requirement (for Developer, no infrastructure authored here):** `GET /api/studio/fs/list` adds Boolean `writable` from `os.access(folder, os.W_OK)`. `POST /api/studio/fs/mkdir` accepts `{parent, name}`, uses the same loopback + CSRF guard and mutation policy as existing operations, and creates exactly one child folder. Validate the name server-side, resolve the parent and enforce a single child component; do not create missing ancestors. Expected response 201 `{path, name, parent}`; invalid payload/name 400, missing parent 404, duplicate file/folder 409, permission/session/CSRF denial 403. Writability is a hint; actual mkdir may still fail. Keep error shape compatible with `_json_error`. Cover permission denied, existing file/folder, CSRF and successful creation in pytest, plus traversal/empty-name rejection.

### T1.2 — Server picker everywhere

**Locations:** `ctx.browseFolder` in `app.js`, `#create-browse`/`#create-folders` in `agent_dialog.js`, Import → Connect. The current create-project path already delegates to `ctx.browseFolder`, which mounts the server browser; consolidate and verify rather than introducing a second picker.

In server mode every folder Browse action uses `mountFolderBrowser` and `server.listFolders`. Never select the viewer's local machine as a server path, including when Studio is accessed through a tunnel. Use the current typed path as `start`, falling back to the existing server default when blank. An invalid path shows its error in the mounted picker with a way back to the last valid parent/default. Add a short helper **Folders on the ALFRD server** above the browser to make location clear.

Create-project picker is **selection mode**: folder rows and New folder remain; hide Connect, Connect all projects and Connect this folder actions. Its footer has secondary **Cancel** and primary **Use this folder**, disabled until a valid listing exists. Use fills the Folder input with the server's canonical current path, emits its normal input/change notification, closes/destroys the mounted picker and returns focus to Folder. It does not create the project; **Create project** remains the final action. Browse again restores the selected path. Import → Connect keeps its existing connection controls and Use this folder semantics.

Only browser-only mode may use the existing browser directory input/`webkitdirectory` fallback. Label that picker **Folder on this computer**. If browser selection cannot supply an absolute server path, do not fabricate one. Hide/disable server-only project creation according to current capabilities. Server API failure stays inline; it must never fall back to a local picker. Destroy listeners on picker/dialog closure and repeated Browse mounting.

### T1.3 — Collapsible sidebar

**Locations:** shell markup generated in `app.js`, `.app`/`.rail`/`.console` in `studio.css`.

Add `.rail-head` as the first child of Workspace navigation. It contains **Workspace** (muted 12 px label) and a toggle with `aria-controls` targeting the destination list and `aria-expanded="true|false"`. Use a simple panel/sidebar outline icon (add `sidebar` to `icon()` if absent); label/title is **Collapse sidebar (Alt+Shift+S)** or **Expand sidebar (Alt+Shift+S)**. Expose `aria-keyshortcuts="Alt+Shift+S"`; register in existing command handling/palette, ignore editable fields, composition and modal text entry. The button always provides an alternative if the shortcut is intercepted by the platform.

Expanded keeps the existing 240 px width and navigation styling. Collapsed is 64 px: centered toggle at the top, destination icon buttons 44×44, 22 px icons and 6–8 px vertical gap. Destination spans are visually hidden while their accessible names remain; each link gets its full destination title/tooltip on hover and focus. Preserve active background and `aria-current="page"`. Do not hide links or remove them from tab order. The Workspace label may be visually hidden in collapsed mode. Focus stays on the toggle when toggling.

Set `.app[data-sidebar="collapsed"]` (or equivalent stable shell class) and one shared `--rail` value controlling grid width and console left edge. Ensure `.console`, banner and main align with both states; avoid a separate magic console offset. Store Boolean under the existing UI storage namespace (`sidebarCollapsed`, default false); restore before initial shell paint. Guard unavailable/malformed localStorage and continue in memory.

At ≤767 px preserve the existing bottom navigation and labels; hide the desktop toggle and do not consume the shortcut there. Desktop preference remains stored for return to a larger viewport. No width animation is required. Test reload, active destination, open console, 200% zoom and mobile breakpoint.

### T1.4 — Settings sections

**Location:** `openSettings()` and `drawProjects()` in `app.js`; `.set-*` in `studio.css`.

Modal header becomes **Studio settings** with existing Close. Below header use a flex `.seg.set-tabs` tablist with four equal tabs, icon + label: Projects (`database`), Preferences (`gear`), Browser data (`reset`), Server (`power`). Width 100%, 8 px gaps, ≥40 px tall; at narrow width tabs wrap into two rows without changing order. The modal is constrained to viewport height (`max-height:calc(100dvh - 32px)`) and uses flex column with `min-height:0`. Tabs and header remain outside the scrolling content. One `.set-panel` scroll container fills the rest.

Native button tabs use `role="tab"`, `aria-selected`, `aria-controls`, roving tabindex; Left/Right cycle, Home/End jump, Enter/Space select (manual activation). Each section is a labelled `role="tabpanel"`; inactive sections use `hidden` and have no focusable descendants exposed. Selected tab uses `.on`, accent text and visible border; hover/focus follow shared contract. Clicking switches sections without closing the dialog or resetting controls. Store last active section with existing UI persistence (`settingsSection`); default Projects, malformed keys fall back. Retain all four tabs in browser-only mode: Projects panel says **Project management is available when connected to an ALFRD server.**; Server says **Browser-only mode. No ALFRD server is connected.** No disabled tab with unexplained missing content.

**Projects:** reuse `.set-sec-h` title and existing New project, visibility and Forget actions. Place search below heading, above list: label **Filter projects**, `type="search"`, placeholder **Search name or path…**, optional clear button labelled **Clear project filter**. Case-insensitive substring matching over full display name and full path, including hidden projects. Display **[shown] of [total] projects** in a polite status only after user input. Search does not alter visibility or global project selection. Keep search and caret mounted during list refresh; filter client-side from the current project listing. List scrolls within this panel (`min-height:0; overflow-y:auto`); 100+ rows never push other Settings sections into view. Names wrap, paths truncate visually with full value available on focus/tooltip; action clusters wrap rather than compressing text to zero.

States: **Loading projects…**; no projects **No connected projects yet.** with New project when authorized; no match **No projects match ‘[query]’.** with Clear filter; fetch error **Couldn’t load projects.** with Retry. Keep query during retry and visibility mutations. Existing destructive semantics remain; do not redesign Forget in this batch.

**Preferences:** only Live updates and Rows per page, existing handlers/copy and control states. **Browser data:** existing usage total and Reset/Clear actions; copy explicitly includes **unsaved task drafts**. Reset view state clears sidebar/tab/disclosure preferences, not draft text. Clear all saved data clears drafts too; its destructive confirmation must say that unsaved task drafts and notes will be removed. **Server:** move Mode/runtime/change-permission facts here with existing Quit control and explanation; Data-source fact can also live here. No duplicated fact strip outside the tabs. Keep existing session guards.

## E2 — Live-run stability

### T2.1 — Preserve metadata disclosures and reading position

**Locations:** every `<details>` emitted by `panels.js` (including text at line 46, files at line 76 and outer panel cards), and `metadata_avica.js` (section cards, metadata files and input files). Use existing `ui.open` section behavior as the pattern; do not replace native details.

Use a stable disclosure identity containing project identifier, workflow/view, panel identity, instance/workdir identity and canonical file `rel` (fallback name scoped to that instance). Outer cards use a section identity without a file. Include target/code where a project has several metadata contexts. Never use array position alone, status, bytes or modification time as a key: background changes must not change disclosure identity. Render a stable `data-detail-key` and restore explicit true **and false** values. Previously unvisited items use current defaults; reading one file must not open a same-named file elsewhere.

Save on native toggle with a capture listener or direct listener (`toggle` does not bubble normally), in the metadata UI state map and existing UI storage. Snapshot actual DOM states before replacement as protection against pending toggle events. Restore state before exposing the replacement DOM; restoration-generated events must not replace user state with defaults. Do not discard keys on a poll or temporary file absence. A new metadata response must not forcibly fold any section. This affects AVICA disclosure stability too, while preserving its original content/defaults.

Before re-render capture scrollTop/scrollLeft of the actual metadata scroll container and any inner code/table viewport. Restore after layout, clamped to available extent, only for the same context and current render generation. Use a visible file anchor/offset where feasible to avoid a jump when content above grows. Preserve focused summary by identity without scrollIntoView; never steal focus from another control. Explicit project/file navigation can use its usual new-context scroll. No extra “live update” banner or forced reopening.

Check Task → task.md opened for ≥60 seconds with live events, then explicitly close it and check it remains closed for ≥60 seconds. Also test nested AVICA metadata/input disclosures, identical filenames in separate instances, asynchronous response ordering and horizontal scroll in a code block. Acceptance is indefinite stability; 60 seconds is the minimum reproducible test.

### T2.2 — Loop-results KPIs fit their available width

**Location:** `.loop-results` rules in `studio.css`, including overrides of `.kpis.small` and media queries.

KPIs are a full-width block above the results table, in normal document flow, with 12 px gap and ≥16 px separation before `.loop-result-table`. Scope all changes to `.loop-results` so non-loop KPI layout stays intact. Use `grid-template-columns:repeat(auto-fit,minmax(140px,1fr))`, `min-width:0`, `max-width:100%`; for a container <140 px use `minmax(min(140px,100%),1fr)`. KPI children get `min-width:0; max-width:100%`. Values use tabular numerals, `font-size:clamp(20px,2vw,26px)`, `line-height:1.3`, `white-space:normal; overflow-wrap:anywhere`; labels/helper text wrap. Preserve full numeric value: no ellipsis or hover-only number. Cards grow vertically as necessary; no fixed height, absolute positioning or negative margins.

Table wrapper has `min-width:0; max-width:100%; overflow-x:auto`, independently of KPIs. Keep its table's existing 620 px minimum width. Consolidate competing scoped rules so `.kpis.small` and the ≤1280 rule cannot force four columns. Test 1024, 1280, 1440 and 1920 px with sidebar expanded/collapsed, the available results card narrower than viewport, long numbers and 200% zoom. No intersection between KPI and table bounding boxes, and no page-level horizontal overflow from either.

### T2.3 — Reach the last grid column during a live run

**Locations:** `.grid-scroll`, `.grid-card`, `.ov-body`, schedule/grid wrappers in `studio.css`; grid rendering owners in `overview.js`/`plans.js` and existing `data/run_grid.js` where applicable.

Use one base `.grid-scroll`: `min-width:0; min-height:0; max-width:100%; overflow:auto`. Keep the schedule-only `max-height:46vh` under `.wf-sched .grid-scroll` (or a schedule wrapper class); remove the duplicate global rule at line 726. Grid tables use `width:max-content; min-width:100%`; keep all five step columns and their log controls inside the same scrollable area. Do not squeeze columns, hide last actions or make the entire page scroll sideways.

Give the parent flex/grid chain `min-width:0` and `min-height:0`; use `minmax(0,1fr)` tracks. Inspect the live DOM with computed overflow and widths. `.grid-card` currently has `overflow:hidden`, but that alone does not prove it is the culprit; move horizontal scrolling to a bounded child and remove/replace a clipping ancestor only where it makes the child/action unreachable. Preserve intentional root `main` viewport containment. A CSS-only guessed removal of all overflow rules is insufficient validation.

Keep existing sticky selection/name columns where present. For a simple plan grid make the first name column sticky at left 0, opaque `--surface` body / `--subtle` header background and subtle trailing border. Use z-index body 2, header 3, corner 4; never an overlay wide enough to cover the last step. A ≤220 px bounded sticky name avoids swallowing the viewport. Scroll region is focusable with visible focus, `role="region"`, label **Targets by workflow steps** (or **Runs by workflow steps** for the loop view). When overflow exists, a short helper **Scroll sideways to see all steps.** may appear. Native scrollbar/trackpad and keyboard navigation must work.

Preserve grid scrollLeft/scrollTop across polling, using stable project/run/grid keys. If a focused log button is replaced, restore focus to the same action without jumping back to column one. In a live 2×5 run, scroll to maximum right, wait through several polls, click the fifth-step log button and verify its correct log opens. Test both overview and schedule grids if both expose the matrix, mouse/trackpad and keyboard, with drawer/console open.

### T2.4 — Consistent log icon

**Locations:** add `log` in `js/utils/dom.js`; replace log meanings in `jobs_tray.js`, `logview.js`, `overview.js`, `plans.js` and audit remaining terminal references under `web`.

Use the current icon SVG wrapper/viewBox and stroke conventions. Inside a 24×24 viewBox, draw four rounded horizontal strokes at y=5,10,15,20, starting x=4 and ending x=20,16,20,13 respectively. This reads as a document/list of log entries at 16 px, without a command prompt glyph. Keep `terminal` for actual terminals. Log actions use `icon("log")`; accessible labels remain **View logs**, **Open run log**, **View target logs**, or contextual **Open log for [step]**. No color/style changes to existing affordances. Validate every known call site and grep all terminal references; a remaining terminal use must represent a terminal.

## E3 — Agent-loop overview and run history

### Workflow scoping and shared layout

Use explicit agent-loop metadata, matching the existing detection in `results.js`: `tree.defs.template === "agent-loop"`, or a repeat workflow with handoff steps. Apply per workflow/project, not simply “no targets”: `targetlessProjects()` also handles unknown types. Keep generic targetless behavior and non-loop workflows. For All projects with mixed types, show loop projects in their run sections and retain AVICA target controls only for relevant non-loop sections; exclude loop rows from target filters. Saved AVICA code/target filters must not invisibly filter loop history. Preserve those saved values for returning to AVICA.

Loop overview wireframe:

```text
Project overview     [Runtime · N runs]                         [Export ▾]
[Search runs…                        ] [Status ▾] [Run ▾]
Progress sheet / run history (full available width)
Run           ALFRD project*  Status  Progress  Runtime  Open run  View results  step columns…
[run label]                  [badge] [bar]      [time]   [action]  [action]      …
```

`*` Project column is useful in All projects and can be omitted for a single project. Actions appear in the existing Project code then MS storage column positions. Keep topbar global controls; this change concerns the overview's target-specific controls and export semantics. If a global Export also offers overview exports, make its agent-loop route use the same history exporter so it cannot emit a target-shaped file.

### T3.1 — Remove inapplicable fields and filters

In loop-only overview remove Target, FILENAMES, PROJECT_CODE/project-code chips and filter (`#ov-code`), MS storage path, path-copy, target-upload menu, code grouping, no-work-folder preset and target-specific drawer content. Also remove them from column chooser, search placeholder, filter badges, exports and empty/setup guidance. Use **Search runs…**, **[N] runs**, **Run details** and **Runs by workflow steps**; retain ALFRD project names, which are different from Project code. Internal runtime identifiers can remain in data structures; the user-facing table must be run-oriented. Reset/ignore irrelevant filters only within loop filtering, without erasing AVICA preferences.

Do not retain “Add targets” in Get started. A new loop project's empty card says **No runs yet.** / **Edit task.md, review your agents, then start a run in Workflow.** with **Edit task** (existing Task editor) and **Open Workflow** actions. No FILENAMES or MS setup step is required to start a loop.

### T3.2 — Reclaim Last run space

Remove the entire Latest run/Last run card in `renderHome()` for agent-loop (current heading is “Latest run”). Avoid an empty placeholder or reserved margin. After setup is complete, the filters and progress sheet start directly under the overview header. Hide the target drawer in this branch and set the content track to `minmax(0,1fr)` so run history uses its width. Active and latest runs remain discoverable in the run selector and first history row. Preserve existing home/drawer behavior for other workflow types.

### T3.3 — Export run history; omit upload

Remove loop Targets upload and do not replace it with Upload history, for the capability reason above. Export menu includes **Export run history · CSV** and **Export run history · JSON**, using `download`. Use existing server `planStatus(project)` and its `plans` list (`scheduler.list_plans`, newest-created first); filter to loop plans. Historical records come from plans, never config-file history or target datasets. No new runtime import endpoint, database or backend export endpoint is needed.

Scope is **all known loop runs for the selected project**, or all loop projects in All projects; it is not restricted to visible page, search or status filters. Put helper text in the menu/near trigger: **All runs in the selected project scope.** Export a frozen snapshot captured when clicked, so polls do not mix versions. Disable repeated clicks while gathering with **Preparing history…**; success announces **Exported [N] runs.** Failure is inline or toast **Couldn’t export run history. [reason]**, with no silently partial download. With zero runs disable both entries and explain **No run history to export.** Read-only server sessions can export.

Proposed CSV schema v1, one record per plan: `project_id,project_name,run_id,run_label,status,created_at,started_at,finished_at,runtime_seconds,iterations,completed_turns,total_turns`. IDs and project scope are mandatory; display label uses available task/title data or **Run [id]**, never an invented title. Map source timestamps (`created` etc.) into UTC ISO 8601; missing values are empty cells, not zero. Do not infer finished time from last poll or present live elapsed time as final runtime. Counts/duration unavailable from a plan summary stay empty unless loaded correctly; derive loop aggregates from authoritative loop data, not the truncated `units[-200:]` slice.

JSON envelope: `{schema_version:1,exported_at:<UTC ISO>,scope:{project_ids:[...]},runs:[...]}`. Each run uses the CSV field names with numbers/nulls rather than strings/empty cells. Optional `steps` summaries may be included only if loaded consistently for every exported run; no prompts, task text, log bodies, workspace paths, tokens or cost in this batch. Filename `alfrd-run-history-[project-or-all]-[YYYYMMDD].csv|json`, sanitize the display component. Reuse CSV/download helpers, escape embedded quotes/newlines and prevent spreadsheet formula execution for textual cells. JSON retains original textual values. This is a portable report, not a restorable execution archive; do not promise re-import.

### T3.4 — Progress sheet lists past runs

Extend existing `overview.js`/`data/run_grid.js` run presentation rather than turning historical plans into fake targets. Default history selection is **All runs**, newest first; current/active run is included once with a status badge. One summary row per plan. Preserve detailed iteration/turn grids beneath the selected run or via Open run; repeated iterations are not extra “past runs.” Reuse existing search, status and run filter components and rows-per-page preference. Fetch only needed details, retain stable row IDs `[project,run]`, and avoid scroll/focus resets during refresh.

Column contract:

| Column | Presentation |
| --- | --- |
| Run (replaces Target name) | Flexible, min 180 px, preferred 260 px; task/title if available, otherwise Run [id]. Name uses two-line clamp (`display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; overflow:hidden; overflow-wrap:anywhere; white-space:normal`). ID in small mono if label differs. Full label in DOM/accessibility tree and hover/focus tooltip; do not depend solely on native title for keyboard/touch access. |
| ALFRD project | Existing name/chip, for cross-project context. |
| Open run (replaces Project code) | `.btn.sm` with `play`, visible “Open run”; accessible name “Open run [id]”. Explicitly select this project and this plan ID, then open its existing run/schedule view. It must not select the newest plan by default. |
| View results (replaces MS storage) | `.btn.sm` with an existing results/chart icon, visible “View results”; accessible name “View results for run [id]”. Select this exact project and plan ID in Results before navigation. |
| Status / Progress / Runtime | Reuse badge, progress and tabular-duration patterns; count turns for loops and label accordingly. Unknown values show em dash rather than misleading zeros. |
| Step columns | Reuse existing run grid cell and log behaviors, with horizontal scrolling from T2.3. |

Row actions are independently focusable, stop row-selection propagation and remain usable during polls. Historical step columns must reflect that run's frozen definition; if histories have different steps, use per-run expansion/selection instead of labelling all history with today's workflow. No target-name or path detail drawer is opened by loop rows.

Loading: **Loading run history…** in the sheet; error: **Couldn’t load run history.** + Retry; empty filtered: **No runs match these filters.** + Clear filters. When a deleted/unavailable run is opened: **This run is no longer available. Refresh run history.** Failed runs still have View results when records exist. No results yet: disable action with adjacent/helper **No results yet**; active run with partial results may open them. Disconnected/browser-only project: show available cached history, explain **Connect to the ALFRD server to open this run.**; disable server navigation, permit export only of an explicitly labelled cached snapshot if available. Never silently open another run.

## E4 — task.md drafts

### T4.1 — Recover edited text after any dialog closure

**Location:** `openTask()` in `agent_dialog.js`, existing modal `beforeclose` path and UI storage helpers. This is a local browser draft, separate from task.md and its version history.

**Flow:** Open Task → edit → draft retained on every input → close by backdrop, Esc or Close → reopen same project/run → restored text + banner → continue editing → Save task → successful server save clears draft. Nothing writes to task.md or initial handoff before Save.

Key drafts by server identity + stable project identifier + run ID captured when the editor opens; use explicit `next` context where no run exists. Do not key by display name or current global selection after the dialog has opened. `openTask(ctx,project)` currently has no run argument: Developer should supply/resolve the intended run context at entry and freeze it for the dialog lifetime. If there is no selected run, next is the deterministic context. A draft for another run must not silently replace this one; if next becomes a real run, retain it under next unless the user explicitly opens that draft context.

Store in memory immediately on input, and localStorage through the existing namespace, with short debounced writes (about 250 ms) plus synchronous final flush on `beforeclose` and `pagehide`. Include schema version, project/run identity, text, original `base_hash`, baseline text or equivalent comparison data, updated time and the “Use this goal for the next run” checkbox value. Compare exact editor text including empty strings: an intentional blank task is a draft. If edits return exactly to the baseline, clear the draft. Polling and project scans must never overwrite an open editor. Catch malformed/quota/unavailable storage; memory recovery must still work after dialog closure in that tab.

Place `.callout.warn` directly above `#task-text`, compact 8–12 px padding, draft/status icon `file` or `info`, **Unsaved draft** and inline **Discard** (`.link-btn`). Supporting text: **Kept in this browser. Save task to update task.md.** Banner appears when editing and when a draft is restored. It uses `role="status"` once on restoration, not on each keystroke. Keep textarea dimensions and task-seed explanation. Footer primary stays **Save task**; Close remains non-destructive and may have helper **Closing keeps your draft.**

**Discard:** confirm **Discard this unsaved task draft?** with explicit **Discard draft** / **Keep editing** using existing confirmation convention. On discard remove only this key, reload current server task text/hash and checkbox default, hide banner, focus textarea. If reload fails keep the editor text/draft and show error; do not delete the only recoverable text first. Ordinary closure never prompts.

**Save states:** submitting disables Save and preferably textarea/seed to prevent races, label **Saving…**. Success updates baseline from the actual saved response, removes this draft from memory and storage, hides banner, announces existing **Saved. Start a new run for this task.** If editing remains enabled instead, only clear the exact submitted revision and retain newer input. Error retains draft and text. 409 shows **task.md changed since this draft started. Your draft is kept. Review the current file before saving.** Provide **View current file** as a read-only comparison using existing modal/file viewer; never silently adopt the new hash to bypass conflict protection. Reopening restores the draft with its original baseline; it must not bless stale edits with the latest hash. Explicit Discard can load the newest version.

Storage failure banner: **Draft kept for this tab only. Browser storage is unavailable; copy your text before closing this tab.** Offer existing Copy action if feasible. It is acceptable to lose memory on tab closure only when persistence is unavailable and this limitation is displayed; normal storage must restore after reload. No automatic expiry or eviction of unsaved drafts. Clear all saved data in Settings intentionally removes them with explicit warning; Reset view state does not.

Tests cover backdrop, Esc and Close separately, reload recovery, project/run isolation, blank draft, reverted edits, successful save clearing, failed save/409 retaining, storage failure and typing during any asynchronous save. Verify on-disk task and handoff bytes stay unchanged until Save.

## F1 — Backlog placeholder only

**Ticket:** Agent-loop token usage by turn and agent. Future data: input/output/cached tokens and cost, potentially reuse `usage_view.js`. Needs authoritative provider reporting, missing-data handling and pricing semantics. No panel, controls, cost estimates, schema expansion or implementation in this batch.

## Developer handoff and verification

1. Implement E2 disclosure/scroll/KPI fixes and log icon. Reuse existing metadata UI state owners; audit `metadata.js` as needed to capture DOM state around rendering.
2. Implement E4 draft persistence before changing surrounding modal flows. Protect existing hash conflicts.
3. Implement E1: mkdir endpoint + guarded tests, shared selection/connect picker modes, sidebar persistence and Settings tabs/search.
4. Implement E3 with explicit loop detection, run-ID-aware navigation and CSV/JSON history export; omit upload. Inspect `data/run_grid.js`, `loop_results.js` and existing storage/download utilities before extending them. Do not broaden backend scope beyond the requested mkdir/list addition.
5. Run the repository's existing Python and Studio JS checks plus targeted new coverage. Perform manual `alfrd serve` validation through a tunnel; report any unavailable tunnel/environment as a validation limitation, never as a pass. Use fixtures for a live 2×5 matrix and ≥2 distinct historical loop plans, including a failed run and differing step definitions. Keep all supplied repository shell commands prefixed with `rtk`.

Acceptance checklist (implementation, not claimed complete by this design turn):

- [ ] Server-mode create-project and Import Browse list server folders through a tunnel; selection fills the correct canonical path. No local fallback on API failure.
- [ ] New folder is shown only for writable + mutation-enabled listings, creates one server child, validates names and shows errors inline. mkdir success, permission, exists and CSRF tests pass.
- [ ] Sidebar collapse/expand works, survives reload, preserves keyboard navigation and aligns console; mobile bottom navigation stays intact.
- [ ] Settings has four selectable sections, one visible at a time, remembered section and usable filter/list with 100+ projects.
- [ ] Every opened/closed metadata disclosure survives indefinite live refresh; demonstrate ≥60 seconds, context isolation and retained reading position.
- [ ] Loop KPIs do not overlap the table at 1024–1920 px, with long values, sidebar states and 200% zoom.
- [ ] Live 2×5 matrix reaches the last column, remains there across updates and its fifth-step log action opens the correct log.
- [ ] Every log affordance uses the line-based log icon; any remaining terminal icon has genuine terminal meaning.
- [ ] Loop overview contains none of Target, FILENAMES, PROJECT_CODE, MS path or Latest/Last run block; no target upload; history CSV/JSON export exists and includes past runs in project scope.
- [ ] Progress sheet has historical Run rows with two-line labels/full tooltip and exact-run View results/Open run actions; no latest-run substitution.
- [ ] Task draft survives backdrop, Esc, Close and normal reload, is isolated by project/run, writes no file until Save and clears only after successful save or explicit discard.
- [ ] Existing tests pass and AVICA/non-loop presentation and behavior remain intact, apart from requested shared shell/log/disclosure improvements.

## Open risks and boundaries

No design blocker. History upload is intentionally omitted under the approved fallback. Existing `ctx.openRun(project)` and unparameterized `#/results` affordances may default to the latest run; Developer must extend frontend selection to carry the row's explicit plan ID using the existing `?id=` reads. History summary counts may require per-plan reads; do not fabricate values or export truncated-unit totals. Actual live overflow ancestry and tunnel behavior require runtime inspection. This document does not claim code tests, visual browser QA or tunnel validation have been performed.
