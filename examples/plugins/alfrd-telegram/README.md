# alfrd-telegram

Ask ALFRD about your plans from Telegram: status, recent runs, the last 30 log
lines, and pause/resume after a Yes/No confirmation. The bot answers **one**
chat. You configure and run it from the Studio (Settings → Plugins → Telegram)
or from a terminal (`alfrd telegram run`).

Notifications don't need the bot: the built-in `telegram` route sends them from
the runner (see [notifications](../../../docs/notifications.md#telegram-notifyjson-only)).
A route without `token` and `chat_id` uses the values saved here, so you paste
the token once.

## Before you start

1. In Telegram, talk to **@BotFather**, send `/newbot` and copy the token.
2. Open a chat with your new bot and send it `/start` (a bot can't write first).
3. Find your **numeric** chat id: open `https://api.telegram.org/bot<token>/getUpdates`
   in a browser and read `result[0].message.chat.id` (a group id starts with `-`).
   Close that tab afterwards: Telegram allows one reader per bot, so a second
   `getUpdates` reader, or a webhook set on the bot, stops the bot (HTTP 409).
4. **Use a private chat with the bot.** The bot authorizes by chat, not by
   person: in a group, every member can read status and logs and press **Yes**
   to pause or resume a plan.

## Install and set up

```bash
alfrd plugin install ./examples/plugins/alfrd-telegram   # asks first; --yes skips the question
```

Restart `alfrd serve`, then open **Settings → Plugins → Telegram → Configure**:

1. Paste the **Bot token** and the **Allowed chat id**, then **Save**. The token
   is stored in `~/.config/alfrd/plugin-settings.json` (mode 0600). The Studio
   only ever shows whether it is set.
2. **Test** checks both with `getMe` and `getChat`; it sends nothing. It warns
   when the chat isn't private.
3. Under **Background**, **Start** runs the bot as a child of `alfrd serve`;
   **Log** shows its output. Tick **Start with the Studio** to start it with
   every `alfrd serve`.

The bot stops when `alfrd serve` stops. A rejected token or chat (HTTP 400, 401,
403, 404) stops it as `failed` without retries: fix the settings, then Start.

### Quieter notifications

Two optional settings shape the plan notifications (`via: telegram` routes in
`notify.json`; see `docs/notifications.md`):

- **Digest every (minutes)**: collect notifications and send one digest, split
  only when it is longer than one Telegram message, once the oldest is this many
  minutes old. Useful while you test runs. Keep the **Bot** service running so a
  digest goes out on time even after the last run ends. Empty or `0` sends each
  notification right away (and the bot sends anything still held).
- **Mute while I use the Studio**: while you have clicked, typed or scrolled in
  ALFRD Studio (`alfrd serve`) in the last 5 minutes, notifications are dropped
  and held digests wait. Replies to your bot commands are never muted.

Plan-end messages carry no Studio link (it can't open from a phone); for an
`avica` project they list each target's steps with status and duration.

### From a terminal

```bash
alfrd telegram run                 # Ctrl+C stops it
```

It uses the saved settings, or else the first `via: telegram` route in
`~/.config/alfrd/notify.json` that has a `token` and `chat_id`.
`--db PATH` (or `ALFRD_RUNTIME_DB`) reads another runtime database (default
`runtime.sqlite` in the ALFRD folder). Only one bot per token runs at a time:
a second one, in a terminal or the Studio, exits with a message.

The bot lists the projects ALFRD remembers in that database; open a project once
in the Studio or CLI if `/status` doesn't show it. The token and chat id never
appear on the command line, in the bot's output or in its replies.

## Commands

| Command | Reply |
|---|---|
| `/status` | Shows the remembered project and targets; opens the picker if nothing is remembered |
| `/status last` | Same as `/status`; if nothing is remembered, says "Nothing remembered yet." and opens the picker |
| `/status latest` | Shows the most recently active target in the most recently active project; keeps your remembered choice |
| `/status new` | Starts over: pick a project, then one or more targets |
| `/status NAME` | Selects a project by id or name (case-insensitive; a unique prefix is enough), then asks for targets |
| `/status more` or reply `more` | Shows the next 5 entries of the open list, or the next 5 target cards |
| Reply `2`, `1 3`, `1,3` or `all` | Answers the open list; tappable numbers select one, **All** selects every target across all pages, and **More ▸** shows the next page |
| `/runs` | The 10 most recent plans of the selected project |
| `/log` or `/logs` | Menu: pick a target, then a step; shows the last 30 lines of its log |
| `/log TARGET STEP` | The last 30 lines of that step's log, without the menu |
| `/handoff` | The final handoff of the newest agent-loop plan in the selected project |
| `/whoareyou`, `/whoami` | Host, user, system and versions of the machine running the bot |
| `/pause` | Asks Yes/No; Yes lets active commands finish and starts no new ones |
| `/resume` | Asks Yes/No; Yes continues the plan, failed cells stay as they are |
| `/help`, `/start` | This list |

At start the bot also fills Telegram's `/` command menu (`setMyCommands`), so
typing `/` in the chat lists the commands.

### Picking and remembering status

Pick a project, then one or more targets. While a list is open, a bare number
answers that list. To select several targets, type `1 3` or `1,3`; number buttons
select one target immediately. With only one project, `/status new` skips the
project list. A project with only one target skips the target list.

Lists and target cards show 5 entries at a time, most recently active first.
Project activity comes from the latest plan's newest step start or finish time
(or the plan's creation time if no step has started). Projects without plans
come last, labelled "no runs yet". Target entries show the current step, such as
`s3`, `s2 (failed)` or `s1 (next)`.

The choice survives bot restarts. It is saved per chat in
`<plugins_dir>/telegram/status-<hash of chat id>.json`; the hash is the first
12 characters of the chat id's SHA-256 hash. On Linux, `plugins_dir` defaults to
`~/.local/share/alfrd/plugins` (under `XDG_DATA_HOME` when set). A damaged file is ignored.
`/runs`, `/log`, `/handoff`, `/pause` and `/resume` use the remembered project (or the only
project if none is selected). `/status latest` leaves that selection unchanged.

Replies use Telegram's HTML formatting (bold titles, monospace step names,
emoji status icons), sized for a phone. Each target has a card:

```text
📁 Project · 🎯 T1
🔄 s3
━━━━━━━━━━━━━━━━

✅ Finished
  • s1  42s
  • s2  1h 02m

🔄 Running
  • s3  2m 05s

⏳ Next
  • s4
```

Finished steps show their duration; running steps show elapsed time. Empty
sections are omitted. A `❌ Failed` section appears when a step has stopped,
including blocked, cancelled or interrupted steps. The second line shows the running
step, otherwise `X (failed)`, `X (next)` or `done` (`nothing to run` if there are
no finished or pending steps). A paused or stopped plan is noted at the end.
If a remembered target is gone, its card says "Not in the latest plan".
Every project, target and log text is HTML-escaped; if Telegram still rejects a
message's markup, the bot sends it again as plain text.

### Logs from a menu

`/log` (or `/logs`) without arguments lists the selected project's targets that
have started a command, most recently active first, then that target's steps
(newest first, with their state). Pick one to get its last 30 log lines in a
monospace block. A list with a single entry is skipped. `/log TARGET STEP` still
works without the menu.

### Agent-loop handoffs

While it runs, the bot checks every 20 seconds for agent-loop plans (in every
project ALFRD remembers) that it has seen running. When one finishes, fails or
is cancelled, it posts the **final handoff**: the last published agent response,
with headings, bold/italic, code, checklists, links and tables converted to
Telegram formatting. A long handoff is split at headings into at most 4
messages; the rest stays in the Studio. Loops that ended while the bot was
stopped are not posted; send `/handoff` to get the newest one on demand.

### Which machine is this?

When bots on several machines report to the same chat (or one token moves
between machines), `/whoareyou` (or `/whoami`)
replies with the host name, OS user, system, Python, ALFRD and plugin versions,
the ALFRD folder, the bot's PID and uptime, and how many projects it knows.

A pause/resume confirmation:

- applies to the plan shown in the question, even if a newer plan starts later;
- expires after 5 minutes and can be used once;
- is replaced by a newer `/pause` or `/resume`;
- **No** changes nothing.

Messages from other chats get no answer at all. Commands sent while the bot was
stopped are answered when it starts (Telegram keeps them for 24 hours); a
**Yes** pressed while it was stopped counts as expired.

## When it stops

| Exit | Cause | What to do |
|---|---|---|
| 0 | Ctrl+C / `SIGINT` / Stop in the Studio | — |
| 1 | Another bot already runs with this token, or Telegram answered 409 | Stop the other one (or the webhook / `getUpdates` tab). The Studio retries a 409 |
| 2 | No settings; `chat_id` isn't numeric; Telegram answered 400, 401, 403 or 404 | 401/404: wrong or revoked token. 400/403: wrong chat id, or send `/start` to the bot. Fix the settings, then Start |

Network errors, rate limits (429) and Telegram outages (5xx) don't stop the bot:
it retries every 3 seconds and logs the cause, e.g.
`Telegram API unavailable (getUpdates: URLError: timed out).` A failed reply (e.g.
a button pressed on an old message) is logged as `Telegram reply failed (…)` and
polling continues.

## Run it without the Studio

Settings → Plugins runs the bot only while `alfrd serve` runs. To keep it
running regardless:

**systemd (user unit).** Save as `~/.config/systemd/user/alfrd-telegram.service`:

```ini
[Unit]
Description=ALFRD Telegram bot
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
ExecStart=%h/.local/bin/alfrd telegram run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

Adjust `ExecStart` to the output of `command -v alfrd`. Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now alfrd-telegram
journalctl --user -u alfrd-telegram -f    # its log
loginctl enable-linger "$USER"            # optional: keep running after you log out
```

`StartLimitBurst` stops the restarts after five failures in ten minutes, so a
revoked token doesn't loop forever (or add `RestartPreventExitStatus=2`). After a fix: `systemctl --user restart alfrd-telegram`.
The settings are read at start: restart the bot after changing them (the Studio
does this for the bot it runs). Don't also Start it in the Studio: one bot per token.

**nohup**, without systemd:

```bash
mkdir -p ~/.local/state
nohup alfrd telegram run >> ~/.local/state/alfrd-telegram.log 2>&1 &
echo $! > ~/.local/state/alfrd-telegram.pid     # stop: kill "$(cat ~/.local/state/alfrd-telegram.pid)"
```

This doesn't restart after a failure or a reboot; read the log if replies stop.

## Development

From the ALFRD checkout, no installation needed:

```bash
uv run pytest -q tests/test_telegram_plugin.py tests/test_telegram_notifier.py tests/test_studio_notify.py
PYTHONPATH=examples/plugins/alfrd-telegram uv run pytest -q examples/plugins/alfrd-telegram/tests
```

The tests fake the Telegram API; they make no network calls.
