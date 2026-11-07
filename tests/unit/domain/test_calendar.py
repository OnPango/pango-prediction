"""Tests for the calendar covariate.

The dates used here are the ones the notebook names in section 11, so the
numbers can be checked against it rather than against this file.
"""

from datetime import date, timedelta

import pytest

from pango_prediction.domain.calendar import (
    day_weight,
    full_week_business_days,
    week_business_days,
    weekly_business_days,
)
from pango_prediction.domain.parameters import DomainParameters

NO_CLOSURES: frozenset[date] = frozenset()

# Monday 30 March 2026. Good Friday is 3 April and Holy Saturday 4 April.
EASTER_WEEK = date(2026, 3, 30)

# Monday 28 December 2026, the week the year-start closure falls in.
YEAR_END_WEEK = date(2026, 12, 28)


# --- the weight table ---


def test_a_full_week_is_five_point_six() -> None:
    """Monday to Friday at 1.0, Saturday at 0.6, Sunday at 0."""
    assert full_week_business_days() == pytest.approx(5.6)


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 3, 30), 1.0),  # Monday
        (date(2026, 4, 3), 1.0),  # Friday
        (date(2026, 4, 4), 0.6),  # Saturday
        (date(2026, 4, 5), 0.0),  # Sunday
    ],
)
def test_each_weekday_carries_its_weight(day: date, expected: float) -> None:
    assert day_weight(day, NO_CLOSURES) == pytest.approx(expected)


def test_an_unclosed_week_is_worth_a_full_week() -> None:
    assert week_business_days(date(2026, 6, 1), NO_CLOSURES) == pytest.approx(5.6)


# --- closed dates layer on top of the table ---


def test_closing_a_weekday_removes_a_whole_day() -> None:
    """One public holiday takes a week from 5.6 to 4.6, as the notebook found."""
    holiday = frozenset({date(2026, 6, 1)})

    assert week_business_days(date(2026, 6, 1), holiday) == pytest.approx(4.6)


def test_closing_a_saturday_removes_only_its_weight() -> None:
    closed = frozenset({date(2026, 6, 6)})

    assert week_business_days(date(2026, 6, 1), closed) == pytest.approx(5.0)


def test_closing_a_sunday_changes_nothing() -> None:
    """The weekday weight is already zero, so the two mechanisms layer."""
    closed = frozenset({date(2026, 6, 7)})

    assert week_business_days(date(2026, 6, 1), closed) == pytest.approx(5.6)


def test_holy_saturday_and_good_friday_together() -> None:
    """Holy Saturday is not an official Colombian holiday (notebook 11.2 B).

    It only reaches the calendar because the tenant sends it as a closed date,
    which is exactly why the dates are data rather than a computation.
    """
    closed = frozenset({date(2026, 4, 3), date(2026, 4, 4)})

    assert week_business_days(EASTER_WEEK, closed) == pytest.approx(4.0)


def test_the_year_start_closure_nearly_empties_its_week() -> None:
    """30 December to 6 January. The week of 28 December keeps two days."""
    closed = frozenset(
        {
            date(2026, 12, 30),
            date(2026, 12, 31),
            date(2027, 1, 1),
            date(2027, 1, 2),
            date(2027, 1, 3),
        }
    )

    assert week_business_days(YEAR_END_WEEK, closed) == pytest.approx(2.0)


def test_a_week_can_be_closed_entirely() -> None:
    closed = frozenset(
        {date(2026, 6, 1) + timedelta(days=offset) for offset in range(7)}
    )

    assert week_business_days(date(2026, 6, 1), closed) == 0.0


# --- the covariate is known into the future ---


def test_a_future_week_needs_no_history() -> None:
    """The value reads only the dates and the calibration.

    The notebook learned its year-start closure from past sales, which cannot
    be done for a future year, so its future year-ends scored a full week while
    its historical ones scored almost nothing. Here both are one rule.
    """
    assert week_business_days(date(2028, 5, 1), NO_CLOSURES) == pytest.approx(5.6)


def test_the_covariate_lines_up_with_the_weeks_it_was_asked_for() -> None:
    weeks = (date(2026, 3, 30), date(2026, 4, 6), date(2026, 4, 13))
    closed = frozenset({date(2026, 4, 3), date(2026, 4, 4)})

    assert weekly_business_days(weeks, closed) == pytest.approx((4.0, 5.6, 5.6))


def test_no_weeks_gives_no_values() -> None:
    assert weekly_business_days((), NO_CLOSURES) == ()


# --- guards ---


def test_a_week_that_does_not_start_on_monday_is_refused() -> None:
    """Otherwise a Tuesday would measure a Tuesday-to-Monday window."""
    with pytest.raises(ValueError, match="starts on a Monday"):
        week_business_days(date(2026, 3, 31), NO_CLOSURES)


# --- another tenant's calibration ---


def test_a_tenant_that_trades_on_sundays_has_a_seven_day_week() -> None:
    """The reason the whole table is a parameter and not a Saturday weight."""
    always_open = DomainParameters(weekday_weights=(1.0,) * 7)

    assert full_week_business_days(always_open) == pytest.approx(7.0)
    assert week_business_days(
        date(2026, 6, 1), NO_CLOSURES, always_open
    ) == pytest.approx(7.0)


def test_a_tenant_closed_on_saturdays() -> None:
    weekdays_only = DomainParameters(
        weekday_weights=(1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0)
    )

    assert full_week_business_days(weekdays_only) == pytest.approx(5.0)


@pytest.mark.parametrize(
    "weights,message",
    [
        ((1.0, 1.0, 1.0), "one weight per day"),
        ((1.0,) * 8, "one weight per day"),
        ((1.0, 1.0, 1.0, 1.0, 1.0, -0.5, 0.0), "non-negative"),
        ((0.0,) * 7, "at least one weekday"),
    ],
)
def test_an_unusable_weight_table_is_rejected(
    weights: tuple[float, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DomainParameters(weekday_weights=weights)
