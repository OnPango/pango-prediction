"""HTTP routes.

This module adapts HTTP to the use case and back. It builds no weeks, reads no
stock and selects no model: it maps the request onto domain types, calls the
application service and renders what comes back.

``build_router`` takes its settings and its service as arguments rather than
reaching for module-level globals. That is dependency injection in its plainest
form, and it is what lets a test run the API against its own settings without
touching the environment.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from pango_prediction.adapters.inbound.api.errors import (
    batch_too_large,
    unauthorized,
)
from pango_prediction.adapters.inbound.api.schemas import (
    ForecastRequest,
    ForecastResponse,
    HealthResponse,
    SeriesErrorResponse,
    SeriesRequest,
    SeriesResultResponse,
    SummaryResponse,
)
from pango_prediction.application.forecast_service import (
    ForecastCommand,
    ForecastService,
    SeriesOutcome,
)
from pango_prediction.config import (
    CONTRACT_VERSION,
    SERVICE_NAME,
    SERVICE_VERSION,
    Settings,
)
from pango_prediction.domain.types import (
    DemandObservation,
    ForecastSeriesInput,
    StockObservation,
)


def to_series_input(series: SeriesRequest) -> ForecastSeriesInput:
    """Map one HTTP series onto the domain input.

    The tenant schema identifies a series by ``product_warehouses.id``, an
    integer. The domain keeps ``series_id`` an opaque string so the same code
    serves either forecast grain, so the conversion happens here, at the edge.
    """
    return ForecastSeriesInput(
        series_id=str(series.series_id),
        product_id=str(series.product_id),
        warehouse_id=str(series.warehouse_id),
        demand=tuple(
            DemandObservation(date=row.date, quantity=row.quantity)
            for row in series.demand
        ),
        stock=tuple(
            StockObservation(date=row.date, closing_stock=row.closing_stock)
            for row in series.stock
        ),
        metadata=series.metadata,
    )


def to_command(request: ForecastRequest) -> ForecastCommand:
    return ForecastCommand(
        history_end=request.history_end,
        horizon=request.horizon,
        series=tuple(to_series_input(series) for series in request.series),
    )


def to_series_result(
    series_id: int,
    outcome: SeriesOutcome,
) -> SeriesResultResponse:
    return SeriesResultResponse(
        series_id=series_id,
        status="ok" if outcome.ok else "error",
        # Nothing populates periods until a model exists; the field stays in the
        # envelope so Pango Core can integrate against the final shape.
        periods=[],
        warnings=list(outcome.warnings),
        error=(
            None
            if outcome.error is None
            else SeriesErrorResponse(
                code=outcome.error.code.value,
                message=outcome.error.message,
            )
        ),
    )


def build_router(settings: Settings, service: ForecastService) -> APIRouter:
    router = APIRouter()

    def require_service_token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        """Check the shared service credential.

        Compared with ``compare_digest`` rather than ``==`` so that the time the
        check takes does not reveal how much of the token was correct.
        """
        scheme, _, token = (authorization or "").partition(" ")

        if scheme.lower() != "bearer" or not token:
            raise unauthorized()

        if not secrets.compare_digest(
            token.encode("utf-8"),
            settings.service_token.encode("utf-8"),
        ):
            raise unauthorized()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Liveness probe.

        Left unauthenticated on purpose: load balancers and container probes
        call it, and handing them a credential buys nothing — the response
        carries no tenant or business data.
        """
        return HealthResponse(
            status="ok",
            service=SERVICE_NAME,
            service_version=SERVICE_VERSION,
        )

    @router.post(
        "/v1/forecasts",
        response_model=ForecastResponse,
        dependencies=[Depends(require_service_token)],
    )
    def create_forecasts(request: ForecastRequest) -> ForecastResponse:
        """Forecast a batch of series.

        Answers 200 even when individual series fail: each result carries its
        own status and stable code (plan B17). A non-200 is reserved for
        failures affecting the whole request.
        """
        if len(request.series) > settings.max_series_per_request:
            raise batch_too_large(
                len(request.series),
                settings.max_series_per_request,
            )

        batch = service.run(to_command(request))

        results = [
            to_series_result(series.series_id, outcome)
            for series, outcome in zip(request.series, batch.outcomes, strict=True)
        ]

        return ForecastResponse(
            contract_version=CONTRACT_VERSION,
            service_version=SERVICE_VERSION,
            policy_version=batch.policy_version,
            request_id=request.request_id,
            forecast_run_id=request.forecast_run_id,
            summary=SummaryResponse(
                total=len(results),
                ok=batch.ok_count,
                error=batch.error_count,
            ),
            results=results,
        )

    return router
