"""Forecast use case.

Coordinates the domain for a batch of series. It owns no forecasting algorithm
and no HTTP concern: it calls domain functions in order and decides what each
series' outcome is.

The intended full flow (plan B13) is:

    input -> series -> availability -> calendar -> profile -> policy -> model

What runs today is everything up to the policy: series regularization,
availability, the demand profile and the calendar covariate. Policy and model
selection are not built, so every series still ends as ``MODEL_NOT_AVAILABLE``.
That is deliberate - the service says it cannot forecast rather than returning
a number no model produced - but the series is no longer a black box while it
waits: Pango Core learns what each series looks like, why it will be routed the
way it will, and how many business days its horizon holds.

One series failing never fails the batch (plan B17).
"""

from dataclasses import dataclass, field
from datetime import date

from pango_prediction.application.errors import SeriesErrorCode
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.calendar import ClosedDates, weekly_business_days
from pango_prediction.domain.parameters import (
    DEFAULT_PARAMETERS,
    DomainParameters,
)
from pango_prediction.domain.profile import build_demand_profile
from pango_prediction.domain.series import build_weekly_series, future_weeks
from pango_prediction.domain.types import (
    DemandClass,
    DemandProfile,
    ForecastSeriesInput,
    StockHandling,
)


@dataclass(frozen=True, slots=True)
class ForecastCommand:
    """What the use case needs, in domain terms rather than HTTP terms."""

    history_end: date
    horizon: int
    series: tuple[ForecastSeriesInput, ...]

    closed_dates: ClosedDates = field(default_factory=ClosedDates)
    """Which dates the tenant does not operate, per warehouse.

    Data, not calibration: the weight rule is shared code, the dates arrive
    with the request. See `domain/calendar.py`.
    """

    parameters: DomainParameters = DEFAULT_PARAMETERS
    """The tenant's calibration.

    On the command rather than on the service, which is the multi-tenancy
    decision in this file. One request serves one tenant, but one process
    serves many, so a `ForecastService` instance holding a single calibration
    would silently apply one tenant's thresholds to another's series.
    """


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
    profile: DemandProfile | None = None
    """What the series' demand behaves like. None only when it never got that
    far, so a profile and an error can and do travel together."""
    horizon_business_days: tuple[float, ...] = ()
    """Equivalent business days of each week being forecast, warehouse-aware."""

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
            availability = build_weekly_availability(
                regularized, series.stock, command.parameters
            )
        except ValueError as error:
            return SeriesOutcome(
                series_id=series.series_id,
                error=SeriesError(
                    code=SeriesErrorCode.INVALID_SERIES,
                    message=str(error),
                ),
            )

        # Not wrapped in try/except on purpose. The profile only rejects an
        # empty series or a mismatched grid, both of which this method has just
        # established, so a ValueError here would be a bug in the service.
        # Reporting it as INVALID_SERIES would blame Pango Core for our fault.
        profile = build_demand_profile(
            series.demand,
            regularized,
            availability,
            command.history_end,
            command.parameters,
        )

        return SeriesOutcome(
            series_id=series.series_id,
            warnings=self._warnings(profile),
            profile=profile,
            horizon_business_days=weekly_business_days(
                future_weeks(regularized.dates[-1], command.horizon),
                command.closed_dates.for_warehouse(series.warehouse_id),
                command.parameters,
            ),
            error=SeriesError(
                code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                message=(
                    "No forecasting model is deployed yet: the model policy and "
                    "the model implementations are not part of this release."
                ),
            ),
        )

    @staticmethod
    def _warnings(profile: DemandProfile) -> tuple[str, ...]:
        """What Pango Core should know about this series before a model exists.

        Warnings rather than a status of their own. Route A's reply sketches a
        third outcome, NO_FORECAST with a reason, and section 9 of the domain
        rules shows three quarters of the catalogue falling into it. Inventing
        that status here would be guessing at a decision the policy format has
        not made yet, so these travel as text until it does.
        """
        warnings: list[str] = []

        if profile.stock_handling is StockHandling.NOT_CLASSIFIABLE:
            warnings.append(
                "Stock handling could not be classified, so stockouts were not "
                "evaluated for this series."
            )
        elif profile.stock_handling is StockHandling.MADE_TO_ORDER:
            warnings.append(
                "The series is sold to order, so zero stock was not read as a "
                "stockout."
            )

        if profile.inactive:
            warnings.append(
                f"The series has not sold for {profile.weeks_since_last_sale} "
                "weeks and is read as inactive."
            )

        if not profile.evaluable:
            warnings.append(
                f"Only {profile.observed_weeks} weeks can be read as demand, "
                "which is too few to forecast this series on its own."
            )

        if profile.demand_class is DemandClass.NOT_CLASSIFIABLE:
            warnings.append(
                "Every month of the classification window was spent in a "
                "stockout, so the demand pattern could not be established."
            )
        elif profile.demand_class is DemandClass.INSUFFICIENT:
            warnings.append(
                "The series sold in too few months for its demand pattern to "
                "be measured."
            )

        return tuple(warnings)
