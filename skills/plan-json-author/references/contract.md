# Plan fields and decisions

Sources: [chat.py](../../../src/alfrd/chat.py), plan_prompt, repair_prompt, replan_prompt; [orchestration](../../../docs/orchestration.md), “Protocol” and “How a request runs”. Schemas in src/alfrd/schemas/orchestration_*.v1.json are authoritative.

A plan has at least one of message, tasks, decisions. Each task requires id, title, owner, acceptance_criteria (at least one nonempty string). Optional fields:

- role, description: task-specific context.
- depends_on: unique task IDs in this plan.
- inputs: path@sha256 refs or {ref, note} objects.
- expected_outputs: paths or {path, description}; relative paths resolve from project root, not shell cwd. Paths must be inside allowed writable folders.
- budget: only max_cost_usd, max_seconds (positive), max_retries (integer 0–5), within supplied bounds.

Task IDs match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}. ALFRD also checks owner availability, dependency existence/cycles, access and max_tasks. An invalid plan gets one repair call; return corrected fenced JSON only.

Replans may supply decisions with id plus choice, answer, reassign (agent key) or cancel: true, and optional tasks. A normal choice reruns the same task with the same access; reassign or cancel if that cannot work. Only explicitly delegated grant options let the orchestrator choose access within those options. It cannot invent broader grants or approve mailbox action hashes. If a person must decide, return only a message. Replans are bounded; repeated identical blockers go to the user.
