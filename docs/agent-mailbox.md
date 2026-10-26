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

Message kinds and payloads are defined in the versioned schemas described below.
An `info` payload is any JSON object (default `{}`); every message, payload
included, must fit in 32 KiB of canonical UTF-8 JSON.
Permission hashes are `sha256:` plus the SHA-256 digest of canonical UTF-8 JSON
containing action, target, arguments (default `{}`), and scope. Canonical JSON
uses sorted keys, no whitespace, and literal Unicode. Scope is `once`.
The first valid resolution wins; changed idempotent retries and stale versions
return 409. Expiry and cancellation never grant permission. A receipt is not an
approval. Native sandbox and tool approval policies continue to apply.

## Mailbox v2 records and compatibility

New requests pin `mailbox_protocol` to `alfrd.mailbox/2` when created, before
any mail is written. A request pinned to v1, or an older request without the
field, keeps writing `alfrd.mailbox/1` even after restart. Readers accept both;
v1 display defaults are projected without rewriting JSONL. Do not add v2
metadata to a v1 send. Older ALFRD versions cannot read v2 records.

The v2 schemas are `src/alfrd/schemas/agent_message.v2.json` and
`agent_mailbox_event.v2.json`; the v1 schemas remain supported. Kind-specific
payload rules are unchanged. The optional display fields are:

| Field | Meaning and default |
|---|---|
| `code` | Type code below; inferred if omitted |
| `category` | Code's category; a conflicting code/category is rejected |
| `priority` | `low`, `normal`, `high`, or `urgent`; defaults from code |
| `summary` | Short display text; defaults to subject |
| `description` | Extra detail, at most 2,048 characters; defaults to empty text |
| `step_id` | Step identifier or null; defaults to null. Starts with an ASCII letter/digit, then letters/digits, `_`, `.`, or `-`, at most 160 characters |

Uncoded info defaults to `NTE`. A category-only info send selects the first
matching code in the table. Uncoded questions use `ASK` for the user and `ASO`
for the orchestrator; permissions use `PRM`. Replies use store-owned `REP`,
inheriting the original category and priority. A code conflicting with kind is
rejected. The complete canonical UTF-8 record, including metadata and payload,
must fit within 32 KiB (32,768 bytes), not 32,768 characters.

| Code | Kind | Category | Default priority | Meaning |
|---|---|---|---|---|
| PRG | info | progress | low | Step progress `{step, of, label}` |
| STR | info | progress | low | Work started |
| DON | info | result | normal | Main work finished |
| ART | info | result | normal | Output file ready `{path}` |
| NTE | info | note | low | FYI |
| WRN | info | error | high | Degraded, continuing |
| ERR | info | error | high | A step failed `{cause?}` |
| BLK | info | blocker | urgent | Cannot proceed `{needs?}` |
| DEP | info | coordination | normal | Note to a sibling or dependent task |
| CLM | info | coordination | high | Claims a shared path `{path}` |
| OUT | info | coordination | high | An expected output moved `{output, to, from?, reason?}` |
| ASK | question | question | high | Question for the user |
| ASO | question | question | high | Question for the orchestrator |
| PRM | permission_request | permission | urgent | Approval for an exact action |
| REP | reply | (inherited) | (inherited) | Set by the store |

Payload hints in the meanings describe conventions for informational mail;
they do not add requirements to its free-form JSON object. `OUT` is the
exception: its payload must name the output id (`o1`, `o2`, …) and the new
absolute path in `to`. When the task finishes, ALFRD applies the OUT mail that
attempt sent together with the result's `outputs_changed` (the result wins for
the same output; the last mail per output wins otherwise). The same checks
apply: the new path must be inside the task's writable folders, and one refused
change refuses them all and retries the task (see "An agent can move an
output" in [orchestration](orchestration.md)).

State is derived from lifecycle events, never accepted on send. Unacknowledged
info is `pending`; its recipient's receipt makes it `acked`. A receipt leaves
questions and permissions `pending`; only a valid reply resolves them. Other
terminal states are `expired` and `cancelled`. Only unresolved user questions
and permissions count toward `pending_user`.

## CLI v2

The scheduler supplies `ALFRD_MAILBOX_CONTEXT`, `ALFRD_RUN_ID`, `ALFRD_TASK_ID`
and `ALFRD_AGENT_ID`; the context file has mode 0600. Commands use the current
execution's context without a server or credentials. Agents see incoming mail,
their own outgoing mail and replies to their execution. Browser sender is
always the user; CLI sender is assigned from context.

`read` returns JSON with `messages`, lifecycle `events`, `next_cursor`,
`has_more`, `at` and `pending_user`. Use `--after` with the opaque `next_cursor`
and drain pages sequentially while `has_more` is true. `--limit` defaults to
100 and accepts 1–200. Reads never acknowledge.

`read --compact` replaces messages with rows containing `seq`, `id`, `code`,
`priority`, `from`, `to`, `state`, `summary` and a display `line` in the form
`#seq CODE priority from→to state "summary"`. It excludes body, description and
payload, including permission hashes. `read --message` returns full status with
`message`, `state`, `version` and `reply`. It accepts a message id or a quoted
`'#seq'` handle scoped to the current request. Always quote the handle in a
shell, where an unquoted `#` starts a comment. Only this read option resolves
handles; `ack`, `reply` and `wait` require the full message id.

The following Bash examples are independent, run inside an active **v2**
execution, and use Python 3 for JSON. Run in a scratch directory: they create
local files. The inspect example assumes a visible message at `#1`; substitute
the sequence from your compact inbox. The receipt/reply example assumes that
`#1` is a question addressed to your execution with an option id `a`.
`tests/test_mailbox_docs.py` executes every shell block against isolated real
mailboxes, including these incoming-message fixtures, without contacting agents.

Inspect the code table, compact inbox and full message:

```sh
alfrd mailbox codes
alfrd mailbox read --compact --limit 100
alfrd mailbox read --message '#1'
```

Send a progress note. All v2 send metadata flags are illustrated; `--kind` may
be omitted because `--code` determines it. `--to` accepts `user`, `orchestrator`
or `task:<id>` for an existing task in this request. Reuse a key only for an
identical retry; changed content returns a conflict.

```sh
printf '%s\n' 'Checking mailbox documentation.' > body.txt
printf '%s\n' 'Examples run in an isolated mailbox.' > description.txt
printf '%s\n' '{"step":1,"of":2,"label":"examples"}' > progress.json
alfrd mailbox send --to orchestrator --code PRG --category progress --priority low --summary 'Checking examples' --description-file description.txt --step-id docs-check --subject 'Mailbox docs' --body-file body.txt --payload-file progress.json --key docs-progress-1 > sent.json
python3 -c 'import json; m=json.load(open("sent.json"))["message"]; assert m["code"] == "PRG"; print(m["id"])'
```

Ask the user a question with explicit options and optional free text. Use `ASO`
and `--to orchestrator` for an orchestrator question. Plain sends without a code
remain supported through the kind defaults above.

```sh
printf '%s\n' 'Which output should I prepare?' > question.txt
printf '%s\n' '{"options":[{"id":"a","label":"Text"}],"allow_text":true}' > options.json
alfrd mailbox send --to user --code ASK --subject 'Choose output' --body-file question.txt --payload-file options.json --key docs-question-1 > question-sent.json
```

Request permission for an exact action. This sends a request only; it performs
no publish. The hash covers action, target, arguments (default `{}`), and scope
using sorted keys, no whitespace and literal Unicode in UTF-8. Description and
choices are required permission payload fields but are outside the hash.

```sh
python3 - <<'PYCODE'
import hashlib
import json
from pathlib import Path
payload = {"action": "publish", "target": "preview", "arguments": {"revision": "abc"},
           "scope": "once", "description": "Publish preview revision abc",
           "choices": ["approve", "deny"]}
inputs = {key: payload[key] for key in ("action", "target", "arguments", "scope")}
canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
payload["action_hash"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
Path("permission.json").write_text(json.dumps(payload), encoding="utf-8")
Path("permission.txt").write_text("May I publish preview revision abc?", encoding="utf-8")
PYCODE
alfrd mailbox send --to user --code PRM --subject 'Publish preview' --body-file permission.txt --payload-file permission.json --expires-at 2099-01-01T00:00:00Z --key docs-permission-1 > permission-sent.json
```

Acknowledge and answer an incoming question (see the fixture assumptions above).
Fetch full status again after ack to obtain its current version. The recipient
alone can acknowledge or reply; an agent cannot answer a user-directed question
or grant itself permission.

```sh
alfrd mailbox read --message '#1' > incoming.json
message_id=$(python3 -c 'import json; print(json.load(open("incoming.json"))["message"]["id"])')
alfrd mailbox ack "$message_id"
alfrd mailbox read --message "$message_id" > incoming.json
version=$(python3 -c 'import json; print(json.load(open("incoming.json"))["version"])')
printf '%s\n' 'Prepare text.' > answer.txt
alfrd mailbox reply "$message_id" --choice-id a --answer-file answer.txt --expected-version "$version" --key docs-answer-1
alfrd mailbox wait "$message_id" --timeout 0
```

`reply` defaults to outcome `answer` and expected version 1. For a permission,
the authorized recipient must use outcome `approve` or `deny` and supply
`--action-hash` with the exact hash fetched from the full message. Fetch and
inspect the full action and arguments before deciding; a compact line is never
sufficient. Before acting, the requesting agent must fetch full status, verify
that it is resolved with an approve reply for that exact hash, and ensure the
request is still unexpired and uncancelled. A receipt, a read, a sent request,
or an ordinary answer is never approval. Native sandbox/tool policy still
applies; mailbox approval cannot widen a running process's filesystem or
command permissions.

`wait` defaults to 60 seconds (`--timeout` accepts 0–3600). It exits 0 on an
answered question or approved permission, 2 while still pending at the timeout,
and 3 on denial, expiry or cancellation. Mailbox operation errors exit 1 with
JSON `error` and `status`; argument/usage errors use Typer diagnostics. `codes`
prints a Markdown table instead of JSON; the documentation test checks every
code, kind, category, default priority and meaning against that shared table. The first
valid resolution wins; stale versions and changed idempotent retries conflict.

For a stopped task, include `mailbox_message_id` on its orchestration blocker.
The resolved event acts as a durable continuation outbox, replayed at scheduling
checkpoints. Projection to a decision and WAITING→TODO are idempotent; a running
attempt is never duplicated. Continuation prompts include relevant mail as data.
Mailbox posting alone does not interrupt a running CLI process. Prompt digests
use compact lines; bodies are included only for unresolved mail addressed to the
current task, up to 2 KB total. Digest budgets are 600 characters empty and
2.5 KB with 20 messages. Fetch full mail for details and permission checks.

The activity route remains compatible:
`GET .../chat/sessions/<session>/activity?lines=40&request=<request>&units=<unit ids>`.
Without units it returns running attempts. Explicit units (maximum 32) retrieve
completed attempts too. `items` adds status, finished time, recorded cost or null,
tail revision/state/truncation and normalized display_tail. Legacy running/tail
fields remain available. Lines are clamped to 1–400 and reads are bounded.
