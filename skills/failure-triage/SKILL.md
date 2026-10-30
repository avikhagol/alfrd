---
name: failure-triage
description: Triage failed or stalled ALFRD chat tasks using bounded logs and existing retry, fallback, replan and recovery rules before repeating work.
---

# Triage failure

- Inspect task status, attempt, error and a bounded log tail/activity response. Distinguish invalid reply, missing/refused output, provider limit, permission/input blocker, command failure and stalled runner.
- Fix concrete local defects and verify when authorized. Before repeating an operation with side effects, inspect what the earlier attempt changed; failed replies do not prove nothing executed.
- Respect existing retry/fallback budgets. Do not add retry loops, error-policy fields or idempotency metadata to the protocol.
- Use [blocker-reporting](../blocker-reporting/SKILL.md) for missing decisions/access; report unresolved exhausted failures as failed. Preserve cause, evidence, attempted fixes and remaining action in summary or a report artifact. Read compact mail before finalizing.

Read [recovery paths](references/contract.md) to select the existing mechanism.
