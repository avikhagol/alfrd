"""Telegram commands for ALFRD; the host provides the API and dependencies."""

import hashlib
import os
from pathlib import Path
from typing import Annotated

import typer

from alfrd import get_alfrd_dir, notifiers, notify
from alfrd.extensions import Plugin, Service, SettingField
from alfrd.runtime import RuntimeService, RuntimeStore

from .bot import Bot, Plans

PLUGIN_ID = "telegram"
#: Exit code for "fix the settings first": Settings → Plugins doesn't restart the bot after it.
CONFIG_ERROR = 2
CHAT_ID = r"-?[0-9]{1,20}"

cli = typer.Typer(help="Read plan status and confirm pause/resume from Telegram.")


@cli.callback()
def main():
    """Telegram bot commands."""


def configured_route():
    """``(route, where)``: Settings → Plugins → Telegram first, else the first telegram route in notify.json."""
    from alfrd.extensions import settings

    saved = settings.values(PLUGIN_ID)
    if saved.get("token") and saved.get("chat_id"):
        return {"via": "telegram", "token": str(saved["token"]), "chat_id": str(saved["chat_id"])}, "settings"
    routes, _ = notify.load_routes([], user_file=notify.user_config_file())
    route = next((r for r in routes if r["via"] == "telegram" and r.get("token") and r.get("chat_id")), None)
    return route, "notify.json" if route else None


def _lock(token: str):
    """Hold a per-token lock for the bot's lifetime: Telegram allows one getUpdates reader (else HTTP 409)."""
    try:
        import fcntl
    except ImportError:  # Windows: no lock; Telegram's 409 still stops a second reader
        return object()
    from alfrd.extensions import plugins_dir

    folder = plugins_dir() / "services"
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(token.encode()).hexdigest()[:12]
    handle = open(folder / f"telegram-{name}.lock", "w")  # noqa: SIM115 - kept open while the bot runs
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def status_file(chat: str) -> Path:
    """Where /status remembers the chat's project and targets (one file per chat)."""
    from alfrd.extensions import plugins_dir

    name = hashlib.sha256(chat.strip().encode()).hexdigest()[:12]
    return plugins_dir() / "telegram" / f"status-{name}.json"


@cli.command()
def run(db: Annotated[Path | None, typer.Option("--db", envvar="ALFRD_RUNTIME_DB",
                                                help="Runtime SQLite database.")] = None):
    """Long-poll Telegram with the token and chat id from Settings → Plugins (or notify.json)."""
    route, where = configured_route()
    if route is None:
        typer.echo("No Telegram settings: fill in Settings → Plugins → Telegram, "
                   "or add a telegram route to the user's notify.json.", err=True)
        raise typer.Exit(CONFIG_ERROR)
    if not str(route["chat_id"]).strip().lstrip("-").isdigit():
        typer.echo("The telegram route's chat_id must be the numeric chat id (see docs/notifications.md).", err=True)
        raise typer.Exit(CONFIG_ERROR)
    lock = _lock(str(route["token"]).strip())
    if lock is None:
        typer.echo("Another alfrd telegram bot is already running with this token "
                   "(a terminal, or Settings → Plugins → Telegram).", err=True)
        raise typer.Exit(1)
    try:  # Stop (SIGINT) can arrive while the database opens, too: exit quietly either way
        store = RuntimeStore(db or get_alfrd_dir() / "runtime.sqlite")
        store.initialize()
        bot = Bot(route, Plans(RuntimeService(store)), log=typer.echo, state=status_file(str(route["chat_id"])))
        source = "Settings → Plugins → Telegram" if where == "settings" else where
        typer.echo(f"Telegram bot started (token and chat from {source}; pid {os.getpid()}).")
        stopped = bot.run()
    except KeyboardInterrupt:
        typer.echo("Telegram bot stopped.")
        return
    if stopped:
        # 409: another reader may go away, so try again later; the others need new settings.
        raise typer.Exit(1 if stopped == 409 else CONFIG_ERROR)


def check(values):
    """Settings → Plugins → Test: who the bot is and whether it can reach the chat. Sends nothing."""
    token, chat = str(values.get("token") or "").strip(), str(values.get("chat_id") or "").strip()
    try:
        me = notifiers.telegram_call(token, "getMe", {}, 15) or {}
    except notifiers.TelegramError as exc:
        raise ValueError(_problem(exc, "the token")) from None
    try:
        info = notifiers.telegram_call(token, "getChat", {"chat_id": chat}, 15) or {}
    except notifiers.TelegramError as exc:
        raise ValueError(f"Token OK (@{me.get('username', '?')}), but " + _problem(exc, "the chat")) from None
    kind = info.get("type", "?")
    note = "" if kind == "private" else " Anyone in this chat can pause and resume plans: prefer a private chat."
    return f"Connected as @{me.get('username', '?')} to a {kind} chat.{note}"


def _problem(exc, what):
    if exc.status in (401, 404):
        return f"Telegram rejected {what} (HTTP {exc.status}). Copy the token from @BotFather again."
    if exc.status in (400, 403):
        return (f"Telegram can't reach {what} (HTTP {exc.status}). Send /start to the bot "
                "and check the numeric chat id.")
    return f"Telegram is unreachable ({'HTTP ' + str(exc.status) if exc.status else 'network error'})."


plugin = Plugin(
    id=PLUGIN_ID, version="0.2.0", alfrd_api=">=1,<2", title="Telegram",
    description="Plan status, logs and confirmed pause/resume in one allowed chat.", cli=cli,
    settings=[
        SettingField("token", "Bot token", kind="secret", required=True, pattern=r"[0-9]+:[A-Za-z0-9_-]{20,}",
                     help="From @BotFather, like 123456789:AAH…", placeholder="123456789:AAH…"),
        SettingField("chat_id", "Allowed chat id", required=True, pattern=CHAT_ID,
                     help="The numeric id of your private chat with the bot (see the getUpdates step in the README)",
                     placeholder="123456789"),
    ],
    services=[Service("bot", ("telegram", "run"), title="Bot",
                      description="Answers /status, /runs, /log, /pause and /resume in the allowed chat.")],
    check=check,
)
