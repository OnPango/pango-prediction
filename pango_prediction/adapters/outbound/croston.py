"""Croston-family models for intermittent demand.

Only SBA (Syntetos-Boylan Approximation) for now. Route A's policy sends the
`INTERMITTENT` and `LUMPY` quadrants to it, which is most of the individually
forecast catalogue.

Written in plain Python, like the baselines, so the service takes no array
library. The recipe follows StatsForecast's `CrostonSBA` (2.1.1) exactly, so the
evaluation can check this implementation against it with a stated tolerance:

- demand sizes and inter-demand intervals are each smoothed with simple
  exponential smoothing, with one shared ``alpha``;
- the smoothing starts at the first value: the size at the first sale, the
  interval at the position of the first sale plus one;
- the forecast is ``(1 - alpha / 2) * size / interval``, flat over the horizon.

Masked weeks are dropped before fitting (S31), through `observed_demand`, like
every non-seasonal model: a stockout week neither counts as a zero nor
lengthens the interval to the next sale.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pango_prediction.domain.availability import observed_demand
from pango_prediction.domain.intervals import (
    empirical_interval,
    horizon_total_interval,
    one_step_residuals,
)
from pango_prediction.domain.series import future_weeks
from pango_prediction.domain.types import ForecastPeriod, ForecastTotal
from pango_prediction.ports.model import InsufficientHistoryError, ModelRequest

DEFAULT_ALPHA = 0.1
"""StatsForecast's fixed value. Route A evaluates SBA with it, untuned."""

MIN_DEMAND_WEEKS = 2
"""Weeks with a sale needed to fit SBA.

One sale fixes the starting size and interval but never updates them, and
leaves no residual to build an interval from. A series in the `INTERMITTENT`
or `LUMPY` quadrant always has at least two months with sales, so a policy that
routes only those quadrants here never reaches this limit.
"""


@dataclass(frozen=True, slots=True)
class SbaFit:
    """The fitted SBA state on the observable weeks of one series."""

    values: tuple[float, ...]
    origin_forecasts: tuple[float | None, ...]
    estimate: float


def fit_sba(values: Sequence[float], alpha: float) -> SbaFit:
    """Fit SBA on ``values`` and replay its one-step forecast at every origin.

    ``origin_forecasts[t]`` uses ``values[:t]`` only, so the residuals built
    from it are honest in-sample residuals. It is ``None`` until the first sale
    has been seen. Croston only updates on a sale, so the forecast carries
    unchanged through a run of zero weeks.
    """
    if not 0 < alpha <= 1:
        raise ValueError("sba needs 'alpha' between 0 (excluded) and 1")

    if sum(1 for value in values if value > 0) < MIN_DEMAND_WEEKS:
        raise InsufficientHistoryError(
            f"sba needs at least {MIN_DEMAND_WEEKS} observable weeks with a sale"
        )

    debias = 1 - alpha / 2
    size: float | None = None
    interval = 0.0
    last_sale = -1
    forecasts: list[float | None] = []

    for index, value in enumerate(values):
        forecasts.append(None if size is None else debias * size / interval)

        if value <= 0:
            continue

        if size is None:
            size = value
            interval = float(index + 1)
        else:
            size += alpha * (value - size)
            interval += alpha * ((index - last_sale) - interval)

        last_sale = index

    # Guaranteed by the minimum of sales above; stated for the type checker.
    assert size is not None

    return SbaFit(
        values=tuple(values),
        origin_forecasts=tuple(forecasts),
        estimate=debias * size / interval,
    )


class SbaModel:
    """Syntetos-Boylan Approximation: Croston with its bias corrected."""

    name = "sba"

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        fit = self._fit(request)
        lower, upper = empirical_interval(
            fit.estimate,
            one_step_residuals(fit.values, fit.origin_forecasts),
            request.quantiles,
        )

        return tuple(
            ForecastPeriod(
                period_start=week,
                estimate=fit.estimate,
                lower=lower,
                upper=upper,
            )
            for week in future_weeks(request.series.dates[-1], request.horizon)
        )

    def predict_total(
        self,
        request: ModelRequest,
        periods: tuple[ForecastPeriod, ...],
    ) -> ForecastTotal:
        del periods
        fit = self._fit(request)
        total, lower, upper = horizon_total_interval(
            fit.estimate,
            fit.values,
            fit.origin_forecasts,
            request.horizon,
            request.quantiles,
        )
        return ForecastTotal(estimate=total, lower=lower, upper=upper)

    def _fit(self, request: ModelRequest) -> SbaFit:
        if not request.series.dates:
            raise InsufficientHistoryError("sba needs a non-empty series")

        alpha = _alpha_param(request.params)
        return fit_sba(observed_demand(request.series, request.availability), alpha)


def _alpha_param(params: Mapping[str, object]) -> float:
    value = params.get("alpha", DEFAULT_ALPHA)

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("sba needs a numeric 'alpha' parameter")

    return float(value)
