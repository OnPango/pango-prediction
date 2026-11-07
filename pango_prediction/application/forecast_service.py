"""Forecast use case.

Coordinates the domain for a batch of series. It owns no forecasting algorithm
and no HTTP concern: it calls domain functions in order and decides what each
series' outcome is.

The intended full flow (plan B13) is:

    input -> series -> availability -> calendar -> profile -> policy -> model

What runs today is the part the domain implements: series regularization and
availability. Calendar, profile, policy and models are not built yet, so every
series ends as ``MODEL_NOT_AVAILABLE``. That is deliberate: the service reports
that it cannot forecast rather than returning a number no model produced.

One series failing never fails the batch (plan B17).
"""

from dataclasses import dataclass, field
from datetime import date

from pango_prediction.application.errors import SeriesErrorCode
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.series import build_weekly_series
from pango_prediction.domain.types import (
    ForecastSeriesInput,
    StockHandling,
)


@dataclass(frozen=True, slots=True)
class ForecastCommand:
    """What the use case needs, in domain terms rather than HTTP terms."""

    history_end: date
    horizon: int
    series: tuple[ForecastSeriesInput, ...]


@dataclass(frozen=True, slots=True)
class SeriesError:
    code: SeriesErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class SeriesOutcome:
    """What happened to one series. ``error`` is None when it succeeded."""

    series_id: str
    warnings: tuple[str, ...] = ()
    error: SeriesError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class ForecastResultBatch:
    outcomes: tuple[SeriesOutcome, ...] = field(default_factory=tuple)
    policy_version: str | None = None

    @property
    def ok_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.ok)

    @property
    def error_count(self) -> int:
        return len(self.outcomes) - self.ok_count


class ForecastService:
    """Runs the forecast pipeline over a batch of series."""

    def run(self, command: ForecastCommand) -> ForecastResultBatch:
        return ForecastResultBatch(
            outcomes=tuple(
                self._run_one(series, command) for series in command.series
            ),
            # No policy is loaded yet, so there is no version to report. Null is
            # the honest answer; a placeholder string would read as a real one.
            policy_version=None,
        )

    def _run_one(
        self,
        series: ForecastSeriesInput,
        command: ForecastCommand,
    ) -> SeriesOutcome:
        try:
            regularized = build_weekly_series(series, command.history_end)
        except ValueError as error:
            return SeriesOutcome(
                series_id=series.series_id,
                error=SeriesError(
                    code=SeriesErrorCode.INVALID_SERIES,
                    message=str(error),
                ),
            )

        if not regularized.dates:
            return SeriesOutcome(
                series_id=series.series_id,
                error=SeriesError(
                    code=SeriesErrorCode.INSUFFICIENT_HISTORY,
                    message="The series has no week with observed demand.",
                ),
            )

        try:
            availability = build_weekly_availability(regularized, series.stock)
        except ValueError as error:
            return SeriesOutcome(
                series_id=series.series_id,
                error=SeriesError(
                    code=SeriesErrorCode.INVALID_SERIES,
                    message=str(error),
                ),
            )

        warnings: list[str] = []

        if availability.stock_handling is StockHandling.NOT_CLASSIFIABLE:
            warnings.append(
                "Stock handling could not be classified, so stockouts were not "
                "evaluated for this series."
            )
        elif availability.stock_handling is StockHandling.MADE_TO_ORDER:
            warnings.append(
                "The series is sold to order, so zero stock was not read as a "
                "stockout."
            )

        return SeriesOutcome(
            series_id=series.series_id,
            warnings=tuple(warnings),
            error=SeriesError(
                code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                message=(
                    "No forecasting model is deployed yet: the model policy and "
                    "the model implementations are not part of this release."
                ),
            ),
        )
