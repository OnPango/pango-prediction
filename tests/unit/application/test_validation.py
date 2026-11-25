"""Tests for the holdout validation use case.

The validation holds out the last complete weeks of the history, forecasts them
with everything before, and scores the forecast with Route A's series-block
convention. What is asserted here is the part that is easy to get wrong:

- the forecast sees nothing after the cut, and is exactly the production run on
  the history cut there (no leakage, and parity with production);
- the held-out weeks are labelled by the domain's own availability rules;
- each kind of series ends in the state Route A's criteria give it, and none is
  silently scored as a perfect or a zero forecast by accident.

The delivered Route A policy is used, so the model is the deployed one: decline
inactive series, otherwise `moving_average(window=4)`.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.application.errors import SeriesErrorCode
from pango_prediction.application.forecast_service import (
    ForecastCommand,
    ForecastService,
)
from pango_prediction.application.validation import (
    HoldoutValidation,
    SeriesValidation,
    ValidationSkip,
    validation_window,
)
from pango_prediction.domain.parameters import DEFAULT_PARAMETERS
from pango_prediction.domain.policy import load_policy
from pango_prediction.domain.types import (
    AvailabilityState,
    DemandObservation,
    ForecastSeriesInput,
    NoForecastReason,
    StockObservation,
)

ROUTE_A_POLICY = Path(__file__).parents[3] / "config" / "model_policy.json"

HISTORY_END = date(2026, 11, 1)  # a Sunday
LAST_WEEK = date(2026, 10, 26)
HELD_OUT = (
    date(2026, 10, 5),
    date(2026, 10, 12),
    date(2026, 10, 19),
    date(2026, 10, 26),
)
TRAIN_END = date(2026, 10, 4)  # the Sunday before the first held-out week
HORIZON = 4

TRAIN = (5.0,) * 16 + (2.0, 3.0, 4.0, 5.0)
"""Twenty weeks before the cut. The last four average 3.5, the MA4 forecast."""

ACTUAL = (2.0, 4.0, 6.0, 3.0)


def route_a_service() -> ForecastService:
    registry = default_registry()
    policy = load_policy(
        ROUTE_A_POLICY,
        registry_names=frozenset(registry.names),
        parameters=DEFAULT_PARAMETERS,
    )
    return ForecastService(policy, registry)


def weekly_demand(
    values: Sequence[float], last_week: date = LAST_WEEK
) -> tuple[DemandObservation, ...]:
    """One sale on the Wednesday of each week; a zero week records no sale."""
    first = last_week - timedelta(weeks=len(values) - 1)

    return tuple(
        DemandObservation(date=first + timedelta(weeks=index, days=2), quantity=value)
        for index, value in enumerate(values)
        if value > 0
    )


def weekly_stock(
    closings: Sequence[float], last_week: date = LAST_WEEK
) -> tuple[StockObservation, ...]:
    """A closing balance on the Sunday of each week."""
    first = last_week - timedelta(weeks=len(closings) - 1)

    return tuple(
        StockObservation(date=first + timedelta(weeks=index, days=6), closing_stock=value)
        for index, value in enumerate(closings)
    )


def series(
    values: Sequence[float],
    closings: Sequence[float] | None = None,
    *,
    series_id: str = "1042",
    warehouse_id: str = "1",
    history_only: bool = False,
    extra_demand: tuple[DemandObservation, ...] = (),
) -> ForecastSeriesInput:
    closings = (10.0,) * len(values) if closings is None else closings

    return ForecastSeriesInput(
        series_id=series_id,
        product_id="318",
        warehouse_id=warehouse_id,
        demand=weekly_demand(values) + extra_demand,
        stock=weekly_stock(closings),
        history_only=history_only,
    )


def command(
    *items: ForecastSeriesInput,
    history_end: date = HISTORY_END,
    holds_stock: dict[str, bool] | None = None,
) -> ForecastCommand:
    return ForecastCommand(
        history_end=history_end,
        horizon=HORIZON,
        series=items,
        warehouse_holds_stock=holds_stock or {"1": True},
    )


def validate_one(
    item: ForecastSeriesInput,
    *,
    service: ForecastService | None = None,
    history_end: date = HISTORY_END,
    holds_stock: dict[str, bool] | None = None,
) -> SeriesValidation:
    validation = HoldoutValidation(service or route_a_service())
    results = validation.run(
        command(item, history_end=history_end, holds_stock=holds_stock)
    )
    assert len(results) == 1
    return results[0]


# --- the window ---


def test_the_window_is_the_last_complete_weeks_of_the_history() -> None:
    window = validation_window(HISTORY_END, HORIZON)

    assert window.train_end == TRAIN_END
    assert window.weeks == HELD_OUT
    assert window.labels_end == HISTORY_END


def test_a_trailing_partial_week_is_not_part_of_the_window() -> None:
    """Wednesday 4-nov: the week it starts is incomplete, as in the forecast."""
    assert validation_window(date(2026, 11, 4), HORIZON) == validation_window(
        HISTORY_END, HORIZON
    )


# --- a healthy series ---


def test_the_held_out_weeks_are_forecast_from_the_history_before_them() -> None:
    result = validate_one(series(TRAIN + ACTUAL))

    assert result.skipped is None
    assert result.train_end == TRAIN_END
    assert result.forecast.ok
    assert result.forecast.model_name == "moving_average"
    assert result.counted_as_zero is False

    assert [week.period_start for week in result.weeks] == list(HELD_OUT)
    assert [week.actual for week in result.weeks] == list(ACTUAL)
    assert [week.estimate for week in result.weeks] == pytest.approx([3.5] * 4)
    assert all(week.availability is AvailabilityState.AVAILABLE for week in result.weeks)
    assert all(week.observable for week in result.weeks)

    # Route A's block convention, worked by hand: y = 15, f = 14, e = f - y.
    metrics = result.metrics
    assert metrics is not None
    assert metrics.evaluable_weeks == 4
    assert metrics.actual_total == 15.0
    assert metrics.forecast_total == pytest.approx(14.0)
    assert metrics.error == pytest.approx(-1.0)
    assert metrics.wape == pytest.approx(1 / 15)
    assert metrics.bias == pytest.approx(-1 / 15)
    assert metrics.weekly_coverage is not None
    assert metrics.total_inside_interval is not None


def test_the_holdout_forecast_is_the_production_run_on_the_cut_history() -> None:
    """Parity: what is scored is what the service would have returned then.

    The cut is made here by hand, independently of the use case.
    """
    item = series(TRAIN + ACTUAL)
    service = route_a_service()
    cut = ForecastSeriesInput(
        series_id=item.series_id,
        product_id=item.product_id,
        warehouse_id=item.warehouse_id,
        demand=tuple(row for row in item.demand if row.date <= TRAIN_END),
        stock=tuple(row for row in item.stock if row.date <= TRAIN_END),
    )
    direct = service.run(
        ForecastCommand(
            history_end=TRAIN_END,
            horizon=HORIZON,
            series=(cut,),
            warehouse_holds_stock={"1": True},
        )
    ).outcomes[0]

    assert validate_one(item, service=service).forecast == direct


def test_nothing_after_the_cut_reaches_the_holdout_forecast() -> None:
    """Leakage: change every held-out week, and the forecast must not move."""
    seen = validate_one(series(TRAIN + ACTUAL))
    changed = validate_one(
        series(TRAIN + (40.0, 0.0, 0.0, 90.0), (10.0,) * 20 + (0.0, 0.0, 0.0, 5.0))
    )

    assert changed.forecast == seen.forecast
    assert [week.actual for week in changed.weeks] == [40.0, 0.0, 0.0, 90.0]
    assert changed.metrics != seen.metrics


def test_a_sale_in_the_trailing_partial_week_is_not_scored() -> None:
    late_sale = DemandObservation(date=date(2026, 11, 3), quantity=100.0)

    result = validate_one(
        series(TRAIN + ACTUAL, extra_demand=(late_sale,)),
        history_end=date(2026, 11, 4),
    )

    assert result.metrics is not None
    assert result.metrics.actual_total == 15.0


# --- how the held-out weeks are labelled ---


def test_a_stockout_week_is_left_out_of_the_error() -> None:
    """No sale and no stock, then it sells again: CENSORED, not zero demand."""
    result = validate_one(
        series(TRAIN + (2.0, 0.0, 6.0, 3.0), (10.0,) * 21 + (0.0, 10.0, 10.0))
    )

    week = result.weeks[1]
    assert week.availability is AvailabilityState.CENSORED
    assert week.observable is False

    assert result.metrics is not None
    assert result.metrics.evaluable_weeks == 3
    assert result.metrics.actual_total == 11.0
    assert result.metrics.forecast_total == pytest.approx(10.5)
    # Three weeks cannot be compared with a four-week total interval.
    assert result.metrics.total_inside_interval is None


def test_a_stockout_at_the_end_of_the_window_reads_as_zero_demand() -> None:
    """A known limitation, pinned so that changing it is a decision.

    The labels only see the history up to the end of the window. Weeks after a
    series' last sale are inactivity to the availability rules, so a stockout
    covering the end of the window cannot be told apart from no demand: it is
    UNKNOWN, which Route A counts as evaluable (criteria §1.3).
    """
    result = validate_one(
        series(TRAIN + (2.0, 0.0, 0.0, 0.0), (10.0,) * 21 + (0.0, 0.0, 0.0))
    )

    assert [week.availability for week in result.weeks[1:]] == [
        AvailabilityState.UNKNOWN
    ] * 3
    assert all(week.observable for week in result.weeks)
    assert result.metrics is not None
    assert result.metrics.actual_total == 2.0


def test_a_block_with_fewer_than_two_evaluable_weeks_is_not_scored() -> None:
    result = validate_one(
        series(TRAIN + (0.0, 0.0, 0.0, 3.0), (10.0,) * 20 + (0.0, 0.0, 0.0, 10.0))
    )

    assert result.skipped is ValidationSkip.TOO_FEW_EVALUABLE_WEEKS
    assert result.metrics is None
    assert result.forecast.ok
    # The facts are still returned, so the reason can be shown.
    assert [week.observable for week in result.weeks] == [False, False, False, True]
    assert result.weeks[0].estimate == pytest.approx(3.5)


# --- series without a model forecast at the cut (Route A DEC-1) ---


def test_a_series_inactive_at_the_cut_counts_as_a_zero_forecast() -> None:
    """Ten weeks of sales, thirty silent ones, then it sells again."""
    result = validate_one(series((5.0,) * 10 + (0.0,) * 30 + ACTUAL))

    assert result.forecast.no_forecast is NoForecastReason.INACTIVE
    assert result.counted_as_zero is True
    assert [week.estimate for week in result.weeks] == [0.0] * 4
    assert all(week.lower is None for week in result.weeks)

    assert result.metrics is not None
    assert result.metrics.error == -15.0
    assert result.metrics.wape == 1.0
    assert result.metrics.weekly_coverage is None
    assert result.metrics.total_inside_interval is None


def test_too_little_history_for_the_model_counts_as_a_zero_forecast() -> None:
    """Three weeks before the cut; MA4 needs five observable weeks."""
    result = validate_one(series((2.0, 3.0, 4.0) + ACTUAL))

    assert result.forecast.error is not None
    assert result.forecast.error.code is SeriesErrorCode.MODEL_FAILED
    assert result.counted_as_zero is True
    assert result.metrics is not None
    assert result.metrics.error == -15.0


# --- series that are not scored at all ---


def test_a_series_with_no_sale_before_the_cut_is_not_scored() -> None:
    result = validate_one(series(ACTUAL))

    assert result.skipped is ValidationSkip.NO_HISTORY_AT_CUTOFF
    assert result.metrics is None
    assert result.counted_as_zero is False


@pytest.mark.parametrize(
    ("item", "holds_stock"),
    [
        (series(TRAIN + ACTUAL, history_only=True), {"1": True}),
        (series(TRAIN + ACTUAL), {"1": False}),
    ],
    ids=["history_only", "non_stock_warehouse"],
)
def test_a_series_declined_by_the_request_is_not_scored(
    item: ForecastSeriesInput, holds_stock: dict[str, bool]
) -> None:
    """Configured out by an administrator, not by its demand: no error to show."""
    result = validate_one(item, holds_stock=holds_stock)

    assert result.skipped is ValidationSkip.DECLINED_BY_REQUEST
    assert result.metrics is None
    assert result.counted_as_zero is False


def test_a_failed_forecast_is_not_scored_as_zero() -> None:
    """No policy loaded: MODEL_NOT_AVAILABLE is not a forecast of nothing."""
    result = validate_one(series(TRAIN + ACTUAL), service=ForecastService())

    assert result.skipped is ValidationSkip.FORECAST_FAILED
    assert result.metrics is None


def test_invalid_data_in_the_held_out_weeks_is_reported_not_scored() -> None:
    bad = DemandObservation(date=date(2026, 10, 14), quantity=-1.0)

    result = validate_one(series(TRAIN + ACTUAL, extra_demand=(bad,)))

    # The cut history is valid, so the forecast itself ran.
    assert result.forecast.ok
    assert result.skipped is ValidationSkip.INVALID_SERIES
    assert result.metrics is None
    assert result.weeks == ()


# --- a batch ---


def test_each_series_keeps_its_own_identity_and_order() -> None:
    validation = HoldoutValidation(route_a_service())

    results = validation.run(
        command(
            series(TRAIN + ACTUAL, series_id="a"),
            series(ACTUAL, series_id="b"),
            series(TRAIN + (6.0, 6.0, 6.0, 6.0), series_id="c"),
        )
    )

    assert [result.series_id for result in results] == ["a", "b", "c"]
    assert results[0].metrics is not None
    assert results[0].metrics.actual_total == 15.0
    assert results[1].skipped is ValidationSkip.NO_HISTORY_AT_CUTOFF
    assert results[2].metrics is not None
    assert results[2].metrics.actual_total == 24.0
