"""Optional pinned-library smoke test: real signing, fake HTTP, no Google calls."""

import json
import time

import pytest


def test_real_auth_refresh_is_synchronous_and_cached(gsheet):
    pytest.importorskip("google.oauth2.service_account")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    requests = pytest.importorskip("requests")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    raw = json.dumps({"type": "service_account", "client_email": "offline@example.com",
                      "token_uri": "https://oauth2.googleapis.com/token",
                      "private_key": key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()})
    calls = []

    class Session:
        closed = False

        def __enter__(self):
            return self

        def close(self):
            self.closed = True

        def __exit__(self, *args):
            self.close()

        def request(self, method, url, **kwargs):
            assert not self.closed  # auth Request must not destroy the API session early
            calls.append((method, url, kwargs["timeout"]))
            response = requests.Response()
            response.status_code = 200
            if url == "https://oauth2.googleapis.com/token":
                assert "assertion=" in str(kwargs["data"])
                response._content = b'{"access_token":"offline-token","expires_in":3600,"token_type":"Bearer"}'
            else:
                assert url.startswith("https://sheets.googleapis.com/")  # no background IAM lookup
                assert kwargs["headers"]["authorization"] == "Bearer offline-token"
                response._content = b'{"values":[["offline",42]]}'
            return response

    client = gsheet.client.RestClient(raw, 1, session_factory=Session)
    for _ in range(2):
        assert client.get("a" * 24, "A1:B1", deadline=time.monotonic() + 2) == [["offline", 42]]
    assert len(calls) == 3  # one synchronous OAuth refresh and two API requests
    assert all(0 < call[2] <= 1 for call in calls)
