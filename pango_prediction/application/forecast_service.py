"""Forecast use case.

Coordinates the domain for a batch of series. It owns no forecasting algorithm
and no HTTP concern: it calls domain functions in order and decides what each
series' outcome is.

The intended full flow (plan B13) is:

    input -> series -> availability -> calendar -> profile -> policy -> model

The policy and model registry are injected so this use case can execute the
same domain pipeline with a development policy today and Route A's evaluated
policy later.

A series has one of three outcomes: a forecast, a decision by the policy not
to forecast it, or a failure. The second is not an error and is reported apart.

One series failing never fails the batch (plan B17). The one thing that does
refuse a whole command is a horizon the policy was not calibrated for, which
is a fault of the request and not of any series.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import cast

from pango_prediction.application.errors import (
    HorizonMismatchError,
    SeriesErrorCode,
)
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.calendar import ClosedDates, weekly_business_days
from pango_prediction.domain.parameters import (
    DEFAULT_PARAMETERS,
    DomainParameters,
)
from pango_prediction.domain.policy import ModelPolicy, PolicyError
from pango_prediction.domain.profile import build_demand_profile
from pango_prediction.domain.series import build_weekly_series, future_weeks
from pango_prediction.domain.types import (
    DemandClass,
    DemandProfile,
    ForecastPeriod,
    ForecastSeriesInput,
    ForecastTotal,
    NoForecastReason,
    StockHandling,
)
from pango_prediction.ports.model import (
    ForecastTotalModel,
    ModelError,
    ModelRequest,
    ModelUnavailableError,
)
from pango_prediction.ports.registry import ModelNotRegisteredError, ModelRegistry


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
    """What happened to one series: a forecast, a decision not to, or a failure.

    Exactly one of the three holds. ``no_forecast`` is the policy declining the
    series on purpose, with its reason; ``error`` is something going wrong.
    They are kept apart here whatever the caller is able to understand, and
    rendering a declined series for a client that only knows two statuses is
    the inbound adapter's concern, not this one's.
    """

    series_id: str
    warnings: tuple[str, ...] = ()
    error: SeriesError | None = None
    no_forecast: NoForecastReason | None = None
    """Why the policy declined the series. None when it did not."""
    profile: DemandProfile | None = None
    """What the series' demand behaves like. None only when it never got that
    far, so a profile and an error can and do travel together."""
    horizon_business_days: tuple[float, ...] = ()
    """Equivalent business days of each week being forecast, warehouse-aware."""
    periods: tuple[ForecastPeriod, ...] = ()
    total: ForecastTotal | None = None
    model_name: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        if self.error is not None and self.no_forecast is not None:
            raise ValueError("a series is either declined or failed, not both")

    @property
    def ok(self) -> bool:
        """Whether the series was forecast."""
        return self.error is None and self.no_forecast is None


@dataclass(frozen=True, slots=True)
class ForecastResultBatch:
    outcomes: tuple[SeriesOutcome, ...] = field(default_factory=tuple)
    policy_version: str | None = None

    @property
    def ok_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.ok)

    @property
    def no_forecast_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.no_forecast is not None)

    @property
    def error_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.error is not None)


class ForecastService:
    """Runs the forecast pipeline over a batch of series."""

    def __init__(
        self,
        policy: ModelPolicy | None = None,
        registry: ModelRegistry | None = None,
    ) -> None:
        self._policy = policy
        self._registry = registry or ModelRegistry()

    def run(self, command: ForecastCommand) -> ForecastResultBatch:
        """Forecast every series of the command.

        Raises `HorizonMismatchError` before touching any series when the
        command's horizon is not the one the loaded policy declares. Without a
        policy there is nothing to disagree with: no model runs, and each
        series reports MODEL_NOT_AVAILABLE on its own.
        """
        if self._policy is not None and command.horizon != self._policy.horizon:
            raise HorizonMismatchError(
                requested=command.horizon,
                expected=self._policy.horizon,
                policy_version=self._policy.policy_version,
            )

        return ForecastResultBatch(
            outcomes=tuple(
                self._run_one(series, command) for series in command.series
            ),
            policy_version=(
                None if self._policy is None else self._policy.policy_version
            ),
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

        horizon_business_days = weekly_business_days(
            future_weeks(regularized.dates[-1], command.horizon),
            command.closed_dates.for_warehouse(series.warehouse_id),
            command.parameters,
        )

        if self._policy is None:
            return SeriesOutcome(
                series_id=series.series_id,
                warnings=self._warnings(profile),
                profile=profile,
                horizon_business_days=horizon_business_days,
                error=SeriesError(
                    code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                    message="No model policy is loaded.",
                ),
            )

        try:
            decision = self._policy.resolve(profile)
        except PolicyError as error:
            return SeriesOutcome(
                series_id=series.series_id,
                warnings=self._warnings(profile),
                profile=profile,
                horizon_business_days=horizon_business_days,
                error=SeriesError(
                    code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                    message=str(error),
                ),
            )

        if decision.model is None and decision.reason is not None:
            # The policy declined the series on purpose. That is an outcome of
            # its own: nothing failed, and the profile and warnings are the
            # whole of what there is to say.
            return SeriesOutcome(
                series_id=series.series_id,
                warnings=self._warnings(profile),
                profile=profile,
                horizon_business_days=horizon_business_days,
                no_forecast=decision.reason,
            )

        if decision.model is None:
            # No model and no reason: the policy has nothing for this series
            # and does not say why, so it stays a missing model. This is the
            # path history insufficiency travels until a rule gives it a reason.
            return SeriesOutcome(
                series_id=series.series_id,
                warnings=self._warnings(profile),
                profile=profile,
                horizon_business_days=horizon_business_days,
                error=SeriesError(
                    code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                    message="The policy selected no model.",
                ),
            )

        try:
            model = self._registry.resolve(decision.model.name)
            model_request = ModelRequest(
                series=regularized,
                availability=availability,
                horizon=command.horizon,
                params=decision.model.params,
            )
            periods = model.predict(model_request)
            if not hasattr(model, "predict_total"):
                raise ModelError(
                    f"Model '{model.name}' does not provide a calibrated horizon total."
                )
            total_model = cast(ForecastTotalModel, model)
            total = total_model.predict_total(model_request, periods)
        except (ModelNotRegisteredError, ModelUnavailableError) as error:
            return SeriesOutcome(
                series_id=series.series_id,
                warnings=self._warnings(profile),
                profile=profile,
                horizon_business_days=horizon_business_days,
                error=SeriesError(
                    code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                    message=str(error),
                ),
            )
        except ModelError as error:
            return SeriesOutcome(
                series_id=series.series_id,
                warnings=self._warnings(profile),
                profile=profile,
                horizon_business_days=horizon_business_days,
                error=SeriesError(
                    code=SeriesErrorCode.MODEL_FAILED,
                    message=str(error),
                ),
            )

        return SeriesOutcome(
            series_id=series.series_id,
            warnings=self._warnings(profile),
            profile=profile,
            horizon_business_days=horizon_business_days,
            periods=periods,
            total=total,
            model_name=model.name,
            model_version="development",
        )

    @staticmethod
    def _warnings(profile: DemandProfile) -> tuple[str, ...]:
        """What Pango Core should know about this series before a model exists.

        Explanations in words, alongside whichever outcome the series has. A
        declined series also carries its reason from the closed vocabulary;
        these say what the profile shows, which is more than one code can.
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
