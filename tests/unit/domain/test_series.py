from datetime import date

import pytest

from pango_prediction.domain.series import build_weekly_series
from pango_prediction.domain.types import (
    DemandObservation,
    ForecastSeriesInput,
)


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
                date=date(2026, 10, 5),
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series)

    assert result.dates == (date(2026, 10, 5),)
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

    result = build_weekly_series(series)

    assert result.dates == (date(2026, 10, 5),)
    assert result.values == (5.0,)


def test_sunday_is_grouped_into_previous_monday() -> None:
    series = make_input(
        (
            DemandObservation(
                date=date(2026, 10, 11),
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series)

    assert result.dates == (date(2026, 10, 5),)
    assert result.values == (5.0,)
    
def test_daily_demand_is_aggregated_by_week() -> None:
    series = make_input(
        (
            DemandObservation(
                date=date(2026, 10, 5),
                quantity=3.0,
            ),
            DemandObservation(
                date=date(2026, 10, 6),
                quantity=2.0,
            ),
            DemandObservation(
                date=date(2026, 10, 11),
                quantity=4.0,
            ),
        )
    )

    result = build_weekly_series(series)

    assert result.dates == (date(2026, 10, 5),)
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

    result = build_weekly_series(series)

    assert result.values == (5.0,)
    
def test_missing_weeks_are_filled_with_zero() -> None:
    series = make_input(
        (
            DemandObservation(
                date=date(2026, 10, 5),
                quantity=5.0,
            ),
            DemandObservation(
                date=date(2026, 10, 19),
                quantity=8.0,
            ),
        )
    )

    result = build_weekly_series(series)

    assert result.dates == (
        date(2026, 10, 5),
        date(2026, 10, 12),
        date(2026, 10, 19),
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
                date=date(2026, 10, 5),
                quantity=5.0,
            ),
        )
    )

    result = build_weekly_series(series)

    assert result.series_id == "PW-001"
    
def test_negative_demand_is_rejected() -> None:
    series = make_input(
        (
            DemandObservation(
                date=date(2026, 10, 5),
                quantity=-3.0,
            ),
        )
    )

    with pytest.raises(ValueError, match="Demand quantity cannot be negative"):
        build_weekly_series(series)