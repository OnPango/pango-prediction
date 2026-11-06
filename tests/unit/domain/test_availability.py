from datetime import date, timedelta

import pytest

from pango_prediction.domain.availability import (
    MAX_STOCKOUT_EPISODE_WEEKS,
    build_weekly_availability,
)
from pango_prediction.domain.types import (
    AvailabilityState,
    RegularizedSeries,
    StockObservation,
)

WEEK_1 = date(2026, 10, 5)  # Monday


def week(index: int) -> date:
    """Monday of the week ``index`` weeks after WEEK_1."""
    return WEEK_1 + timedelta(weeks=index)


def make_series(values: tuple[float, ...]) -> RegularizedSeries:
    return RegularizedSeries(
        series_id="PW-001",
        dates=tuple(week(i) for i in range(len(values))),
        values=values,
    )


def stock_on(day: date, closing_stock: float) -> StockObservation:
    return StockObservation(date=day, closing_stock=closing_stock)


# --- one test per availability rule (plan B5) ---


def test_zero_demand_and_zero_closing_stock_is_censored() -> None:
    series = make_series((5.0, 0.0, 4.0))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(1), 0.0),
        stock_on(week(2), 7.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.states == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.CENSORED,
        AvailabilityState.AVAILABLE,
    )
    assert result.available_mask == (True, False, True)


def test_positive_demand_with_zero_closing_stock_stays_available() -> None:
    """Notebook S25: a week that sold until it ran out stays observed."""
    series = make_series((5.0, 3.0, 4.0))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(1), 0.0),
        stock_on(week(2), 7.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.states[1] == AvailabilityState.AVAILABLE
    assert result.available_mask[1] is True


def test_zero_demand_with_positive_stock_is_available() -> None:
    series = make_series((5.0, 0.0, 4.0))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(1), 8.0),
        stock_on(week(2), 7.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.states[1] == AvailabilityState.AVAILABLE
    assert result.available_mask[1] is True


def test_week_without_stock_information_is_unknown() -> None:
    series = make_series((5.0, 0.0, 4.0))

    result = build_weekly_availability(series, ())

    assert result.states == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.UNKNOWN,
        AvailabilityState.AVAILABLE,
    )


def test_unknown_weeks_are_not_masked() -> None:
    """Notebook convention: only stockout weeks are masked (available_mask = y.notna())."""
    series = make_series((5.0, 0.0, 4.0))

    result = build_weekly_availability(series, ())

    assert result.available_mask == (True, True, True)


def test_weeks_after_the_last_sale_are_unknown_not_censored() -> None:
    """Notebook 7.3: trailing zeros are inactivity and are handled by the profile."""
    series = make_series((5.0, 0.0, 0.0))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(1), 0.0),
        stock_on(week(2), 0.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.states == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.UNKNOWN,
        AvailabilityState.UNKNOWN,
    )
    assert result.available_mask == (True, True, True)


# --- weekly closing stock (notebook 7.2) ---


def test_weekly_closing_stock_is_the_last_observation_of_the_week() -> None:
    """Stock is a level, not a flow: the week's closing is its last daily closing."""
    series = make_series((5.0,))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(0) + timedelta(days=3), 4.0),
        stock_on(week(0) + timedelta(days=6), 2.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.closing_stock == (2.0,)


def test_week_without_observation_carries_the_previous_closing() -> None:
    """Notebook 7.2: a week without stock movement keeps the previous balance."""
    series = make_series((5.0, 0.0, 0.0, 2.0))
    stock = (stock_on(week(0), 6.0),)

    result = build_weekly_availability(series, stock)

    assert result.closing_stock == (6.0, 6.0, 6.0, 6.0)
    assert result.states[1] == AvailabilityState.AVAILABLE


def test_negative_closing_stock_counts_as_zero_available() -> None:
    """Notebook 7.2: a negative balance means nothing available, not negative stock."""
    series = make_series((5.0, 0.0, 4.0))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(1), -3.0),
        stock_on(week(2), 7.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.closing_stock[1] == -3.0
    assert result.states[1] == AvailabilityState.CENSORED


def test_stock_observed_before_the_series_start_seeds_the_carry() -> None:
    series = make_series((5.0, 0.0, 4.0))
    stock = (stock_on(week(0) - timedelta(days=3), 9.0),)

    result = build_weekly_availability(series, stock)

    assert result.closing_stock == (9.0, 9.0, 9.0)
    assert result.states[1] == AvailabilityState.AVAILABLE


def test_duplicate_stock_observations_keep_the_last_one() -> None:
    series = make_series((5.0,))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(0), 0.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.closing_stock == (0.0,)


def test_non_finite_closing_stock_is_rejected() -> None:
    series = make_series((5.0,))
    stock = (stock_on(week(0), float("nan")),)

    with pytest.raises(ValueError, match="Closing stock must be finite"):
        build_weekly_availability(series, stock)


# --- stockout episodes and the 13-week threshold (notebook S26) ---


def _episode_series(episode_weeks: int) -> tuple[RegularizedSeries, tuple[StockObservation, ...]]:
    """A sale, then ``episode_weeks`` weeks of stockout, then another sale."""
    values = (5.0,) + (0.0,) * episode_weeks + (3.0,)
    series = make_series(values)

    stock = (
        (stock_on(week(0), 10.0),)
        + tuple(stock_on(week(i + 1), 0.0) for i in range(episode_weeks))
        + (stock_on(week(episode_weeks + 1), 8.0),)
    )

    return series, stock


def test_stockout_episode_of_exactly_13_weeks_is_censored() -> None:
    series, stock = _episode_series(MAX_STOCKOUT_EPISODE_WEEKS)

    result = build_weekly_availability(series, stock)
    episode = result.states[1 : MAX_STOCKOUT_EPISODE_WEEKS + 1]

    assert set(episode) == {AvailabilityState.CENSORED}


def test_stockout_episode_longer_than_13_weeks_is_out_of_assortment() -> None:
    series, stock = _episode_series(MAX_STOCKOUT_EPISODE_WEEKS + 1)

    result = build_weekly_availability(series, stock)
    episode = result.states[1 : MAX_STOCKOUT_EPISODE_WEEKS + 2]

    assert set(episode) == {AvailabilityState.OUT_OF_ASSORTMENT}


def test_out_of_assortment_weeks_are_masked_like_censored_weeks() -> None:
    series, stock = _episode_series(MAX_STOCKOUT_EPISODE_WEEKS + 1)

    result = build_weekly_availability(series, stock)
    episode_mask = result.available_mask[1 : MAX_STOCKOUT_EPISODE_WEEKS + 2]

    assert set(episode_mask) == {False}


def test_week_without_observation_continues_the_episode_through_the_carry() -> None:
    """Week 2 has no observation, but the carried balance is still zero."""
    series = make_series((5.0, 0.0, 0.0, 0.0, 4.0))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(1), 0.0),
        stock_on(week(3), 0.0),
        stock_on(week(4), 5.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.states[1:4] == (
        AvailabilityState.CENSORED,
        AvailabilityState.CENSORED,
        AvailabilityState.CENSORED,
    )


def test_unknown_week_breaks_a_stockout_episode() -> None:
    """A week with no stock data at all cannot extend a stockout run."""
    series = make_series((5.0, 0.0, 0.0, 4.0))
    stock = (
        stock_on(week(2), 0.0),
        stock_on(week(3), 5.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.states == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.UNKNOWN,
        AvailabilityState.CENSORED,
        AvailabilityState.AVAILABLE,
    )


def test_episodes_are_measured_independently() -> None:
    series = make_series((5.0, 0.0, 3.0, 0.0, 4.0))
    stock = (
        stock_on(week(0), 10.0),
        stock_on(week(1), 0.0),
        stock_on(week(2), 6.0),
        stock_on(week(3), 0.0),
        stock_on(week(4), 6.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.states == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.CENSORED,
        AvailabilityState.AVAILABLE,
        AvailabilityState.CENSORED,
        AvailabilityState.AVAILABLE,
    )


# --- shape of the result ---


def test_series_id_and_dates_are_preserved() -> None:
    series = make_series((5.0, 0.0))

    result = build_weekly_availability(series, ())

    assert result.series_id == "PW-001"
    assert result.dates == series.dates


def test_empty_series_returns_empty_availability() -> None:
    series = RegularizedSeries(series_id="PW-001", dates=(), values=())

    result = build_weekly_availability(series, ())

    assert result.dates == ()
    assert result.states == ()
    assert result.available_mask == ()
    assert result.closing_stock == ()
