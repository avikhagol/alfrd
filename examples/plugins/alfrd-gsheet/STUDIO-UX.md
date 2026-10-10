# Google Sheet Studio UX

This specification supports B1–B4 of the plugins/UI task. B1 and B2 presentation
changes are implemented in `web/project_ui.js` and `web/index.css`. B3 filename matching and B4 explicit column creation are implemented.

## Overview control (B1)

Superseded for the current Overview task by `OVERVIEW-UX.md`: replace this
standalone section with a Google Sheet menu beside Export and a connection
dialog. The B2–B4 form and data behavior below remain applicable.

Use a quiet status row within the existing Google Sheet project section. Setup
is a secondary action on a page whose main purpose is understanding project runs;
an inline action avoids competing with the primary run controls. Keep status and
action adjacent so the next step is evident without reading a paragraph.

| State | Visible status | Action and explanation |
| --- | --- | --- |
| No mapping, credentials present | Not connected | Connect sheet opens the existing attach dialog. |
| No mapping, no credentials | Not connected | Set up credentials opens Settings → Plugins. Helper explains that a service-account key is required. |
| Mapping readable | ✓ Connected | Keep Open sheet, tab/key/rule facts, the sync switch, and existing mapping/validation/preview actions. Connected means a mapping is saved; Validate checks current access. |
| Mapping unreadable | ! Needs attention | Show the escaped error, Reload, and the existing delete/reconnect recovery path. |
| Last sync failed | Connected plus Last sync: … (error) | Preserve the recorded result. Do not imply a successful live connection just because a mapping exists. A future error chip may supplement, but must not replace, the timestamp and explanation. |
| Server disallows writes | Preserve state | Disable Connect sheet, keep its policy explanation in the title, and retain read-only Validate/Preview. |

`.gs-connection`: wrapping flex row, center aligned, 8px vertical / 12px horizontal
gap. Status chips use 2px 7px padding and theme radius. The inline button has a
30px minimum height, 4px 6px padding, 13px semibold text, and an underline. It uses
`--accent-text`, with `--accent-bg` on hover and `--subtle` while active. Keyboard
focus gets a solid 2px `--text` outline with 2px offset. Disabled text uses
`--muted` with no underline and the native disabled attribute. At ≤640px, its
minimum height becomes 44px. Use a button, because this action opens a dialog.

Status words and glyphs carry meaning independently of color. Connected/error
borders use `--ok-text`/`--fail-text`; chip text stays `--text` on `--surface-2`.

## Connect and mapping form (B2)

Retain the existing host dialog, close/unsaved-change handling, protected actions,
and sticky action footer. Do not introduce a second modal framework.

Connect flow:

1. **Spreadsheet:** URL or ID; helper asks the user to share with the
   service-account email in Settings → Plugins using Editor access. Keep the
   optional Settings-default checkbox and Load action.
2. **Tab:** worksheet and header row, each with an explicit label and helper.
3. **Columns:** target-name column and optional project-code column; explain
   matching and disambiguation. Keep sample values and the current match count
   directly below the controls. The footer Attach action remains disabled until
   the existing readiness check succeeds.
4. After attachment, open the mapping editor as today. Distinguish saving mapping
   settings from writing run data. Preview is the next safe way to inspect writes.

The step indicator is an ordered list of small surface tokens. The current step
uses `aria-current="step"`, a stronger border, bold text, and an underline. It is
an indicator, not a set of navigation buttons. Sections remain visible as the
existing flow reveals them, allowing the user to check earlier choices.

Mapping editor:

- **Outbound (Studio → sheet):** helper explains choosing values/destinations and
  using Preview; retain rule columns, reordering, duplicate/delete and Add actions.
- **Inbound (sheet → plan):** preserve the existing restrictions next to the table.
- **Sync options:** missing-row behavior, optional read range, verify-before-write
  and sync enabled. Separate layout from rule editing to reduce scanning effort.
- Keep validation messages beside the control and in the linked summary. Keep the
  comments-removal notice and revision-conflict recovery.

Presentation values:

| Element | Specification |
| --- | --- |
| Form body | Studio font; 13px body/labels, 12px helper text; line-height 1.5; theme text/surfaces. |
| Section | Native fieldset/legend; 16px padding, 20px bottom margin, 1px theme border and theme radius. At ≤640px: 12px padding, 16px margin. |
| Paired fields | Two equal `minmax(0, 1fr)` grid columns with 16px gap; one column at ≤640px. |
| Label and input | Label above control, 6px gap, 12px between fields. IDs and `for` remain unique. |
| Helper | Directly below its control; `aria-describedby` references its ID. Error descriptions supplement helpers and clearing errors restores them. |
| Inputs | Minimum height 36px; theme surface; `--text-3` boundary for visible control edges. Monospace only for IDs/patterns. |
| Disabled input | Native disabled; surface-2 background, muted text, not-allowed cursor. Preserve the reason nearby. |
| Focus | Solid 2px theme foreground outline, 2px offset; never remove keyboard focus to achieve a visual effect. |
| Error | Text explanation plus 3px fail-text left border; invalid control also uses aria-invalid. Do not rely on red alone. |
| Rule table | Own horizontal scroll region, labelled and keyboard focusable; minimum 900px (820px at ≤1100px). Page and modal must fit without horizontal overflow. |
| Rule cells | 12px vertical padding; controls aligned at the top. No compressed labels or hidden actions. |
| Buttons | Wrap labels; minimum height 36px in form; existing host footer stays visible. |

Contrast targets are ≥4.5:1 for normal text and ≥3:1 for essential control/focus
boundaries. Verify actual computed colors in both Obsidian Orbit and Daylight
Orbit, rather than assuming that a token name guarantees contrast. Decorative
section borders may remain subtle. Preserve native form semantics, screen-reader
labels and the host dialog's focus/return-focus behavior. No new animation is
required. Check a 390px viewport and a zoomed desktop layout as well as 1024px.

## AVICA file matching (B3, implemented)

The current matcher uses plan targets. Inspect `mapping.py`, `sync.py`,
`project.py`, the header action and AVICA's execution manifest before changing
the contract; a UI alias alone must not advertise working file matching.

Use **Row-match column** as the selector label once both behaviors are supported.
Show a separate labelled **Match against** control: Plan target / Plan filenames.
Selecting `FILENAMES` can suggest Plan filenames, but make the resolved choice
visible and editable. Keep existing TARGET_NAME mappings working without edits.

For filenames, helper: “Match this column to the files listed for each plan
target.” Show samples as `sheet value → plan target`, with a match count from
the same resolver the backend uses. Unmatched samples receive text explaining
the mismatch; ambiguous samples say “Matches multiple targets” and require
correction before writes. Do not silently choose a row or overwrite target names.

Implemented contract: `rows.match_against` defaults to `target` and can be `files`.
The shared backend resolver uses ALFRD's comma/whitespace/newline file-list parser,
lexical path normalization, case-sensitive paths and set membership. All listed
files must belong to one target; a subset of that target's files is allowed.
Order and duplicate filenames are ignored; basename guesses and filesystem-based
resolution are excluded. Project code can disambiguate shared filenames. Multiple
sheet rows resolving to one target fail validation/sync. Unmatched rows are left
alone. Missing-row append uses the full plan file list and requires unambiguous,
non-empty files. Hooks use the running plan; Studio uses the configured plan CSV.
The setup preview uses this resolver for target mode too, matching sync's exact,
case-sensitive target comparison. Legacy mappings need no edits.

## Missing columns (B4, implemented)

Offer recovery at the place where validation identifies missing destinations.
Keep other mapping errors visible. Do not change the existing **Add status columns
for all steps** behavior (currently it adds mapping rules for existing headers)
into a remote sheet write under the same label.

1. Below Outbound rules, show a callout: “3 destination columns are missing.”
   List their expanded names; for `{step} RAM`, show e.g. `calibrate RAM` and
   `image RAM`, not the unresolved pattern.
2. Provide **Review column creation…**. Open a small confirmation through the
   existing dialog API, showing worksheet, header row, concrete new names and
   destination letters. Helper: “Adds headers at the end of this sheet. Existing
   headers and data are kept.” Disable creation if that promise cannot be met.
3. Confirm label: **Create 3 columns**, with Cancel as the secondary action.
   Creation is explicit; saving a mapping must not silently mutate sheet headers.
4. While creating, show “Creating columns…” in a polite status region, disable
   the duplicate action and retain the dialog's existing busy-close handling.
5. On success, refetch headers, revalidate, keep draft rules and focus, and report
   “Created 3 columns. Review your mapping, then Save.” Saving the mapping remains
   a separate action so the user can review it.
6. On failure, preserve the draft and show the server's safe error beside recovery.
   Retry must first refresh headers to avoid duplicate creation after a partial
   success. If headers changed, show the updated preview before another write.

Only named outbound destinations qualify for automatic creation. Missing row-key
columns, inbound source columns and ambiguous duplicate headers need correction;
do not invent empty keys or assume missing inbound data should be created. Treat
column letters as existing locations, not new names. Respect configured header
row, read-range bounds, sheet grid capacity, server write policy and dry-run.
In dry-run, use **Preview column creation** with a “No columns will be written”
notice; never display a success message claiming actual creation.

Backend handoff: return structured missing-column data, expand/deduplicate via
the same validator as sync, recheck current headers before writing, avoid
overwriting occupied cells, and refresh headers afterward. Test wildcard
expansion, already-existing headers, concurrent changes, policy, dry-run and
partial failure.

Implementation notes: creation inserts new columns at the actual grid end (after
all existing columns, including empty columns), then writes literal string headers
in the same Sheets batch. The review shows those concrete letters; a bounded
read range must be widened or cleared before creation outside its bounds. Existing
columns/cells are preserved by insertion even if another client appends columns
between the recheck and the atomic batch. A digest of the sheet/grid/header and
expanded destinations rejects stale reviews; the shared spreadsheet lock
serializes ALFRD sync and creation. Non-idempotent insert requests have one attempt,
so a lost response is reconciled with a new read instead of blindly replayed.
The existing host dialog's confirmation footer is used so the editor stays mounted
and its draft, revision, dirty state and focus are preserved. Creation is a
protected mutating action; preview is read-only. The normal column limit is checked
locally; Google enforces remaining spreadsheet limits atomically.

REST contract references: [InsertDimensionRequest and UpdateCellsRequest](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request),
[atomic batch requests](https://developers.google.com/workspace/sheets/api/guides/batch).
