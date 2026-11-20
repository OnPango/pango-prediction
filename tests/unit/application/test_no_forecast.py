"""A series the policy declines is not a failure.

Most of a catalogue is not forecast on purpose: inactive products, warehouses
that hold no stock. Reporting those as errors made a decision look like a
fault, and left Pango Core unable to tell "this broke" from "this was never
meant to be forecast". The use case therefore reports three outcomes, and it
does so regardless of which contract version the caller speaks: rendering an
outcome for an older client is the HTTP adapter's business.
"""

from datetime import date, timedelta
from pathlib import Path

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.application.errors import SeriesErrorCode
from pango_prediction.application.forecast_service import (
    ForecastCommand,
    ForecastResultBatch,
    ForecastService,
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

DEVELOPMENT_POLICY = Path(__file__).parents[3] / "config" / "development_policy.json"


def policy(inactive_rule: dict[str, object]) -> ModelPolicy:
    return load_policy(
        {
            "policy_version": "test-1",
            "frequency": "WEEKLY",
            "horizon": HORIZON,
            "params_version": DEFAULT_PARAMETERS.fingerprint(),
            "rules": [{"when": {"inactive": True}} | inactive_rule],
            "default": {"model": "naive"},
        },
        registry_names=frozenset(default_registry().names),
        parameters=DEFAULT_PARAMETERS,
    )


def series(series_id: str, demand: tuple[DemandObservation, ...]) -> ForecastSeriesInput:
    return ForecastSeriesInput(
        series_id=series_id,
        product_id="318",
        warehouse_id="1",
        demand=demand,
        stock=(),
    )


def selling_every_week(weeks: int = 30) -> tuple[DemandObservation, ...]:
    first_monday = LAST_WEEK - timedelta(weeks=weeks - 1)

    return tuple(
        DemandObservation(date=first_monday + timedelta(weeks=index, days=2), quantity=5.0)
        for index in range(weeks)
    )


def last_sold_long_ago() -> tuple[DemandObservation, ...]:
    """One sale almost two years before the cutoff: inactive by any threshold."""
    return (DemandObservation(date=date(2025, 1, 8), quantity=5.0),)


def batch_for(policy_: ModelPolicy, *inputs: ForecastSeriesInput) -> ForecastResultBatch:
    return ForecastService(policy_, default_registry()).run(
        ForecastCommand(history_end=HISTORY_END, horizon=HORIZON, series=inputs)
    )


def test_a_series_the_policy_declines_is_reported_as_no_forecast() -> None:
    batch = batch_for(
        policy({"reason": "inactive"}),
        series("dormant", last_sold_long_ago()),
    )

    outcome = batch.outcomes[0]

    assert outcome.no_forecast is NoForecastReason.INACTIVE
    assert outcome.error is None
    assert not outcome.ok
    assert outcome.periods == ()
    assert outcome.total is None
    assert outcome.model_name is None


def test_a_declined_series_still_says_what_the_service_learned() -> None:
    """The profile is the whole of what there is to say about such a series."""
    batch = batch_for(
        policy({"reason": "inactive"}),
        series("dormant", last_sold_long_ago()),
    )

    outcome = batch.outcomes[0]

    assert outcome.profile is not None
    assert outcome.profile.inactive
    assert any("inactive" in warning for warning in outcome.warnings)
    assert len(outcome.horizon_business_days) == HORIZON


def test_the_batch_counts_the_three_outcomes_apart() -> None:
    batch = batch_for(
        policy({"reason": "inactive"}),
        series("selling", selling_every_week()),
        series("dormant", last_sold_long_ago()),
        series("broken", (DemandObservation(date=date(2026, 10, 28), quantity=-1.0),)),
    )

    assert (batch.ok_count, batch.no_forecast_count, batch.error_count) == (1, 1, 1)
    assert [outcome.series_id for outcome in batch.outcomes] == [
        "selling",
        "dormant",
        "broken",
    ]


def test_declining_without_a_reason_is_still_an_error() -> None:
    """No reason, no status: the policy simply has nothing for the series.

    That keeps the path history insufficiency travels today. Route A's answer
    is that it stays an error until a policy rule routes it with a reason of
    its own, and none of the five agreed reasons is that one.
    """
    batch = batch_for(policy({}), series("dormant", last_sold_long_ago()))

    outcome = batch.outcomes[0]

    assert outcome.no_forecast is None
    assert outcome.error is not None
    assert outcome.error.code is SeriesErrorCode.MODEL_NOT_AVAILABLE
    assert batch.error_count == 1
    assert batch.no_forecast_count == 0


def test_the_development_policy_declines_an_inactive_series_with_the_agreed_reason() -> None:
    development = load_policy(
        DEVELOPMENT_POLICY,
        registry_names=frozenset(default_registry().names),
        parameters=DEFAULT_PARAMETERS,
    )

    batch = batch_for(development, series("dormant", last_sold_long_ago()))

    assert batch.outcomes[0].no_forecast is NoForecastReason.INACTIVE
