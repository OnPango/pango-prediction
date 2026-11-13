"""Tests for the application assembly.

The logging ones exist because of a bug the request-log test could not catch.
`caplog` attaches a handler of its own, so the contract's section 20 record
appeared in the tests while being dropped in a real container: uvicorn
configures only its own loggers and leaves the root logger without a handler,
so an INFO record from this package never reached one. These assert the
configuration instead of the output.
"""

import logging
from collections.abc import Iterator

import pytest

from pango_prediction.config import Settings
from pango_prediction.main import PACKAGE_LOGGER, configure_logging, create_app


@pytest.fixture
def unconfigured_logger() -> Iterator[logging.Logger]:
    """Put the package logger back to how a fresh process would find it.

    Tests share one process, so an earlier `create_app` would otherwise have
    configured it already and these assertions would pass for the wrong reason.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    handlers, level = list(logger.handlers), logger.level

    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)

    yield logger

    logger.handlers[:] = handlers
    logger.setLevel(level)


def test_without_configuration_an_info_record_has_nowhere_to_go(
    unconfigured_logger: logging.Logger,
) -> None:
    """The state the container was actually in before the fix."""
    assert unconfigured_logger.handlers == []
    assert unconfigured_logger.level == logging.NOTSET


def test_create_app_gives_the_service_logger_a_handler(
    unconfigured_logger: logging.Logger,
) -> None:
    create_app(Settings(service_token="t", max_series_per_request=10))

    assert unconfigured_logger.handlers
    assert unconfigured_logger.isEnabledFor(logging.INFO)


def test_the_settings_level_is_the_one_applied(
    unconfigured_logger: logging.Logger,
) -> None:
    create_app(
        Settings(service_token="t", max_series_per_request=10, log_level="WARNING")
    )

    assert not unconfigured_logger.isEnabledFor(logging.INFO)
    assert unconfigured_logger.isEnabledFor(logging.WARNING)


def test_configuring_twice_does_not_duplicate_the_handler(
    unconfigured_logger: logging.Logger,
) -> None:
    """Every test building an app would otherwise add another line of output."""
    configure_logging("INFO")
    configure_logging("INFO")

    assert len(unconfigured_logger.handlers) == 1


def test_records_still_propagate(unconfigured_logger: logging.Logger) -> None:
    """A host that does configure the root logger must still receive them.

    It is also what lets `caplog` see the request log at all.
    """
    configure_logging("INFO")

    assert unconfigured_logger.propagate is True


def test_only_this_packages_logger_is_touched(
    unconfigured_logger: logging.Logger,
) -> None:
    """Configuring the root logger would reshape the output of anything else
    sharing the process."""
    before = list(logging.getLogger().handlers)

    configure_logging("INFO")

    assert logging.getLogger().handlers == before


# --- the factory itself ---


def test_the_app_exposes_the_documented_routes() -> None:
    app = create_app(Settings(service_token="t", max_series_per_request=10))

    paths = set(app.openapi()["paths"])

    assert paths == {"/health", "/v1/forecasts", "/v1/forecast-evaluations"}


def test_there_is_no_module_level_app() -> None:
    """Building one at import time would read the environment because something
    imported this module, and fail an unrelated import with no credential set."""
    import pango_prediction.main as main

    assert not hasattr(main, "app")
