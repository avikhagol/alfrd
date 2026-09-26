from __future__ import annotations

import pytest

from alfrd.manifest import ManifestError, discover_manifest, parse_manifest


def test_manifest_accepts_bounded_declarative_summaries():
    manifest = parse_manifest(
        {
            "name": "demo",
            "summaries": {
                "config": {
                    "title": "Effective inputs",
                    "rows": [
                        {
                            "step": "reduce",
                            "parameter": "threads",
                            "source": "manifest",
                            "value": 4,
                        }
                    ],
                },
                "result": {"columns": ["dataset", "step", "status"]},
            },
        }
    )

    assert manifest.extra["summaries"]["config"]["rows"][0]["value"] == 4


@pytest.mark.parametrize("value", [["not", "scalar"], {"nested": True}])
def test_manifest_rejects_non_scalar_config_snapshot_values(value):
    with pytest.raises(ManifestError, match="summaries"):
        parse_manifest(
            {
                "name": "demo",
                "summaries": {
                    "config": {
                        "rows": [{"step": "x", "parameter": "p", "value": value}]
                    }
                },
            }
        )


def test_manifest_rejects_unknown_result_columns_and_nested_properties():
    with pytest.raises(ManifestError, match="summaries"):
        parse_manifest(
            {"name": "demo", "summaries": {"result": {"columns": ["command"]}}}
        )
    with pytest.raises(ManifestError, match="summaries"):
        parse_manifest(
            {
                "name": "demo",
                "summaries": {"config": {"rows": [], "unexpected": True}},
            }
        )


def test_legacy_manifest_name_wins_over_alias(tmp_path):
    legacy = tmp_path / "alfrd.yaml"
    alias = tmp_path / ".alfrd.yaml"
    legacy.write_text("name: legacy\n", encoding="utf-8")
    alias.write_text("name: alias\n", encoding="utf-8")

    assert discover_manifest(tmp_path) == legacy.resolve()
    assert discover_manifest(alias) == legacy.resolve()
