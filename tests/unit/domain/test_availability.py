from datetime import date, timedelta

import pytest

from pango_prediction.domain.availability import (
    build_weekly_availability,
    classify_stock_handling,
)
from pango_prediction.domain.parameters import (
    DEFAULT_PARAMETERS,
    DomainParameters,
)
from pango_prediction.domain.types import (
    AvailabilityState,
    RegularizedSeries,
    StockHandling,
    StockObservation,
)

WEEK_1 = date(2026, 10, 5)  # Monday

MAX_STOCKOUT_EPISODE_WEEKS = DEFAULT_PARAMETERS.max_stockout_episode_weeks
MIN_WEEKS_WITH_STOCK_DATA = DEFAULT_PARAMETERS.min_weeks_with_stock_data

WARMUP = MIN_WEEKS_WITH_STOCK_DATA
"""Selling weeks prepended so that a scenario qualifies as stock managed (S24)."""

WARMUP_BALANCE = 40.0


def week(index: int) -> date:
    """Monday of the week ``index`` weeks after WEEK_1."""
    return WEEK_1 + timedelta(weeks=index)


def scenario_week(offset: int) -> date:
    """Monday of a scenario week, i.e. one that follows the warm-up."""
    return week(WARMUP + offset)


def stock_on(day: date, closing_stock: float) -> StockObservation:
    return StockObservation(date=day, closing_stock=closing_stock)


def make_series(values: tuple[float, ...]) -> RegularizedSeries:
    return RegularizedSeries(
        series_id="PW-001",
        dates=tuple(week(i) for i in range(len(values))),
        values=values,
    )


def stock_managed_series(values: tuple[float, ...]) -> RegularizedSeries:
    """``values`` preceded by selling weeks that establish stock management.

    Availability only evaluates censoring for series that are held in stock, and
    that needs at least ``MIN_WEEKS_WITH_STOCK_DATA`` selling weeks with a known
    opening balance. Scenario week ``k`` is therefore at index ``WARMUP + k``.
    """
    return make_series((5.0,) * WARMUP + values)


def stock_managed_stock(
    observations: tuple[StockObservation, ...],
) -> tuple[StockObservation, ...]:
    """``observations`` preceded by the warm-up's healthy balances."""
    warmup = (stock_on(week(0) - timedelta(days=1), WARMUP_BALANCE),) + tuple(
        stock_on(week(i), WARMUP_BALANCE) for i in range(WARMUP)
    )

    return warmup + observations


# --- one test per availability rule (plan B5) ---


def test_zero_demand_and_zero_closing_stock_is_censored() -> None:
    series = stock_managed_series((5.0, 0.0, 4.0))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(1), 0.0),
            stock_on(scenario_week(2), 7.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.states[WARMUP:] == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.CENSORED,
        AvailabilityState.AVAILABLE,
    )
    assert result.available_mask[WARMUP:] == (True, False, True)


def test_positive_demand_with_zero_closing_stock_stays_available() -> None:
    """Notebook S25: a week that sold until it ran out stays observed."""
    series = stock_managed_series((5.0, 3.0, 4.0))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(1), 0.0),
            stock_on(scenario_week(2), 7.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.states[WARMUP + 1] == AvailabilityState.AVAILABLE
    assert result.available_mask[WARMUP + 1] is True


def test_zero_demand_with_positive_stock_is_available() -> None:
    series = stock_managed_series((5.0, 0.0, 4.0))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(1), 8.0),
            stock_on(scenario_week(2), 7.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.states[WARMUP + 1] == AvailabilityState.AVAILABLE
    assert result.available_mask[WARMUP + 1] is True


def test_weeks_after_the_last_sale_are_unknown_not_censored() -> None:
    """Notebook 7.3: trailing zeros are inactivity and are handled by the profile."""
    series = stock_managed_series((5.0, 0.0, 0.0))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(1), 0.0),
            stock_on(scenario_week(2), 0.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.states[WARMUP:] == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.UNKNOWN,
        AvailabilityState.UNKNOWN,
    )
    assert result.available_mask[WARMUP:] == (True, True, True)


# --- weekly closing stock (notebook 7.2) ---


def test_weekly_closing_stock_is_the_last_observation_of_the_week() -> None:
    """Stock is a level, not a flow: the week's closing is its last daily closing."""
    series = stock_managed_series((5.0,))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(0) + timedelta(days=3), 4.0),
            stock_on(scenario_week(0) + timedelta(days=6), 2.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.closing_stock[WARMUP:] == (2.0,)


def test_week_without_observation_carries_the_previous_closing() -> None:
    """Notebook 7.2: a week without stock movement keeps the previous balance."""
    series = stock_managed_series((5.0, 0.0, 0.0, 2.0))
    stock = stock_managed_stock((stock_on(scenario_week(0), 6.0),))

    result = build_weekly_availability(series, stock)

    assert result.closing_stock[WARMUP:] == (6.0, 6.0, 6.0, 6.0)
    assert result.states[WARMUP + 1] == AvailabilityState.AVAILABLE


def test_negative_closing_stock_counts_as_zero_available() -> None:
    """Notebook 7.2: a negative balance means nothing available."""
    series = stock_managed_series((5.0, 0.0, 4.0))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(1), -3.0),
            stock_on(scenario_week(2), 7.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.closing_stock[WARMUP + 1] == -3.0
    assert result.states[WARMUP + 1] == AvailabilityState.CENSORED


def test_duplicate_stock_observations_keep_the_last_one() -> None:
    series = stock_managed_series((5.0,))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(0), 0.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.closing_stock[WARMUP:] == (0.0,)


def test_stock_observed_before_the_series_start_seeds_the_carry() -> None:
    series = make_series((5.0, 0.0, 4.0))
    stock = (stock_on(week(0) - timedelta(days=3), 9.0),)

    result = build_weekly_availability(series, stock)

    assert result.closing_stock == (9.0, 9.0, 9.0)
    assert result.states[1] == AvailabilityState.AVAILABLE


def test_non_finite_closing_stock_is_rejected() -> None:
    series = make_series((5.0,))
    stock = (stock_on(week(0), float("nan")),)

    with pytest.raises(ValueError, match="Closing stock must be finite"):
        build_weekly_availability(series, stock)


# --- stockout episodes and the 13-week threshold (notebook S26) ---


def _episode(
    episode_weeks: int,
) -> tuple[RegularizedSeries, tuple[StockObservation, ...]]:
    """A sale, then ``episode_weeks`` weeks of stockout, then another sale."""
    series = stock_managed_series((5.0,) + (0.0,) * episode_weeks + (3.0,))

    stock = stock_managed_stock(
        (stock_on(scenario_week(0), 10.0),)
        + tuple(stock_on(scenario_week(i + 1), 0.0) for i in range(episode_weeks))
        + (stock_on(scenario_week(episode_weeks + 1), 8.0),)
    )

    return series, stock


def test_stockout_episode_of_exactly_13_weeks_is_censored() -> None:
    series, stock = _episode(MAX_STOCKOUT_EPISODE_WEEKS)

    result = build_weekly_availability(series, stock)
    episode = result.states[WARMUP + 1 : WARMUP + 1 + MAX_STOCKOUT_EPISODE_WEEKS]

    assert len(episode) == MAX_STOCKOUT_EPISODE_WEEKS
    assert set(episode) == {AvailabilityState.CENSORED}


def test_stockout_episode_longer_than_13_weeks_is_out_of_assortment() -> None:
    length = MAX_STOCKOUT_EPISODE_WEEKS + 1
    series, stock = _episode(length)

    result = build_weekly_availability(series, stock)
    episode = result.states[WARMUP + 1 : WARMUP + 1 + length]

    assert len(episode) == length
    assert set(episode) == {AvailabilityState.OUT_OF_ASSORTMENT}


def test_out_of_assortment_weeks_are_masked_like_censored_weeks() -> None:
    length = MAX_STOCKOUT_EPISODE_WEEKS + 1
    series, stock = _episode(length)

    result = build_weekly_availability(series, stock)
    episode_mask = result.available_mask[WARMUP + 1 : WARMUP + 1 + length]

    assert set(episode_mask) == {False}


def test_week_without_observation_continues_the_episode_through_the_carry() -> None:
    """Scenario week 2 has no observation, but the carried balance is still zero."""
    series = stock_managed_series((5.0, 0.0, 0.0, 0.0, 4.0))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(1), 0.0),
            stock_on(scenario_week(3), 0.0),
            stock_on(scenario_week(4), 5.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.states[WARMUP + 1 : WARMUP + 4] == (
        AvailabilityState.CENSORED,
        AvailabilityState.CENSORED,
        AvailabilityState.CENSORED,
    )


def test_episodes_are_measured_independently() -> None:
    series = stock_managed_series((5.0, 0.0, 3.0, 0.0, 4.0))
    stock = stock_managed_stock(
        (
            stock_on(scenario_week(0), 10.0),
            stock_on(scenario_week(1), 0.0),
            stock_on(scenario_week(2), 6.0),
            stock_on(scenario_week(3), 0.0),
            stock_on(scenario_week(4), 6.0),
        )
    )

    result = build_weekly_availability(series, stock)

    assert result.states[WARMUP:] == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.CENSORED,
        AvailabilityState.AVAILABLE,
        AvailabilityState.CENSORED,
        AvailabilityState.AVAILABLE,
    )


# --- weeks with no stock information at all ---
#
# Once a balance is known it is carried forward, so a week can only lack stock
# information before the first observation. These series therefore start with an
# unobserved stretch and only then establish stock management.


def test_week_without_stock_information_is_unknown() -> None:
    series = make_series((5.0, 0.0, 5.0, 5.0, 5.0, 5.0, 5.0))
    stock = tuple(stock_on(week(i), 12.0) for i in range(2, 7))

    result = build_weekly_availability(series, stock)

    assert result.stock_handling is StockHandling.STOCK_MANAGED
    assert result.closing_stock[1] is None
    assert result.states[1] == AvailabilityState.UNKNOWN


def test_unknown_weeks_are_not_masked() -> None:
    """Notebook convention: only stockout weeks are masked (available_mask = y.notna())."""
    series = make_series((5.0, 0.0, 5.0, 5.0, 5.0, 5.0, 5.0))
    stock = tuple(stock_on(week(i), 12.0) for i in range(2, 7))

    result = build_weekly_availability(series, stock)

    assert set(result.available_mask) == {True}


def test_unknown_week_breaks_a_stockout_episode() -> None:
    """A week with no stock data cannot be absorbed into a stockout run."""
    series = make_series((5.0, 0.0, 0.0, 5.0, 5.0, 5.0, 5.0, 5.0))
    stock = (stock_on(week(2), 0.0),) + tuple(
        stock_on(week(i), 12.0) for i in range(3, 8)
    )

    result = build_weekly_availability(series, stock)

    assert result.stock_handling is StockHandling.STOCK_MANAGED
    assert result.states[:3] == (
        AvailabilityState.AVAILABLE,
        AvailabilityState.UNKNOWN,
        AvailabilityState.CENSORED,
    )


# --- stock handling, the S24 gate ---


def test_series_held_in_stock_is_classified_as_stock_managed() -> None:
    series = stock_managed_series(())
    stock = stock_managed_stock(())

    assert classify_stock_handling(series, stock) is StockHandling.STOCK_MANAGED


def test_series_selling_mostly_without_opening_stock_is_made_to_order() -> None:
    series = make_series((5.0,) * 5)
    stock = (stock_on(week(0) - timedelta(days=1), 0.0),) + tuple(
        stock_on(week(i), 0.0) for i in range(5)
    )

    assert classify_stock_handling(series, stock) is StockHandling.MADE_TO_ORDER


def test_exactly_half_of_the_selling_weeks_without_stock_is_made_to_order() -> None:
    """The notebook's threshold is inclusive: pct_abre_0 >= 0.5."""
    series = make_series((5.0,) * 4)
    stock = (
        stock_on(week(0) - timedelta(days=1), 10.0),
        stock_on(week(0), 0.0),
        stock_on(week(1), 0.0),
        stock_on(week(2), 10.0),
        stock_on(week(3), 10.0),
    )

    assert classify_stock_handling(series, stock) is StockHandling.MADE_TO_ORDER


def test_below_half_of_the_selling_weeks_without_stock_is_stock_managed() -> None:
    series = make_series((5.0,) * 4)
    stock = (
        stock_on(week(0) - timedelta(days=1), 10.0),
        stock_on(week(0), 0.0),
        stock_on(week(1), 10.0),
        stock_on(week(2), 10.0),
        stock_on(week(3), 10.0),
    )

    assert classify_stock_handling(series, stock) is StockHandling.STOCK_MANAGED


def test_too_few_selling_weeks_with_stock_data_is_not_classifiable() -> None:
    series = make_series((5.0,) * (MIN_WEEKS_WITH_STOCK_DATA - 1))
    stock = (stock_on(week(0) - timedelta(days=1), 10.0),) + tuple(
        stock_on(week(i), 10.0) for i in range(MIN_WEEKS_WITH_STOCK_DATA - 1)
    )

    assert classify_stock_handling(series, stock) is StockHandling.NOT_CLASSIFIABLE


def test_not_classifiable_takes_precedence_over_made_to_order() -> None:
    """The notebook applies the sin_dato assignment last, so it overrides."""
    series = make_series((5.0,) * (MIN_WEEKS_WITH_STOCK_DATA - 1))
    stock = (stock_on(week(0) - timedelta(days=1), 0.0),) + tuple(
        stock_on(week(i), 0.0) for i in range(MIN_WEEKS_WITH_STOCK_DATA - 1)
    )

    assert classify_stock_handling(series, stock) is StockHandling.NOT_CLASSIFIABLE


def test_made_to_order_series_has_no_censored_weeks() -> None:
    """Zero stock is this product's normal state, so it says nothing about demand."""
    series = make_series((5.0, 5.0, 5.0, 5.0, 5.0, 0.0, 5.0))
    stock = (stock_on(week(0) - timedelta(days=1), 0.0),) + tuple(
        stock_on(week(i), 0.0) for i in range(7)
    )

    result = build_weekly_availability(series, stock)

    assert result.stock_handling is StockHandling.MADE_TO_ORDER
    assert AvailabilityState.CENSORED not in result.states
    assert result.states[5] == AvailabilityState.UNKNOWN
    assert set(result.available_mask) == {True}


def test_not_classifiable_series_has_no_censored_weeks() -> None:
    series = make_series((5.0, 0.0, 5.0))
    stock = (
        stock_on(week(0) - timedelta(days=1), 10.0),
        stock_on(week(0), 0.0),
        stock_on(week(1), 0.0),
        stock_on(week(2), 0.0),
    )

    result = build_weekly_availability(series, stock)

    assert result.stock_handling is StockHandling.NOT_CLASSIFIABLE
    assert AvailabilityState.CENSORED not in result.states
    assert result.states[1] == AvailabilityState.UNKNOWN


# --- shape of the result ---


def test_series_id_and_dates_are_preserved() -> None:
    series = make_series((5.0, 0.0))

    result = build_weekly_availability(series, ())

    assert result.series_id == "PW-001"
    assert result.dates == series.dates


# --- a different tenant's calibration gives a different answer ---
#
# This is the whole point of the parameters being tenant-scoped: the same
# history is read differently by a company that replenishes weekly than by one
# that replenishes monthly.


def test_a_shorter_stockout_threshold_turns_a_gap_into_out_of_assortment() -> None:
    """A tenant replenishing weekly reads a 5-week gap as no longer supplied."""
    series, stock = _episode(5)
    parameters = DomainParameters(max_stockout_episode_weeks=4)

    with_default = build_weekly_availability(series, stock)
    with_tenant = build_weekly_availability(series, stock, parameters)

    assert with_default.states[WARMUP + 1] == AvailabilityState.CENSORED
    assert with_tenant.states[WARMUP + 1] == AvailabilityState.OUT_OF_ASSORTMENT


def test_a_lower_data_requirement_makes_a_short_series_classifiable() -> None:
    series = make_series((5.0, 5.0))
    stock = (
        stock_on(week(0) - timedelta(days=1), 10.0),
        stock_on(week(0), 8.0),
        stock_on(week(1), 6.0),
    )

    assert classify_stock_handling(series, stock) is StockHandling.NOT_CLASSIFIABLE

    relaxed = DomainParameters(min_weeks_with_stock_data=2)

    assert (
        classify_stock_handling(series, stock, relaxed)
        is StockHandling.STOCK_MANAGED
    )


def test_a_stricter_made_to_order_threshold_keeps_a_series_stock_managed() -> None:
    """Half the selling weeks open empty: made to order at 0.5, not at 0.75."""
    series = make_series((5.0,) * 4)
    stock = (
        stock_on(week(0) - timedelta(days=1), 10.0),
        stock_on(week(0), 0.0),
        stock_on(week(1), 0.0),
        stock_on(week(2), 10.0),
        stock_on(week(3), 10.0),
    )

    assert classify_stock_handling(series, stock) is StockHandling.MADE_TO_ORDER

    strict = DomainParameters(made_to_order_threshold=0.75)

    assert (
        classify_stock_handling(series, stock, strict)
        is StockHandling.STOCK_MANAGED
    )


def test_the_default_calibration_is_used_when_none_is_given() -> None:
    series, stock = _episode(MAX_STOCKOUT_EPISODE_WEEKS)

    implicit = build_weekly_availability(series, stock)
    explicit = build_weekly_availability(series, stock, DEFAULT_PARAMETERS)

    assert implicit.states == explicit.states
    assert implicit.stock_handling is explicit.stock_handling


def test_empty_series_returns_empty_availability() -> None:
    series = RegularizedSeries(series_id="PW-001", dates=(), values=())

    result = build_weekly_availability(series, ())

    assert result.dates == ()
    assert result.states == ()
    assert result.available_mask == ()
    assert result.closing_stock == ()
    assert result.stock_handling is StockHandling.NOT_CLASSIFIABLE
