"""Global settings and a read-only connection Test; errors never echo inputs."""

from __future__ import annotations

import math
import time

from alfrd.extensions import SettingField

from .client import RestClient, SyncError, credentials_info
from .mapping import spreadsheet_id

FIELDS = (
    SettingField("credentials_json", "Service-account key JSON", kind="secret", required=True,
                 help="Paste the JSON key contents. Share your sheet with the key's client_email."),
    SettingField("default_spreadsheet_id", "Default spreadsheet id or URL",
                 help="Used when the project mapping has no spreadsheet_id. Required for Test."),
    SettingField("value_input_option", "Value input option", pattern="RAW|USER_ENTERED",
                 help="RAW (default) stores literal values; USER_ENTERED lets Google interpret them."),
    SettingField("conflict_policy", "Conflict policy", pattern="overwrite|skip|fail-soft",
                 help="skip (default), overwrite, or fail-soft."),
    SettingField("request_timeout", "Request timeout (seconds)", kind="number",
                 help="Positive seconds per HTTP request, default 15; hooks have a shared 25-second budget."),
    SettingField("dry_run", "Dry run", kind="bool", help="Preview changes without writing to Sheets or the plan CSV."),
    SettingField("check_drive", "Check edit permission with the Drive API", kind="bool",
                 help="Test also asks Drive whether the account can edit (needs the Drive API enabled). "
                      "Off: Test checks read access only; edit access is verified on the first write."),
)


def normalize(values):
    out = dict(values)
    credentials_info(out.get("credentials_json"))
    if out.get("default_spreadsheet_id"):
        out["default_spreadsheet_id"] = spreadsheet_id(out["default_spreadsheet_id"])
    for key, default, allowed in (
        ("value_input_option", "RAW", ("RAW", "USER_ENTERED")),
        ("conflict_policy", "skip", ("overwrite", "skip", "fail-soft")),
    ):
        out[key] = out.get(key) or default
        if out[key] not in allowed:
            raise SyncError(f"Invalid {key}; choose {' or '.join(allowed)}.")
    try:
        value = out.get("request_timeout")
        timeout = float(15 if value in (None, "") else value)
    except (TypeError, ValueError):
        raise SyncError("Request timeout must be a positive number of seconds.") from None
    if isinstance(value, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise SyncError("Request timeout must be a positive number of seconds.")
    out["request_timeout"] = timeout
    if not isinstance(out.get("dry_run", False), bool):
        raise SyncError("Dry run must be true or false.")
    out.setdefault("dry_run", False)
    if not isinstance(out.get("check_drive", False), bool):
        raise SyncError("Check edit permission must be true or false.")
    out.setdefault("check_drive", False)
    return out


def make_client(values):
    saved = normalize(values)
    return RestClient(saved["credentials_json"], saved["request_timeout"])


class LazyClient:
    """No credential validation/import until the engine makes an API call."""

    def __init__(self, values):
        self.values = values
        self.client = None

    def __getattr__(self, name):
        def call(*args, **kwargs):
            if self.client is None:
                self.client = make_client(self.values)
            return getattr(self.client, name)(*args, **kwargs)
        return call


def make_engine(timeout: float = 25, *, values: dict | None = None):
    """The sync engine with the saved global settings and a lazy client (hooks and CLI)."""
    from alfrd.extensions import settings as store

    from .sync import Sync

    saved = dict(store.values("gsheet") if values is None else values)
    saved["value_input_option"] = saved.get("value_input_option") or "RAW"
    saved["conflict_policy"] = saved.get("conflict_policy") or "skip"
    return Sync(LazyClient(saved), saved, timeout=timeout)


def check(values):
    deadline = time.monotonic() + 25
    saved = normalize(values)
    sid = saved.get("default_spreadsheet_id")
    if not sid:
        raise SyncError("Set a default spreadsheet id or URL before running Test.")
    client = make_client(saved)
    metadata = client.metadata(sid, deadline=deadline)
    # Titles/email are untrusted input. Counts suffice for a safe one-line result.
    sheets = len(metadata.get("sheets", []))
    if not saved["check_drive"]:
        return (f"ok: service account can read the default spreadsheet ({sheets} sheets); "
                "edit access is verified on the first write.")
    if not client.can_edit(sid, deadline=deadline):
        raise SyncError("The service account has read-only access; share the sheet with Editor permission.")
    return f"ok: service account can edit the default spreadsheet ({sheets} sheets)."
