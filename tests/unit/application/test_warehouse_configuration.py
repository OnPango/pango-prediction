"""Series the request itself says are not to be forecast.

Two facts about a series are not in its history and cannot be derived from it:
whether its warehouse holds stock, and whether the series is only there to
carry history. Both are administrator-managed configuration that Pango Core
sends with the request. A series they rule out is declined, with a reason of
its own, whatever the policy would have said about its demand.
"""

from datetime import date, timedelta

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.application.errors import SeriesErrorCode
from pango_prediction.application.forecast_service import (
    ForecastCommand,
    ForecastResultBatch,
    ForecastService,
    SeriesOutcome,
)
from pango_prediction.domain.parameters import DEFAULT_PARAMETERS
from pango_prediction.domain.policy import ModelPolicy, load_policy
from pango_prediction.domain.types import (
    DemandObservation,
    ForecastSeriesInput,
    NoForecastReason,
)

HISTORY_END = date(2026, 11, 1)  # a Sunday, so its week is complete
LAST_WEEK = date(2026, 10, 26)
HORIZON = 4


def policy() -> ModelPolicy:
    """Declines inactive series and forecasts everything else with `naive`."""
    return load_policy(
        {
            "policy_version": "test-1",
            "frequency": "WEEKLY",
            "horizon": HORIZON,
            "params_version": DEFAULT_PARAMETERS.fingerprint(),
            "rules": [{"when": {"inactive": True}, "reason": "inactive"}],
            "default": {"model": "naive"},
        },
        registry_names=frozenset(default_registry().names),
        parameters=DEFAULT_PARAMETERS,
    )


def selling_every_week(weeks: int = 30) -> tuple[DemandObservation, ...]:
    first_monday = LAST_WEEK - timedelta(weeks=weeks - 1)

    return tuple(
        DemandObservation(date=first_monday + timedelta(weeks=index, days=2), quantity=5.0)
        for index in range(weeks)
    )


def series(
    series_id: str = "1042",
    *,
    warehouse_id: str = "1",
    demand: tuple[DemandObservation, ...] | None = None,
    history_only: bool = False,
) -> ForecastSeriesInput:
    return ForecastSeriesInput(
        series_id=series_id,
        product_id="318",
        warehouse_id=warehouse_id,
        demand=selling_every_week() if demand is None else demand,
        stock=(),
        history_only=history_only,
    )


def run(
    *inputs: ForecastSeriesInput,
    holds_stock: dict[str, bool] | None = None,
    with_policy: bool = True,
) -> ForecastResultBatch:
    service = ForecastService(policy() if with_policy else None, default_registry())

    return service.run(
        ForecastCommand(
            history_end=HISTORY_END,
            horizon=HORIZON,
            series=inputs,
            warehouse_holds_stock={} if holds_stock is None else holds_stock,
        )
    )


def only(batch: ForecastResultBatch) -> SeriesOutcome:
    assert len(batch.outcomes) == 1

    return batch.outcomes[0]


# --- warehouses that hold no stock --------------------------------------------


def test_a_series_in_a_warehouse_that_holds_no_stock_is_declined() -> None:
    outcome = only(run(series(warehouse_id="7"), holds_stock={"7": False}))

    assert outcome.no_forecast is NoForecastReason.NON_STOCK_WAREHOUSE
    assert outcome.error is None
    assert outcome.periods == ()


def test_a_series_in_a_stock_warehouse_is_forecast_as_before() -> None:
    outcome = only(run(series(warehouse_id="1"), holds_stock={"1": True}))

    assert outcome.ok
    assert len(outcome.periods) == HORIZON


def test_only_the_series_of_the_non_stock_warehouse_are_declined() -> None:
    batch = run(
        series("a", warehouse_id="1"),
        series("b", warehouse_id="7"),
        holds_stock={"1": True, "7": False},
    )

    assert [outcome.no_forecast for outcome in batch.outcomes] == [
        None,
        NoForecastReason.NON_STOCK_WAREHOUSE,
    ]
    assert (batch.ok_count, batch.no_forecast_count, batch.error_count) == (1, 1, 0)


# --- a warehouse the request says nothing about -------------------------------


def test_a_warehouse_the_request_does_not_describe_counts_as_holding_stock() -> None:
    """The agreed default, so that a client that sends no block keeps working."""
    outcome = only(run(series(warehouse_id="1")))

    assert outcome.ok
    assert len(outcome.periods) == HORIZON


def test_the_series_says_when_its_warehouse_was_assumed_to_hold_stock() -> None:
    """Assumed, not known: the stockout reading depends on it, so it is said."""
    outcome = only(run(series(warehouse_id="1")))

    assert any(
        "warehouse 1" in warning and "holds stock" in warning
        for warning in outcome.warnings
    )


def test_a_described_warehouse_raises_no_such_warning() -> None:
    outcome = only(run(series(warehouse_id="1"), holds_stock={"1": True}))

    assert not any("holds stock" in warning for warning in outcome.warnings)


# --- series sent only for their history ---------------------------------------


def test_a_history_only_series_is_declined() -> None:
    outcome = only(run(series(history_only=True), holds_stock={"1": True}))

    assert outcome.no_forecast is NoForecastReason.HISTORY_ONLY
    assert outcome.error is None
    assert outcome.periods == ()


def test_a_history_only_series_still_reports_its_profile() -> None:
    """Every series sent gets a result, and this is what there is to say."""
    outcome = only(run(series(history_only=True), holds_stock={"1": True}))

    assert outcome.profile is not None
    assert len(outcome.horizon_business_days) == HORIZON


def test_history_only_is_the_reason_given_even_in_a_non_stock_warehouse() -> None:
    """The flag is about the series itself, so it speaks first."""
    outcome = only(run(series(warehouse_id="7", history_only=True), holds_stock={"7": False}))

    assert outcome.no_forecast is NoForecastReason.HISTORY_ONLY


def test_a_history_only_series_is_not_warned_about_its_warehouse() -> None:
    """Whether a historical warehouse holds stock decides nothing."""
    outcome = only(run(series(history_only=True)))

    assert not any("holds stock" in warning for warning in outcome.warnings)


# --- what the request says comes before what the policy would say -------------


def test_the_request_is_heard_before_the_policy() -> None:
    """A dormant series in a non-stock warehouse is declined for the warehouse.

    Both reasons hold. The one the administrator configured is reported,
    because it stays true however the series' demand changes.
    """
    dormant = (DemandObservation(date=date(2025, 1, 8), quantity=5.0),)

    outcome = only(run(series(warehouse_id="7", demand=dormant), holds_stock={"7": False}))

    assert outcome.no_forecast is NoForecastReason.NON_STOCK_WAREHOUSE


def test_the_request_declines_a_series_even_with_no_policy_loaded() -> None:
    """The decision does not come from the policy, so it does not need one."""
    outcome = only(run(series(history_only=True), with_policy=False))

    assert outcome.no_forecast is NoForecastReason.HISTORY_ONLY


def test_a_declined_series_with_no_demand_is_declined_not_failed() -> None:
    """Too little history is no failure for a series nobody meant to forecast."""
    outcome = only(run(series(demand=(), history_only=True)))

    assert outcome.no_forecast is NoForecastReason.HISTORY_ONLY
    assert outcome.error is None


def test_invalid_data_is_still_an_error_in_a_declined_series() -> None:
    """Bad data is bad data: declining the series must not hide it."""
    negative = (DemandObservation(date=date(2026, 10, 28), quantity=-1.0),)

    outcome = only(run(series(demand=negative, history_only=True)))

    assert outcome.no_forecast is None
    assert outcome.error is not None
    assert outcome.error.code is SeriesErrorCode.INVALID_SERIES
