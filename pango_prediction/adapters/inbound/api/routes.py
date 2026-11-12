"""HTTP routes.

This module adapts HTTP to the use case and back. It builds no weeks, reads no
stock and selects no model: it maps the request onto domain types, calls the
application service and renders what comes back.

``build_router`` takes its settings and its service as arguments rather than
reaching for module-level globals. That is dependency injection in its plainest
form, and it is what lets a test run the API against its own settings without
touching the environment.
"""

import logging
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from pango_prediction.adapters.inbound.api.errors import (
    batch_too_large,
    unauthorized,
)
from pango_prediction.adapters.inbound.api.schemas import (
    ForecastEvaluationRequest,
    ForecastEvaluationResponse,
    ForecastRequest,
    ForecastResponse,
    HealthResponse,
    MetricsResponse,
    PeriodResponse,
    ProfileResponse,
    SeriesErrorResponse,
    SeriesEvaluationResponse,
    SeriesRequest,
    SeriesResultResponse,
    SummaryResponse,
    TotalResponse,
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
from pango_prediction.domain.calendar import ClosedDates
from pango_prediction.domain.evaluation import (
    EvaluationPeriod,
    EvaluationTotal,
    ForecastMetrics,
    evaluate_forecast,
)
from pango_prediction.domain.types import (
    DemandObservation,
    DemandProfile,
    ForecastSeriesInput,
    StockObservation,
)

logger = logging.getLogger(__name__)
"""Named after this module, with no handler attached here.

A module that called ``basicConfig`` would reshape the logging of whatever
imported it. The handler is attached once by `main.configure_logging`, at the
composition root, because uvicorn configures only its own loggers and leaves
the root one without any - so these records would otherwise be dropped at
root's default WARNING.
"""


def to_metrics_response(metrics: ForecastMetrics) -> MetricsResponse:
    return MetricsResponse(
        evaluated_periods=metrics.evaluated_periods,
        actual_total=metrics.actual_total,
        estimate_total=metrics.estimate_total,
        error_total=metrics.error_total,
        wape=metrics.wape,
        bias=metrics.bias,
        mae=metrics.mae,
        mase=metrics.mase,
        interval_coverage=metrics.interval_coverage,
        total_inside_interval=metrics.total_inside_interval,
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


def to_closed_dates(request: ForecastRequest) -> ClosedDates:
    """Map the calendar block onto the domain value object.

    Warehouse keys become strings here for the same reason ``series_id`` does:
    the schema numbers its warehouses, the domain treats the identifier as
    opaque, and the conversion belongs at the edge.
    """
    return ClosedDates.of(
        everywhere=request.calendar.closed_dates,
        by_warehouse={
            str(warehouse_id): dates
            for warehouse_id, dates in request.calendar.closed_dates_by_warehouse.items()
        },
    )


def to_command(request: ForecastRequest) -> ForecastCommand:
    return ForecastCommand(
        history_end=request.history_end,
        horizon=request.horizon,
        series=tuple(to_series_input(series) for series in request.series),
        closed_dates=to_closed_dates(request),
        # The tenant's calibration will arrive with the policy artifact. Until
        # then the command's own default applies, which is EquipMaster's.
    )


def to_profile(profile: DemandProfile) -> ProfileResponse:
    return ProfileResponse(
        demand_class=profile.demand_class.value,
        history_class=profile.history_class.value,
        stock_handling=profile.stock_handling.value,
        adi=profile.adi,
        cv2=profile.cv2,
        observable_months=profile.observable_months,
        nonzero_months=profile.nonzero_months,
        history_weeks=profile.history_weeks,
        observed_weeks=profile.observed_weeks,
        weeks_since_last_sale=profile.weeks_since_last_sale,
        inactive=profile.inactive,
        evaluable=profile.evaluable,
    )


def to_series_result(
    series_id: int,
    outcome: SeriesOutcome,
) -> SeriesResultResponse:
    return SeriesResultResponse(
        series_id=series_id,
        status="ok" if outcome.ok else "error",
        periods=[
            PeriodResponse(
                period_start=period.period_start,
                estimate=period.estimate,
                lower=period.lower,
                upper=period.upper,
            )
            for period in outcome.periods
        ],
        total=(
            None
            if outcome.total is None
            else TotalResponse(
                estimate=outcome.total.estimate,
                lower=outcome.total.lower,
                upper=outcome.total.upper,
            )
        ),
        model_name=outcome.model_name,
        model_version=outcome.model_version,
        warnings=list(outcome.warnings),
        profile=(None if outcome.profile is None else to_profile(outcome.profile)),
        horizon_business_days=list(outcome.horizon_business_days),
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

        started = time.perf_counter()
        batch = service.run(to_command(request))
        duration_ms = (time.perf_counter() - started) * 1000

        results = [
            to_series_result(series.series_id, outcome)
            for series, outcome in zip(request.series, batch.outcomes, strict=True)
        ]

        if batch.error_count == 0:
            status = "ok"
        elif batch.ok_count == 0:
            status = "error"
        else:
            status = "partial"

        # Contract section 20. The fields go in the message so that they are
        # greppable under uvicorn's plain formatter, and in `extra` so that a
        # structured handler can read them without parsing the text.
        #
        # Nothing from the payload is logged: section 20 forbids logging full
        # histories, and a batch of 500 series would otherwise write megabytes
        # of a tenant's sales per request.
        #
        # Model identity is per series, while this log is per request. The
        # request-level fields therefore remain null; each result carries its
        # selected model when forecasting succeeds.
        fields = {
            "request_id": request.request_id,
            "forecast_run_id": request.forecast_run_id,
            "series_count": len(results),
            "policy_version": batch.policy_version,
            "model_name": None,
            "model_version": None,
            "duration_ms": round(duration_ms, 1),
            "status": status,
        }

        logger.info(
            " ".join(f"{key}={value}" for key, value in fields.items()),
            extra=fields,
        )

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

    @router.post(
        "/v1/forecast-evaluations",
        response_model=ForecastEvaluationResponse,
        dependencies=[Depends(require_service_token)],
    )
    def evaluate_forecasts(
        request: ForecastEvaluationRequest,
    ) -> ForecastEvaluationResponse:
        """Evaluate completed forecasts against realised demand.

        This is deliberately a separate endpoint: actual demand is only
        available after the forecast horizon has elapsed.
        """
        if len(request.series) > settings.max_series_per_request:
            raise batch_too_large(
                len(request.series),
                settings.max_series_per_request,
            )

        results: list[SeriesEvaluationResponse] = []
        all_periods: list[EvaluationPeriod] = []
        total_estimate = 0.0
        total_lower = 0.0
        total_upper = 0.0
        has_totals = True

        for series in request.series:
            periods = tuple(
                EvaluationPeriod(
                    period_start=period.period_start,
                    estimate=period.estimate,
                    lower=period.lower,
                    upper=period.upper,
                    actual=period.actual,
                    observable=period.observable,
                )
                for period in series.periods
            )
            total = (
                None
                if series.total is None
                else EvaluationTotal(
                    estimate=series.total.estimate,
                    lower=series.total.lower,
                    upper=series.total.upper,
                )
            )
            try:
                metrics = evaluate_forecast(
                    periods,
                    training_values=series.training_values,
                    total=total,
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error

            results.append(
                SeriesEvaluationResponse(
                    series_id=series.series_id,
                    metrics=to_metrics_response(metrics),
                )
            )
            all_periods.extend(periods)
            if total is None:
                has_totals = False
            else:
                total_estimate += total.estimate
                total_lower += total.lower
                total_upper += total.upper

        aggregate_total = (
            None
            if not has_totals
            else EvaluationTotal(
                estimate=total_estimate,
                lower=total_lower,
                upper=total_upper,
            )
        )
        try:
            aggregate = evaluate_forecast(
                tuple(all_periods),
                total=aggregate_total,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        return ForecastEvaluationResponse(
            contract_version=CONTRACT_VERSION,
            service_version=SERVICE_VERSION,
            request_id=request.request_id,
            forecast_run_id=request.forecast_run_id,
            results=results,
            aggregate=to_metrics_response(aggregate),
        )

    return router
