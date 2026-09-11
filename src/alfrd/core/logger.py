"""Deprecated compatibility import for :mod:`alfrd.core.logging`."""

import warnings

from alfrd.core.logging import logger

warnings.warn(
    "alfrd.core.logger is deprecated; import alfrd.core.logging instead",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["logger"]