# Agent loops (ALFRD)

`template: agent-loop`: agents take turns on one task, each reading the previous
turn's handoff (`{target}/next-step-<agent>.md`) and writing the next one.

```yaml
loop:
  workspace: worktree       # each task works in its own git worktree: {target}/workspace, branch alfrd/<task>
workflows:
  - name: agent-loop
    repeat:
      iterations: 10        # TOTAL turns (agent runs) = the project maximum per task
      sequence: [claude, claude, codex]   # one pass, repeats; may stop mid-pass
      # passes: 3           # instead of iterations: whole passes
      # sequence: [[claude, codex], [claude, claude, codex]]   # custom passes; the last repeats
    turns:                  # single turns, by number or id (t004-codex)
      3: {human_review: true}             # hold the handoff for a person
      t004-codex: {manual: true, after: "+1h"}   # a person writes it; start 1 h after turn 3
entrypoint:
  - {name: claude, cmd: [claude, -p], model: opus, fallback_models: [sonnet]}
  - {name: gemini, cmd: [gemini], adapter: generic, model_option: --model}   # any stdin→stdout CLI
```

- Step ids are `t<NNN>-<agent>`; older projects (`repeat.iterations` + `steps`)
  still count passes and use `i<NNN>-<step>`.
- Tasks: `{target}/task.md` + `{target}/.alfrd-task.json` (`{"iterations": N, "version": 2}`
  = turns, at most the project maximum). Tasks run in parallel as separate plans.
  ALFRD never commits, merges or pushes a worktree; the user merges `alfrd/<task>`.
  Outside git, tasks share one tree (`workspace_warning` on the plan).
- Review is decided when a turn's command ends, so `plan turn … --review` works
  on the running turn. Other overrides only on turns not yet started. They live
  in `.alfrd/plans/<id>/overrides.json` (this plan only; `alfrd.yaml` unchanged).
- Fallback: Claude gets `--fallback-model`; other agents are relaunched with the
  next model after a runtime failure (never after an invalid or rejected response).
- Each turn records `logical_turn_id`, `attempt_number`, `retry_of`, `outcome`
  (`accepted failed_validation rejected abandoned failed_runtime`), `usage_source`,
  `agent_usage.input_uncached_tokens`, and `handoff.raw_input_chars /
  trimmed_handoff_chars / prompt_chars / sections_truncated`.
