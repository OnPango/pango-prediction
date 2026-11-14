"""Tests for the baseline models and, through them, for the model port."""

from datetime import date, timedelta

import pytest

from pango_prediction.adapters.outbound.baselines import (
    MovingAverageModel,
    NaiveModel,
    ZeroModel,
)
from pango_prediction.domain.availability import (
    build_weekly_availability,
    observed_demand,
)
from pango_prediction.domain.types import (
    ForecastPeriod,
    RegularizedSeries,
    StockObservation,
)
from pango_prediction.ports.model import (
    ForecastModel,
    InsufficientHistoryError,
    ModelRequest,
    ModelUnavailableError,
)

WEEK_1 = date(2026, 10, 5)  # Monday
HORIZON = 4


def week(index: int) -> date:
    return WEEK_1 + timedelta(weeks=index)


def make_request(
    values: tuple[float, ...],
    *,
    stock: tuple[StockObservation, ...] = (),
    horizon: int = HORIZON,
    params: dict[str, object] | None = None,
) -> ModelRequest:
    """A request whose availability is derived from real stock observations.

    With no stock the weeks come back UNKNOWN, which is not masked, so the whole
    series is observable. Passing stock is how a test masks weeks.
    """
    series = RegularizedSeries(
        series_id="PW-001",
        dates=tuple(week(i) for i in range(len(values))),
        values=values,
    )

    return ModelRequest(
        series=series,
        availability=build_weekly_availability(series, stock),
        horizon=horizon,
        params={} if params is None else params,
    )


def stocked_out(
    values: tuple[float, ...],
    weeks: tuple[int, ...],
    params: dict[str, object] | None = None,
) -> ModelRequest:
    """A request where ``weeks`` are censored, so they must be dropped (S31).

    The seed balance before the series start gives the first selling week a
    known opening, which the series needs to be classed as stock managed.
    """
    stock = (
        StockObservation(date=week(0) - timedelta(days=1), closing_stock=20.0),
    ) + tuple(
        StockObservation(
            date=week(index),
            closing_stock=0.0 if index in weeks else 20.0,
        )
        for index in range(len(values))
    )

    return make_request(values, stock=stock, params=params)


# --- the contraction every non-seasonal model relies on (S31) ---


def test_observed_demand_drops_the_masked_weeks_and_joins_the_rest() -> None:
    request = stocked_out((6.0, 6.0, 0.0, 0.0, 6.0, 6.0), weeks=(2, 3))

    assert observed_demand(request.series, request.availability) == (
        6.0,
        6.0,
        6.0,
        6.0,
    )


def test_observed_demand_keeps_a_real_zero() -> None:
    """A week with stock on hand that did not sell is demand, and stays."""
    request = make_request(
        (6.0, 0.0, 6.0),
        stock=tuple(
            StockObservation(date=week(index), closing_stock=20.0)
            for index in range(3)
        ),
    )

    assert observed_demand(request.series, request.availability) == (6.0, 0.0, 6.0)


def test_observed_demand_refuses_a_mismatched_grid() -> None:
    series = RegularizedSeries(
        series_id="PW-001",
        dates=(week(0), week(1)),
        values=(1.0, 2.0),
    )
    shorter = RegularizedSeries(
        series_id="PW-001",
        dates=(week(0),),
        values=(1.0,),
    )

    with pytest.raises(ValueError, match="share one grid"):
        observed_demand(series, build_weekly_availability(shorter, ()))


# --- the port contract, checked through every implementation ---

MODELS: list[tuple[ForecastModel, dict[str, object]]] = [
    (ZeroModel(), {}),
    (NaiveModel(), {}),
    (MovingAverageModel(), {"window": 3}),
]


@pytest.mark.parametrize("model,params", MODELS, ids=lambda value: str(value))
def test_every_model_returns_one_period_per_horizon_step(
    model: ForecastModel,
    params: dict[str, object],
) -> None:
    request = make_request((4.0, 2.0, 5.0, 3.0, 6.0, 1.0), params=params)

    periods = model.predict(request)

    assert len(periods) == HORIZON
    assert all(isinstance(period, ForecastPeriod) for period in periods)


@pytest.mark.parametrize("model,params", MODELS, ids=lambda value: str(value))
def test_every_model_forecasts_the_weeks_after_the_history(
    model: ForecastModel,
    params: dict[str, object],
) -> None:
    request = make_request((4.0, 2.0, 5.0, 3.0, 6.0, 1.0), params=params)

    periods = model.predict(request)

    assert [period.period_start for period in periods] == [
        week(6),
        week(7),
        week(8),
        week(9),
    ]
    assert all(period.period_start.weekday() == 0 for period in periods)


@pytest.mark.parametrize("model,params", MODELS, ids=lambda value: str(value))
def test_every_model_returns_an_interval_around_its_estimate(
    model: ForecastModel,
    params: dict[str, object],
) -> None:
    """Mandatory: the schema stores both bounds as NOT NULL."""
    request = make_request((4.0, 2.0, 5.0, 3.0, 6.0, 1.0), params=params)

    for period in model.predict(request):
        assert period.lower <= period.estimate <= period.upper
        assert period.lower >= 0.0


def test_censored_weeks_are_dropped_before_averaging() -> None:
    """S31: a stockout zero is a missing observation, not real demand.

    Averaging it in pulls the forecast down, which is the skew the project
    exists to avoid. The censored weeks sit inside the averaging window, which
    is where they do the damage.

    Only the moving average can show this. Zero always forecasts zero, and the
    last week of the selling window is a sale by definition, so the naive
    forecast cannot change either.
    """
    values = (6.0, 6.0, 0.0, 0.0, 6.0, 6.0)
    params: dict[str, object] = {"window": 3}

    honest = MovingAverageModel().predict(
        stocked_out(values, weeks=(2, 3), params=params)
    )
    counting_the_zeros = MovingAverageModel().predict(
        make_request(values, params=params)
    )

    assert honest[0].estimate == pytest.approx(6.0)
    assert counting_the_zeros[0].estimate == pytest.approx(4.0)


# --- each model's own rule ---


def test_zero_forecasts_nothing() -> None:
    periods = ZeroModel().predict(make_request((4.0, 2.0, 5.0)))

    assert all(period.estimate == 0.0 for period in periods)


def test_zero_still_reports_an_upper_bound() -> None:
    """Forecasting nothing is not the same as claiming nothing can happen."""
    periods = ZeroModel().predict(make_request((4.0, 2.0, 5.0, 9.0)))

    assert periods[0].upper > 0.0


def test_naive_repeats_the_last_observable_week() -> None:
    periods = NaiveModel().predict(make_request((4.0, 2.0, 7.0)))

    assert all(period.estimate == 7.0 for period in periods)


def test_moving_average_uses_the_last_window_weeks() -> None:
    request = make_request((1.0, 1.0, 10.0, 20.0, 30.0), params={"window": 3})

    periods = MovingAverageModel().predict(request)

    assert periods[0].estimate == pytest.approx(20.0)


def test_moving_average_requires_its_window_from_the_policy() -> None:
    """An unstated window would silently differ from the evaluated model."""
    request = make_request((1.0, 2.0, 3.0, 4.0))

    with pytest.raises(ModelUnavailableError, match="integer 'window'"):
        MovingAverageModel().predict(request)


def test_moving_average_rejects_a_non_integer_window() -> None:
    request = make_request((1.0, 2.0, 3.0, 4.0), params={"window": 2.5})

    with pytest.raises(ModelUnavailableError, match="integer 'window'"):
        MovingAverageModel().predict(request)


def test_moving_average_rejects_a_window_below_one() -> None:
    request = make_request((1.0, 2.0, 3.0, 4.0), params={"window": 0})

    with pytest.raises(ModelUnavailableError, match="at least 1"):
        MovingAverageModel().predict(request)


# --- refusing rather than improvising ---


def test_naive_refuses_a_single_week_of_history() -> None:
    """One week gives no residual, so there would be no interval."""
    with pytest.raises(InsufficientHistoryError, match="at least 2"):
        NaiveModel().predict(make_request((4.0,)))


def test_moving_average_refuses_a_history_shorter_than_its_window() -> None:
    """A 26-week average over 6 weeks is not the model that was evaluated."""
    request = make_request((1.0, 2.0, 3.0), params={"window": 26})

    with pytest.raises(InsufficientHistoryError, match="at least 27"):
        MovingAverageModel().predict(request)


def test_a_model_refuses_an_empty_series() -> None:
    with pytest.raises(InsufficientHistoryError):
        ZeroModel().predict(
            ModelRequest(
                series=RegularizedSeries(series_id="PW-001", dates=(), values=()),
                availability=build_weekly_availability(
                    RegularizedSeries(series_id="PW-001", dates=(), values=()), ()
                ),
                horizon=HORIZON,
            )
        )


# --- the request validates itself at the boundary ---


def test_a_horizon_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one period"):
        make_request((1.0, 2.0), horizon=0)


def test_a_mismatched_availability_grid_is_rejected() -> None:
    """The guard that keeps demand and availability on one definition of week."""
    series = RegularizedSeries(
        series_id="PW-001",
        dates=(week(0), week(1)),
        values=(1.0, 2.0),
    )
    shorter = RegularizedSeries(
        series_id="PW-001",
        dates=(week(0),),
        values=(1.0,),
    )

    with pytest.raises(ValueError, match="share one grid"):
        ModelRequest(
            series=series,
            availability=build_weekly_availability(shorter, ()),
            horizon=HORIZON,
        )


def test_an_availability_for_another_series_is_rejected() -> None:
    series = RegularizedSeries(
        series_id="PW-001",
        dates=(week(0), week(1)),
        values=(1.0, 2.0),
    )
    other = RegularizedSeries(
        series_id="PW-999",
        dates=(week(0), week(1)),
        values=(1.0, 2.0),
    )

    with pytest.raises(ValueError, match="must match"):
        ModelRequest(
            series=series,
            availability=build_weekly_availability(other, ()),
            horizon=HORIZON,
        )


# --- the horizon total is read from real 4-week sums (D4) ---

# 40 intermittent weeks. Reference values from an independent numpy computation
# of the same recipe; the old weekly-residuals-times-4 upper bound is in each
# comment, to show what the correction changes.
SERIES_A = (3.0, 0, 0, 2, 0, 5, 0, 0, 0, 1, 0, 4, 0, 0, 2, 0, 0, 0, 0, 6,
            0, 1, 0, 0, 3, 0, 2, 0, 0, 0, 4, 0, 0, 1, 0, 0, 5, 0, 0, 2)


@pytest.mark.parametrize(
    ("model", "params", "expected"),
    [
        (ZeroModel(), None, (0.0, 0.0, 7.0)),  # x4 upper: 16.0
        (NaiveModel(), None, (8.0, 0.0, 15.0)),  # x4 upper: 24.0
        (MovingAverageModel(), {"window": 4}, (7.0, 2.2, 11.8)),  # x4: 0.5-21.5
    ],
)
def test_the_total_interval_matches_the_horizon_sum_reference(
    model: ForecastModel,
    params: dict[str, object] | None,
    expected: tuple[float, float, float],
) -> None:
    request = make_request(tuple(float(v) for v in SERIES_A), params=params)
    total = model.predict_total(request, model.predict(request))  # type: ignore[attr-defined]

    assert (total.estimate, total.lower, total.upper) == pytest.approx(
        expected, abs=1e-9
    )
