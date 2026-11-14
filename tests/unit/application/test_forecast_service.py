"""Tests for the forecast use case.

The service owns no forecasting algorithm, so what is asserted here is
orchestration: which domain functions run, in which order, what each series'
outcome is, and - the part easiest to get wrong - that the tenant's own
calibration and calendar actually reach the domain rather than sitting unused
on the command.
"""

from datetime import date, timedelta

import pytest

from pango_prediction.adapters.outbound.registry import default_registry
from pango_prediction.application.errors import SeriesErrorCode
from pango_prediction.application.forecast_service import (
    ForecastCommand,
    ForecastService,
)
from pango_prediction.domain.calendar import ClosedDates
from pango_prediction.domain.parameters import DEFAULT_PARAMETERS, DomainParameters
from pango_prediction.domain.policy import ModelPolicy, load_policy
from pango_prediction.domain.types import (
    DemandClass,
    DemandObservation,
    ForecastSeriesInput,
    HistoryClass,
    StockObservation,
)

HISTORY_END = date(2026, 11, 1)  # a Sunday, so its week is complete
LAST_WEEK = date(2026, 10, 26)
FIRST_HORIZON_WEEK = date(2026, 11, 2)
HORIZON = 4


def series_input(
    series_id: str = "1042",
    *,
    warehouse_id: str = "1",
    demand: tuple[DemandObservation, ...] = (),
    stock: tuple[StockObservation, ...] = (),
) -> ForecastSeriesInput:
    return ForecastSeriesInput(
        series_id=series_id,
        product_id="318",
        warehouse_id=warehouse_id,
        demand=demand,
        stock=stock,
    )


def weekly_sales(
    weeks: int,
    quantity: float = 5.0,
    *,
    last_week: date = LAST_WEEK,
) -> tuple[DemandObservation, ...]:
    """One sale every Wednesday for ``weeks`` weeks, ending in ``last_week``."""
    first_monday = last_week - timedelta(weeks=weeks - 1)

    return tuple(
        DemandObservation(
            date=first_monday + timedelta(weeks=index, days=2),
            quantity=quantity,
        )
        for index in range(weeks)
    )


def command(
    *series: ForecastSeriesInput,
    closed_dates: ClosedDates | None = None,
    parameters: DomainParameters | None = None,
) -> ForecastCommand:
    return ForecastCommand(
        history_end=HISTORY_END,
        horizon=HORIZON,
        series=series,
        closed_dates=closed_dates or ClosedDates(),
        parameters=parameters or DomainParameters(),
    )


# --- what a healthy series gets back ---


def test_a_series_is_profiled_even_though_no_model_is_deployed() -> None:
    """The point of this step: it cannot be forecast, and here is why."""
    outcome = ForecastService().run(
        command(series_input(demand=weekly_sales(30)))
    ).outcomes[0]

    assert outcome.error is not None
    assert outcome.error.code is SeriesErrorCode.MODEL_NOT_AVAILABLE

    assert outcome.profile is not None
    assert outcome.profile.history_weeks == 30
    assert outcome.profile.history_class is HistoryClass.LIMITED
    assert outcome.profile.inactive is False
    assert outcome.profile.evaluable is True


def test_the_covariate_has_one_value_per_week_of_the_horizon() -> None:
    outcome = ForecastService().run(
        command(series_input(demand=weekly_sales(30)))
    ).outcomes[0]

    assert outcome.horizon_business_days == pytest.approx((5.6, 5.6, 5.6, 5.6))


def test_no_policy_version_is_reported_while_no_policy_is_loaded() -> None:
    batch = ForecastService().run(command(series_input(demand=weekly_sales(12))))

    assert batch.policy_version is None


# --- the per-series failures, which never fail the batch ---


def test_a_series_with_no_demand_has_too_little_history() -> None:
    outcome = ForecastService().run(command(series_input())).outcomes[0]

    assert outcome.error is not None
    assert outcome.error.code is SeriesErrorCode.INSUFFICIENT_HISTORY
    assert outcome.profile is None


def test_negative_demand_is_rejected_as_invalid_input() -> None:
    """Demand is gross, so a return is not a negative sale."""
    bad = series_input(
        demand=(DemandObservation(date=date(2026, 10, 7), quantity=-4.0),)
    )

    outcome = ForecastService().run(command(bad)).outcomes[0]

    assert outcome.error is not None
    assert outcome.error.code is SeriesErrorCode.INVALID_SERIES
    assert outcome.profile is None


def test_one_bad_series_does_not_fail_the_batch() -> None:
    batch = ForecastService().run(
        command(
            series_input("1042", demand=weekly_sales(30)),
            series_input("1500", demand=(
                DemandObservation(date=date(2026, 10, 7), quantity=-1.0),
            )),
            series_input("1255", demand=weekly_sales(20)),
        )
    )

    assert [outcome.series_id for outcome in batch.outcomes] == ["1042", "1500", "1255"]
    assert batch.error_count == 3  # all three, since no model is deployed
    assert batch.outcomes[1].profile is None
    assert batch.outcomes[0].profile is not None
    assert batch.outcomes[2].profile is not None


# --- the tenant's calibration has to reach the domain ---


def test_the_commands_parameters_decide_inactivity_not_the_defaults() -> None:
    """A regression guard.

    The service used to call the domain without passing the command's
    parameters, so one tenant's thresholds were applied to every tenant's
    series. The same series is inactive under a short window and active under a
    long one, which can only be true if the command's value is the one used.
    """
    stale = series_input(demand=weekly_sales(6, last_week=date(2026, 6, 1)))

    short_window = ForecastService().run(
        command(stale, parameters=DomainParameters(inactivity_weeks=8))
    ).outcomes[0]
    long_window = ForecastService().run(
        command(stale, parameters=DomainParameters(inactivity_weeks=52))
    ).outcomes[0]

    assert short_window.profile is not None
    assert long_window.profile is not None

    assert short_window.profile.inactive is True
    assert long_window.profile.inactive is False


def test_the_commands_weekday_weights_decide_the_covariate() -> None:
    weekdays_only = DomainParameters(
        weekday_weights=(1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0)
    )

    outcome = ForecastService().run(
        command(series_input(demand=weekly_sales(30)), parameters=weekdays_only)
    ).outcomes[0]

    assert outcome.horizon_business_days == pytest.approx((5.0, 5.0, 5.0, 5.0))


# --- the calendar resolves per warehouse ---


def test_a_warehouse_closure_touches_only_that_warehouses_series() -> None:
    in_warehouse_3 = series_input("1198", warehouse_id="3", demand=weekly_sales(30))
    in_warehouse_1 = series_input("1042", warehouse_id="1", demand=weekly_sales(30))

    batch = ForecastService().run(
        command(
            in_warehouse_3,
            in_warehouse_1,
            closed_dates=ClosedDates.of(
                by_warehouse={"3": [FIRST_HORIZON_WEEK]}
            ),
        )
    )

    assert batch.outcomes[0].horizon_business_days[0] == pytest.approx(4.6)
    assert batch.outcomes[1].horizon_business_days[0] == pytest.approx(5.6)


def test_a_tenant_wide_closure_reaches_every_warehouse() -> None:
    batch = ForecastService().run(
        command(
            series_input("1198", warehouse_id="3", demand=weekly_sales(30)),
            series_input("1042", warehouse_id="1", demand=weekly_sales(30)),
            closed_dates=ClosedDates.of(everywhere=[FIRST_HORIZON_WEEK]),
        )
    )

    for outcome in batch.outcomes:
        assert outcome.horizon_business_days[0] == pytest.approx(4.6)


# --- the warnings, which are what Core can act on today ---


def test_an_inactive_series_says_so() -> None:
    stale = series_input(demand=weekly_sales(6, last_week=date(2026, 3, 2)))

    outcome = ForecastService().run(command(stale)).outcomes[0]

    assert outcome.profile is not None
    assert outcome.profile.inactive is True
    assert any("inactive" in warning for warning in outcome.warnings)


def test_a_series_with_too_few_observable_weeks_says_so() -> None:
    outcome = ForecastService().run(
        command(series_input(demand=weekly_sales(4)))
    ).outcomes[0]

    assert outcome.profile is not None
    assert outcome.profile.evaluable is False
    assert any("too few to forecast" in warning for warning in outcome.warnings)


def test_a_series_that_sold_in_one_month_says_its_pattern_is_unmeasurable() -> None:
    """One month of sales has no spread, so no quadrant can be computed."""
    one_month = series_input(
        demand=(
            DemandObservation(date=date(2026, 10, 7), quantity=5.0),
            DemandObservation(date=date(2026, 10, 14), quantity=7.0),
        )
    )

    outcome = ForecastService().run(command(one_month)).outcomes[0]

    assert outcome.profile is not None
    assert outcome.profile.demand_class is DemandClass.INSUFFICIENT
    assert any("too few months" in warning for warning in outcome.warnings)


def test_a_series_with_no_stock_history_says_censoring_was_not_evaluated() -> None:
    outcome = ForecastService().run(
        command(series_input(demand=weekly_sales(30)))
    ).outcomes[0]

    assert any("Stock handling" in warning for warning in outcome.warnings)


# --- a bad policy parameter must not sink the batch --------------------------


def intermittent_sales(weeks: int = 80, every: int = 6) -> tuple[DemandObservation, ...]:
    """One sale every ``every`` weeks, which profiles as INTERMITTENT.

    Long gaps between sales are what separates it from `weekly_sales`, whose
    steady weekly demand profiles as SMOOTH. The two shapes let one policy rule
    match one series and not the other.
    """
    first_monday = LAST_WEEK - timedelta(weeks=weeks - 1)

    return tuple(
        DemandObservation(
            date=first_monday + timedelta(weeks=index, days=2),
            quantity=20.0,
        )
        for index in range(weeks)
        if index % every == 0
    )


def policy_routing_everything_to(
    model: str, params: dict[str, object]
) -> ModelPolicy:
    """A policy whose default sends every series to ``model`` with ``params``.

    Built through `load_policy` rather than constructed by hand, so the test
    proves what the loader actually accepts. It cannot validate these params:
    only the model knows what it can run with, which is exactly why the model
    has to refuse them safely.
    """
    return load_policy(
        {
            "policy_version": "test-1",
            "frequency": "WEEKLY",
            "horizon": HORIZON,
            "params_version": DEFAULT_PARAMETERS.fingerprint(),
            "rules": [
                {
                    "when": {"inactive": True},
                    "reason": "Inactive series are not forecast.",
                }
            ],
            "default": {"model": model, "params": params},
        },
        registry_names=frozenset(default_registry().names),
        parameters=DEFAULT_PARAMETERS,
    )


def test_a_policy_parameter_the_model_cannot_accept_fails_one_series_at_a_time() -> None:
    """The regression this branch exists for.

    `moving_average` needs an integer window. Given a string it used to raise a
    bare ValueError, which no layer caught, so a single misconfigured rule
    turned the whole request into a 500 and lost the series that were fine.
    """
    policy = policy_routing_everything_to("moving_average", {"window": "four"})
    service = ForecastService(policy, default_registry())

    batch = service.run(
        command(
            series_input("1042", demand=weekly_sales(30)),
            series_input("1043", demand=weekly_sales(30)),
        )
    )

    assert batch.ok_count == 0
    assert [outcome.series_id for outcome in batch.outcomes] == ["1042", "1043"]

    for outcome in batch.outcomes:
        assert outcome.error is not None
        assert outcome.error.code is SeriesErrorCode.MODEL_NOT_AVAILABLE
        assert "window" in outcome.error.message
        # The series was understood; only the model could not run.
        assert outcome.profile is not None
        assert outcome.horizon_business_days


def test_a_misconfigured_model_does_not_lose_the_series_that_are_fine() -> None:
    """Partial results are the point of per-series errors.

    One rule is broken and the default is sound, so the inactive-series rule
    cannot be what saves the batch: the healthy series has to come back with a
    forecast while the broken one comes back with an error.
    """
    policy = load_policy(
        {
            "policy_version": "test-2",
            "frequency": "WEEKLY",
            "horizon": HORIZON,
            "params_version": DEFAULT_PARAMETERS.fingerprint(),
            "rules": [
                {
                    "when": {"demand_class": "SMOOTH"},
                    "model": "moving_average",
                    "params": {"window": 0},
                }
            ],
            "default": {"model": "naive"},
        },
        registry_names=frozenset(default_registry().names),
        parameters=DEFAULT_PARAMETERS,
    )
    service = ForecastService(policy, default_registry())

    batch = service.run(
        command(
            series_input("smooth", demand=weekly_sales(30, quantity=5.0)),
            series_input("lumpy", demand=intermittent_sales()),
        )
    )

    by_id = {outcome.series_id: outcome for outcome in batch.outcomes}

    assert by_id["smooth"].error is not None
    assert by_id["smooth"].error.code is SeriesErrorCode.MODEL_NOT_AVAILABLE
    assert by_id["lumpy"].error is None
    assert by_id["lumpy"].periods
