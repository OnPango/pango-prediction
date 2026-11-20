"""Product to warehouse allocation of the forecast mean.

The product is forecast as a whole; this module decides how much of that mean
each of its warehouses receives. It is the "top-down" arm of the product versus
warehouse comparison (Route A, decision D1 and its evaluation protocol T4), in
the minimal scope agreed with Route B (A15): shares and the mean only, without
per-warehouse intervals and without a connection to ``application``.

The rules come from Route A's specification ("Desagregación producto → bodega")
and reproduce its reference implementation, the EDA v2 notebook (cell 12 B),
which is where the prior strength was estimated:

1. **Exposure.** Within a window of recent complete weeks, a warehouse only
   counts the weeks in which it could sell: from its first movement on, and
   without the stockout weeks. Its units are projected to the whole window,
   so a stockout or a late arrival does not read as low demand.
2. **Prior.** A hierarchical pattern, company then product group, says how a
   product of that group usually splits across warehouses. It is restricted to
   the product's active warehouses.
3. **Shares.** Each share is the posterior mean of a Dirichlet-multinomial
   model: the warehouse's projected units plus ``prior_strength`` units
   borrowed from the prior. The more units the product sold, the less the
   prior weighs: ``product_projected_units / (product_projected_units +
   prior_strength)`` is the weight of the product's own data.
4. **Mean.** Expectation is linear, so the mean splits exactly: each warehouse
   receives ``warehouse_share · product_estimate``. The part of the warehouses
   that do not hold stock is returned as not forecast (``non_stock_warehouse``)
   so that the parts always add up to the product mean.

The prior strength and the window length are arguments, not
``DomainParameters``: they are frozen in Route A's ``freeze.json``, outside the
parameter fingerprint (A14).
"""

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from math import isclose, isfinite

from pango_prediction.domain.series import last_complete_week_start, week_start
from pango_prediction.domain.types import RegularizedSeries, WeeklyAvailability

NON_STOCK_WAREHOUSE = "non_stock_warehouse"
"""``NO_FORECAST`` reason for the part of the mean that falls on a warehouse that
does not hold stock: that demand must not drive the replenishment of the stock
warehouses."""

NO_ACTIVE_WAREHOUSE = "no_active_warehouse"
"""``NO_FORECAST`` reason for a product that has no active warehouse to receive
its mean."""

SHARE_TOLERANCE = 1e-9
"""Tolerance for checking that shares add up to one. Guards against float noise;
not a business rule."""


@dataclass(frozen=True, slots=True)
class WindowExposure:
    """What one product × warehouse series shows inside the allocation window.

    ``exposed_weeks`` are the window weeks in which the warehouse could sell, and
    ``observed_units`` the gross demand of those weeks.
    """

    window_weeks: int
    exposed_weeks: int
    observed_units: float

    def __post_init__(self) -> None:
        if self.window_weeks < 1:
            raise ValueError("the allocation window needs at least one week")

        if not 0 <= self.exposed_weeks <= self.window_weeks:
            raise ValueError(
                f"exposed weeks must lie between 0 and the window length ({self.window_weeks})"
            )

        if not isfinite(self.observed_units) or self.observed_units < 0:
            raise ValueError("observed units must be finite and non-negative")

        if self.observed_units > 0 and self.exposed_weeks == 0:
            raise ValueError("units can only be observed in exposed weeks")

    @property
    def projected_units(self) -> float | None:
        """Units the warehouse would have sold had it been exposed the whole window.

        ``None`` when it was never exposed: there is no evidence to project, and
        the shares impute the value from the prior instead.
        """
        if self.exposed_weeks == 0:
            return None

        return self.observed_units * self.window_weeks / self.exposed_weeks


@dataclass(frozen=True, slots=True)
class AllocationPrior:
    """How the catalogue splits across warehouses, for the company and per group.

    Products without a group are pooled under the key ``None``.
    """

    company_shares: Mapping[str, float]
    group_shares: Mapping[str | None, Mapping[str, float]]


@dataclass(frozen=True, slots=True)
class WarehouseMean:
    """The part of the product mean that one warehouse receives, week by week.

    ``reason`` is ``None`` when the warehouse is forecast, and
    ``NON_STOCK_WAREHOUSE`` when its part is returned as not forecast. The
    estimates are kept either way, so that the parts add up to the product.
    """

    warehouse_id: str
    estimates: tuple[float, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class MeanAllocation:
    """The product mean split across its active warehouses.

    ``warehouses`` is in ascending ``warehouse_id`` order. A product without
    active warehouses cannot be split: ``warehouses`` is empty and ``reason`` is
    ``NO_ACTIVE_WAREHOUSE``.
    """

    warehouses: tuple[WarehouseMean, ...]
    reason: str | None

    def __post_init__(self) -> None:
        if (self.reason == NO_ACTIVE_WAREHOUSE) != (not self.warehouses):
            raise ValueError(
                "a product is not allocated exactly when it has no active warehouse"
            )


def _check_prior_strength(prior_strength: float) -> None:
    if not isfinite(prior_strength) or prior_strength < 0:
        raise ValueError("the prior strength must be finite and non-negative")


def _check_shares(shares: Mapping[str, float], name: str) -> None:
    if any(not isfinite(share) or not 0 <= share <= 1 for share in shares.values()):
        raise ValueError(f"{name} must lie between 0 and 1")

    if shares and not isclose(sum(shares.values()), 1.0, abs_tol=SHARE_TOLERANCE):
        raise ValueError(f"{name} must add up to one")


def allocation_window(cutoff: date, window_weeks: int) -> tuple[date, ...]:
    """The Mondays of the ``window_weeks`` complete weeks that end at ``cutoff``.

    Only data up to the cutoff may shape the shares, like every other input of
    the forecast, so the window ends at the last week fully within it.
    """
    if window_weeks < 1:
        raise ValueError("the allocation window needs at least one week")

    last_week = last_complete_week_start(cutoff)

    return tuple(last_week - timedelta(weeks=back) for back in range(window_weeks - 1, -1, -1))


def measure_exposure(
    window: tuple[date, ...],
    first_movement: date,
    series: RegularizedSeries,
    availability: WeeklyAvailability,
) -> WindowExposure:
    """Count the exposed weeks of one warehouse series and the units they sold.

    A window week is exposed when it is not before the week of
    ``first_movement`` and its demand can be read as demand: stockout weeks
    (``CENSORED`` and ``OUT_OF_ASSORTMENT``) are not; ``UNKNOWN`` weeks are.

    ``first_movement`` is the first ledger movement of the product in this
    warehouse. It cannot be derived from ``series``, whose grid starts at the
    first *sale*: the weeks between the two had the product on hand and sold
    nothing, so they are exposed with zero units.

    ``series`` and ``availability`` must be built with data up to the cutoff, so
    the series ends exactly at the last window week; anything else means data
    from after the cutoff, or from another cutoff, and is rejected.
    """
    if not window:
        raise ValueError("the allocation window needs at least one week")

    if window[0] != week_start(window[0]) or any(
        later - earlier != timedelta(weeks=1) for earlier, later in pairwise(window)
    ):
        raise ValueError("the window must be consecutive Monday-Sunday weeks")

    if series.series_id != availability.series_id or series.dates != availability.dates:
        raise ValueError("the series and its availability must share one grid")

    first_week = week_start(first_movement)

    if series.dates:
        if series.dates[-1] != window[-1]:
            raise ValueError("the series must end at the last week of the allocation window")

        if first_week > series.dates[0]:
            raise ValueError("the first movement cannot come after the first sale")

    position = {week: index for index, week in enumerate(series.dates)}
    exposed_weeks = 0
    observed_units = 0.0

    for week in window:
        if week < first_week:
            continue

        index = position.get(week)

        if index is None:
            # Before the first sale: on hand and not sold.
            exposed_weeks += 1
        elif availability.available_mask[index]:
            exposed_weeks += 1
            observed_units += series.values[index]

    return WindowExposure(
        window_weeks=len(window),
        exposed_weeks=exposed_weeks,
        observed_units=observed_units,
    )


def build_prior(
    exposures: Iterable[tuple[str | None, str, WindowExposure]],
    prior_strength: float,
) -> AllocationPrior:
    """Build the company and group patterns from every series of the catalogue.

    ``exposures`` holds ``(group, warehouse_id, exposure)`` for every product ×
    warehouse series at the cutoff. Only series with exposed weeks inform the
    pattern: an imputed value would make the prior depend on itself.

    Each group is shrunk towards the company with ``prior_strength`` units, the
    same strength the product is later shrunk with towards its group.
    """
    _check_prior_strength(prior_strength)

    company_units: defaultdict[str, float] = defaultdict(float)
    group_units: defaultdict[str | None, defaultdict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )

    for group, warehouse_id, exposure in exposures:
        projected_units = exposure.projected_units

        if projected_units is None:
            continue

        company_units[warehouse_id] += projected_units
        group_units[group][warehouse_id] += projected_units

    company_total = sum(company_units.values())
    company_shares = {
        warehouse_id: units / company_total if company_total > 0 else 0.0
        for warehouse_id, units in company_units.items()
    }

    group_shares: dict[str | None, dict[str, float]] = {}

    for group, units_by_warehouse in group_units.items():
        denominator = sum(units_by_warehouse.values()) + prior_strength

        group_shares[group] = {
            warehouse_id: (
                (units_by_warehouse.get(warehouse_id, 0.0) + prior_strength * company_share)
                / denominator
                if denominator > 0
                else company_share
            )
            for warehouse_id, company_share in company_shares.items()
        }

    return AllocationPrior(company_shares=company_shares, group_shares=group_shares)


def restrict_prior(
    prior: AllocationPrior,
    group: str | None,
    active_warehouses: Collection[str],
) -> dict[str, float]:
    """The prior share of each active warehouse of a product of ``group``.

    A product receives no demand in a warehouse where it is not active, so the
    group pattern is cut to ``active_warehouses`` and renormalised. If the
    group never sold in any of them, the company pattern is used; if the
    company never did either, the split is uniform. A group the prior does not
    know uses the company pattern.
    """
    warehouses = sorted(active_warehouses)

    if len(set(warehouses)) != len(warehouses):
        raise ValueError("active warehouses must not repeat")

    if not warehouses:
        return {}

    pattern = prior.group_shares.get(group, prior.company_shares)

    for candidate in (pattern, prior.company_shares):
        weights = {warehouse_id: candidate.get(warehouse_id, 0.0) for warehouse_id in warehouses}
        total = sum(weights.values())

        if total > 0:
            return {warehouse_id: weight / total for warehouse_id, weight in weights.items()}

    return {warehouse_id: 1 / len(warehouses) for warehouse_id in warehouses}


def compute_warehouse_shares(
    exposures: Mapping[str, WindowExposure],
    prior_shares: Mapping[str, float],
    prior_strength: float,
) -> dict[str, float]:
    """The share of the product mean that each active warehouse receives.

    ``exposures`` and ``prior_shares`` are keyed by the product's active
    warehouses. A warehouse without exposed weeks has no evidence of its own:
    its projected units are imputed so that, against the warehouses that do
    have evidence, it keeps the proportion the prior gives it (zero when those
    warehouses carry no prior at all). Then::

        warehouse_share = (projected_units + prior_strength · prior_share)
                          / (product_projected_units + prior_strength)

    A product with no projected units at all takes the prior shares.
    """
    _check_prior_strength(prior_strength)

    if exposures.keys() != prior_shares.keys():
        raise ValueError("exposures and prior shares must cover the same warehouses")

    _check_shares(prior_shares, "prior shares")

    projected = {
        warehouse_id: exposure.projected_units for warehouse_id, exposure in exposures.items()
    }
    observed = {
        warehouse_id: units for warehouse_id, units in projected.items() if units is not None
    }
    observed_prior = sum(prior_shares[warehouse_id] for warehouse_id in observed)
    units_per_prior_share = sum(observed.values()) / observed_prior if observed_prior > 0 else 0.0

    projected_units = {
        warehouse_id: (
            units if units is not None else prior_shares[warehouse_id] * units_per_prior_share
        )
        for warehouse_id, units in projected.items()
    }
    product_projected_units = sum(projected_units.values())
    denominator = product_projected_units + prior_strength

    if denominator <= 0:
        return dict(prior_shares)

    return {
        warehouse_id: (units + prior_strength * prior_shares[warehouse_id]) / denominator
        for warehouse_id, units in projected_units.items()
    }


def allocate_mean(
    product_estimates: tuple[float, ...],
    warehouse_shares: Mapping[str, float],
    stock_warehouses: Collection[str],
) -> MeanAllocation:
    """Split the product's weekly mean across its active warehouses.

    Each warehouse receives ``warehouse_share · product_estimate`` for every
    week. Warehouses outside ``stock_warehouses`` keep their part but return
    it as not forecast, with ``NON_STOCK_WAREHOUSE``. Without active
    warehouses the product is not allocated at all (``NO_ACTIVE_WAREHOUSE``).

    The split is exact, so the forecast parts plus the not-forecast parts add
    up to ``product_estimates`` week by week. This works because the
    persisted value is the mean; a median could not be split this way.
    """
    if not product_estimates:
        raise ValueError("there must be at least one week to allocate")

    if any(not isfinite(estimate) or estimate < 0 for estimate in product_estimates):
        raise ValueError("product estimates must be finite and non-negative")

    _check_shares(warehouse_shares, "warehouse shares")

    if not warehouse_shares:
        return MeanAllocation(warehouses=(), reason=NO_ACTIVE_WAREHOUSE)

    return MeanAllocation(
        warehouses=tuple(
            WarehouseMean(
                warehouse_id=warehouse_id,
                estimates=tuple(
                    warehouse_shares[warehouse_id] * estimate for estimate in product_estimates
                ),
                reason=None if warehouse_id in stock_warehouses else NON_STOCK_WAREHOUSE,
            )
            for warehouse_id in sorted(warehouse_shares)
        ),
        reason=None,
    )
