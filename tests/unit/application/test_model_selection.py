from datetime import date, timedelta

import pytest

from pango_prediction.application.model_selection import (
    InsufficientBacktestHistoryError,
    select_model,
)
from pango_prediction.domain.parameters import DEFAULT_PARAMETERS
from pango_prediction.domain.series import build_weekly_series
from pango_prediction.domain.types import (
    DemandObservation,
    ForecastPeriod,
    ForecastSeriesInput,
)
from pango_prediction.ports.model import ModelError, ModelRequest
from pango_prediction.ports.registry import ModelRegistry

LAST_WEEK = date(2026, 10, 26)


class ConstantModel:
    def __init__(self, name: str, estimate: float) -> None:
        self.name = name
        self._estimate = estimate

    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        return tuple(
            ForecastPeriod(
                period_start=week,
                estimate=self._estimate,
                lower=self._estimate,
                upper=self._estimate,
            )
            for week in (
                request.series.dates[-1] + timedelta(weeks=step)
                for step in range(1, request.horizon + 1)
            )
        )


class FailingModel(ConstantModel):
    def predict(self, request: ModelRequest) -> tuple[ForecastPeriod, ...]:
        del request
        raise ModelError("candidate failed")


def input_series(weeks: int) -> ForecastSeriesInput:
    first_monday = LAST_WEEK - timedelta(weeks=weeks - 1)
    return ForecastSeriesInput(
        series_id="1042",
        product_id="318",
        warehouse_id="1",
        demand=tuple(
            DemandObservation(
                date=first_monday + timedelta(weeks=index, days=2),
                quantity=float(10 + index % 5),
            )
            for index in range(weeks)
        ),
        stock=(),
    )


def test_model_selection_ranks_candidates_across_multiple_metrics() -> None:
    series = input_series(24)
    regularized = build_weekly_series(series, LAST_WEEK + timedelta(days=6))
    registry = ModelRegistry(
        ConstantModel("naive", 12.0),
        ConstantModel("moving_average", 0.0),
        ConstantModel("sba", 30.0),
    )

    selection = select_model(
        series,
        regularized,
        horizon=4,
        registry=registry,
        parameters=DEFAULT_PARAMETERS,
    )

    assert selection.model.name == "naive"
    assert len(selection.scores) == 3
    assert selection.scores[0].mean_rank == pytest.approx(1.0)
    assert selection.scores[0].wape is not None
    assert selection.scores[0].bias is not None
    assert selection.scores[0].mae is not None
    assert selection.scores[0].mase is not None
    assert selection.scores[0].folds == 2


def test_a_candidate_failure_does_not_block_the_other_models() -> None:
    series = input_series(24)
    regularized = build_weekly_series(series, LAST_WEEK + timedelta(days=6))
    registry = ModelRegistry(
        ConstantModel("naive", 12.0),
        ConstantModel("moving_average", 0.0),
        FailingModel("sba", 30.0),
    )

    selection = select_model(
        series,
        regularized,
        horizon=4,
        registry=registry,
        parameters=DEFAULT_PARAMETERS,
    )

    assert selection.model.name == "naive"
    assert {score.model.name for score in selection.scores} == {
        "naive",
        "moving_average",
    }
    assert selection.excluded_models == (("sba", "ModelError"),)


def test_model_selection_requires_enough_history_for_two_full_folds() -> None:
    series = input_series(12)
    regularized = build_weekly_series(series, LAST_WEEK + timedelta(days=6))
    registry = ModelRegistry(ConstantModel("naive", 12.0), ConstantModel("sba", 30.0))

    with pytest.raises(InsufficientBacktestHistoryError, match="two"):
        select_model(
            series,
            regularized,
            horizon=4,
            registry=registry,
            parameters=DEFAULT_PARAMETERS,
        )
