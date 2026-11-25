"""Forecast use case.

Coordinates the domain for a batch of series. It owns no forecasting algorithm
and no HTTP concern: it calls domain functions in order and decides what each
series' outcome is.

The intended full flow (plan B13) is:

    input -> series -> availability -> calendar -> profile -> model comparison -> model

The production composition uses rolling-origin model comparison against the
registered local candidates. An explicitly supplied policy remains available
to isolated compatibility tests, but the HTTP application does not load one.

A series has one of three outcomes: a forecast, a rule-based decision not to
forecast it, or a failure. The second is not an error and is reported apart.

One series failing never fails the batch (plan B17). The one thing that does
refuse a whole command is a horizon the policy was not calibrated for, which
is a fault of the request and not of any series.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import cast

from pango_prediction.application.errors import (
    HorizonMismatchError,
    SeriesErrorCode,
)
from pango_prediction.application.model_selection import (
    InsufficientBacktestHistoryError,
    ModelSelectionError,
    select_model,
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
    InsufficientHistoryError,
    ModelError,
    ModelRequest,
    ModelUnavailableError,
)
from pango_prediction.ports.registry import ModelNotRegisteredError, ModelRegistry

logger = logging.getLogger(__name__)


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

    warehouse_holds_stock: Mapping[str, bool] = field(default_factory=dict)
    """Whether each warehouse holds stock, keyed by ``warehouse_id``.

    Administrator-managed configuration, sent with the request because nothing
    in a series' history can tell a warehouse that ran out from one that never
    holds stock. A warehouse missing from the mapping is treated as holding
    stock, and its series say so in a warning.
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
    model_history_insufficient: bool = False
    """The selected model refused the series for too few observable weeks.

    Not a code of its own on purpose: it still travels as MODEL_FAILED, because
    routing history insufficiency to its own outcome is deferred until after
    20-nov (Route A baseline README). The holdout validation needs to tell it
    apart, since Route A's evaluation counted such a series as a zero forecast
    (criteria DEC-1) while every other failure is left unscored.
    """


@dataclass(frozen=True, slots=True)
class SeriesOutcome:
    """What happened to one series: a forecast, a decision not to, or a failure.

    Exactly one of the three holds. ``no_forecast`` is a rule declining the
    series on purpose, with its reason; ``error`` is something going wrong.
    Request-level exclusions remain distinct from model-comparison failures,
    and rendering the outcome for a client is the inbound adapter's concern.
    """

    series_id: str
    warnings: tuple[str, ...] = ()
    error: SeriesError | None = None
    no_forecast: NoForecastReason | None = None
    """Why a rule declined the series. None when it did not."""
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

        Raises `HorizonMismatchError` before touching any series when an
        explicitly supplied policy does not cover the requested horizon.
        Without a policy, the registered candidate models are compared using
        the same horizon in rolling-origin folds.
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

        # Known from the request alone, before any of the series' history is
        # read. Invalid data above still fails the series: declining it must
        # not hide that what was sent is wrong.
        declined = self._declined_by_request(series, command)

        if not regularized.dates:
            if declined is not None:
                # Too little history is no failure for a series that nobody
                # meant to forecast.
                return SeriesOutcome(series_id=series.series_id, no_forecast=declined)

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

        warnings = self._warnings(profile) + self._warehouse_warnings(series, command)

        if declined is not None:
            # The request comes before the policy: this was configured by an
            # administrator and stays true however the series' demand changes,
            # so it needs no policy and is not overridden by one.
            return SeriesOutcome(
                series_id=series.series_id,
                warnings=warnings,
                profile=profile,
                horizon_business_days=horizon_business_days,
                no_forecast=declined,
            )

        if self._policy is None:
            try:
                selected = select_model(
                    series,
                    regularized,
                    horizon=command.horizon,
                    registry=self._registry,
                    parameters=command.parameters,
                )
            except InsufficientBacktestHistoryError as error:
                return SeriesOutcome(
                    series_id=series.series_id,
                    warnings=warnings,
                    profile=profile,
                    horizon_business_days=horizon_business_days,
                    error=SeriesError(
                        code=SeriesErrorCode.INSUFFICIENT_HISTORY,
                        message=str(error),
                    ),
                )
            except ModelSelectionError as error:
                return SeriesOutcome(
                    series_id=series.series_id,
                    warnings=warnings,
                    profile=profile,
                    horizon_business_days=horizon_business_days,
                    error=SeriesError(
                        code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                        message=str(error),
                    ),
                )

            model_spec = selected.model
            logger.debug(
                "Internal model comparison selected=%s scores=%s excluded=%s",
                selected.model.name,
                [
                    {
                        "model": score.model.name,
                        "wape": score.wape,
                        "absolute_bias": (
                            None if score.bias is None else abs(score.bias)
                        ),
                        "mae": score.mae,
                        "mase": score.mase,
                        "mean_rank": score.mean_rank,
                    }
                    for score in selected.scores
                ],
                selected.excluded_models,
            )
        else:
            try:
                decision = self._policy.resolve(profile)
            except PolicyError as error:
                return SeriesOutcome(
                    series_id=series.series_id,
                    warnings=warnings,
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
                    warnings=warnings,
                    profile=profile,
                    horizon_business_days=horizon_business_days,
                    no_forecast=decision.reason,
                )

            if decision.model is None:
                return SeriesOutcome(
                    series_id=series.series_id,
                    warnings=warnings,
                    profile=profile,
                    horizon_business_days=horizon_business_days,
                    error=SeriesError(
                        code=SeriesErrorCode.MODEL_NOT_AVAILABLE,
                        message="The policy selected no model.",
                    ),
                )

            model_spec = decision.model

        try:
            model = self._registry.resolve(model_spec.name)
            model_request = ModelRequest(
                series=regularized,
                availability=availability,
                horizon=command.horizon,
                params=model_spec.params,
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
                warnings=warnings,
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
                warnings=warnings,
                profile=profile,
                horizon_business_days=horizon_business_days,
                error=SeriesError(
                    code=SeriesErrorCode.MODEL_FAILED,
                    message=str(error),
                    model_history_insufficient=isinstance(
                        error, InsufficientHistoryError
                    ),
                ),
            )

        return SeriesOutcome(
            series_id=series.series_id,
            warnings=warnings,
            profile=profile,
            horizon_business_days=horizon_business_days,
            periods=periods,
            total=total,
            model_name=model.name,
            model_version=(
                "rolling-origin-average-rank-v1"
                if self._policy is None
                else "development"
            ),
        )

    @staticmethod
    def _declined_by_request(
        series: ForecastSeriesInput,
        command: ForecastCommand,
    ) -> NoForecastReason | None:
        """Why the request itself rules the series out, if it does.

        ``history_only`` is about the series and is checked first; a warehouse
        that holds no stock is about where the series lives. A warehouse the
        request does not describe is treated as holding stock.
        """
        if series.history_only:
            return NoForecastReason.HISTORY_ONLY

        if command.warehouse_holds_stock.get(series.warehouse_id) is False:
            return NoForecastReason.NON_STOCK_WAREHOUSE

        return None

    @staticmethod
    def _warehouse_warnings(
        series: ForecastSeriesInput,
        command: ForecastCommand,
    ) -> tuple[str, ...]:
        """Say when a warehouse was assumed, not known, to hold stock.

        The stockout reading depends on it: zero stock in a warehouse that
        never holds any is not a stockout. A history-only series is left out,
        because nothing is decided from the stock of a historical warehouse.
        """
        if series.history_only or series.warehouse_id in command.warehouse_holds_stock:
            return ()

        return (
            f"The request does not say whether warehouse {series.warehouse_id} "
            "holds stock, so it was treated as one that does.",
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
