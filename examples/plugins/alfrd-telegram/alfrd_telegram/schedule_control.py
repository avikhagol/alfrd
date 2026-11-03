"""Telegram command adapter for Studio's durable scheduled batches."""

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from alfrd import chat


USAGE = ("Use /schedule in 2h <text> (m/h/d), or /schedule YYYY-MM-DD HH:MM <text>. "
         "Default timezone: Europe/Athens. Use an ISO timestamp with UTC offset, "
         "or put an IANA timezone after HH:MM, e.g. Europe/London.")


def parse_schedule(value, *, now=None):
    """Return an aware timestamp, display timezone and lossless request text."""
    now = now or datetime.now(timezone.utc)
    relative = re.fullmatch(r"in\s+([1-9][0-9]*)([mhd])\s+([\s\S]+)", value.strip(), re.I)
    zone = "Europe/Athens"
    try:
        if relative:
            count, unit, text = relative.groups()
            dt = now + timedelta(seconds=int(count) * {"m": 60, "h": 3600, "d": 86400}[unit.lower()])
        else:
            absolute = re.fullmatch(
                r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)\s+([\s\S]+)",
                value.strip())
            if not absolute:
                raise ValueError(USAGE)
            date, clock, text = absolute.groups()
            # A timezone token is optional; ordinary text is never consumed.
            token, _, rest = text.partition(" ")
            if token == "UTC" or re.fullmatch(r"[A-Za-z_]+/[A-Za-z_/+-]+", token):
                ZoneInfo(token)
                zone, text = token, rest
            dt = datetime.fromisoformat(f"{date}T{clock}".replace("Z", "+00:00"))
            if dt.tzinfo is None:
                local = dt.replace(tzinfo=ZoneInfo(zone))
                if local.astimezone(timezone.utc).astimezone(local.tzinfo).replace(tzinfo=None) != dt:
                    raise ValueError("This local time does not exist because of daylight saving time; use a UTC offset.")
                if local.utcoffset() != local.replace(fold=1).utcoffset():
                    raise ValueError("This local time is ambiguous because of daylight saving time; use a UTC offset.")
                dt = local
        if not text.strip():
            raise ValueError("A scheduled message needs text. " + USAGE)
        if dt <= now:
            raise ValueError("Schedule time must be in the future.")
        return dt.isoformat(), zone, text.strip()
    except (OverflowError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Invalid schedule time or timezone. " + USAGE) from exc
    except ValueError as exc:
        if str(exc).startswith(("Use /", "This local", "A scheduled", "Schedule time")):
            raise
        raise ValueError("Invalid schedule time. " + USAGE) from exc


class ScheduleControl:
    def is_superuser(self, sender_id):
        admins = re.split(r"[\s,]+", str(self.route.get("admin_chat_ids") or "").strip())
        return sender_id is not None and str(sender_id).isdigit() and int(sender_id) > 0 and str(sender_id) in admins

    def telegram_schedules(self, sender_id):
        prefix = f"telegram.{self.chat}.{sender_id}."
        for project, info in self.plans.projects().items():
            for item in chat.list_sessions(info["root"]):
                store = chat.session(info["root"], item["id"])
                for batch in store.load_schedule()["batches"]:
                    if batch.get("client_schedule_id", "").startswith(prefix):
                        yield project, store, batch

    def schedule_command(self, command, value, sender_id, message_id):
        if not self.is_superuser(sender_id):
            return self.send("Only a configured superuser can schedule, list or cancel scheduled messages.")
        if command == "/scheduled":
            if value:
                return self.send("Use /scheduled to list your pending messages.")
            pending = sorted((b for _, _, b in self.telegram_schedules(sender_id)
                              if b["status"] in chat.SCHEDULE_OPEN), key=lambda b: b["run_at"])
            return self.text_parts("\n\n".join(
                f"{b['id']} · {datetime.fromisoformat(b['run_at'].replace('Z', '+00:00')).astimezone(ZoneInfo(b['timezone'])).isoformat()} "
                f"({b['timezone']}) · {b['status']}\n" + "\n".join(i["text"] for i in b["items"] if not i.get("removed_at"))
                for b in pending) or "No pending scheduled messages.")
        if command == "/unschedule":
            if not re.fullmatch(r"sch_[a-f0-9]{32}", value):
                return self.send("Use /unschedule <sch_id> from /scheduled.")
            for _, store, batch in self.telegram_schedules(sender_id):
                if batch["id"] == value and batch["status"] in chat.SCHEDULE_OPEN:
                    try:
                        chat.mutate_schedule(store.root, store.id, value, "cancel",
                                             expected_schedule_revision=store.load_schedule()["revision"],
                                             operation_id="telegram.cancel." + secrets.token_hex(12))
                    except chat.ScheduleConflictError:
                        return self.send("The schedule changed or started running. Refresh /scheduled and try again.")
                    return self.text_parts(f"Cancelled {value}.")
            return self.send("No pending schedule with that ID belongs to you in this chat.")
        cid = f"telegram.{self.chat}.{sender_id}." + (str(message_id) if message_id is not None else secrets.token_hex(12))
        # Look up the receipt before parsing relative time: update replay must not shift it.
        for _, _, batch in self.telegram_schedules(sender_id):
            if batch["client_schedule_id"] == cid:
                return self.text_parts(f"Already scheduled: {batch['id']} · {batch['status']}.")
        run_at, zone, text = parse_schedule(value)
        self.project()  # Resolve ambiguity before creating anything.
        project = self.selected
        root = self.plans.projects()[project]["root"]
        data = chat.create_session(root, "Telegram schedule: " + text[:60])
        store = chat.session(root, data["id"])
        # Persist reply routing before publishing work to the scheduler.
        self.chat_cursors[json.dumps([project, store.id])] = 0
        self.save_state(strict=True)
        with store.lock():
            out = chat.create_schedule(root, store.id, run_at=run_at, timezone=zone,
                                       client_schedule_id=cid, expected_schedule_revision=0)
            bid = out["selected_schedule_id"]
            try:
                chat.post_message(root, store.id, text, client_message_id=cid, schedule_id=bid,
                                  expected_schedule_revision=out["schedule_revision"])
            except Exception:
                chat.mutate_schedule(root, store.id, bid, "cancel",
                                     expected_schedule_revision=store.load_schedule()["revision"],
                                     operation_id="telegram.cleanup." + secrets.token_hex(12))
                raise
        display_at = datetime.fromisoformat(run_at).astimezone(ZoneInfo(zone)).isoformat()
        self.text_parts(f"Scheduled {bid} for {display_at} ({zone}). Results will return to this chat. "
                        "Use /scheduled or /unschedule " + bid + ".")
