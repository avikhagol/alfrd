# Changelog

## [Unreleased] — 0.2.2

- **Agent sequences.** `repeat.sequence: [claude, claude, codex, …]` sets the order of turns; `iterations` is now the **total number of turns** (custom passes and `passes:` also accepted). New agent-loop projects use it; older projects (`repeat.iterations` + `steps`) keep counting passes unchanged.
- **Agent agnostic.** Built-in adapters for Claude and Codex, and a `generic` adapter for any stdin→stdout CLI (`adapter:`, `model_option:`). Studio project creation takes an agent sequence; `alfrd projects create --sequence`.
- **Fallback models.** `fallback_models:` per entrypoint or turn. Claude uses `--fallback-model`; other agents are relaunched with the next model after a runtime failure (never after an invalid or rejected response).
- **Per-turn human edit and review.** `workflow.turns` sets `manual`, `human_review` or `after` for single turns; Studio's Agents dialog has a per-turn table.
- **Edit a running plan.** Handoffs → *Turns of this run* (or `alfrd plan turn`) adds review to any turn — including the running one — or changes human turns, delays and models of turns not yet started, for that plan only. The review gate now lives in the runner, after the command exits; **Reject and stop** (`alfrd plan reject`) refuses a held handoff. No plan CSV columns are added.
- **Delays.** `after: "+1h"` starts a step that long after the previous one finished; `waiting[]` reports `kind: delay`; **Run now** / `--after 0`.
- **Tasks.** The project's iterations are the maximum per task (enforced). With `loop.workspace: worktree` (new projects) each task gets its own git worktree at `<task>/workspace` on branch `alfrd/<task>`, so tasks run in parallel without sharing files. Overview lists tasks (turns, worktree branch, runs) above the run history; click one to filter.
- **Attempt records.** Each turn records `logical_turn_id`, `attempt_number`, `retry_of`, `treatment`, `run_kind`, `outcome`, `usage_source`, normalized `input_uncached_tokens`, and raw / trimmed / final prompt sizes with truncated sections.
- **Fix:** plan token totals mixed Claude's uncached input with Codex's cache-inclusive input; totals now add `input_uncached_tokens` (raw counters unchanged).
- **Baseline report.** `alfrd plan baseline` reports consecutive production turns with coverage gates (80% per field), retries, truncation and tokens per accepted result.

## [0.2.1.0]

- AVICA: result CSVs are named `result__<TARGET>__<CODE>__<wd>.csv` by default. Earlier `result_<TARGET>_<CODE>_<wd>.csv` and `<TARGET>_result.csv` names are still read. Targets and project codes never keep a leading or trailing `_`, so an `alfrd.yaml` without a `result_csv` artifact no longer shows `_0554+580` / `_BV015_`.
- AVICA: `alfrd serve` in a completely empty folder starts a new AVICA project with the default `alfrd.yaml` (dot-entries such as `.alfrd/` are ignored). Nothing is written until Project settings → Save.

- Studio: guide new projects through setup, use plain run and step labels, link runs to results and filtered logs, keep errors visible, and avoid unchanged polling renders.

- Add agent personalities, per-turn and combined roles, cycling role schedules, and Studio personality editing.

#### Studio: easier agent-loop work
Results counts waiting turns by their latest attempt, keeps refreshing during manual responses and reviews, and offers **CSV results / collections** for imported results. Archived reply links open and scroll to the selected turn. Header controls wrap in narrow windows; remote browsers see why project creation is disabled.

- **Create projects from the header.** Click **+ New project** beside the project picker.
- **See loop results without importing CSVs.** Results shows completed turns, waiting reviews, elapsed time and archived replies. Choose a run or open **Read responses / handoffs**.
- **Choose fields or YAML.** Project settings opens with name, description, loop iterations and timeout fields. Click the edit icon for **Edit YAML file**. Both views keep the same draft; **Save alfrd.yaml** writes it.
- **Keep advanced settings.** Field edits preserve other configuration keys. YAML sections touched by a field are reformatted; comments outside those sections stay in place.
- **Results loads on demand.** The measured lazy assets fit a 64 KiB compressed allowance; the startup allowance stays 165 KiB.

#### Fixed

- **Failed launches trigger recovery once.**
  - Why it matters: failure actions no longer run twice.
  - How to use: retry a failed turn normally.
- **Loops accept either manifest filename.**
  - Why it matters: hidden manifests no longer crash loops.
  - How to use: keep alfrd.yaml or .alfrd.yaml.
- **Invalid manual replies keep waiting.**
  - Why it matters: missing headings no longer fail the plan.
  - How to use: fix the listed headings and resubmit.
- **Loop turns cannot be skipped.**
  - Why it matters: every agent receives the preceding reply.
  - How to use: retry turns, or cancel the plan.

#### Added

- **Turn headings and instructions can be customised.**
  - Why it matters: workflows can use their own reply structure.
  - How to use: set loop.headings and loop.contract in YAML.
- **Project settings include folder and shell permissions.**
  - Why it matters: agents can access your chosen working folders.
  - How to use: open Agents / human review, apply, then save.
- **Manual replies can be submitted from the command line.**
  - Why it matters: chat replies work without Studio.
  - How to use: run alfrd plan response with your Markdown file.
- **Handoffs loads large replies in pages.**
  - Why it matters: opening the dialog stays quick.
  - How to use: expand a turn, then Load more or Copy.
- **Handoffs protects unsaved edits.**
  - Why it matters: switching files or refreshing preserves your choice.
  - How to use: save edits, or confirm discarding them.
- **Schedule shows turns, models and elapsed time.**
  - Why it matters: progress and waiting states are easier to follow.
  - How to use: follow Turn N of M; submit waiting replies.
- **Human adjustments:** enable human review in Project settings → Agents / human review. Workflow exposes the same checkboxes for choosing which agent turns need review. The next agent waits until you approve or adjust the response; the original response is kept.
- **Agent models:** choose a Claude or Codex model in Project settings. Schedule and Handoffs distinguish the requested model from the model reported by the CLI.
- **Task editor:** edit `task.md` from Settings or Workflow. Save can also seed the first agent's handoff for a new run, with conflict protection and history.

#### Changed

- **New scaffolds follow the workflow's handoff filenames.**
  - Why it matters: reversed workflows receive the right initial task.
  - How to use: create a project; Codex uses next-step-codex.md.
- **Loop creation previews the number of turns.**
  - Why it matters: iteration counts show their actual workload.
  - How to use: select agent-loop and adjust Iterations.
- **Loop Run hides settings that cannot apply.**
  - Why it matters: rejected options no longer distract users.
  - How to use: run the loop with its fixed settings.
- **Creation warns about unused iteration options.**
  - Why it matters: non-loop templates no longer silently ignore them.
  - How to use: choose a known template with --template.
- **Shared limits preserve the 10 MiB reply allowance.**
  - Why it matters: submission and publication use matching limits.
  - How to use: keep replies below 10 MiB.
- **Studio's loaded-on-demand size allowance is 62 KiB compressed.**
  - Why it matters: paging and settings fit the measured allowance.
  - How to use: startup loading remains unchanged.

#### Next

- Send reviewed replies back for another attempt.
- Retry replies with missing headings automatically.
- Use live status events instead of polling.
- Compare edited replies against the original agent reply.
- Save activity metadata less often.
- Make tool-result clipping configurable.
- Show estimated remaining time for agent turns.
- Support multiple task rows and workflows.
- Share the supported-agent list with Studio.

[0.2.1.0]: https://github.com/avikhagol/alfrd/compare/v0.2.0.8...v0.2.1.0
[0.2.0.8]: https://github.com/avikhagol/alfrd/compare/v0.2.0.7...v0.2.0.8
[0.2.0.7]: https://github.com/avikhagol/alfrd/compare/v0.2.0.6...v0.2.0.7
[0.2.0.5]: https://github.com/avikhagol/alfrd/compare/v0.2.0.4...v0.2.0.5
[0.2.0.4]: https://github.com/avikhagol/alfrd/compare/v0.2.0.3...v0.2.0.4
[0.2.0.3]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.3  
[0.2.0.2]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.2
[0.2.0.1]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.1
[0.2.0.0]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.0
