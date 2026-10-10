"""Injected Sheets contract and deadline-bounded service-account REST client.

All calls receive an absolute monotonic deadline. A transport must cap *all*
attempts, authentication, backoff and request timeouts by that deadline, and
raise SyncError with safe text rather than propagating HTTP bodies or secrets.
Values are ROWS-oriented, UNFORMATTED_VALUE; batch_get preserves range order.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections import OrderedDict
from queue import Empty, Queue
from typing import Any, Protocol
from urllib.parse import quote


class SyncError(ValueError):
    """A safe, one-line message suitable for the runner and sync history."""


class SheetsClient(Protocol):
    def get(self, spreadsheet_id: str, range_: str, *, deadline: float) -> list[list[Any]]: ...

    def batch_get(self, spreadsheet_id: str, ranges: list[str], *, deadline: float) -> list[list[list[Any]]]: ...

    def batch_update(self, spreadsheet_id: str, data: list[dict[str, Any]], *,
                     value_input_option: str, deadline: float) -> None: ...

    def worksheet_title(self, spreadsheet_id: str, gid: int, *, deadline: float) -> str: ...

    def metadata(self, spreadsheet_id: str, *, deadline: float) -> dict[str, Any]: ...

    def create_columns(self, spreadsheet_id: str, *, sheet_id: int, header_row: int,
                       start: int, names: list[str], deadline: float) -> None: ...


SCOPES = ("https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive.metadata.readonly")
_cache: OrderedDict[str, tuple[Any, threading.Lock]] = OrderedDict()
_cache_lock = threading.Lock()


def credentials_info(value: Any) -> dict[str, Any]:
    try:
        info = json.loads(value)
    except (ValueError, TypeError):
        raise SyncError("Service-account credentials must be valid JSON.") from None
    if (not isinstance(info, dict) or info.get("type") != "service_account"
            or not isinstance(info.get("client_email"), str) or "@" not in info["client_email"]
            or not isinstance(info.get("private_key"), str) or not info["private_key"]):
        raise SyncError("Credentials must contain a service-account client_email and private_key.")
    # Do not allow pasted credentials to redirect a signed assertion to another host.
    if info.get("token_uri") != "https://oauth2.googleapis.com/token":
        raise SyncError("Credentials must use Google's service-account token endpoint.")
    return info


_DISABLED = {"SERVICE_DISABLED", "accessNotConfigured"}
_QUOTA = {"RATE_LIMIT_EXCEEDED", "rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}


def _denied(url: str, response: Any) -> str:
    """A safe 403 message from Google's machine-readable reason; never the response text."""
    api = "Drive API" if "//www.googleapis.com/drive/" in url else "Sheets API"
    try:
        error = response.json().get("error") or {}
    except Exception:  # noqa: BLE001
        error = {}
    if not isinstance(error, dict):
        error = {}
    reasons, project = set(), ""
    for item in (error.get("details") or []) + (error.get("errors") or []):
        if isinstance(item, dict):
            reasons.add(str(item.get("reason") or ""))
            meta = item.get("metadata")
            if isinstance(meta, dict) and str(meta.get("consumer", "")).startswith("projects/"):
                digits = str(meta["consumer"])[len("projects/"):]
                project = digits if digits.isdigit() else project
    if reasons & _DISABLED:
        where = f" (project {project})" if project else ""
        link = (f": https://console.developers.google.com/apis/api/{'drive' if api == 'Drive API' else 'sheets'}"
                f".googleapis.com/overview?project={project}" if project else ".")
        hint = " Or untick 'Check edit permission with the Drive API'." if api == "Drive API" else ""
        return f"Google denied access (403): the {api} is disabled in the key's Google Cloud project{where}; enable it{link}{hint}"
    if reasons & _QUOTA:
        return f"Google denied access (403): {api} quota exceeded; retry later."
    return f"Google denied access (403, {api}): share the sheet with the service account's client_email as Editor."


def remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise SyncError("Google Sheet request deadline exceeded.")
    return left


def _bounded(call, deadline):
    """Bound wall time as well as socket inactivity (including auth/library backoff).

    A timed-out worker may finish an already-sent request, but every subsequent
    authentication/request/retry checks the original deadline before doing work.
    """
    out = Queue(maxsize=1)

    def run():
        try:
            out.put((True, call()))
        except Exception as exc:  # noqa: BLE001 - never leak library errors
            out.put((False, exc if isinstance(exc, SyncError) else
                     SyncError("Google Sheet authentication or network request failed.")))

    left = remaining(deadline)
    threading.Thread(target=run, daemon=True).start()
    try:
        ok, result = out.get(timeout=left)
    except Empty:
        raise SyncError("Google Sheet request deadline exceeded.") from None
    remaining(deadline)
    if not ok:
        raise result from None
    return result


def _credentials(raw: str, deadline: float):
    from google.oauth2.service_account import Credentials

    key = hashlib.sha256(raw.encode()).hexdigest()
    if not _cache_lock.acquire(timeout=remaining(deadline)):
        raise SyncError("Google Sheet request deadline exceeded.")
    try:
        if key not in _cache:
            try:
                creds = Credentials.from_service_account_info(credentials_info(raw), scopes=SCOPES)
            except SyncError:
                raise
            except Exception:  # noqa: BLE001
                raise SyncError("Service-account private key is invalid.") from None
            _cache[key] = creds, threading.Lock()
            if len(_cache) > 8:
                _cache.popitem(last=False)
        _cache.move_to_end(key)
        return _cache[key]
    finally:
        _cache_lock.release()


class RestClient:
    """Lazy REST client with a fresh session per API call and cached credentials.

    Factories are injectable so tests exercise the real REST/retry paths offline.
    """

    def __init__(self, credentials_json: str, request_timeout: float = 15, *,
                 session_factory=None, credential_factory=None):
        self._raw = credentials_json
        self.request_timeout = request_timeout
        self._session_factory = session_factory
        self._credential_factory = credential_factory or _credentials

    def _request(self, method, url, *, deadline, attempts=3, **kwargs):
        def perform():
            remaining(deadline)
            creds, lock = self._credential_factory(self._raw, deadline)
            factory = self._session_factory
            if factory is None:
                from requests import Session
                factory = Session
            with factory() as session:
                session.trust_env = False  # no ambient netrc overriding the bearer token
                auth_transport = None

                def auth_request(*args, **auth_kwargs):
                    from google.auth.transport.requests import Request

                    nonlocal auth_transport
                    if auth_transport is None:
                        auth_transport = Request(session=session)
                    auth_kwargs["timeout"] = min(self.request_timeout, remaining(deadline))
                    return auth_transport(*args, **auth_kwargs)

                for attempt in range(attempts):
                    headers = {}
                    if not lock.acquire(timeout=remaining(deadline)):
                        raise SyncError("Google Sheet request deadline exceeded.")
                    try:
                        # Use synchronous OAuth refresh/apply. before_request in
                        # google-auth 2.61 starts background regional lookups,
                        # which would outlive this session/deadline.
                        if not creds.valid:
                            creds.refresh(auth_request)
                        creds.apply(headers)
                    except SyncError:
                        raise
                    except Exception:  # noqa: BLE001
                        raise SyncError("Service-account authentication failed; check the key and account.") from None
                    finally:
                        lock.release()
                    response = session.request(method, url, headers=headers, allow_redirects=False,
                                               timeout=min(self.request_timeout, remaining(deadline)), **kwargs)
                    remaining(deadline)
                    status = response.status_code
                    try:
                        if 200 <= status < 300:
                            try:
                                data = response.json()
                            except ValueError:
                                raise SyncError("Google returned an invalid response.") from None
                            if not isinstance(data, dict):
                                raise SyncError("Google returned an invalid response.")
                            return data
                        if status == 403:
                            raise SyncError(_denied(url, response))
                        if status == 404:
                            raise SyncError("Google Sheet not found (404): check the id and service-account sharing.")
                        if status == 401:
                            raise SyncError("Google rejected the service-account credentials (401).")
                        if status != 429 and not 500 <= status < 600:
                            raise SyncError(f"Google Sheet request failed (HTTP {status}).")
                        if attempt == attempts - 1:
                            raise SyncError(f"Google Sheet unavailable after {attempts} attempts (HTTP {status}).")
                    finally:
                        response.close()
                    delay = 0.5 * 2 ** attempt + random.uniform(0, 0.25)
                    if delay >= remaining(deadline):
                        raise SyncError("Google Sheet request deadline exceeded.")
                    time.sleep(delay)
        return _bounded(perform, deadline)

    @staticmethod
    def _url(spreadsheet_id):
        return "https://sheets.googleapis.com/v4/spreadsheets/" + quote(spreadsheet_id, safe="")

    def get(self, spreadsheet_id, range_, *, deadline):
        data = self._request("GET", self._url(spreadsheet_id) + "/values/" + quote(range_, safe=""),
                             deadline=deadline, params={"majorDimension": "ROWS",
                                                        "valueRenderOption": "UNFORMATTED_VALUE"})
        return self._rows(data)

    @staticmethod
    def _rows(data):
        rows = data.get("values", [])
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise SyncError("Google returned invalid cell values.")
        return rows

    def batch_get(self, spreadsheet_id, ranges, *, deadline):
        if not ranges:
            return []
        data = self._request("GET", self._url(spreadsheet_id) + "/values:batchGet", deadline=deadline,
                             params={"ranges": ranges, "majorDimension": "ROWS",
                                     "valueRenderOption": "UNFORMATTED_VALUE"})
        values = data.get("valueRanges")
        if (not isinstance(values, list) or len(values) != len(ranges)
                or any(not isinstance(item, dict) for item in values)):
            raise SyncError("Google returned invalid batch ranges.")
        return [self._rows(item) for item in values]

    def batch_update(self, spreadsheet_id, data, *, value_input_option, deadline):
        if data:
            self._request("POST", self._url(spreadsheet_id) + "/values:batchUpdate", deadline=deadline,
                          json={"valueInputOption": value_input_option, "data": data})

    def create_columns(self, spreadsheet_id, *, sheet_id, header_row, start, names, deadline):
        """Insert fresh columns and set literal headers atomically, with no unsafe replay.

        Insertion preserves cells even if another editor adds columns after our
        read. A lost response must be reconciled by a new preview before retrying.
        """
        requests = [
            {"insertDimension": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                                             "startIndex": start, "endIndex": start + len(names)},
                                 "inheritFromBefore": start > 0}},
            {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": header_row - 1, "columnIndex": start},
                             "rows": [{"values": [{"userEnteredValue": {"stringValue": name}} for name in names]}],
                             "fields": "userEnteredValue"}},
        ]
        self._request("POST", self._url(spreadsheet_id) + ":batchUpdate", deadline=deadline,
                      attempts=1, json={"requests": requests})

    def metadata(self, spreadsheet_id, *, deadline):
        data = self._request("GET", self._url(spreadsheet_id), deadline=deadline,
                             params={"fields": "properties(title),sheets(properties(sheetId,title,gridProperties))"})
        sheets = data.get("sheets")
        if (not isinstance(sheets, list) or any(not isinstance(sheet, dict)
                or not isinstance(sheet.get("properties"), dict) for sheet in sheets)):
            raise SyncError("Google returned invalid spreadsheet metadata.")
        return data

    def worksheet_title(self, spreadsheet_id, gid, *, deadline):
        data = self.metadata(spreadsheet_id, deadline=deadline)
        for sheet in data.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("sheetId") == gid and isinstance(props.get("title"), str):
                return props["title"]
        raise SyncError("Worksheet gid was not found in the Google Sheet.")

    def can_edit(self, spreadsheet_id, *, deadline):
        data = self._request("GET", "https://www.googleapis.com/drive/v3/files/" + quote(spreadsheet_id, safe=""),
                             deadline=deadline, params={"fields": "capabilities/canEdit", "supportsAllDrives": "true"})
        capabilities = data.get("capabilities", {})
        value = capabilities.get("canEdit") if isinstance(capabilities, dict) else None
        if not isinstance(value, bool):
            raise SyncError("Google did not report edit permission.")
        return value
