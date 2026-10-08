"""Plugins Phase 4: the catalog (fetch, schema, cache, merge) and its Studio route."""
from __future__ import annotations

import copy
import json

import pytest

from alfrd import extensions
from alfrd.extensions import catalog
from test_studio import studio_app  # noqa: F401  (fixture)

SHA = "a" * 64
CATALOG = {
    "schema_version": 1,
    "name": "Test catalog",
    "plugins": [
        {"id": "markdown", "title": "Markdown", "description": "Render .md", "kinds": ["viewer"],
         "alfrd_api": ">=1,<2", "homepage": "https://example.org/md",
         "versions": [{"version": "0.1.0", "wheel": "file:///w/alfrd_markdown-0.1.0-py3-none-any.whl", "sha256": SHA},
                      {"version": "0.2.0", "wheel": "https://example.org/alfrd_markdown-0.2.0-py3-none-any.whl",
                       "sha256": "b" * 64, "requirements": [f"mistune==3.0.2 --hash=sha256:{'c' * 64}"]}]},
        {"id": "ps2pdf", "title": "PostScript to PDF", "kinds": ["converter"], "alfrd_api": ">=1",
         "requires_bin": ["surely-not-a-binary-xyz"],
         "versions": [{"version": "1.0", "wheel": "file:///w/alfrd_ps2pdf-1.0-py3-none-any.whl", "sha256": SHA}]},
        {"id": "future", "title": "Future", "kinds": ["theme"], "alfrd_api": ">=2",
         "versions": [{"version": "1", "wheel": "file:///w/future-1-py3-none-any.whl", "sha256": SHA}]},
    ],
}


def _use(tmp_path, data, name="index.json"):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    extensions.state_file().parent.mkdir(parents=True, exist_ok=True)
    extensions.state_file().write_text(json.dumps({"catalog_url": path.as_uri()}))
    return path


@pytest.mark.parametrize("mutate, message", [
    (lambda c: c["plugins"][0]["versions"][0].pop("sha256"), "sha256"),
    (lambda c: c["plugins"][0]["versions"][0].update(sha256="ABC"), "does not match"),
    (lambda c: c["plugins"][0]["versions"][0].update(wheel="http://example.org/x.whl"), "does not match"),
    (lambda c: c["plugins"][0].update(id="Bad Id"), "does not match"),
    (lambda c: c["plugins"][1].update(id="markdown"), "duplicate id"),
    (lambda c: c["plugins"][1]["versions"].append(dict(c["plugins"][1]["versions"][0], version="1.0.0")),
     "duplicate version"),
    (lambda c: c["plugins"][0]["versions"][1].update(requirements=["mistune>=3"]), "does not match"),
    (lambda c: c.update(schema_version=2), "1 was expected"),
    (lambda c: c["plugins"][0].update(extra=1), "Additional properties"),
])
def test_validate_rejects_bad_catalogs(mutate, message):
    data = copy.deepcopy(CATALOG)
    catalog.validate(copy.deepcopy(CATALOG))
    mutate(data)
    with pytest.raises(catalog.CatalogError, match=message):
        catalog.validate(data)


def test_no_url_means_no_catalog_and_http_is_ignored(tmp_path):
    assert catalog.get()["catalog"] is None and catalog.get()["url"] is None
    extensions.state_file().parent.mkdir(parents=True, exist_ok=True)
    extensions.state_file().write_text(json.dumps({"catalog_url": "http://example.org/index.json"}))
    assert catalog.get() == {"url": None, "fetched_at": None, "stale": False, "error": None, "catalog": None}


def test_fetch_caches_for_a_day_and_refresh_refetches(tmp_path):
    path = _use(tmp_path, CATALOG)
    first = catalog.get(now=1000.0)
    assert first["catalog"]["name"] == "Test catalog" and first["fetched_at"] == 1000.0 and not first["stale"]
    assert json.loads(catalog.cache_file().read_text())["url"] == path.as_uri()
    path.write_text(json.dumps(dict(CATALOG, name="Changed")))
    assert catalog.get(now=1000.0 + catalog.TTL - 1)["catalog"]["name"] == "Test catalog"  # cached
    assert catalog.get(now=1001.0, refresh=True)["catalog"]["name"] == "Changed"
    path.write_text(json.dumps(dict(CATALOG, name="Later")))
    assert catalog.get(now=1001.0 + catalog.TTL)["catalog"]["name"] == "Later"  # expired


def test_failed_refresh_serves_the_last_good_copy_as_stale(tmp_path):
    path = _use(tmp_path, CATALOG)
    catalog.get(now=1.0)
    path.write_text("{not json")
    got = catalog.get(refresh=True, now=2.0)
    assert got["stale"] and "not JSON" in got["error"] and got["catalog"]["name"] == "Test catalog"
    catalog.cache_file().unlink()
    got = catalog.get(refresh=True, now=3.0)
    assert got["catalog"] is None and "not JSON" in got["error"]
    path.unlink()
    assert "Cannot fetch" in catalog.get(refresh=True)["error"]


def test_cache_for_another_url_is_not_used(tmp_path):
    _use(tmp_path, CATALOG)
    catalog.get(now=1.0)
    other = _use(tmp_path, dict(CATALOG, name="Other"), "other.json")
    assert catalog.get(now=2.0)["catalog"]["name"] == "Other"
    assert json.loads(catalog.cache_file().read_text())["url"] == other.as_uri()


def test_oversized_catalog_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_BYTES", 100)
    _use(tmp_path, CATALOG)
    assert "larger than" in catalog.get()["error"]


def test_merge_reports_updates_compatibility_and_binaries():
    rows = {r["id"]: r for r in catalog.merged(CATALOG, {"markdown": {"version": "0.1.0", "source": "x.whl"}})}
    md = rows["markdown"]
    assert md["installed"] == "0.1.0" and md["latest"] == "0.2.0" and md["update_available"] and md["compatible"]
    assert [v["version"] for v in md["versions"]] == ["0.2.0", "0.1.0"]
    assert rows["ps2pdf"]["installed"] is None and not rows["ps2pdf"]["update_available"]
    assert rows["ps2pdf"]["missing_bin"] == ["surely-not-a-binary-xyz"]
    assert rows["future"]["compatible"] is False
    entry, version = catalog.find(CATALOG, "markdown")
    assert version["version"] == "0.2.0" and catalog.find(CATALOG, "markdown", "0.1.0")[1]["sha256"] == SHA
    with pytest.raises(catalog.CatalogError):
        catalog.find(CATALOG, "markdown", "9.9")
    with pytest.raises(catalog.CatalogError):
        catalog.find(None, "markdown")


def test_catalog_route_merges_installed_state(studio_app, tmp_path):
    app, _ = studio_app
    client = app.test_client()
    empty = client.get("/api/studio/plugins/catalog").get_json()
    assert empty["catalog_url"] is None and empty["plugins"] == [] and empty["gui_install"] is True and empty["user"]
    _use(tmp_path, CATALOG)
    extensions.plugins_dir().mkdir(parents=True, exist_ok=True)
    (extensions.plugins_dir() / "installed.json").write_text(json.dumps({"markdown": {"version": "0.1.0"}}))
    data = client.get("/api/studio/plugins/catalog").get_json()
    assert data["name"] == "Test catalog" and data["fetched_at"] and not data["stale"]
    assert {p["id"]: p["update_available"] for p in data["plugins"]} == {"markdown": True, "ps2pdf": False,
                                                                        "future": False}
    _use(tmp_path, "[]")
    data = client.get("/api/studio/plugins/catalog?refresh=1").get_json()
    assert data["stale"] and "Invalid plugin catalog" in data["error"] and len(data["plugins"]) == 3
