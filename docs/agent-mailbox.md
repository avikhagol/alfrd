# Agent mailbox and task output polling

Each chat request has durable mail under
`.alfrd/plans/chat-<session>/mailbox/<request>/`. `messages.jsonl` and
`events.jsonl` are authoritative; `state.json` is a disposable checkpoint.
HTTP and local commands serialize writes with the same file lock. Complete
corrupt records fail explicitly; an incomplete trailing append is recovered.

Studio routes use the existing authentication and POST CSRF protection. With
`B=/api/studio/projects/<project>/chat/sessions/<session>/requests/<request>/mailbox`:

- `POST B/messages`: recipient, kind, subject, body, idempotency_key and payload;
  optional reply_to (null), correlation_id and expires_at. Sender and persistence
  fields are assigned by the store. Browser sender is always the user.
- `GET B/messages?after=<opaque cursor>&limit=100`: messages, lifecycle events,
  next_cursor and has_more. Drain pages sequentially. Reads never acknowledge.
- `GET B/pending`: unresolved user questions and permission requests.
- `POST B/messages/<id>/ack`: recipient receipt, independent of approval.
- `POST B/messages/<id>/reply`: idempotency_key, expected_version (initially 1),
  outcome, optional choice_id, answer and action_hash. Question outcomes use
  `answer`; permissions use `approve` or `deny` with the exact action hash.

Message kinds and payloads are defined in `src/alfrd/schemas/agent_message.v1.json`.
An `info` payload is any JSON object (default `{}`); every message, payload
included, must fit in 32 KiB of canonical UTF-8 JSON.
Permission hashes are `sha256:` plus the SHA-256 digest of canonical UTF-8 JSON
containing action, target, arguments (default `{}`), and scope. Canonical JSON
uses sorted keys, no whitespace, and literal Unicode. Scope is `once`.
The first valid resolution wins; changed idempotent retries and stale versions
return 409. Expiry and cancellation never grant permission. A receipt is not an
approval. Native sandbox and tool approval policies continue to apply.

The scheduler supplies `ALFRD_MAILBOX_CONTEXT`, `ALFRD_RUN_ID`, `ALFRD_TASK_ID`
and `ALFRD_AGENT_ID`; the context file has mode 0600. Agents use:

```sh
alfrd mailbox read --limit 100
alfrd mailbox send --to task:frontend --kind info --subject 'API ready' --body-file /tmp/note.txt --key api-ready-1
alfrd mailbox send --to orchestrator --kind info --subject 'Ping' --body-file /tmp/ping.txt --payload-file /tmp/ping.json --key ping-1
alfrd mailbox send --to user --kind question --subject 'Choose layout' --body-file /tmp/question.txt --payload-file /tmp/options.json --key layout-1
alfrd mailbox ack <message-id>
alfrd mailbox reply <message-id> --answer-file /tmp/answer.txt --key answer-1
alfrd mailbox wait <message-id> --timeout 60
```

Commands return JSON. Wait exits 0 on answer/approval, 2 if still pending and 3
on denial/expiry/cancellation. Agents can read their inbox, own outgoing mail
and replies to their execution. They cannot reply to user-directed requests.
For a stopped task, include `mailbox_message_id` on its orchestration blocker.
The resolved event acts as a durable continuation outbox, replayed at scheduling
checkpoints. Projection to a decision and WAITING→TODO are idempotent; a running
attempt is never duplicated. Continuation prompts include relevant mail as data.
Mailbox posting alone does not interrupt a running CLI process.

The activity route remains compatible:
`GET .../chat/sessions/<session>/activity?lines=40&request=<request>&units=<unit ids>`.
Without units it returns running attempts. Explicit units (maximum 32) retrieve
completed attempts too. `items` adds status, finished time, recorded cost or null,
tail revision/state/truncation and normalized display_tail. Legacy running/tail
fields remain available. Lines are clamped to 1–400 and reads are bounded.
