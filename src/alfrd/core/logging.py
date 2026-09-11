"""Standard logging integration for ALFRD.

ALFRD is a library, so it does not configure the root logger or choose output
handlers. Applications and the CLI remain in control of logging policy.
"""

import logging


logger = logging.getLogger("alfrd")
if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
    logger.addHandler(logging.NullHandler())

__all__ = ["logger"]