import logging


def test_package_uses_standard_library_logger():
    from alfrd.core.logging import logger

    assert isinstance(logger, logging.Logger)
    assert logger.name == "alfrd"


def test_library_logger_has_null_handler_and_does_not_configure_root():
    from alfrd.core.logging import logger

    assert any(isinstance(handler, logging.NullHandler) for handler in logger.handlers)
    assert logger.propagate is True


def test_project_messages_are_emitted_through_standard_logging(caplog):
    from alfrd.core.project import Project

    caplog.set_level(logging.INFO, logger="alfrd")
    Project("logging-test").create()

    assert "project logging-test created!" in caplog.messages
