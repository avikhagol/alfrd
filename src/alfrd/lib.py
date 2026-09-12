"""Legacy compatibility imports.

New code should import :class:`LogFrame` from ``alfrd.core.logframe``.
"""

from alfrd.core.logframe import LogFrame, LogFrameAdapter, LogFrameEventSink

__all__ = ["LogFrame", "LogFrameAdapter", "LogFrameEventSink"]