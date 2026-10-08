"""The plugin catalog: a curated ``index.json`` the Studio's Browse tab lists.

``plugins.json`` names it with ``catalog_url`` (https or file; there is no
default). The server fetches it, checks it against
``schemas/plugin_catalog.v1.json`` and caches it in ``plugins/catalog.json``
for :data:`TTL` seconds; a failed refresh keeps serving the last good copy
marked ``stale``. Catalog installs are pinned to a listed version and sha256.
"""
from __future__ import annotations

from datetime import datetime, timezone
from importlib import resources
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname, urlopen

from . import _version, api_ok, catalog_url_ok, plugins_dir, read_state

TTL = 24 * 3600
TIMEOUT = 20
#: index.json is metadata only; anything larger is not a catalog.
MAX_BYTES = 2 * 1024 * 1024


class CatalogError(RuntimeError):
    """The catalog can't be fetched, parsed or validated (user-facing message)."""


def _key(version: str) -> tuple[int, ...]:
    """Numeric version order; ``1.0`` and ``1.0.0`` are the same version."""
    parts = list(_version(version))
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def cache_file() -> Path:
    return plugins_dir() / "catalog.json"


def _schema() -> dict:
    return json.loads(resources.files("alfrd.schemas").joinpath("plugin_catalog.v1.json").read_text(encoding="utf-8"))


def validate(data: Any) -> dict:
    """Raise :class:`CatalogError` naming the first schema error; duplicate ids/versions are errors too."""
    import jsonschema

    error = jsonschema.exceptions.best_match(jsonschema.Draft202012Validator(_schema()).iter_errors(data))
    if error is not None:
        where = "/".join(str(p) for p in error.absolute_path) or "(top)"
        raise CatalogError(f"Invalid plugin catalog at {where}: {error.message}")
    seen: set[str] = set()
    for entry in data["plugins"]:
        if entry["id"] in seen:
            raise CatalogError(f"Invalid plugin catalog: duplicate id {entry['id']!r}")
        seen.add(entry["id"])
        versions = [v["version"] for v in entry["versions"]]
        if len(set(map(_key, versions))) != len(versions):
            raise CatalogError(f"Invalid plugin catalog: duplicate version for {entry['id']!r}")
        try:
            api_ok(entry["alfrd_api"])
        except ValueError as exc:
            raise CatalogError(f"Invalid plugin catalog: {entry['id']}: {exc}") from exc
    return data


def open_url(url: str, limit: int, timeout: float = TIMEOUT):
    """A binary stream for an https or file URL; the caller reads at most ``limit`` + 1 bytes."""
    if not catalog_url_ok(url):
        raise CatalogError(f"Only https:// and file:// URLs are allowed: {url!r}")
    parts = urlsplit(url)
    if parts.scheme.lower() == "file":
        if parts.netloc not in ("", "localhost"):
            raise CatalogError(f"file:// URLs must be local: {url!r}")
        return open(url2pathname(parts.path), "rb")
    return urlopen(url, timeout=timeout)  # noqa: S310 - scheme checked above (https only)


def fetch(url: str) -> dict:
    try:
        with open_url(url, MAX_BYTES) as stream:
            raw = stream.read(MAX_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise CatalogError(f"Cannot fetch the plugin catalog {url}: {exc}") from exc
    if len(raw) > MAX_BYTES:
        raise CatalogError(f"Plugin catalog is larger than {MAX_BYTES // 1024} KiB: {url}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise CatalogError(f"Plugin catalog is not JSON: {exc}") from exc
    return validate(data)


def _read_cache() -> dict | None:
    try:
        data = json.loads(cache_file().read_text(encoding="utf-8"))
        validate(data["catalog"])
        return data if isinstance(data.get("url"), str) and isinstance(data.get("fetched_at"), (int, float)) else None
    except (OSError, ValueError, KeyError, TypeError, CatalogError):
        return None


def _write_cache(entry: dict) -> None:
    folder = plugins_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(entry, stream, indent=1, sort_keys=True)
        os.replace(tmp, cache_file())
    finally:
        Path(tmp).unlink(missing_ok=True)


def get(*, refresh: bool = False, now: float | None = None) -> dict:
    """``{url, fetched_at, stale, error, catalog}``; ``catalog`` is None without a URL or any good copy."""
    url = read_state()["catalog_url"]
    out: dict[str, Any] = {"url": url, "fetched_at": None, "stale": False, "error": None, "catalog": None}
    if not url:
        return out
    now = time.time() if now is None else now
    cached = _read_cache()
    if cached and cached["url"] != url:
        cached = None
    if cached and not refresh and now - cached["fetched_at"] < TTL:
        return {**out, "fetched_at": cached["fetched_at"], "catalog": cached["catalog"]}
    try:
        catalog = fetch(url)
    except CatalogError as exc:
        if cached:
            return {**out, "fetched_at": cached["fetched_at"], "catalog": cached["catalog"], "stale": True, "error": str(exc)}
        return {**out, "error": str(exc)}
    _write_cache({"url": url, "fetched_at": now, "catalog": catalog})
    return {**out, "fetched_at": now, "catalog": catalog}


def latest(entry: dict) -> dict:
    return max(entry["versions"], key=lambda v: _key(v["version"]))


def find(catalog: dict | None, plugin_id: str, version: str | None = None) -> tuple[dict, dict]:
    """The catalog entry and one of its versions (the newest when ``version`` is None)."""
    entry = next((p for p in (catalog or {}).get("plugins", []) if p["id"] == plugin_id), None)
    if entry is None:
        raise CatalogError(f"{plugin_id!r} is not in the plugin catalog")
    if version is None:
        return entry, latest(entry)
    chosen = next((v for v in entry["versions"] if v["version"] == version), None)
    if chosen is None:
        raise CatalogError(f"{plugin_id} {version} is not in the plugin catalog")
    return entry, chosen


def merged(catalog: dict | None, inventory: dict[str, dict]) -> list[dict]:
    """Catalog entries with the installed version, ``update_available``, ``compatible`` and ``missing_bin``."""
    out = []
    for entry in (catalog or {}).get("plugins", []):
        newest = latest(entry)
        have = (inventory.get(entry["id"]) or {}).get("version")
        try:
            update = bool(have) and _key(newest["version"]) > _key(have)
        except ValueError:  # a non-numeric installed version: offer the catalog one
            update = bool(have) and newest["version"] != have
        out.append({
            **{k: entry.get(k) for k in ("id", "title", "description", "kinds", "alfrd_api", "homepage")},
            "requires_bin": entry.get("requires_bin", []),
            "versions": [{"version": v["version"], "wheel": v["wheel"], "sha256": v["sha256"]}
                         for v in sorted(entry["versions"], key=lambda v: _key(v["version"]), reverse=True)],
            "latest": newest["version"],
            "installed": have,
            "installed_source": (inventory.get(entry["id"]) or {}).get("source"),
            "update_available": update,
            "compatible": api_ok(entry["alfrd_api"]),
            "missing_bin": [b for b in entry.get("requires_bin", []) if shutil.which(b) is None],
        })
    return out


def fetched_iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None
