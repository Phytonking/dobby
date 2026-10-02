import logging

from bot.diagnostics import log_startup_failure


def test_startup_diagnostics_show_location_without_exception_content(caplog):
    logger = logging.getLogger("startup-test")
    try:
        try:
            raise ValueError("sensitive-inner-value")
        except ValueError as cause:
            raise RuntimeError("sensitive-outer-value") from cause
    except RuntimeError as error:
        log_startup_failure(logger, error, "initialization")

    assert "stage=initialization type=RuntimeError" in caplog.text
    assert "startup_exception type=ValueError" in caplog.text
    assert "test_diagnostics.py" in caplog.text
    assert "function=test_startup_diagnostics" in caplog.text
    assert "sensitive-inner-value" not in caplog.text
    assert "sensitive-outer-value" not in caplog.text


def test_startup_diagnostics_respect_suppressed_context(caplog):
    try:
        try:
            raise ValueError("hidden")
        except ValueError:
            raise RuntimeError("hidden") from None
    except RuntimeError as error:
        log_startup_failure(logging.getLogger("startup-test"), error, "imports")
    assert "type=RuntimeError" in caplog.text
    assert "type=ValueError" not in caplog.text
