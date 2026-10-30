# Mail details

Sources: [chat.py](../../../src/alfrd/chat.py), MAILBOX_RULES and MAILBOX_CONTRACT; [agent-mailbox.md](../../../docs/agent-mailbox.md), “Mailbox v2 records and compatibility” and “CLI v2”.

Requests pin their mailbox version. Do not add v2 metadata to v1 sends or manufacture stored records. REP is store-owned. Compact rows omit payloads and hashes. Only read --message resolves '#seq'; ack/reply/wait require full IDs. Reads never acknowledge, and receipts leave questions/permissions pending. Fetch current version before replying; first valid resolution wins and stale versions conflict.

Informational payload hints are conventions. OUT requires --payload-file JSON with {output: "o1", to: "/absolute/new/path"}, optional from/reason. It is validated at completion like outputs_changed; see artifact-handoff.

ASK/ASO may use options: [{id, label}] and allow_text. PRM specifies action, target, arguments (default {}), scope: once, description, choices: [approve, deny], action_hash. The hash is sha256: plus SHA-256 of canonical UTF-8 JSON containing action, target, arguments and scope: sorted keys, no whitespace, literal Unicode. Use the executable example in agent-mailbox.md when constructing it.

Send PRM to the user. A requesting agent cannot approve itself; reads, receipts, ordinary answers, expiry and cancellation do not grant permission. Inspect the full exact action/arguments and resolved approve reply hash before acting. Mailbox approval cannot expand native access; delegated blocker choices do not allow orchestrators to approve action hashes.

wait defaults to 60 seconds, accepts timeout 0–3600; exits 0 for answered question/approved permission, 2 pending at timeout, 3 denied/expired/cancelled, 1 operation error. Answered questions are not permission approvals. Complete canonical messages including payload must fit 32 KiB. Include the actual mailbox_message_id on a blocked exit; posting mail alone does not interrupt a running process.
