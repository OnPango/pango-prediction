from datetime import date, timedelta
from itertools import pairwise

import pytest

from pango_prediction.domain.allocation import (
    NO_ACTIVE_WAREHOUSE,
    NON_STOCK_WAREHOUSE,
    AllocationPrior,
    MeanAllocation,
    WarehouseMean,
    WindowExposure,
    allocate_mean,
    allocation_window,
    build_prior,
    compute_warehouse_shares,
    measure_exposure,
    restrict_prior,
)
from pango_prediction.domain.types import (
    AvailabilityState,
    RegularizedSeries,
    StockHandling,
    WeeklyAvailability,
)

AVAILABLE = AvailabilityState.AVAILABLE
CENSORED = AvailabilityState.CENSORED
OUT_OF_ASSORTMENT = AvailabilityState.OUT_OF_ASSORTMENT
UNKNOWN = AvailabilityState.UNKNOWN

CUTOFF = date(2026, 4, 12)  # Sunday
WINDOW_WEEKS = 8
WINDOW = allocation_window(CUTOFF, WINDOW_WEEKS)


def exposure(exposed_weeks: int, observed_units: float) -> WindowExposure:
    return WindowExposure(
        window_weeks=WINDOW_WEEKS,
        exposed_weeks=exposed_weeks,
        observed_units=observed_units,
    )


def weekly(
    start: date,
    values: tuple[float, ...],
    states: tuple[AvailabilityState, ...] | None = None,
) -> tuple[RegularizedSeries, WeeklyAvailability]:
    """A weekly series and its availability, starting at ``start``."""
    states = states or (AVAILABLE,) * len(values)
    dates = tuple(start + timedelta(weeks=i) for i in range(len(values)))
    series = RegularizedSeries(series_id="PW-001", dates=dates, values=values)
    availability = WeeklyAvailability(
        series_id="PW-001",
        dates=dates,
        states=states,
        available_mask=tuple(state not in (CENSORED, OUT_OF_ASSORTMENT) for state in states),
        closing_stock=(None,) * len(values),
        stock_handling=StockHandling.STOCK_MANAGED,
    )
    return series, availability


# --- The window --------------------------------------------------------------


def test_window_is_the_complete_weeks_before_the_cutoff() -> None:
    assert len(WINDOW) == WINDOW_WEEKS
    assert WINDOW[0] == date(2026, 2, 16)
    assert WINDOW[-1] == date(2026, 4, 6)
    assert all(later - earlier == timedelta(weeks=1) for earlier, later in pairwise(WINDOW))


def test_window_drops_the_partial_week_of_a_midweek_cutoff() -> None:
    assert allocation_window(date(2026, 4, 15), WINDOW_WEEKS) == WINDOW


def test_window_needs_at_least_one_week() -> None:
    with pytest.raises(ValueError):
        allocation_window(CUTOFF, 0)


# --- Exposure ----------------------------------------------------------------


def test_projected_units_scale_the_exposed_weeks_to_the_window() -> None:
    assert exposure(7, 10.0).projected_units == pytest.approx(10.0 * 8 / 7)


def test_projected_units_are_unknown_without_exposed_weeks() -> None:
    assert exposure(0, 0.0).projected_units is None


@pytest.mark.parametrize(
    ("exposed_weeks", "observed_units"),
    [(9, 1.0), (-1, 0.0), (0, 1.0), (3, -1.0), (3, float("nan"))],
)
def test_exposure_rejects_impossible_counts(exposed_weeks: int, observed_units: float) -> None:
    with pytest.raises(ValueError):
        exposure(exposed_weeks, observed_units)


def test_a_fully_exposed_warehouse_counts_every_window_week() -> None:
    series, availability = weekly(WINDOW[0], (1.0, 0.0, 2.0, 0.0, 0.0, 3.0, 0.0, 1.0))

    result = measure_exposure(WINDOW, WINDOW[0] - timedelta(weeks=10), series, availability)

    assert result == exposure(8, 7.0)


def test_only_window_weeks_are_counted() -> None:
    start = WINDOW[0] - timedelta(weeks=4)
    series, availability = weekly(start, (50.0,) * 4 + (1.0,) * 8)

    result = measure_exposure(WINDOW, start, series, availability)

    assert result == exposure(8, 8.0)


def test_stockout_weeks_are_not_exposed_but_unknown_weeks_are() -> None:
    states = (
        AVAILABLE,
        CENSORED,
        AVAILABLE,
        OUT_OF_ASSORTMENT,
        AVAILABLE,
        AVAILABLE,
        UNKNOWN,
        UNKNOWN,
    )
    series, availability = weekly(WINDOW[0], (2.0, 0.0, 1.0, 0.0, 3.0, 0.0, 0.0, 0.0), states)

    result = measure_exposure(WINDOW, WINDOW[0], series, availability)

    assert result == exposure(6, 6.0)


def test_a_warehouse_that_arrives_mid_window_is_exposed_from_its_first_movement() -> None:
    # First movement on a Wednesday of the third window week, first sale two weeks later.
    first_movement = WINDOW[2] + timedelta(days=2)
    series, availability = weekly(WINDOW[4], (1.0, 0.0, 2.0, 1.0))

    result = measure_exposure(WINDOW, first_movement, series, availability)

    # Weeks 3 and 4 had the product but no sale: exposed, with zero units.
    assert result == exposure(6, 4.0)


def test_a_warehouse_that_never_sold_is_exposed_with_zero_units() -> None:
    series, availability = weekly(WINDOW[0], ())

    result = measure_exposure(WINDOW, WINDOW[5], series, availability)

    assert result == exposure(3, 0.0)


def test_exposure_rejects_a_series_that_runs_past_the_window() -> None:
    series, availability = weekly(WINDOW[0], (1.0,) * 9)

    with pytest.raises(ValueError, match="window"):
        measure_exposure(WINDOW, WINDOW[0], series, availability)


def test_exposure_rejects_a_first_movement_after_the_first_sale() -> None:
    series, availability = weekly(WINDOW[0], (1.0,) * 8)

    with pytest.raises(ValueError, match="first movement"):
        measure_exposure(WINDOW, WINDOW[1], series, availability)


def test_exposure_rejects_an_availability_from_another_grid() -> None:
    series, _ = weekly(WINDOW[0], (1.0,) * 8)
    _, other = weekly(WINDOW[0] - timedelta(weeks=1), (1.0,) * 9)

    with pytest.raises(ValueError):
        measure_exposure(WINDOW, WINDOW[0], series, other)


def test_exposure_rejects_a_window_with_gaps() -> None:
    series, availability = weekly(WINDOW[0], (1.0,) * 8)
    gapped = WINDOW[:3] + WINDOW[4:]

    with pytest.raises(ValueError):
        measure_exposure(gapped, WINDOW[0], series, availability)


# --- The prior ---------------------------------------------------------------


def catalogue() -> list[tuple[str | None, str, WindowExposure]]:
    """Projected units: G sells 6 in W1 and 2 in W2; H sells 2 in W1 and 0 in W3."""
    return [
        ("G", "W1", exposure(8, 6.0)),
        ("G", "W2", exposure(8, 2.0)),
        ("H", "W1", exposure(8, 2.0)),
        ("H", "W3", exposure(8, 0.0)),
        # Stocked out the whole window: no projected units, so no evidence.
        ("G", "W3", exposure(0, 0.0)),
    ]


def test_company_shares_split_the_projected_units_of_the_catalogue() -> None:
    prior = build_prior(catalogue(), prior_strength=2.0)

    assert prior.company_shares == pytest.approx({"W1": 0.8, "W2": 0.2, "W3": 0.0})


def test_group_shares_shrink_towards_the_company() -> None:
    prior = build_prior(catalogue(), prior_strength=2.0)

    # G: (units + 2 · company) / (8 + 2); H: (units + 2 · company) / (2 + 2).
    assert prior.group_shares["G"] == pytest.approx({"W1": 0.76, "W2": 0.24, "W3": 0.0})
    assert prior.group_shares["H"] == pytest.approx({"W1": 0.9, "W2": 0.1, "W3": 0.0})


def test_products_without_a_group_are_pooled_into_one_group() -> None:
    prior = build_prior([*catalogue(), (None, "W2", exposure(8, 4.0))], prior_strength=0.0)

    assert prior.group_shares[None] == pytest.approx({"W1": 0.0, "W2": 1.0, "W3": 0.0})


def test_a_group_without_units_and_without_strength_uses_the_company() -> None:
    prior = build_prior([*catalogue(), ("Z", "W1", exposure(8, 0.0))], prior_strength=0.0)

    assert prior.group_shares["Z"] == pytest.approx(prior.company_shares)


def test_a_catalogue_without_sales_has_zero_company_shares() -> None:
    prior = build_prior([("G", "W1", exposure(8, 0.0))], prior_strength=1.0)

    assert prior.company_shares == {"W1": 0.0}


def test_restrict_prior_renormalises_the_group_over_the_active_warehouses() -> None:
    prior = build_prior(catalogue(), prior_strength=2.0)

    assert restrict_prior(prior, "G", ["W2", "W1"]) == pytest.approx({"W1": 0.76, "W2": 0.24})
    assert restrict_prior(prior, "G", ["W2", "W3"]) == pytest.approx({"W2": 1.0, "W3": 0.0})


def test_restrict_prior_uses_the_company_for_an_unknown_group() -> None:
    prior = build_prior(catalogue(), prior_strength=2.0)

    assert restrict_prior(prior, "Z", ["W1", "W2"]) == pytest.approx({"W1": 0.8, "W2": 0.2})


def test_restrict_prior_falls_back_to_the_company_then_to_uniform() -> None:
    prior = build_prior(catalogue(), prior_strength=0.0)

    # H never sold in W2 or W3; the company sold in W2.
    assert restrict_prior(prior, "H", ["W2", "W3"]) == pytest.approx({"W2": 1.0, "W3": 0.0})
    # Nobody ever sold in W3 or W9.
    assert restrict_prior(prior, "H", ["W3", "W9"]) == pytest.approx({"W3": 0.5, "W9": 0.5})


def test_restrict_prior_without_active_warehouses_is_empty() -> None:
    assert restrict_prior(build_prior(catalogue(), prior_strength=1.0), "G", []) == {}


def test_restrict_prior_rejects_repeated_warehouses() -> None:
    with pytest.raises(ValueError):
        restrict_prior(build_prior(catalogue(), prior_strength=1.0), "G", ["W1", "W1"])


# --- Warehouse shares: the worked example of the specification ---------------

# Route A, "Desagregación producto → bodega", ejemplo numérico: warehouse A was
# stocked out one week, C does not hold stock.
EXAMPLE_EXPOSURES = {"A": exposure(7, 10.0), "B": exposure(8, 2.0), "C": exposure(8, 1.0)}
EXAMPLE_PRIOR_SHARES = {"A": 0.5, "B": 0.3, "C": 0.2}


def test_projected_units_reproduce_the_worked_example() -> None:
    projected = {
        warehouse_id: exposure.projected_units
        for warehouse_id, exposure in EXAMPLE_EXPOSURES.items()
    }

    assert projected == pytest.approx({"A": 11.43, "B": 2.00, "C": 1.00}, abs=5e-3)


@pytest.mark.parametrize(
    ("prior_strength", "expected"),
    [
        (1.18, (0.770, 0.151, 0.079)),
        (0.0, (0.792, 0.139, 0.069)),
        (0.59, (0.781, 0.145, 0.074)),
        (2.36, (0.751, 0.161, 0.088)),
        (1e9, (0.5, 0.3, 0.2)),
    ],
)
def test_warehouse_shares_reproduce_the_worked_example(
    prior_strength: float, expected: tuple[float, float, float]
) -> None:
    shares = compute_warehouse_shares(EXAMPLE_EXPOSURES, EXAMPLE_PRIOR_SHARES, prior_strength)

    assert (shares["A"], shares["B"], shares["C"]) == pytest.approx(expected, abs=5e-4)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_a_warehouse_without_exposure_is_imputed_from_the_prior() -> None:
    exposures = {"A": exposure(8, 6.0), "B": exposure(8, 2.0), "C": exposure(0, 0.0)}
    prior_shares = {"A": 0.5, "B": 0.3, "C": 0.2}

    shares = compute_warehouse_shares(exposures, prior_shares, prior_strength=1.0)

    # C is imputed 0.2 · (6 + 2) / (0.5 + 0.3) = 2 units, so the product has 10.
    assert shares == pytest.approx({"A": 6.5 / 11, "B": 2.3 / 11, "C": 2.2 / 11})


def test_imputation_is_zero_when_the_observed_warehouses_have_no_prior() -> None:
    exposures = {"A": exposure(8, 5.0), "B": exposure(0, 0.0)}

    shares = compute_warehouse_shares(exposures, {"A": 0.0, "B": 1.0}, prior_strength=1.0)

    assert shares == pytest.approx({"A": 5 / 6, "B": 1 / 6})


@pytest.mark.parametrize("prior_strength", [0.0, 1.18])
def test_a_product_without_projected_units_takes_the_prior(prior_strength: float) -> None:
    exposures = {"A": exposure(8, 0.0), "B": exposure(0, 0.0), "C": exposure(0, 0.0)}

    shares = compute_warehouse_shares(exposures, EXAMPLE_PRIOR_SHARES, prior_strength)

    assert shares == pytest.approx(EXAMPLE_PRIOR_SHARES)


def test_warehouse_shares_without_active_warehouses_are_empty() -> None:
    assert compute_warehouse_shares({}, {}, prior_strength=1.0) == {}


def test_warehouse_shares_reject_mismatched_warehouses() -> None:
    with pytest.raises(ValueError):
        compute_warehouse_shares({"A": exposure(8, 1.0)}, {"B": 1.0}, prior_strength=1.0)


def test_warehouse_shares_reject_a_prior_that_does_not_sum_to_one() -> None:
    with pytest.raises(ValueError):
        compute_warehouse_shares(EXAMPLE_EXPOSURES, {"A": 0.5, "B": 0.3, "C": 0.1}, 1.0)


@pytest.mark.parametrize("prior_strength", [-0.1, float("nan"), float("inf")])
def test_prior_strength_must_be_finite_and_non_negative(prior_strength: float) -> None:
    with pytest.raises(ValueError):
        compute_warehouse_shares(EXAMPLE_EXPOSURES, EXAMPLE_PRIOR_SHARES, prior_strength)

    with pytest.raises(ValueError):
        build_prior(catalogue(), prior_strength)


# --- Allocating the mean -----------------------------------------------------

PRODUCT_ESTIMATES = (1.2, 1.2, 1.2, 1.2)


def example_allocation() -> MeanAllocation:
    shares = compute_warehouse_shares(EXAMPLE_EXPOSURES, EXAMPLE_PRIOR_SHARES, 1.18)
    return allocate_mean(PRODUCT_ESTIMATES, shares, stock_warehouses={"A", "B"})


def test_stock_warehouses_receive_their_share_of_the_mean() -> None:
    allocation = example_allocation()
    by_id = {warehouse.warehouse_id: warehouse for warehouse in allocation.warehouses}

    assert allocation.reason is None
    assert by_id["B"].reason is None
    assert by_id["B"].estimates == pytest.approx((0.181,) * 4, abs=5e-4)


def test_the_share_of_a_non_stock_warehouse_is_not_forecast() -> None:
    by_id = {warehouse.warehouse_id: warehouse for warehouse in example_allocation().warehouses}

    assert by_id["C"].reason == NON_STOCK_WAREHOUSE
    assert by_id["C"].estimates == pytest.approx((0.079 * 1.2,) * 4, abs=5e-4)


def test_stock_means_and_the_no_forecast_part_add_up_to_the_product_mean() -> None:
    allocation = example_allocation()

    for week_index, product_estimate in enumerate(PRODUCT_ESTIMATES):
        forecast = sum(
            warehouse.estimates[week_index]
            for warehouse in allocation.warehouses
            if warehouse.reason is None
        )
        no_forecast = sum(
            warehouse.estimates[week_index]
            for warehouse in allocation.warehouses
            if warehouse.reason == NON_STOCK_WAREHOUSE
        )
        assert forecast + no_forecast == pytest.approx(product_estimate)


def test_warehouses_come_out_in_ascending_order() -> None:
    shares = {"W3": 0.2, "W1": 0.5, "W2": 0.3}

    allocation = allocate_mean((1.0,), shares, stock_warehouses={"W1", "W2", "W3"})

    assert [warehouse.warehouse_id for warehouse in allocation.warehouses] == ["W1", "W2", "W3"]


def test_a_product_without_active_warehouses_is_not_forecast() -> None:
    allocation = allocate_mean(PRODUCT_ESTIMATES, {}, stock_warehouses={"A"})

    assert allocation == MeanAllocation(warehouses=(), reason=NO_ACTIVE_WAREHOUSE)


def test_a_product_with_only_non_stock_warehouses_is_entirely_not_forecast() -> None:
    allocation = allocate_mean((2.0,), {"A": 0.25, "B": 0.75}, stock_warehouses=set())

    assert allocation.reason is None
    assert allocation.warehouses == (
        WarehouseMean("A", (0.5,), NON_STOCK_WAREHOUSE),
        WarehouseMean("B", (1.5,), NON_STOCK_WAREHOUSE),
    )


@pytest.mark.parametrize(
    ("estimates", "shares"),
    [
        ((1.0,), {"A": 0.5, "B": 0.4}),
        ((1.0,), {"A": 1.2, "B": -0.2}),
        ((-1.0,), {"A": 1.0}),
        ((float("nan"),), {"A": 1.0}),
        ((), {"A": 1.0}),
    ],
)
def test_allocate_mean_rejects_invalid_input(
    estimates: tuple[float, ...], shares: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        allocate_mean(estimates, shares, stock_warehouses={"A", "B"})


def test_mean_allocation_reason_matches_its_warehouses() -> None:
    with pytest.raises(ValueError):
        MeanAllocation(warehouses=(), reason=None)

    with pytest.raises(ValueError):
        MeanAllocation(warehouses=(WarehouseMean("A", (1.0,), None),), reason=NO_ACTIVE_WAREHOUSE)


def test_allocation_prior_is_a_plain_value() -> None:
    prior = AllocationPrior(company_shares={"W1": 1.0}, group_shares={})

    assert restrict_prior(prior, "G", ["W1"]) == {"W1": 1.0}
