"""Tests for the SBA model.

The reference values were produced by StatsForecast 2.1.1 (`CrostonSBA`) and,
for the 4-week totals, by an independent numpy computation of the same recipe.
They are written here as numbers so the service needs neither library to run
its tests. Tolerance: 1e-9 absolute, agreed with Route A.
"""

from datetime import date, timedelta

import pytest

from pango_prediction.adapters.outbound.croston import (
    DEFAULT_ALPHA,
    SbaModel,
    fit_sba,
)
from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.types import RegularizedSeries, StockObservation
from pango_prediction.ports.model import (
    InsufficientHistoryError,
    ModelRequest,
    ModelUnavailableError,
)

TOLERANCE = 1e-9
WEEK_1 = date(2026, 10, 5)  # Monday

# Fixture A: 40 intermittent weeks, starting at a sale as every domain series does.
SERIES_A = (3, 0, 0, 2, 0, 5, 0, 0, 0, 1, 0, 4, 0, 0, 2, 0, 0, 0, 0, 6,
            0, 1, 0, 0, 3, 0, 2, 0, 0, 0, 4, 0, 0, 1, 0, 0, 5, 0, 0, 2)
# Fixture SHORT: 9 weeks, too few for 8 four-week sums, so the total falls back.
SERIES_SHORT = (5, 0, 0, 0, 3, 0, 1, 0, 0)

# CrostonSBA().fit(y).predict(h=4)["mean"][0], StatsForecast 2.1.1.
ORACLE_MEANS = {
    "A": 1.107990919260148,
    "B_leading_zeros": 1.1490285056009626,
    "C_lumpy": 5.425686007034402,
    "D_two_sales": 3.507692307692307,
}
ORACLE_SERIES = {
    "A": SERIES_A,
    "B_leading_zeros": (0, 0, 4, 0, 1, 0, 0, 3, 0, 0, 0, 2, 0, 7, 0, 0, 1, 0),
    "C_lumpy": (12, 0, 0, 0, 1, 0, 0, 30, 0, 0, 2, 0, 0, 0, 0, 18, 0, 1, 0, 0),
    "D_two_sales": (5, 0, 0, 0, 3),
}


def week(index: int) -> date:
    return WEEK_1 + timedelta(weeks=index)


def make_request(
    values: tuple[int, ...],
    *,
    masked: tuple[int, ...] = (),
    params: dict[str, object] | None = None,
) -> ModelRequest:
    """A 4-week request. ``masked`` weeks are censored stockouts.

    With no stock the weeks are UNKNOWN and not masked. To censor a week the
    whole series gets stock data: 20 units on hand, 0 in the masked weeks.
    """
    series = RegularizedSeries(
        series_id="PW-SBA",
        dates=tuple(week(i) for i in range(len(values))),
        values=tuple(float(value) for value in values),
    )
    stock: tuple[StockObservation, ...] = ()
    if masked:
        stock = (
            StockObservation(date=week(0) - timedelta(days=1), closing_stock=20.0),
        ) + tuple(
            StockObservation(
                date=week(index),
                closing_stock=0.0 if index in masked else 20.0,
            )
            for index in range(len(values))
        )

    return ModelRequest(
        series=series,
        availability=build_weekly_availability(series, stock),
        horizon=4,
        params={} if params is None else params,
    )


# --- the point forecast matches StatsForecast ---


@pytest.mark.parametrize("name", sorted(ORACLE_MEANS))
def test_the_estimate_matches_statsforecast_croston_sba(name: str) -> None:
    fit = fit_sba(tuple(float(v) for v in ORACLE_SERIES[name]), DEFAULT_ALPHA)

    assert fit.estimate == pytest.approx(ORACLE_MEANS[name], abs=TOLERANCE)


def test_the_starting_state_is_the_first_sale_and_its_position() -> None:
    # First sale of 4 at index 2: size 4, interval 3. Forecast 0.95 * 4 / 3.
    fit = fit_sba((0.0, 0.0, 4.0, 0.0, 0.0, 1.0), DEFAULT_ALPHA)

    assert fit.origin_forecasts[:3] == (None, None, None)
    assert fit.origin_forecasts[3] == pytest.approx(0.95 * 4 / 3, abs=TOLERANCE)


def test_the_forecast_only_moves_on_a_sale() -> None:
    fit = fit_sba((3.0, 0.0, 0.0, 0.0, 2.0, 0.0), DEFAULT_ALPHA)

    assert fit.origin_forecasts[1] == fit.origin_forecasts[2] == fit.origin_forecasts[4]
    assert fit.origin_forecasts[5] != fit.origin_forecasts[4]


def test_the_forecast_is_flat_across_the_horizon() -> None:
    periods = SbaModel().predict(make_request(SERIES_A))

    assert len(periods) == 4
    for period in periods:
        assert period.estimate == pytest.approx(ORACLE_MEANS["A"], abs=TOLERANCE)
    assert [period.period_start for period in periods] == [week(40 + i) for i in range(4)]


# --- masked weeks are dropped, never read as zeros (S31) ---


def test_a_stockout_week_is_dropped_before_fitting() -> None:
    # Four selling weeks with stock data, so the series is stock managed (S24)
    # and week 2 (no sale, no stock) is a censored stockout.
    request = make_request((3, 0, 0, 2, 0, 4, 1, 0, 2), masked=(2,))
    assert request.availability.available_mask[2] is False

    censored = SbaModel().predict(request)
    contracted = fit_sba((3.0, 0.0, 2.0, 0.0, 4.0, 1.0, 0.0, 2.0), DEFAULT_ALPHA)
    as_zero = fit_sba((3.0, 0.0, 0.0, 2.0, 0.0, 4.0, 1.0, 0.0, 2.0), DEFAULT_ALPHA)

    assert censored[0].estimate == pytest.approx(contracted.estimate, abs=TOLERANCE)
    assert censored[0].estimate != pytest.approx(as_zero.estimate, abs=TOLERANCE)


# --- intervals ---


def test_the_weekly_interval_matches_the_reference() -> None:
    period = SbaModel().predict(make_request(SERIES_A))[0]

    assert period.lower == pytest.approx(0.0, abs=TOLERANCE)
    assert period.upper == pytest.approx(3.429210850531627, abs=TOLERANCE)


def test_the_total_interval_comes_from_real_four_week_sums() -> None:
    request = make_request(SERIES_A)
    model = SbaModel()
    total = model.predict_total(request, model.predict(request))

    assert total.estimate == pytest.approx(4.431963677040592, abs=TOLERANCE)
    assert total.lower == pytest.approx(0.0, abs=TOLERANCE)
    # The weekly residuals scaled by 4 would give 13.7168: almost three times wider.
    assert total.upper == pytest.approx(5.020589052349745, abs=TOLERANCE)


def test_a_short_series_falls_back_to_scaled_weekly_residuals() -> None:
    request = make_request(SERIES_SHORT)
    model = SbaModel()
    total = model.predict_total(request, model.predict(request))

    assert total.estimate == pytest.approx(12.259854014598538, abs=TOLERANCE)
    assert total.lower == pytest.approx(0.0, abs=TOLERANCE)
    assert total.upper == pytest.approx(12.259854014598538, abs=TOLERANCE)


# --- limits and parameters ---


def test_a_single_sale_is_not_enough_history() -> None:
    with pytest.raises(InsufficientHistoryError, match="at least 2"):
        SbaModel().predict(make_request((4, 0, 0, 0)))


def test_alpha_comes_from_the_policy_params() -> None:
    fast = SbaModel().predict(make_request(SERIES_A, params={"alpha": 0.3}))[0]

    assert fast.estimate == pytest.approx(
        fit_sba(tuple(float(v) for v in SERIES_A), 0.3).estimate, abs=TOLERANCE
    )
    assert fast.estimate != pytest.approx(ORACLE_MEANS["A"], abs=TOLERANCE)


@pytest.mark.parametrize("alpha", [0, -0.1, 1.5])
def test_an_alpha_outside_the_unit_range_is_rejected(alpha: float) -> None:
    with pytest.raises(ModelUnavailableError, match="'alpha' between"):
        SbaModel().predict(make_request(SERIES_A, params={"alpha": alpha}))


@pytest.mark.parametrize("alpha", [True, "0.1"])
def test_a_non_numeric_alpha_is_rejected(alpha: object) -> None:
    with pytest.raises(ModelUnavailableError, match="numeric 'alpha'"):
        SbaModel().predict(make_request(SERIES_A, params={"alpha": alpha}))


def test_sba_is_registered_under_its_policy_name() -> None:
    assert default_registry().resolve("sba").name == "sba"
