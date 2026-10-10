# Overview Google Sheet interaction specification

Status: O1–O3 implemented; fixture browser verification covers both themes, keyboard and mobile. Live Telegram delivery remains pending.
This specification supersedes the **Overview control (B1)** section of
`STUDIO-UX.md`. Preserve its attach, mapping, filename matching and column
creation flows. This document records the interaction contract.

## Overview and menu

Desktop header order: project title / existing scope chip / flexible space /
Targets (where applicable) / Export / **Google Sheet ▾**. Remove the gsheet
Overview section registration and its card; preserve other plugins' sections.
Use one menu button, rather than a split button with an ambiguous default write.
Grouping it with Export supports recognition of data destinations; progressive
disclosure keeps setup details out of the run-monitoring view.

- Show the button only when the plugin is active in server mode. On All projects,
  retain the button and show “Select a project to use Google Sheet” in its menu;
  do not borrow the selected target's project. A project switch closes the menu.
- Use the same secondary `.btn` styling and caret as Export. Keep the exact
  visible label “Google Sheet”; never replace it with connection status.
- Menu order: **Export to sheet…**, **Sync**, separator, **Connect & validate…**.
- Export to sheet: existing project result preview/backfill flow, with its
  existing explicit write confirmation. Helper: “Review project results before
  writing.” This exports mapped project results, independently of visible-row
  filters, checked rows and pagination. Say so in the preview. Do not imply a
  new spreadsheet or export of the visible Overview grid.
- Sync: opens the connection dialog at **Automatic sync**, with the switch
  focused. Helper: “Choose whether runs update this sheet.” This is the existing
  mapping `enabled` setting; it is not a new two-way immediate sync operation.
  Display “On” or “Off” as supplementary menu text when known.
- Connect & validate: opens the connection dialog at its top. Keep this available
  in every state, including missing credentials and server write restrictions.
- With no readable mapping, Export and Sync are disabled; nearby menu text says
  “Connect a sheet first” or “Fix the connection first.” With server writes
  disabled, Export is disabled with a visible policy explanation. Read-only
  connection inspection and validation remain available.
- Loading: menu says “Checking connection…”; setup remains accessible. Failure:
  show “Couldn’t load the connection” and an enabled Connect & validate item.
  Never present an unknown state as connected. Opening a menu performs no write.

The current plugin API exposes sections and commands, but no toolbar action or
menu API. Add a small generic Overview-action registration and mount mechanism
in `plugin_api.js` / `project_sections.js` (or a dedicated actions module) and
`overview.js`; keep Sheet-specific behavior in the plugin. Do not have the
plugin query or inject into `#ov-title`, which is replaced on live refresh.
Keep stable action nodes and focus across refresh; isolate plugin errors.

## Connection dialog

Reuse `api.dialog` / `plugin_dialogs.js`, the shell modal, close button, scrollable
body and sticky footer. Title **Google Sheet**, with the project name directly
below. Recommended body structure: intro, connection status, sheet facts,
validation result, Automatic sync, secondary management actions. Footer: Close
and the state-specific primary action. Do not create nested modal shells.

| State | Content and actions |
| --- | --- |
| Not connected, credentials available | “Not connected.” “Connect a sheet to export project results and update step status during runs.” Primary **Connect sheet…** opens the existing attach flow. Export and automatic sync unavailable until attached. |
| Not connected, credentials missing | Same status; “Add a service-account key in Settings → Plugins → Google Sheet, then connect your sheet.” Primary **Set up credentials…** opens existing Settings. Never show a credential input or key in this dialog. |
| Connected | “✓ Connected” means a mapping is saved. Helper: “Validate to check sheet access and mapped columns.” Show spreadsheet title, worksheet, row-match column/mode, outbound/inbound rule counts, **Open sheet ↗**, last sync timestamp and result. Primary **Validate connection**. Body actions **Edit mapping…**, **Preview changes…**, **Detach…**. |
| Needs attention | “! Needs attention” plus a safe actionable error. Preserve readable mapping, sheet facts and last sync. Primary **Retry validation** for access/validation errors; **Reload connection** for a failed state read. For an unreadable mapping show file name and existing explicit delete/reconnect recovery. Never automatically delete or replace it. |

Connected with sync disabled remains connected and says **Automatic sync: Off**.
A last-sync failure adds “Last sync failed” beside the recorded timestamp/result;
it does not establish that the mapping or current sheet access is invalid.
No history: “Last sync: Never.” Successful validation: “✓ Sheet access and
mapping checked.” If validation returns warnings, show them below the success
message without claiming every warning is resolved.

Spreadsheet title is a required display datum, not the worksheet name. Current
`state` has neither the spreadsheet title nor effective Settings-default ID;
`sheet_info` returns IDs and tabs only. Developer should expose title from Sheets
metadata and effective ID through the existing read-only action. Load metadata
in the dialog, not on each Overview refresh. Pending title: “Loading sheet name…”;
unavailable title: show a labelled Sheet ID (or “Sheet configured in Settings”)
and a validation warning. Keep the distinct **Worksheet** label. Do not invent
a title or make the entire dialog depend on a successful remote metadata call.

Automatic sync section: labelled native checkbox with `role="switch"`, **Update
this sheet during runs**, helper “Uses this project's saved mapping.” Preserve
current revision checks, rewrite confirmation and rollback on failure. Announce
“Saving sync setting…” / “Automatic sync on” / “Automatic sync off”; disable
duplicate input until complete. Under dry-run show “Dry run: no cells will be
written”; retain preview and use explicit dry-run wording in the write review.

Connect, Edit mapping, Preview and Detach replace the singleton dialog with their
existing flows. After success, refresh toolbar state and return to the connection
dialog when useful. Preserve drafts and confirmation handling. Final closure of
the dialog flow must restore focus to the originating Google Sheet button, even
if a live refresh rebuilt the header. Capture project identity for every request;
ignore stale replies after a project change or dialog disposal.

## Layout and accessibility

- Dialog: normal shell width, target 560px, max `calc(100vw - 32px)`; use wide
  mode only for existing mapping/preview tables. Body padding 20px desktop,
  16px at ≤640px; gap 16px between sections, 8px within a section.
- Sheet facts: semantic `dl`, two columns (label 120px, value `minmax(0, 1fr)`),
  8px row / 12px column gap. Stack at ≤640px. Long titles/IDs wrap anywhere.
- Body text 13px / 1.5; helpers 12px / 1.5; headings 14px semibold. Reuse Studio
  font and theme surfaces; monospace only for IDs, column keys and file names.
- Toolbar actions wrap together without changing order at narrow widths. Use
  existing button metrics, with 44px touch height at ≤640px. Menu width about
  280px, bounded by viewport; rows at least 36px desktop / 44px touch. Wrap
  hints. Do not introduce horizontal page scrolling at 390px or 200% zoom.
- Reuse `--surface`, `--surface-2`, `--text`, `--text-2`, `--border-strong`,
  `--accent-bg`, `--accent-text`, `--r` and shell menu/button states. Hover uses
  the existing accent surface; active uses existing pressed styling; disabled
  uses native disabled semantics plus visible reason. Focus: 2px solid `--text`,
  2px offset. Status uses text and glyph with semantic border color.
- Verify ≥4.5:1 text contrast and ≥3:1 essential control/focus contrast in both
  Daylight Orbit and Obsidian Orbit. Token names alone do not prove compliance.
- Trigger: native button, `aria-haspopup="menu"`, accurate `aria-expanded` and
  `aria-controls`. Hide decorative caret/icons from assistive technology.
  Reuse shell menu keyboard behavior: Enter/Space opens, arrows navigate,
  Escape closes and focuses trigger. A disabled reason must be discoverable.
- Dialog: accessible title, `aria-modal`, focus trap, logical tab order. Initial
  focus on Close for informational connected/error views, primary setup action
  for not connected, or sync switch for Sync entry. Esc closes an idle dialog;
  existing busy and dirty safeguards take precedence with an announced reason.
  Return focus to the trigger; if it no longer exists, use Export as fallback.
- All controls retain visible labels; helpers/errors use `aria-describedby`.
  Loading and validation results use a polite status region; unexpected errors
  use an alert. Avoid repeated announcements from routine live refreshes.

## Verification handoff

Extend `gsheet_project_ui*.test.mjs` for absence of the card and exact menu order,
active-plugin visibility, All projects scope, not-connected/connected/error
states, missing credentials, missing title, failed last sync, write policy,
dry-run, stale requests and preserved confirmations. Browser checks must cover
both themes, 390px and desktop, keyboard menu access, modal Tab cycle, Escape,
return focus after header refresh and after replacing the dialog with attach.
Verify unrelated plugin sections still mount. Use fixture sheet data; no live
Sheets writes are needed to verify the UI.

## Telegram design review (T7/T8)

Source review complete; live Telegram delivery and the Settings browser review
remain unchecked. Keep the headline / project / target / plan / event / summary
hierarchy. Explicit status words alongside icons already make the messages
understandable without emoji or color. Keep terminal plan messages link-free.

Recommended copy-only refinements for the next implementation turn:

- Use warning **⚠️** for `plugin.hook_failed` instead of **🧩**: the puzzle
  communicates a plugin but not the required attention.
- Keep the existing plan/step status icons; add **⏭️** for `skip` if that status
  reaches the summary, rather than an uninformative bullet.
- Change totals label from “step time” to **“total step time”**. This is the sum
  of durations, not wall-clock elapsed run time; keep that distinction visible.
- Unknown duration should read **“time unavailable”** (including when no step
  duration is known in the totals), rather than implying a measured zero.
- Truncation notice: **“✂️ Summary shortened to fit Telegram.”** when summary
  content is omitted. The current count includes blank/heading lines and is not
  a target count. Long summaries need a terminal link-free readable notice.

Settings remains four fields in one Settings fieldset; no new grouping framework
is needed for two preferences. Keep token/chat fields first, digest/mute next,
Save/Test then the existing Background/Bot service section.

| Field | Label | Helper |
| --- | --- | --- |
| `hold_minutes` | Digest every (minutes) | “Empty or 0 sends notifications immediately. Otherwise, collect a digest until the oldest notification reaches this age. Keep the Bot service running for delivery on time.” |
| `mute_when_active` | Mute while I use the Studio | “Discard notifications for 5 minutes after your last Studio activity. Pending digests wait until you are inactive. Bot command replies still arrive.” |

Keep digest placeholder 0 and native number input; minimum 0 and decimal step
must agree with the existing accepted decimal values. The current renderer has
no min/step (native number step defaults to 1), though its form uses novalidate.
Do not silently round saved fractional minutes. Label the mute checkbox with a
full clickable row, 44px touch height. Both helpers need stable IDs linked by
`aria-describedby`; the current renderer emits unassociated `<small>` elements.
Preserve existing form validation and field keys. Inspect blank, 0, 0.5, 15,
negative and invalid values, off/on mute, server policy, helper wrapping in both
themes and small viewport, and confirmation that Save preserves blank-as-zero.

These are design findings, not claims of implemented changes or visual approval.
