from alfrd_telegram import plugin

from alfrd.extensions import api_ok


def test_manifest():
    assert plugin.id == 'telegram'
    assert api_ok(plugin.alfrd_api)
    assert 'cli' in plugin.kinds
