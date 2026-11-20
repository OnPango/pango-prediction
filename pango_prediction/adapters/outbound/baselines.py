"""Baseline forecasting models.

These are production models, not placeholders: the plan lists them in the model
registry, and a benchmark is only meaningful against them. They use no external
library, but they sit behind the model port like every other implementation, so
the application depends on the port and not on them.

Each one produces a flat forecast across the horizon and an interval from the
empirical quantiles of its own in-sample residuals. The residual definition is
what differs between them, so each computes its own; the step from residuals to
an interval is shared, in `domain.intervals`.

Masked weeks are dropped before fitting, per the notebook's S31. None of these
is seasonal, so the contraction is the correct treatment for all of them.
"""

from collections.abc import Mapping, Sequence

from pango_prediction.domain.availability import observed_demand
from pango_prediction.domain.intervals import (
    empirical_interval,
    horizon_total_interval,
)
from pango_prediction.domain.series import future_weeks
from pango_prediction.domain.types import ForecastPeriod, ForecastTotal
from pango_prediction.ports.model import (
    InsufficientHistoryError,
    ModelRequest,
    ModelUnavailableError,
)


def _flat_forecast(
    request: ModelRequest,
    estimate: float,
    residuals: Sequence[float],
) -> tuple[ForecastPeriod, ...]:
    """Repeat ``estimate`` across the horizon with one shared interval.

    The estimate is passed through untouched. `empirical_interval` guarantees
    the interval contains it, so there is nothing to clamp here — and clamping
    would mean silently forecasting a number the model did not produce.
    """
    lower, upper = empirical_interval(estimate, residuals, request.quantiles)

    return tuple(
        ForecastPeriod(
            period_start=week,
            estimate=estimate,
            lower=lower,
            upper=upper,
        )
        for week in future_weeks(request.series.dates[-1], request.horizon)
    )


def _total_forecast(
    request: ModelRequest,
    estimate: float,
    values: Sequence[float],
    origin_forecasts: Sequence[float | None],
) -> ForecastTotal:
    """The horizon total, with its interval read from real horizon sums (D4).

    ``origin_forecasts[t]`` is what the model would have forecast for each week
    from ``t`` on, using ``values[:t]`` only.
    """
    total, lower, upper = horizon_total_interval(
        estimate,
        values,
        origin_forecasts,
        request.horizon,
        request.quantiles,
    )
    return ForecastTotal(estimate=total, lower=lower, upper=upper)


def _history(request: ModelRequest, minimum: int, model: str) -> tuple[float, ...]:
    """Observable demand, refusing to run on less than ``minimum`` weeks."""
    if not request.series.dates:
        raise InsufficientHistoryError(f"{model} needs a non-empty series")

    values = observed_demand(request.series, request.availability)

    if len(values) < minimum:
        raise InsufficientHistoryError(
            f"{model} needs at least {minimum} observable weeks, "
            f"and this series has {len(values)}"
        )

    return values


def _positive_int_param(params: Mapping[str, object], key: str, model: str) -> int:
    value = params.get(key)

    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelUnavailableError(f"{model} needs an integer '{key}' parameter")

    if value < 1:
        raise ModelUnavailableError(f"{model} needs '{key}' to be at least 1")

    return value


class ZeroModel:
    """Always forecasts nothing.

    The benchmark every other model has to beat. With demand this intermittent
    it is not a straw man: a model that cannot beat zero is not worth deploying.
    """

    name = "zero"

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        values = _history(request, minimum=1, model=self.name)

        # Forecasting zero makes every observation its own residual.
        return _flat_forecast(request, estimate=0.0, residuals=values)

    def predict_total(
        self,
        request: ModelRequest,
        periods: tuple[ForecastPeriod, ...],
    ) -> ForecastTotal:
        del periods
        values = _history(request, minimum=1, model=self.name)
        return _total_forecast(
            request,
            estimate=0.0,
            values=values,
            origin_forecasts=[0.0] * len(values),
        )


class NaiveModel:
    """Forecasts the last observable week, repeated."""

    name = "naive"

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        values = _history(request, minimum=2, model=self.name)

        residuals = [
            later - earlier for earlier, later in zip(values, values[1:], strict=False)
        ]

        return _flat_forecast(request, estimate=values[-1], residuals=residuals)

    def predict_total(
        self,
        request: ModelRequest,
        periods: tuple[ForecastPeriod, ...],
    ) -> ForecastTotal:
        del periods
        values = _history(request, minimum=2, model=self.name)
        return _total_forecast(
            request,
            estimate=values[-1],
            values=values,
            origin_forecasts=[None, *values[:-1]],
        )


class MovingAverageModel:
    """Forecasts the mean of the last ``window`` observable weeks.

    The window is a policy parameter rather than part of the name, because the
    policy picks it per demand class: the model enum carries `moving_average`
    and the rule supplies `{"window": 26}`.
    """

    name = "moving_average"

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        window = _positive_int_param(request.params, "window", self.name)

        # One extra week so that at least one residual can be measured.
        values = _history(request, minimum=window + 1, model=self.name)

        residuals = [
            values[index] - _mean(values[index - window : index])
            for index in range(window, len(values))
        ]

        return _flat_forecast(
            request,
            estimate=_mean(values[-window:]),
            residuals=residuals,
        )

    def predict_total(
        self,
        request: ModelRequest,
        periods: tuple[ForecastPeriod, ...],
    ) -> ForecastTotal:
        del periods
        window = _positive_int_param(request.params, "window", self.name)
        values = _history(request, minimum=window + 1, model=self.name)
        origin_forecasts: list[float | None] = [None] * window + [
            _mean(values[index - window : index])
            for index in range(window, len(values))
        ]
        return _total_forecast(
            request,
            estimate=_mean(values[-window:]),
            values=values,
            origin_forecasts=origin_forecasts,
        )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)
