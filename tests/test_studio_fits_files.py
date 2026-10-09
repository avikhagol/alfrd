"""FITS suggestions use configured storage, bounded traversal and basename output."""
from pathlib import Path

import pytest

from alfrd.gui import create_app
from alfrd.runtime import RuntimeService, RuntimeStore


@pytest.fixture
def fits_client(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.initialize()
    service = RuntimeService(store)
    root = tmp_path / "project"
    root.mkdir()
    row = service.create_project("fits", root)
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.sqlite'}", "RUNTIME_SERVICE": service})
    client = app.test_client()
    with client.session_transaction() as session:
        session["_alfrd_csrf_token"] = "test"
    return client, root, f"/api/studio/avica/{row.identifier}/fits-files"


def test_fits_depth_extensions_and_basenames(fits_client):
    client, root, url = fits_client
    (root / "avica.inp").write_text("folder_for_fits = fits\n")
    folder = root / "fits"
    for relative in ["a.fits", "b.FITS", "c.idifits", "d.IDIFITS", "e.idi1", "f.IDI", "ignored.txt", ".hidden.fits", ".dot/hidden.fits", "one/two/three/depth.fits", "one/two/three/four/too-deep.fits", "one/a.fits"]:
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    data = client.get(url).get_json()
    assert data == {"files": ["a.fits", "b.FITS", "c.idifits", "d.IDIFITS", "depth.fits", "e.idi1", "f.IDI"], "truncated": False}
    assert client.get(url + "?dir=/tmp").status_code == 400


def test_fits_cap(fits_client):
    client, root, url = fits_client
    (root / "avica.inp").write_text("folder_for_fits = .\n")
    for i in range(2001):
        (root / f"{i:04}.fits").touch()
    data = client.get(url).get_json()
    assert len(data["files"]) == 2000 and data["truncated"]
    assert data["files"] == sorted(data["files"])


def test_fits_unset_variables_empty_root_and_missing(fits_client):
    client, root, url = fits_client
    assert client.get(url).get_json()["note"] == "folder_for_fits is unset"
    (root / "avica.inp").write_text("folder_for_fits = $DATA/fits\n")
    assert client.get(url).get_json()["files"] == []
    (root / "avica.inp").write_text("folder_for_fits = absent\n")
    assert client.get(url).status_code == 404
    (root / "avica.inp").write_text('folder_for_fits = ""\n')
    (root / "root.fits").touch()
    assert client.get(url).get_json()["files"] == ["root.fits"]


def test_fits_external_folder_requires_csrf(fits_client, tmp_path):
    client, root, url = fits_client
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "external.fits").touch()
    (root / "avica.inp").write_text(f"folder_for_fits = {outside}\n")
    assert client.get(url).status_code == 403
    assert client.get(url, headers={"X-CSRF-Token": "test"}).get_json()["files"] == ["external.fits"]


def test_fits_permission_error(fits_client, monkeypatch):
    client, root, url = fits_client
    (root / "avica.inp").write_text("folder_for_fits = .\n")
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr("os.walk", denied)
    assert client.get(url).status_code == 403
