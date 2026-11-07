"""Application assembly.

The only place that wires settings, the use case and the HTTP adapter together.
Nothing else imports FastAPI except the `api` package, which keeps the domain
and application layers runnable from a notebook or a test with no web
framework installed.

There is no module-level ``app``: building one at import time would read the
environment just because something imported this module, and would fail an
unrelated import when the service credential is absent. Run it as a factory:

    uvicorn pango_prediction.main:create_app --factory
"""

import logging

from fastapi import FastAPI

from pango_prediction.adapters.inbound.api.routes import build_router
from pango_prediction.application.forecast_service import ForecastService
from pango_prediction.config import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Settings,
    load_settings,
)

PACKAGE_LOGGER = "pango_prediction"


def configure_logging(level: str) -> None:
    """Give the service's own records somewhere to go.

    uvicorn configures only its own loggers and attaches nothing to the root
    one, so a record from this package is filtered at root's default WARNING
    and never printed. The contract's section 20 request log is INFO, so
    without this it exists in the tests and nowhere else.

    That is how it was found: by reading a running container's output and not
    seeing the record the tests were green about. `caplog` attaches its own
    handler, so the test could not have caught it.

    Only this package's logger is touched, never the root one, so the logging
    of anything else sharing the process is left alone. Propagation stays on,
    so a host that does configure root still receives the records.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        logger.addHandler(handler)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the HTTP application.

    ``settings`` is injected by tests; in production it is read from the
    environment, which fails loudly when the service credential is missing.
    """
    resolved = load_settings() if settings is None else settings

    configure_logging(resolved.log_level)

    app = FastAPI(
        title="PANGO Prediction",
        summary="Weekly demand forecasting for product-warehouse series.",
        version=SERVICE_VERSION,
        openapi_tags=[{"name": SERVICE_NAME}],
    )

    app.include_router(build_router(resolved, ForecastService()))

    return app
