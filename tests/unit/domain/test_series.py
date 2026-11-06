from datetime import date

import pytest

from pango_prediction.domain.series import build_weekly_series
from pango_prediction.domain.types import (
    DemandObservation,
    ForecastSeriesInput,
)

# Reference calendar used across these tests (verified weekdays):
#   2026-10-05 Mon .. 2026-10-11 Sun   -> week starting 2026-10-05
#   2026-10-12 Mon .. 2026-10-18 Sun   -> week starting 2026-10-12
#   2026-10-19 Mon .. 2026-10-25 Sun   -> week starting 2026-10-19
WEEK_1 = date(2026, 10, 5)
WEEK_2 = date(2026, 10, 12)
WEEK_3 = date(2026, 10, 19)

END_OF_WEEK_1 = date(2026, 10, 11)
END_OF_WEEK_3 = date(2026, 10, 25)


def make_input(
    demand: tuple[DemandObservation, ...],
) -> ForecastSeriesInput:
    return ForecastSeriesInput(
        series_id="PW-001",
        product_id="P-001",
        warehouse_id="W-001",
        demand=demand,
        stock=(),
    )


def test_monday_belongs_to_same_week_start() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.dates == (WEEK_1,)
    assert result.values == (5.0,)


def test_tuesday_is_grouped_into_previous_monday() -> None:
    series = make_input(
        (
            DemandObservation(
                date=date(2026, 10, 6),
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.dates == (WEEK_1,)
    assert result.values == (5.0,)


def test_sunday_is_grouped_into_previous_monday() -> None:
    series = make_input(
        (
            DemandObservation(
                date=END_OF_WEEK_1,
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.dates == (WEEK_1,)
    assert result.values == (5.0,)


def test_daily_demand_is_aggregated_by_week() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=3.0,
            ),
            DemandObservation(
                date=date(2026, 10, 6),
                quantity=2.0,
            ),
            DemandObservation(
                date=END_OF_WEEK_1,
                quantity=4.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.dates == (WEEK_1,)
    assert result.values == (9.0,)


def test_duplicate_daily_observations_are_aggregated() -> None:
    series = make_input(
        (
            DemandObservation(
                date=date(2026, 10, 6),
                quantity=2.0,
            ),
            DemandObservation(
                date=date(2026, 10, 6),
                quantity=3.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.values == (5.0,)


def test_missing_weeks_are_filled_with_zero() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=5.0,
            ),
            DemandObservation(
                date=WEEK_3,
                quantity=8.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_3)

    assert result.dates == (
        WEEK_1,
        WEEK_2,
        WEEK_3,
    )

    assert result.values == (
        5.0,
        0.0,
        8.0,
    )


def test_series_id_is_preserved() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.series_id == "PW-001"


def test_negative_demand_is_rejected() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=-3.0,
            ),
        )
    )

    with pytest.raises(ValueError, match="Demand quantity cannot be negative"):
        build_weekly_series(series, history_end=END_OF_WEEK_1)


def test_non_finite_demand_is_rejected() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=float("nan"),
            ),
        )
    )

    with pytest.raises(ValueError, match="Demand quantity must be finite"):
        build_weekly_series(series, history_end=END_OF_WEEK_1)


# --- history_end: trailing weeks, incomplete weeks and the start of the series ---


def test_trailing_weeks_without_demand_are_filled_until_history_end() -> None:
    """EDA D5: the grid runs to the end of history, not to the last week with demand.

    Without this, a stockout episode that reaches the end of the history is
    measured shorter than it really is (the EDA records this as limitation S37).
    """
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_3)

    assert result.dates == (
        WEEK_1,
        WEEK_2,
        WEEK_3,
    )

    assert result.values == (
        5.0,
        0.0,
        0.0,
    )


def test_incomplete_final_week_is_excluded() -> None:
    """EDA 7.5: the model series ends at the last complete Monday-Sunday week."""
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=5.0,
            ),
        )
    )

    # Tuesday: the week starting WEEK_3 ends on 2026-10-25 and is therefore partial.
    result = build_weekly_series(series, history_end=date(2026, 10, 20))

    assert result.dates == (
        WEEK_1,
        WEEK_2,
    )

    assert result.values == (
        5.0,
        0.0,
    )


def test_observation_after_history_end_is_rejected() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_3,
                quantity=5.0,
            ),
        )
    )

    with pytest.raises(ValueError, match="after history_end"):
        build_weekly_series(series, history_end=END_OF_WEEK_1)


def test_series_starts_at_first_week_with_positive_demand() -> None:
    """EDA D5: the series starts at the first sale, not at the first observation."""
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=0.0,
            ),
            DemandObservation(
                date=WEEK_3,
                quantity=7.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_3)

    assert result.dates == (WEEK_3,)
    assert result.values == (7.0,)


def test_empty_demand_returns_empty_series() -> None:
    series = make_input(())

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.series_id == "PW-001"
    assert result.dates == ()
    assert result.values == ()


def test_demand_without_any_positive_quantity_returns_empty_series() -> None:
    series = make_input(
        (
            DemandObservation(
                date=WEEK_1,
                quantity=0.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=END_OF_WEEK_1)

    assert result.dates == ()
    assert result.values == ()


def test_first_positive_demand_inside_incomplete_week_returns_empty_series() -> None:
    """EDA 7.5 found 4 products whose first sale fell in the final partial week."""
    series = make_input(
        (
            DemandObservation(
                date=WEEK_3,
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series, history_end=date(2026, 10, 20))

    assert result.dates == ()
    assert result.values == ()
