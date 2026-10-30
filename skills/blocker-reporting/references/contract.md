# Blocker contract

Sources: [chat.py](../../../src/alfrd/chat.py), MAILBOX_CONTRACT and replan_prompt; [orchestration](../../../docs/orchestration.md), “How a request runs” and “Access and permission blockers”; [agent-mailbox.md](../../../docs/agent-mailbox.md), “CLI v2”. Authoritative schema: src/alfrd/schemas/orchestration_blocker.v1.json.

Required: id, reason. Optional: task_id, question, options (strings), needs (user or orchestrator, default user), kind (permission, input, other), requires: {folders: [...], bash_commands: [...]}, mailbox_message_id. Use the actual msg_ ID returned by the mailbox, never the compact #seq handle.

A person can grant access; explicit delegated grant options let the orchestrator choose only within those options. It cannot invent broader access or approve mailbox action hashes. Native access cannot change mid-session: denied folders/commands need a permission blocker and relaunch. An ordinary input answer is not a permission grant.

Normal replan choices rerun the same task/access; reassign or cancel when that cannot work. The same blocker with unchanged owner/command/folders goes to the user rather than another identical replan. Mailbox continuation is replayed idempotently at scheduler checkpoints; sending mail does not interrupt a running CLI process.
