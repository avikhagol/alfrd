import logging


def test_package_uses_standard_library_logger():
    from alfrd.core.logging import logger

    assert isinstance(logger, logging.Logger)
    assert logger.name == "alfrd"


def test_library_logger_has_null_handler_and_does_not_configure_root():
    from alfrd.core.logging import logger

    assert any(isinstance(handler, logging.NullHandler) for handler in logger.handlers)
    assert logger.propagate is True
