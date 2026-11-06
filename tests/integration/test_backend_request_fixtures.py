"""Check the mock Pango Core requests against the real domain.

These fixtures are the contract instance Pango Core has to produce, so it is
not enough that they parse: each series has to land in the segment its name
claims. Otherwise the mocks would quietly stop exercising the paths they exist
to cover.

Parsing goes through the real request schema and the real adapter mapping, so
these tests also catch a fixture that drifts away from the contract.
"""

import json
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from pango_prediction.adapters.inbound.api.routes import to_series_input
from pango_prediction.adapters.inbound.api.schemas import ForecastRequest
from pango_prediction.domain.availability import build_weekly_availability
from pango_prediction.domain.series import build_weekly_series
from pango_prediction.domain.types import (
    AvailabilityState,
    StockHandling,
    WeeklyAvailability,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"

MONDAY = 0


def load_raw(name: str = "forecast_request.json") -> dict[str, Any]:
    """The fixture exactly as it sits on disk, to inspect its wire shape."""
    payload: dict[str, Any] = json.loads(
        (FIXTURES / name).read_text(encoding="utf-8")
    )

    return payload


def load_parsed(name: str = "forecast_request.json") -> ForecastRequest:
    """The fixture validated against the contract schema."""
    return ForecastRequest.model_validate(load_raw(name))


def availability_of(series_id: int) -> WeeklyAvailability:
    request = load_parsed()

    for series in request.series:
        if series.series_id == series_id:
            series_input = to_series_input(series)
            regularized = build_weekly_series(series_input, request.history_end)

            return build_weekly_availability(regularized, series_input.stock)

    raise AssertionError(f"series {series_id} is missing from the fixture")


def state_counts(availability: WeeklyAvailability) -> Counter[AvailabilityState]:
    return Counter(availability.states)


# --- the request envelope ---


def test_the_fixture_validates_against_the_contract_schema() -> None:
    request = load_parsed()

    assert request.contract_version == "1.0"
    assert request.frequency == "WEEKLY"
    assert request.horizon == 4
    assert request.series


def test_history_end_is_a_sunday() -> None:
    """A mid-week cutoff would silently drop the final partial week."""
    assert load_parsed().history_end.weekday() == 6


def test_identifiers_are_integers_as_in_the_tenant_schema() -> None:
    raw = load_raw()

    assert isinstance(raw["forecast_run_id"], int)

    for series in raw["series"]:
        assert isinstance(series["series_id"], int)
        assert isinstance(series["product_id"], int)
        assert isinstance(series["warehouse_id"], int)


def test_no_series_carries_a_made_to_order_flag() -> None:
    """There is no such column: the service derives it from history (S24)."""
    for series in load_raw()["series"]:
        assert "made_to_order" not in series["metadata"]


def test_metadata_carries_the_lead_time_from_the_schema() -> None:
    """`product_warehouses.lead_time_days` makes L data instead of an assumption."""
    for series in load_raw()["series"]:
        assert isinstance(series["metadata"]["lead_time_days"], int)


# --- every valid series produces a regular weekly grid ---


def test_valid_series_produce_gap_free_monday_grids() -> None:
    request = load_parsed()
    checked = 0

    for series in request.series:
        series_input = to_series_input(series)

        if any(row.quantity < 0 for row in series_input.demand):
            continue

        result = build_weekly_series(series_input, request.history_end)
        checked += 1

        assert result.dates, f"series {series.series_id} produced no weeks"
        assert all(day.weekday() == MONDAY for day in result.dates)
        assert result.values[0] > 0, "the series must start at the first sale"

        steps = {
            (later - earlier).days for earlier, later in pairwise(result.dates)
        }
        assert steps <= {7}, f"series {series.series_id} has missing weeks"

    assert checked == 4


# --- one test per segment the fixtures claim to cover ---


def test_stock_managed_series_shows_a_short_stockout() -> None:
    availability = availability_of(1042)
    counts = state_counts(availability)

    assert availability.stock_handling is StockHandling.STOCK_MANAGED
    assert counts[AvailabilityState.CENSORED] == 2
    assert counts[AvailabilityState.OUT_OF_ASSORTMENT] == 0


def test_made_to_order_series_is_never_censored() -> None:
    availability = availability_of(1198)
    counts = state_counts(availability)

    assert availability.stock_handling is StockHandling.MADE_TO_ORDER
    assert counts[AvailabilityState.CENSORED] == 0
    assert counts[AvailabilityState.OUT_OF_ASSORTMENT] == 0
    assert set(availability.available_mask) == {True}


def test_long_gap_series_is_flagged_out_of_assortment() -> None:
    availability = availability_of(1255)
    counts = state_counts(availability)

    assert availability.stock_handling is StockHandling.STOCK_MANAGED
    assert counts[AvailabilityState.OUT_OF_ASSORTMENT] == 15
    assert counts[AvailabilityState.CENSORED] == 0


def test_recently_stocked_series_cannot_be_classified() -> None:
    availability = availability_of(1403)
    counts = state_counts(availability)

    assert availability.stock_handling is StockHandling.NOT_CLASSIFIABLE
    assert counts[AvailabilityState.CENSORED] == 0
    assert len(availability.dates) == 3


def test_series_with_a_negative_quantity_is_rejected() -> None:
    """Partial failure: this one series fails, the batch stays valid."""
    request = load_parsed()

    invalid = next(
        series for series in request.series if series.series_id == 1500
    )

    with pytest.raises(ValueError, match="cannot be negative"):
        build_weekly_series(to_series_input(invalid), request.history_end)


# --- the fixtures and their index agree ---


def test_segment_index_matches_the_request() -> None:
    raw = load_raw()
    index: dict[str, Any] = json.loads(
        (FIXTURES / "segments.json").read_text(encoding="utf-8")
    )

    assert index["history_end"] == raw["history_end"]

    assert [entry["series_id"] for entry in index["series"]] == [
        series["series_id"] for series in raw["series"]
    ]


def test_minimal_request_holds_a_single_series() -> None:
    request = load_parsed("forecast_request_minimal.json")

    assert len(request.series) == 1
    assert request.series[0].series_id == 1042


def test_fixture_dates_are_within_the_history() -> None:
    request = load_parsed()
    history_end: date = request.history_end

    for series in request.series:
        for row in series.demand:
            assert row.date <= history_end
