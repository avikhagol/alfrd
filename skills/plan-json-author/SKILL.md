---
name: plan-json-author
description: Author or repair ALFRD orchestrator plan JSON with reachable owners, explicit dependencies, artifact inputs, expected outputs and bounded budgets.
---

# Author a plan

- When assigned the planner role, create tasks for the supplied agents or return a message with no tasks when no work is needed. ALFRD executes and schedules the tasks.
- Match owners to the supplied agent keys and folder/command reach. Stay within request task count, delegation depth, retries, time and cost bounds.
- Use unique short task IDs, checkable acceptance criteria and explicit dependencies within this plan. Avoid cycles. Copy input artifact refs exactly.
- Give expected outputs as absolute writable paths; a trailing / denotes a folder. ALFRD freezes and checks these exact paths.
- Reply with one fenced JSON block using protocol alfrd.orchestration/1 and kind plan. Put user-facing context in message; use only supported fields.

Read [plan fields and decisions](references/contract.md) for authoring, repair or replan.
