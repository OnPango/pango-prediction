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

from fastapi import FastAPI

from pango_prediction.adapters.inbound.api.routes import build_router
from pango_prediction.application.forecast_service import ForecastService
from pango_prediction.config import (
    SERVICE_NAME,
    SERVICE_VERSION,
    Settings,
    load_settings,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the HTTP application.

    ``settings`` is injected by tests; in production it is read from the
    environment, which fails loudly when the service credential is missing.
    """
    resolved = load_settings() if settings is None else settings

    app = FastAPI(
        title="PANGO Prediction",
        summary="Weekly demand forecasting for product-warehouse series.",
        version=SERVICE_VERSION,
        openapi_tags=[{"name": SERVICE_NAME}],
    )

    app.include_router(build_router(resolved, ForecastService()))

    return app
