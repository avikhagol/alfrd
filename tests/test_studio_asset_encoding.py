"""Static encoding negotiation must preserve cache and range semantics."""
import gzip

from flask import Flask
from alfrd.gui.studio import studio


def test_static_asset_gzip_negotiation_and_revalidation():
    app = Flask(__name__)
    app.register_blueprint(studio)
    client = app.test_client()
    url = "/studio/js/app.js"
    plain = client.get(url)
    encoded = client.get(url, headers={"Accept-Encoding": "gzip"})
    assert encoded.status_code == 200
    assert encoded.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(encoded.data) == plain.data
    assert len(encoded.data) < len(plain.data)
    assert int(encoded.headers["Content-Length"]) == len(encoded.data)
    assert "Accept-Encoding" in encoded.headers["Vary"]
    assert "Accept-Encoding" in plain.headers["Vary"]
    assert encoded.headers["ETag"].startswith('W/')
    cached = client.get(url, headers={"Accept-Encoding": "gzip", "If-None-Match": encoded.headers["ETag"]})
    assert cached.status_code == 304
    assert not cached.data
    refused = client.get(url, headers={"Accept-Encoding": "gzip;q=0"})
    assert "Content-Encoding" not in refused.headers
    assert refused.data == plain.data
    partial = client.get(url, headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
    assert partial.status_code == 206
    assert "Content-Encoding" not in partial.headers
    assert partial.data == plain.data[:10]
