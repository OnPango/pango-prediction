"""Post-forecast accuracy metrics.

This module evaluates a forecast only after the actual demand is available.
It intentionally receives the observable mask explicitly: the caller must use
the same availability semantics as the forecasting run.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class EvaluationPeriod:
    """One forecast period paired with its realised demand."""

    period_start: date
    estimate: float
    lower: float
    upper: float
    actual: float
    observable: bool = True


@dataclass(frozen=True, slots=True)
class EvaluationTotal:
    """The stored horizon-total forecast interval."""

    estimate: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class ForecastMetrics:
    """Accuracy metrics for one evaluated forecast."""

    evaluated_periods: int
    actual_total: float
    estimate_total: float
    error_total: float
    wape: float | None
    bias: float | None
    mae: float | None
    mase: float | None
    interval_coverage: float | None
    total_inside_interval: bool | None


def evaluate_forecast(
    periods: Sequence[EvaluationPeriod],
    *,
    training_values: Sequence[float] = (),
    total: EvaluationTotal | None = None,
    seasonal_periods: int = 4,
) -> ForecastMetrics:
    """Calculate canonical metrics over observable forecast periods.

    Errors use ``actual - estimate``. Censored or otherwise unobservable
    periods are excluded from every metric, including interval coverage.
    WAPE and bias are undefined when the observable actual total is zero and
    therefore return ``None`` rather than a misleading zero.
    """
    if not periods:
        raise ValueError("at least one evaluation period is required")
    if seasonal_periods < 1:
        raise ValueError("seasonal_periods must be at least 1")

    for period in periods:
        _validate_period(period)

    observed = tuple(period for period in periods if period.observable)
    if not observed:
        return ForecastMetrics(
            evaluated_periods=0,
            actual_total=0.0,
            estimate_total=0.0,
            error_total=0.0,
            wape=None,
            bias=None,
            mae=None,
            mase=None,
            interval_coverage=None,
            total_inside_interval=None,
        )

    actual_total = sum(period.actual for period in observed)
    estimate_total = sum(period.estimate for period in observed)
    errors = tuple(period.actual - period.estimate for period in observed)
    absolute_errors = tuple(abs(error) for error in errors)
    denominator = actual_total if actual_total > 0 else None
    mae = sum(absolute_errors) / len(absolute_errors)
    scale = mase_scale(training_values, seasonal_periods)

    total_inside_interval = None
    if total is not None:
        _validate_total(total)
        total_inside_interval = (
            total.lower <= actual_total <= total.upper
        )

    return ForecastMetrics(
        evaluated_periods=len(observed),
        actual_total=actual_total,
        estimate_total=estimate_total,
        error_total=sum(errors),
        wape=None if denominator is None else sum(absolute_errors) / denominator,
        bias=None if denominator is None else sum(errors) / denominator,
        mae=mae,
        mase=None if scale is None else mae / scale,
        interval_coverage=sum(
            period.lower <= period.actual <= period.upper for period in observed
        )
        / len(observed),
        total_inside_interval=total_inside_interval,
    )


def mase_scale(
    training_values: Sequence[float],
    seasonal_periods: int,
) -> float | None:
    """Return the seasonal naive MAE scale used by MASE.

    A scale is unavailable when the training history cannot provide one full
    seasonal difference or when all seasonal differences are zero.
    """
    if seasonal_periods < 1:
        raise ValueError("seasonal_periods must be at least 1")
    if len(training_values) <= seasonal_periods:
        return None
    differences = tuple(
        abs(later - earlier)
        for earlier, later in zip(
            training_values,
            training_values[seasonal_periods:],
            strict=False,
        )
    )
    scale = sum(differences) / len(differences)
    return scale if scale > 0 else None


def _validate_period(period: EvaluationPeriod) -> None:
    if period.actual < 0:
        raise ValueError("actual demand cannot be negative")
    if period.lower < 0:
        raise ValueError("a demand bound cannot be negative")
    if not period.lower <= period.estimate <= period.upper:
        raise ValueError("the estimate must lie inside its interval")


def _validate_total(total: EvaluationTotal) -> None:
    if total.lower < 0:
        raise ValueError("a total demand bound cannot be negative")
    if not total.lower <= total.estimate <= total.upper:
        raise ValueError("the total estimate must lie inside its interval")
