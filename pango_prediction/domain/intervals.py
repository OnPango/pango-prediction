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
