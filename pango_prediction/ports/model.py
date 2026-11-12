"""The forecasting model port.

The application resolves a model through this interface and never imports a
concrete implementation. That is what lets a baseline, a StatsForecast wrapper
and a neural runtime be swapped by configuration instead of by code.

A `Protocol` is used rather than an abstract base class: a model satisfies the
port by having the right shape, so an implementation does not have to inherit
from anything. That keeps a wrapper around an external library free of our
class hierarchy.

The request carries the availability mask alongside the series, which is not
incidental. A model that averaged a stockout week's zero as if it were real
demand would forecast low, which is exactly the training-serving skew this
project exists to avoid. The notebook's rule (S31) is that non-seasonal models
drop masked weeks and join the remainder: `domain.availability.observed_demand`
performs that contraction.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from pango_prediction.domain.intervals import (
    DEFAULT_QUANTILES,
    IntervalQuantiles,
)
from pango_prediction.domain.types import (
    ForecastPeriod,
    ForecastTotal,
    RegularizedSeries,
    WeeklyAvailability,
)


class ModelError(Exception):
    """A model could not produce a forecast for this series."""


class InsufficientHistoryError(ModelError):
    """The series has too few observable weeks for this model.

    Raised rather than worked around: a 26-week average computed over 6 weeks is
    not the model that was evaluated. The policy is expected to route short
    histories to a pooling model instead, so reaching this is a policy gap.
    """


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Everything a model needs to forecast one series."""

    series: RegularizedSeries
    availability: WeeklyAvailability
    horizon: int
    quantiles: IntervalQuantiles = DEFAULT_QUANTILES
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise ValueError("the horizon must be at least one period")

        if self.series.series_id != self.availability.series_id:
            raise ValueError("the series and its availability must match")

        if self.series.dates != self.availability.dates:
            raise ValueError("the series and its availability must share one grid")


class ForecastModel(Protocol):
    """What every forecasting model looks like from the application's side."""

    @property
    def name(self) -> str:
        """The stable name the policy refers to this model by."""
        ...

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        """Forecast ``request.horizon`` periods after the end of the series.

        Returns one period per horizon step, each with its interval. Raises
        ``ModelError`` when this series cannot be forecast by this model.
        """
        ...


class ForecastTotalModel(Protocol):
    """Optional capability for models calibrated on horizon totals."""

    def predict_total(
        self,
        request: ModelRequest,
        periods: tuple[ForecastPeriod, ...],
    ) -> ForecastTotal:
        """Return the interval for the aggregate horizon demand."""
        ...
