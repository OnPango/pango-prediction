"""Holdout validation: how the deployed policy did on a series' latest weeks.

A forecast's own error only exists once its horizon closes, four weeks after
the run. To have an error from the first run, this use case holds out the last
complete weeks of the history a request brings, forecasts them from everything
before with the very same `ForecastService`, and compares::

    |---------------- train ----------------|------ held out ------|
                                        train_end              labels_end
                                        (Sunday)    4 weeks    (Sunday)

Three decisions are worth reading before changing anything here:

- **The forecast is production's.** The history is cut at ``train_end`` and the
  unchanged service runs on it, so the profile, the availability mask, the
  policy decision and the model all come from data up to ``train_end`` only.
- **The labels are the domain's.** The held-out weeks are built with
  `build_weekly_series` and `build_weekly_availability` on the history up to
  ``labels_end``, so CENSORED and OUT_OF_ASSORTMENT weeks leave the error while
  AVAILABLE and UNKNOWN weeks stay in, as in Route A's evaluation (S31). The
  labels see nothing after the window either. That keeps every series equally
  informed, at a known cost: a stockout covering the end of the window cannot
  be told from no demand, because weeks after a series' last sale read as
  inactivity.
- **The score is Route A's.** `evaluate_block` applies criteria §1.3 and §1.7.
  A series the policy declined, or whose model refused it for too little
  history, is scored as a zero forecast, as Route A's evaluation did (DEC-1).
  Anything else that failed is not scored: a failure is not a forecast of zero.

It is a diagnostic of the policy on the series' recent past, not a forecast.
Nothing here changes what the service forecasts for the weeks ahead.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

from pango_prediction.application.errors import SeriesErrorCode
from pango_prediction.application.forecast_service import (
    ForecastCommand,
    ForecastService,
    SeriesOutcome,
)
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.evaluation import (
    BlockMetrics,
    BlockWeek,
    EvaluationTotal,
    evaluate_block,
)
from pango_prediction.domain.series import (
    DAYS_PER_WEEK,
    build_weekly_series,
    future_weeks,
    last_complete_week_start,
    week_start,
)
from pango_prediction.domain.types import (
    AvailabilityState,
    ForecastSeriesInput,
    NoForecastReason,
)

REQUEST_DECLINES = frozenset(
    {NoForecastReason.HISTORY_ONLY, NoForecastReason.NON_STOCK_WAREHOUSE}
)
"""Declines that come from the request's configuration, not from the demand.

An administrator ruled these series out, so the policy never had a say and
there is no forecast, zero or otherwise, whose error would mean anything.
"""


class ValidationSkip(StrEnum):
    """Why a series' held-out weeks were not scored.

    DECLINED_BY_REQUEST
        The series is history-only or lives in a warehouse that holds no stock.
    NO_HISTORY_AT_CUTOFF
        It had not sold yet by ``train_end``, so there was nothing to forecast
        from. Route A reports such pairs apart rather than scoring them.
    FORECAST_FAILED
        The forecast at ``train_end`` failed for a reason other than the model
        refusing a short history: invalid data, or no model available.
    INVALID_SERIES
        The held-out weeks themselves could not be built from the data.
    TOO_FEW_EVALUABLE_WEEKS
        Fewer than two held-out weeks can be read as demand, so Route A's
        criteria discard the block (§1.3).
    """

    DECLINED_BY_REQUEST = "declined_by_request"
    NO_HISTORY_AT_CUTOFF = "no_history_at_cutoff"
    FORECAST_FAILED = "forecast_failed"
    INVALID_SERIES = "invalid_series"
    TOO_FEW_EVALUABLE_WEEKS = "too_few_evaluable_weeks"


@dataclass(frozen=True, slots=True)
class ValidationWindow:
    train_end: date
    """The Sunday the holdout forecast is made at: its history ends here."""
    weeks: tuple[date, ...]
    """The Mondays of the held-out weeks."""
    labels_end: date
    """The Sunday that closes the last held-out week."""


def validation_window(history_end: date, horizon: int) -> ValidationWindow:
    """The last ``horizon`` complete weeks of a history ending at ``history_end``.

    A trailing partial week is left out, as the forecast leaves it out. The
    weeks are derived with `future_weeks`, the function the forecast periods
    come from, so the two cannot disagree on which Monday starts a week.
    """
    last_week = last_complete_week_start(history_end)
    train_end = last_week - timedelta(weeks=horizon - 1, days=1)

    return ValidationWindow(
        train_end=train_end,
        weeks=future_weeks(week_start(train_end), horizon),
        labels_end=last_week + timedelta(days=DAYS_PER_WEEK - 1),
    )


@dataclass(frozen=True, slots=True)
class ValidationWeek:
    """One held-out week: what happened, and what had been forecast for it.

    ``estimate`` is None when the series has no score; it is ``0.0`` with no
    bounds when the series is scored as a zero forecast.
    """

    period_start: date
    actual: float
    availability: AvailabilityState
    observable: bool
    estimate: float | None = None
    lower: float | None = None
    upper: float | None = None


@dataclass(frozen=True, slots=True)
class SeriesValidation:
    """The holdout validation of one series.

    Exactly one of ``metrics`` and ``skipped`` is set. ``forecast`` is the
    outcome of the holdout run itself, made at ``train_end``, so a caller can
    say what the policy decided then: a model, a decline or a failure.
    """

    series_id: str
    train_end: date
    forecast: SeriesOutcome
    counted_as_zero: bool = False
    """Scored as a zero forecast because the policy declined the series or its
    model refused it for too little history (Route A DEC-1)."""
    weeks: tuple[ValidationWeek, ...] = ()
    metrics: BlockMetrics | None = None
    skipped: ValidationSkip | None = None

    def __post_init__(self) -> None:
        if (self.metrics is None) == (self.skipped is None):
            raise ValueError("a validation is either scored or skipped with a reason")


class HoldoutValidation:
    """Validates the policy on the last weeks of each series of a command.

    Takes the forecast service rather than a policy and a registry, so that the
    validation cannot drift from production: it has no pipeline of its own.
    """

    def __init__(self, service: ForecastService) -> None:
        self._service = service

    def run(self, command: ForecastCommand) -> tuple[SeriesValidation, ...]:
        """Validate every series of ``command``, in the command's order.

        Raises what `ForecastService.run` raises for the whole command, which is
        only a horizon the policy is not calibrated for.
        """
        window = validation_window(command.history_end, command.horizon)
        cut = replace(
            command,
            history_end=window.train_end,
            series=tuple(_through(series, window.train_end) for series in command.series),
        )
        batch = self._service.run(cut)

        return tuple(
            _validate(series, outcome, window, command)
            for series, outcome in zip(command.series, batch.outcomes, strict=True)
        )


def _through(series: ForecastSeriesInput, end: date) -> ForecastSeriesInput:
    """The series as it was known on ``end``: no observation after it."""
    return replace(
        series,
        demand=tuple(row for row in series.demand if row.date <= end),
        stock=tuple(row for row in series.stock if row.date <= end),
    )


def _labels(
    series: ForecastSeriesInput,
    window: ValidationWindow,
    command: ForecastCommand,
) -> tuple[ValidationWeek, ...] | None:
    """The held-out weeks, labelled by the domain's availability rules.

    None when the data cannot produce them: invalid observations, or a series
    that only started selling inside the window.
    """
    seen = _through(series, window.labels_end)

    try:
        weekly = build_weekly_series(seen, window.labels_end)
        availability = build_weekly_availability(weekly, seen.stock, command.parameters)
    except ValueError:
        return None

    weeks = tuple(
        ValidationWeek(
            period_start=monday,
            actual=value,
            availability=state,
            observable=observable,
        )
        for monday, value, state, observable in zip(
            weekly.dates,
            weekly.values,
            availability.states,
            availability.available_mask,
            strict=True,
        )
        if monday >= window.weeks[0]
    )

    if tuple(week.period_start for week in weeks) != window.weeks:
        return None

    return weeks


def _skip_before_scoring(outcome: SeriesOutcome) -> ValidationSkip | None:
    """Whether the holdout forecast's outcome rules the series out of a score."""
    if outcome.no_forecast in REQUEST_DECLINES:
        return ValidationSkip.DECLINED_BY_REQUEST

    if outcome.error is None:
        return None

    if outcome.error.code is SeriesErrorCode.INSUFFICIENT_HISTORY:
        # The service's code for a series without a single week of demand.
        return ValidationSkip.NO_HISTORY_AT_CUTOFF

    if outcome.error.model_history_insufficient:
        return None

    return ValidationSkip.FORECAST_FAILED


def _validate(
    series: ForecastSeriesInput,
    outcome: SeriesOutcome,
    window: ValidationWindow,
    command: ForecastCommand,
) -> SeriesValidation:
    labels = _labels(series, window, command)
    skipped = _skip_before_scoring(outcome)

    if skipped is not None:
        # The facts of the window are still returned, when there are any, so
        # a caller can show what happened even though there is no score.
        return SeriesValidation(
            series_id=series.series_id,
            train_end=window.train_end,
            forecast=outcome,
            weeks=labels or (),
            skipped=skipped,
        )

    if labels is None:
        return SeriesValidation(
            series_id=series.series_id,
            train_end=window.train_end,
            forecast=outcome,
            skipped=ValidationSkip.INVALID_SERIES,
        )

    counted_as_zero = not outcome.ok

    if counted_as_zero:
        weeks = tuple(replace(week, estimate=0.0) for week in labels)
    else:
        if tuple(period.period_start for period in outcome.periods) != window.weeks:
            raise RuntimeError("the holdout forecast and its window disagree on the weeks")

        weeks = tuple(
            replace(week, estimate=period.estimate, lower=period.lower, upper=period.upper)
            for week, period in zip(labels, outcome.periods, strict=True)
        )

    metrics = evaluate_block(
        tuple(
            BlockWeek(
                period_start=week.period_start,
                forecast=0.0 if week.estimate is None else week.estimate,
                actual=week.actual,
                observable=week.observable,
                lower=week.lower,
                upper=week.upper,
            )
            for week in weeks
        ),
        total=(
            None
            if outcome.total is None
            else EvaluationTotal(
                estimate=outcome.total.estimate,
                lower=outcome.total.lower,
                upper=outcome.total.upper,
            )
        ),
    )

    return SeriesValidation(
        series_id=series.series_id,
        train_end=window.train_end,
        forecast=outcome,
        counted_as_zero=counted_as_zero,
        weeks=weeks,
        metrics=metrics,
        skipped=None if metrics is not None else ValidationSkip.TOO_FEW_EVALUABLE_WEEKS,
    )
