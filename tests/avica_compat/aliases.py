"""Documented downstream aliases AVICA would add when migrating to ALFRD.

Importing this module and using the names below is exactly what an AVICA
``compat.py`` (or similar) module would do post-migration. Nothing else in
this fixture package imports AVICA; this module only demonstrates that the
ALFRD-side public names exist at the documented import paths.
"""

from __future__ import annotations

from alfrd.config import BaseConfig, CONFIG_MAPPING, Config
from alfrd.core.logframe import LogFrame as LogFramework
from alfrd.core.pipeline import BatchResult as AvicaResult
from alfrd.core.pipeline import PipelineCore as AvicaPipelineCore

__all__ = [
    "AvicaPipelineCore",
    "AvicaResult",
    "LogFramework",
    "BaseConfig",
    "Config",
    "CONFIG_MAPPING",
]
