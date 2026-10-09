"""Domain-neutral AVICA compatibility fixture.

This package proves that ALFRD's public engine and table APIs (as defined
in ``src/alfrd/core/pipeline.py``, ``src/alfrd/core/logframe.py`` and
``src/alfrd/config.py``) can host AVICA-shaped pipelines using only
public ALFRD imports. Nothing here imports AVICA, CASA, or any
radio-astronomy specific behavior; it only mirrors the *shape* of AVICA's
contracts (step/validator base classes, no-argument construction, parameter
precedence, multi-dataset execution against one shared table, and result/
crash hooks) using synthetic, domain-neutral steps.

See ``MIGRATION_NOTES.md`` (in this directory) for the documented AVICA-side
aliases and gaps discovered while building this fixture.
"""
