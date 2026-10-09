# Notifications

ALFRD tells you when a plan needs you or is done: a review is pending, a turn
failed or went quiet, the plan finished. The runner sends them itself, so
**`alfrd serve` doesn't have to run**. An open Studio tab can show them too.

## How it works

Every plan event is appended to `.alfrd/plans/<id>/events.jsonl`, one JSON
object per line:

```json
{"seq": 17, "at": "2026-10-07T17:25:36", "kind": "review.pending", "plan": "…", "project": "/path/to/project",
 "target": "t-idea", "unit": "0001-…", "turn": 1, "turns": 4, "agent": "claude", "roles": ["Product Manager (PM)"],
 "text": "awaiting human review: 0001-…", "data": {}}
```

`seq` counts up per plan, also after the runner is restarted. The file is
rotated past 10 MB (one `events.jsonl.1` is kept). The plan's `history` text is
unchanged. Read the events with
`GET /api/v1/projects/<p>/plans/<id>/events?since=<seq>` (digits only → JSON
`{"events": […], "seq": …}`; without a numeric `since` it is the SSE stream
described in [status-api.md](status-api.md)).

| Kind | When |
|---|---|
| `plan.started` | the runner starts (and again, with `data.scheduled: true`, when a scheduled start time is reached) |
| `plan.finished`, `plan.failed`, `plan.cancelled`, `plan.interrupted` | the plan ends |
| `turn.started`, `turn.finished`, `turn.failed` | a turn (unit) changes status |
| `turn.retrying`, `turn.fallback_model` | a turn is retried, or relaunched with the next fallback model |
| `turn.idle` | a running turn wrote nothing (log, events, usage) for `idle_after` seconds |
| `review.pending`, `review.approved`, `review.rejected` | human review of a handoff |
| `handoff.published` | a handoff was accepted |
| `limit.reached` | `max_runtime` or a timeout was hit |
| `plugin.hook_failed` | a plugin's step hook raised or timed out (the step itself is unaffected) |

The runner delivers them:

- **Routes** choose the notifier (`via`) and the kinds (`on`). `on` takes kinds
  and prefixes: `plan.*`, `turn.*`, `*`.
- **Batching:** events of one plan within 20 s become one message
  (“2 turns failed in t-x”).
- **Timeouts:** each delivery has 10 s and 3 retries (1, 2, 4 s apart). A
  notifier that can't work here (no desktop session, `notify-send` missing,
  http to a remote host) is *skipped*: one warning, no retries. Nothing ever
  blocks or fails the plan. Problems go to the plan's `runner.log`.
- **Cursor:** `.alfrd/plans/<id>/notify.cursor` holds the last delivered `seq`.
  A restarted runner continues after it, so nothing is sent twice. Events older
  than one hour are replaced by one “N earlier event(s) not sent” message.
- **Idle:** `turn.idle` is sent once per quiet period, and again only after the
  turn was active in between. A turn waiting for review or a manual response is
  never idle. Nothing is stopped; it's only a signal.

## Routes

Two places, read in this order (project routes first):

1. the project's `alfrd.yaml`, key `notify` — only `via: desktop` (a
   project file is shared and mustn't run commands or hold secrets);
2. your own `notify.json` in the user config folder (`~/.config/alfrd/notify.json`
   on Linux, `~/Library/Application Support/alfrd/notify.json` on macOS) — any
   notifier. It applies to every project.

```yaml
# alfrd.yaml
notify:
  idle_after: 600        # seconds; 0 or false turns turn.idle off (alias: execution.idle_after)
  routes:
    - via: desktop
      'on': [review.pending, plan.failed, plan.finished, turn.idle]
```

Quote `'on'`: YAML 1.1 reads a bare `on:` key as `true`. ALFRD accepts the bare
form too, but other tools may not.

```json
{"routes": [
  {"via": "desktop", "on": ["review.pending", "plan.failed", "plan.finished", "turn.idle"]},
  {"via": "webhook", "on": ["plan.*"], "url": "https://hooks.example.org/alfrd", "secret": "…"},
  {"via": "command", "on": ["review.pending"], "argv": ["/home/me/bin/alfrd-ntfy"]}
]}
```

A broken `notify.json` is reported as a warning and its routes are ignored; the
plan still runs. An unknown `via`, or an `on` entry that matches no kind, is a
warning too. Settings → Notifications in the Studio lists all routes and
warnings.

**Routes are read when a runner starts.** Edits apply to plans started (or
runners restarted) afterwards.

`plan.*` includes `plan.started`, so a scheduled plan sends “plan started”
twice: when you schedule it and when its start time arrives. List the kinds you
want (`plan.finished`, `plan.failed`) to avoid that.

### What a notifier gets

One `alfrd.notification/1` message per batch:

```json
{"schema": "alfrd.notification/1", "project": "/path/to/project", "project_name": "widget",
 "plan": "20261008-…", "target": "t-x", "title": "review pending in t-x",
 "body": "review pending 0001-t-x-t001-claude turn 1/4 (claude)", "kinds": ["review.pending"],
 "count": 1, "skipped": 0, "dropped": 0,
 "link": "/studio/#/workflow?project=<id>&plan=<plan>&unit=<unit>",
 "events": [{"seq": 17, "at": "…", "kind": "review.pending", "unit": "…", "turn": 1, "turns": 4,
             "agent": "claude", "status": "pending", "link": "…"}]}
```

It never contains the event `text`, `data`, handoff content or prompts. `link`
is relative: prefix it with your Studio address (`http://127.0.0.1:5000`).

## Notifiers

### `desktop`

Linux: `notify-send` (package `libnotify-bin` / `libnotify`); reviews and
failures are sent as critical. macOS: `osascript`. Runners run in the
background, so the session is found through `DBUS_SESSION_BUS_ADDRESS`, then
`/run/user/<uid>/bus`, then `DISPLAY` / `WAYLAND_DISPLAY`. Over SSH, or with no
desktop, it is skipped with one warning in `runner.log`. Other systems: skipped.

### `webhook` (notify.json only)

POSTs the message as JSON. Options: `url` (required), `secret`, `headers`
(name → string). Rules:

- `https://` is required unless the host is loopback (`127.0.0.1`, `::1`, `localhost`).
- Redirects are refused, and a non-2xx answer is retried.
- `Content-Type`, `User-Agent` and `X-Alfrd-Signature` can't be overridden.

With `secret`, `X-Alfrd-Signature: sha256=<hex HMAC-SHA256 of the body>`. A receiver:

```python
import hashlib, hmac, json
from http.server import BaseHTTPRequestHandler, HTTPServer
SECRET = b"…"
class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        good = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        ok = hmac.compare_digest(good, self.headers.get("X-Alfrd-Signature", ""))
        self.send_response(204 if ok else 401); self.end_headers()
        if ok: print(json.loads(body)["title"])
HTTPServer(("127.0.0.1", 8787), Hook).serve_forever()
```

### `command` (notify.json only)

Runs `argv` (a list, no shell) with the message JSON on stdin, 10 s timeout. A
non-zero exit is retried; a missing program is skipped. For example
[ntfy](https://ntfy.sh):

```json
{"via": "command", "on": ["review.pending", "plan.*"],
 "argv": ["curl", "-fsS", "-d@-", "https://ntfy.sh/<your-topic>"]}
```

That posts the whole JSON. For a readable push, use a small script:

```sh
#!/bin/sh
# ~/bin/alfrd-ntfy — title and body from the message
python3 -c 'import json,sys; m=json.load(sys.stdin); print(m["title"]); print(m["body"])' |
  curl -fsS -H "Title: ALFRD" -d @- "https://ntfy.sh/<your-topic>"
```

### `telegram` (notify.json only)

Sends the title, the body and (with `studio_url`) the Studio link as one plain
text message through the Telegram Bot API. Options: `token` and `chat_id`,
`studio_url` (optional, e.g. `http://127.0.0.1:5122`).

With the `alfrd-telegram` plugin installed, leave out `token` and `chat_id`: the
route then uses the values saved in Settings → Plugins → Telegram, so you paste
the token once, in the Studio:

```json
{"routes": [{"via": "telegram", "on": ["review.pending", "plan.failed", "plan.finished"],
             "studio_url": "http://127.0.0.1:5122"}]}
```

Without the plugin, put them in the route:

1. In Telegram, talk to **@BotFather**, send `/newbot` and copy the token it gives you.
2. Open a chat with your new bot and send it `/start` (a bot can't write to you first).
3. Find your chat id: open `https://api.telegram.org/bot<token>/getUpdates` in a
   browser and read `result[0].message.chat.id` (a group id starts with `-`).
4. Add the route and keep the file private:

```json
{"routes": [{"via": "telegram", "on": ["review.pending", "plan.failed", "plan.finished"],
             "token": "<token from BotFather>", "chat_id": "<your chat id>",
             "studio_url": "http://127.0.0.1:5122"}]}
```

```sh
chmod 600 ~/.config/alfrd/notify.json
```

Rules:

- No `parse_mode` (no Markdown or HTML), link previews off; text over 4096
  characters is shortened with `…`.
- 400, 401, 403 and 404 (bad token, unknown chat, bot blocked) are skipped with
  one line in `runner.log`; 429 and 5xx are retried.
- The token is never written to `runner.log` or shown in the Studio.
- The link only opens where that address reaches your Studio (the same machine,
  or over a VPN or SSH tunnel).

## In the Studio

Settings → **Notifications**:

- **Show browser notifications** asks the browser for permission when you turn
  it on (never before). While a Studio tab is open, also in the background, it
  shows `review.pending`, `plan.failed`, `plan.finished` and `turn.idle`.
  Clicking one focuses the tab on that run and turn. If the browser blocks
  notifications, allow them in the site settings and turn the switch on again.
- The **routes** of the selected project (project, then user), with secrets,
  URL queries and command arguments hidden, and the warnings.
- **Send test** sends one test message through that route and shows
  Sent / Skipped / Failed. It needs a local Studio with changes allowed.

An open tab also shows a toast for every kind a route listens to (the four
kinds above when there are no routes). Notification links open
`/studio/#/workflow?project=…&plan=…&unit=…`: that project, that run (kept
selected), and that turn's handoff or log.

## Security

- Notifications carry status only: no handoff text, prompts, event text or `data`.
- `webhook`, `command` and `telegram` are refused in `alfrd.yaml`. A cloned
  project can't make your machine run a command or post to an address.
- Keep `notify.json` private (`chmod 600`); it may hold a webhook `secret` or a
  Telegram bot `token`.
- Webhooks need https unless the host is loopback, and don't follow redirects.
- The Studio's route list never shows `secret`, a Telegram `token` or `chat_id`,
  URL credentials or queries, or command arguments. Send test is a protected change (access token, loopback,
  CSRF).

## Try it

1. Put a desktop route in `~/.config/alfrd/notify.json` (example above) and
   `notify: {idle_after: 60}` in a project's `alfrd.yaml`.
2. Turn on review for turn 1 (Agents dialog → per-turn table, or
   `workflow.turns: {1: {human_review: true}}`) and start a plan with
   `alfrd plan run`. Keep `alfrd serve` stopped.
3. When turn 1 ends, a “review pending” notification appears. Approve the
   handoff in the Studio (Review response → Approve); the plan finishes with
   “plan finished”.
4. Add a webhook to a closed port (`"url": "http://127.0.0.1:9/"`): the next
   turn still starts at once; `runner.log` shows the retries and the give-up.
5. With `alfrd serve` running, turn on Settings → Notifications → Show browser
   notifications, switch to another tab, and start a plan with review: the
   browser notification opens that review.

Steps 1, 3 and 4 are automated in `tests/test_notify_acceptance.py`; idle in
`tests/test_idle.py`.
