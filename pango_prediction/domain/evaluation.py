"""Post-forecast accuracy metrics.

This module evaluates a forecast only after the actual demand is available.
It intentionally receives the observable mask explicitly: the caller must use
the same availability semantics as the forecasting run.

It holds two conventions, and they give different numbers on purpose:

- `evaluate_forecast` is the one contract §19 serves to Pango Core after a
  horizon closes: weekly errors, ``actual - estimate``.
- `evaluate_block` is Route A's series-block convention (`docs/route-a/criteria.md`
  §1.3 and §1.7): one error per block of weeks, ``forecast - actual``, on the
  sums over the evaluable weeks. It is what the official evaluation reported,
  so a number computed with it can sit next to Route A's report.

Neither is a variant of the other. Keeping them apart, with their names, is
what stops a weekly WAPE from being read as Route A's.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite


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


MIN_EVALUABLE_WEEKS = 2
"""Evaluable weeks a block needs, or Route A discards it (criteria §1.3)."""


@dataclass(frozen=True, slots=True)
class BlockWeek:
    """One week of a validation block: what was forecast and what happened.

    ``forecast`` is the weekly point forecast. A series the policy declined, or
    whose model refused it for too little history, enters with ``0.0`` and no
    bounds, which is how Route A's evaluation counted it (criteria DEC-1).
    ``observable`` is false for a CENSORED or OUT_OF_ASSORTMENT week.
    """

    period_start: date
    forecast: float
    actual: float
    observable: bool
    lower: float | None = None
    upper: float | None = None


@dataclass(frozen=True, slots=True)
class BlockMetrics:
    """Route A's metrics of one series-block.

    The components are returned with the ratios because the ratios of a set of
    blocks are ratios of summed components, never means of per-block ratios:
    WAPE of a set = sum of ``absolute_error`` / sum of ``actual_total``, and
    bias of a set = sum of ``error`` / sum of ``actual_total`` (criteria §1.7).
    """

    evaluable_weeks: int
    actual_total: float
    """y: actual demand summed over the evaluable weeks."""
    forecast_total: float
    """f: point forecast summed over the same weeks."""
    error: float
    """e = f - y. Positive means the model forecast too much."""
    absolute_error: float
    wape: float | None
    """abs(e) / y, or None when y is zero."""
    bias: float | None
    """e / y, or None when y is zero. Positive means over-forecast."""
    weekly_coverage: float | None
    """Share of evaluable weeks whose actual lies inside the weekly interval.

    None when the forecast has no interval, as for a series counted as zero.
    Route A only reports it; it does not enter any decision."""
    total_inside_interval: bool | None
    """Whether the block's actual total lies inside the horizon-total interval.

    Only measured when every week of the block is evaluable: a partial actual
    total cannot be compared with an interval for the whole horizon."""


def evaluate_block(
    weeks: Sequence[BlockWeek],
    *,
    total: EvaluationTotal | None = None,
) -> BlockMetrics | None:
    """Evaluate one series-block with Route A's convention.

    Returns None when fewer than `MIN_EVALUABLE_WEEKS` weeks are evaluable:
    Route A discards such a block rather than scoring it on one week.
    ``total`` is the forecast's horizon-total interval, when the forecast has
    one.
    """
    if not weeks:
        raise ValueError("a block needs at least one week")

    for week in weeks:
        _validate_block_week(week)

    if total is not None:
        _validate_total(total)

    evaluable = tuple(week for week in weeks if week.observable)

    if len(evaluable) < MIN_EVALUABLE_WEEKS:
        return None

    actual_total = sum(week.actual for week in evaluable)
    forecast_total = sum(week.forecast for week in evaluable)
    error = forecast_total - actual_total
    denominator = actual_total if actual_total > 0 else None

    weekly_coverage: float | None = None
    if all(week.lower is not None for week in evaluable):
        weekly_coverage = sum(
            _inside(week.actual, week.lower, week.upper) for week in evaluable
        ) / len(evaluable)

    total_inside_interval: bool | None = None
    if total is not None and len(evaluable) == len(weeks):
        total_inside_interval = total.lower <= actual_total <= total.upper

    return BlockMetrics(
        evaluable_weeks=len(evaluable),
        actual_total=actual_total,
        forecast_total=forecast_total,
        error=error,
        absolute_error=abs(error),
        wape=None if denominator is None else abs(error) / denominator,
        bias=None if denominator is None else error / denominator,
        weekly_coverage=weekly_coverage,
        total_inside_interval=total_inside_interval,
    )


def _inside(value: float, lower: float | None, upper: float | None) -> bool:
    return lower is not None and upper is not None and lower <= value <= upper


def _validate_block_week(week: BlockWeek) -> None:
    for value in (week.forecast, week.actual, week.lower, week.upper):
        if value is not None and not isfinite(value):
            raise ValueError("block values must be finite")

    if week.actual < 0:
        raise ValueError("actual demand cannot be negative")

    if week.forecast < 0:
        raise ValueError("a forecast cannot be negative")

    if (week.lower is None) != (week.upper is None):
        raise ValueError("a weekly interval needs both bounds or neither")

    if week.lower is not None and week.upper is not None:
        if week.lower < 0:
            raise ValueError("a demand bound cannot be negative")
        if not week.lower <= week.forecast <= week.upper:
            raise ValueError("the forecast must lie inside its interval")
