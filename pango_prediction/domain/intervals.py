"""Prediction intervals from empirical residuals.

Every forecast has to carry an interval: `forecast_periods.lower_bound` and
`upper_bound` are NOT NULL in the tenant schema, so a model that produces only a
point estimate produces a result that cannot be persisted.

Route A fixed the method: quantiles 0.10 and 0.90, taken from empirical
quantiles of calibration residuals. This module owns that computation so every
model agrees on it; a per-model implementation would drift, and parity with the
evaluation run depends on it being one calculation.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil, floor


@dataclass(frozen=True, slots=True)
class IntervalQuantiles:
    """The quantiles a forecast interval spans.

    Supplied by the policy artifact, which carries them as ``interval``.
    """

    lower: float = 0.10
    upper: float = 0.90

    def __post_init__(self) -> None:
        for name, value in (("lower", self.lower), ("upper", self.upper)):
            if not 0 < value < 1:
                raise ValueError(f"quantile {name} must lie between 0 and 1")

        if self.lower >= self.upper:
            raise ValueError("the lower quantile must be below the upper one")


DEFAULT_QUANTILES = IntervalQuantiles()
"""Route A's choice: the 10th and 90th percentiles."""


def quantile(values: Sequence[float], q: float) -> float:
    """Empirical quantile with linear interpolation between order statistics.

    The same method NumPy uses by default, spelled out here so that the service
    needs no array library and so that the recipe is auditable.
    """
    if not values:
        raise ValueError("a quantile needs at least one value")

    if not 0 <= q <= 1:
        raise ValueError("q must lie between 0 and 1")

    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = q * (len(ordered) - 1)
    below = floor(position)
    above = ceil(position)

    if below == above:
        return ordered[below]

    weight = position - below

    return ordered[below] + (ordered[above] - ordered[below]) * weight


def empirical_interval(
    estimate: float,
    residuals: Sequence[float],
    quantiles: IntervalQuantiles,
) -> tuple[float, float]:
    """Spread ``estimate`` by the empirical quantiles of ``residuals``.

    Two rules keep the result coherent:

    The interval always contains the estimate. When a model is biased in sample
    every residual has the same sign, so a quantile on its own would put both
    bounds on one side of the point forecast. The bound is therefore only ever
    moved away from the estimate, never across it. Correcting the point forecast
    for that bias would be a modelling decision, and it is not this function's
    to make.

    The lower bound is clamped at zero, because demand is gross and cannot be
    negative.
    """
    if not residuals:
        raise ValueError(
            "an interval needs at least one residual; the policy is expected to "
            "route series with too little history to a pooling model instead"
        )

    below = min(quantile(residuals, quantiles.lower), 0.0)
    above = max(quantile(residuals, quantiles.upper), 0.0)

    return max(estimate + below, 0.0), max(estimate + above, 0.0)


MIN_HORIZON_RESIDUALS = 8
"""Horizon-sum residuals needed before the total interval is read from them.

Below this, the quantiles 0.10 and 0.90 of the sums would rest on one or two
order statistics. A platform rule rather than a tenant calibration, so it is not
a field of `DomainParameters` and does not enter the parameter fingerprint.
"""


def one_step_residuals(
    values: Sequence[float],
    origin_forecasts: Sequence[float | None],
) -> tuple[float, ...]:
    """Weekly residuals ``y[t] - f[t]`` at every origin that has a forecast.

    ``origin_forecasts[t]`` is the flat weekly forecast the model would have made
    with ``values[:t]`` only, or ``None`` where it could not make one yet.
    """
    _check_alignment(values, origin_forecasts)

    return tuple(
        value - forecast
        for value, forecast in zip(values, origin_forecasts, strict=True)
        if forecast is not None
    )


def horizon_residuals(
    values: Sequence[float],
    origin_forecasts: Sequence[float | None],
    horizon: int,
) -> tuple[float, ...]:
    """Residuals of the ``horizon``-week total, one per rolling origin.

    At origin ``t`` the model forecasts ``f[t]`` for each of the next
    ``horizon`` weeks, so its total is ``horizon * f[t]``; the realised total is
    ``sum(values[t : t + horizon])``. Origins overlap by design: they are every
    week the forecast could have been issued, as in production.

    This is the decision D4 of Route A: the interval of a total comes from real
    totals. Scaling a weekly residual by the horizon assumes the four weekly
    errors are identical, which over-widens it for intermittent demand and gets
    its asymmetry wrong.
    """
    _check_alignment(values, origin_forecasts)

    if horizon < 1:
        raise ValueError("the horizon must be at least one period")

    return tuple(
        sum(values[start : start + horizon]) - horizon * forecast
        for start, forecast in enumerate(origin_forecasts)
        if forecast is not None and start + horizon <= len(values)
    )


def horizon_total_interval(
    estimate: float,
    values: Sequence[float],
    origin_forecasts: Sequence[float | None],
    horizon: int,
    quantiles: IntervalQuantiles,
) -> tuple[float, float, float]:
    """The total over ``horizon`` weeks of a flat forecast, with its interval.

    Returns ``(total, lower, upper)``. The point total is ``horizon *
    estimate``, which is exact for a flat forecast: the mean of a sum is the sum
    of the means. Only the interval needs the horizon sums.

    With fewer than `MIN_HORIZON_RESIDUALS` horizon sums, the interval falls
    back to the weekly residuals scaled by the horizon, the previous calculation.
    It is declared as a deviation from D4 for short series, and it is not always
    wider: when a model over-forecasts in sample, every residual is negative and
    the upper bound collapses onto the estimate. Coverage must be reported
    separately for totals read from sums and for totals from this fallback.
    """
    total = horizon * estimate
    sums = horizon_residuals(values, origin_forecasts, horizon)

    if len(sums) >= MIN_HORIZON_RESIDUALS:
        lower, upper = empirical_interval(total, sums, quantiles)
        return total, lower, upper

    weekly = one_step_residuals(values, origin_forecasts)
    lower, upper = empirical_interval(
        total,
        [residual * horizon for residual in weekly],
        quantiles,
    )
    return total, lower, upper


def _check_alignment(
    values: Sequence[float],
    origin_forecasts: Sequence[float | None],
) -> None:
    if len(values) != len(origin_forecasts):
        raise ValueError("there must be one origin forecast per observed week")
